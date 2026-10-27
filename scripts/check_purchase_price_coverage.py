"""Compare first XTB purchases with cached Yahoo closing prices.

This is an initial coverage check for currently held instruments.
It does not prove full trading-session coverage or calculate P&L.

No account identifiers, quantities or transaction IDs are exported.
"""

import argparse
import csv
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from pathlib import Path

from app.domain.operation_types import OperationType
from app.ingestion.xtb_cash import (
    CashOperation,
    read_cash_operations,
)
from app.ingestion.xtb_trade_comments import parse_trade_details
from app.market_data.portfolio_mapping import read_csv
from app.market_data.price_cache import (
    open_cache,
    read_closes,
)
from scripts.audit_verified_prices import (
    DEFAULT_POSITIONS,
    DEFAULT_REGISTRY,
    prepare_targets,
)
from scripts.sync_verified_prices import (
    DEFAULT_CACHE,
    cached_quality_flags,
    make_identity,
)


DEFAULT_OUTPUT = Path(
    "data/private/market_audit/purchase_price_coverage.csv"
)

REPORT_FIELDS = [
    "xtb_ticker",
    "yahoo_ticker",
    "first_purchase_utc",
    "first_cached_session",
    "last_cached_session",
    "first_quote_on_or_after_purchase",
    "days_to_first_quote",
    "cached_sessions",
    "status",
    "flags",
]


def load_first_purchases(
    export_files: Iterable[Path],
    *,
    reader: Callable[[Path], list[CashOperation]] = read_cash_operations,
) -> dict[str, date]:
    """Find first actual BUY for each XTB ticker across accounts.

    Duplicate account exports are rejected rather than merged.
    """
    files = list(export_files)

    if not files:
        raise ValueError("No XTB exports selected")

    account_sources = set()
    first_purchases = {}

    for file_path in files:
        operations = reader(file_path)

        account_ids = {
            operation.account_id
            for operation in operations
        }

        if len(account_ids) != 1:
            raise ValueError(
                f"{file_path.name}: cannot determine "
                "one Cash Operations account"
            )

        account_id = next(iter(account_ids))

        if not account_id or not account_id.strip():
            raise ValueError(
                f"{file_path.name}: empty account ID"
            )

        # Cash Operations is the authoritative source.
        # The filename only corroborates the account identity.
        if f"_{account_id}_" not in file_path.name:
            raise ValueError(
                f"{file_path.name}: filename does not "
                "match Cash Operations account"
            )

        if account_id in account_sources:
            raise ValueError(
                "Multiple exports for the same account. "
                "Select one current XLSX per account "
                "with repeated --file arguments."
            )

        account_sources.add(account_id)

        for operation in operations:
            if operation.operation_type != "Stock purchase":
                continue

            if (
                operation.time_utc.tzinfo is None
                or operation.time_utc.utcoffset() is None
            ):
                raise ValueError(
                    f"{file_path.name}: trade time "
                    "must have a timezone"
                )

            execution = parse_trade_details(operation)

            if execution.kind != OperationType.BUY:
                raise ValueError(
                    "Stock purchase parsed as another trade type"
                )

            ticker = execution.ticker.strip().upper()
            purchase_day = operation.time_utc.date()

            previous = first_purchases.get(ticker)

            if previous is None or purchase_day < previous:
                first_purchases[ticker] = purchase_day

    return first_purchases


def assess_purchase_coverage(
    targets: list[dict[str, str]],
    first_purchases: dict[str, date],
    connection,
    start: date,
    end: date,
) -> list[dict[str, str]]:
    """Check whether cached prices begin before the first purchase."""
    if type(start) is not date or type(end) is not date:
        raise ValueError("Expected date objects")

    if start >= end:
        raise ValueError("End must be later than start")

    report = []

    for target in targets:
        ticker = target["xtb_ticker"]

        row = {field: "" for field in REPORT_FIELDS}

        row["xtb_ticker"] = ticker
        row["yahoo_ticker"] = target["yahoo_ticker"]
        row["cached_sessions"] = "0"

        purchase_day = first_purchases.get(ticker)

        if purchase_day is not None:
            row["first_purchase_utc"] = (
                purchase_day.isoformat()
            )

        if not target["yahoo_ticker"]:
            row["status"] = "UNAPPROVED_IDENTITY"
            report.append(row)
            continue

        identity = make_identity(target)

        prices = read_closes(
            connection,
            identity,
            start,
            end,
        )

        days = sorted(prices)
        row["cached_sessions"] = str(len(days))

        flags = cached_quality_flags(
            connection,
            identity,
            start,
            end,
        )

        if days:
            first_cached = days[0]
            last_cached = days[-1]

            row["first_cached_session"] = (
                first_cached.isoformat()
            )
            row["last_cached_session"] = (
                last_cached.isoformat()
            )

            if (
                end - (last_cached + timedelta(days=1))
            ).days > 10:
                flags.add("STALE_LAST_QUOTE")

        if purchase_day is None:
            row["status"] = "NO_BUY_HISTORY"

        elif purchase_day < start:
            row["status"] = "BUY_BEFORE_CACHE_START"

        elif purchase_day >= end:
            row["status"] = "BUY_OUTSIDE_DATE_RANGE"

        elif not days:
            row["status"] = "NO_CACHED_PRICES"

        else:
            next_quotes = [
                day
                for day in days
                if day >= purchase_day
            ]

            if next_quotes:
                first_after_buy = next_quotes[0]

                row["first_quote_on_or_after_purchase"] = (
                    first_after_buy.isoformat()
                )
                row["days_to_first_quote"] = str(
                    (first_after_buy - purchase_day).days
                )

            if purchase_day < days[0]:
                row["status"] = "BUY_BEFORE_FIRST_QUOTE"

            elif not next_quotes:
                row["status"] = "NO_QUOTE_AFTER_BUY"

            elif (
                next_quotes[0] - purchase_day
            ).days > 7:
                row["status"] = "DELAYED_QUOTE_AFTER_BUY"

            else:
                held_period_days = [
                    day
                    for day in days
                    if day >= purchase_day
                ]

                if any(
                    (current - previous).days > 10
                    for previous, current in zip(
                        held_period_days,
                        held_period_days[1:],
                    )
                ):
                    flags.add("LONG_CACHED_GAP")

                if (
                    "LONG_CACHED_GAP" in flags
                    or "STALE_LAST_QUOTE" in flags
                ):
                    row["status"] = "POSSIBLE_SESSION_GAPS"
                else:
                    row["status"] = "START_PRICE_AVAILABLE"

        row["flags"] = "|".join(sorted(flags))
        report.append(row)

    return report


def write_report(
    rows: list[dict[str, str]],
    output: Path,
) -> None:
    """Write a private, quantity-free coverage report."""
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = output.with_suffix(".tmp")

    with temporary.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=REPORT_FIELDS,
        )

        writer.writeheader()
        writer.writerows(rows)

    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether cached prices cover "
            "the first observed XTB purchases."
        )
    )

    parser.add_argument(
        "--exports",
        type=Path,
        default=Path("data/private"),
    )
    parser.add_argument(
        "--file",
        type=Path,
        action="append",
        dest="selected_files",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
    )
    parser.add_argument(
        "--positions",
        type=Path,
        default=DEFAULT_POSITIONS,
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        required=True,
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        required=True,
    )

    args = parser.parse_args()

    if args.start >= args.end:
        parser.error("--end must be later than --start")

    for path in (
        args.registry,
        args.positions,
        args.cache,
    ):
        if not path.is_file():
            parser.error(f"Required file not found: {path}")

    files = (
        args.selected_files
        if args.selected_files
        else sorted(
            path
            for path in args.exports.glob("*.xlsx")
            if not path.name.startswith("~$")
        )
    )

    if not files:
        parser.error("No XTB XLSX exports found")

    for file_path in files:
        if not file_path.is_file():
            parser.error(f"File not found: {file_path}")

        if file_path.suffix.lower() != ".xlsx":
            parser.error(f"Expected XLSX: {file_path}")

    try:
        targets = prepare_targets(
            read_csv(args.positions),
            read_csv(args.registry),
        )

        first_purchases = load_first_purchases(files)

    except (ValueError, KeyError) as exc:
        parser.error(str(exc))

    connection = open_cache(args.cache)

    try:
        rows = assess_purchase_coverage(
            targets,
            first_purchases,
            connection,
            args.start,
            args.end,
        )
    finally:
        connection.close()

    write_report(rows, args.output)

    counts = Counter(
        row["status"]
        for row in rows
    )

    print("\nFIRST PURCHASE / PRICE COVERAGE")
    print(f"Current instruments: {len(targets)}")
    print(f"XTB exports: {len(files)}")
    print(f"Date range: [{args.start}, {args.end})")

    print("\nRESULTS:")

    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    print("\nITEMS REQUIRING FOLLOW-UP:")

    for row in rows:
        if row["status"] == "START_PRICE_AVAILABLE":
            continue

        print(
            f"  {row['xtb_ticker']}: {row['status']}"
            f" | first buy={row['first_purchase_utc'] or '-'}"
            f" | first quote={row['first_cached_session'] or '-'}"
        )

    print(f"\nFull report: {args.output}")

    print(
        "\nFirst purchase dates use UTC calendar dates. "
        "Exchange-local trading dates may differ."
    )
    print(
        "START_PRICE_AVAILABLE is only an initial "
        "coverage check, not final daily valuation approval."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())