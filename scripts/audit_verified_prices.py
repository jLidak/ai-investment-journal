
"""Audit Yahoo price history for currently held, identity-approved instruments.

This is a diagnostic report, NOT an authorization to value a portfolio.
No account numbers, position quantities or historical prices are exported.
"""

import argparse
import csv
import time
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import yfinance as yf

from app.market_data.portfolio_mapping import read_csv


DEFAULT_REGISTRY = Path(
    "data/private/portfolio_mapping/portfolio_mapping_reviewed.csv"
)
DEFAULT_POSITIONS = Path(
    "data/private/portfolio_mapping/current_positions_mapping.csv"
)
DEFAULT_OUTPUT = Path(
    "data/private/market_audit/price_history_audit.csv"
)

FIELDS = [
    "xtb_ticker",
    "yahoo_ticker",
    "isin",
    "expected_currency",
    "yahoo_currency",
    "exchange",
    "start",
    "end_exclusive",
    "first_session",
    "last_session",
    "sessions",
    "dividend_events",
    "split_events",
    "adjusted_close_diff_days",
    "max_daily_change_pct",
    "status",
    "flags",
    "error_type",
]


def norm(value: object) -> str:
    return str(value or "").strip().upper()


def quote_unit(value: object) -> str:
    """Keep British pence distinct from British pounds."""
    raw = str(value or "").strip()

    if raw == "GBp" or raw.upper() == "GBX":
        return "GBp"

    return raw.upper()


def prepare_targets(
    position_rows: list[dict[str, str]],
    registry_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Reject stale current-position reports before any Yahoo request."""
    registry = {}

    for row in registry_rows:
        ticker = norm(row.get("xtb_ticker"))

        if not ticker or ticker in registry:
            raise ValueError(
                f"Empty or duplicated ticker in registry: {ticker!r}"
            )

        registry[ticker] = row

    if not position_rows:
        raise ValueError("Current-position report is empty")

    targets = []
    seen = set()

    for row in position_rows:
        ticker = norm(row.get("xtb_ticker"))

        if not ticker or ticker in seen:
            raise ValueError(
                f"Empty or duplicated current ticker: {ticker!r}"
            )

        seen.add(ticker)
        entry = registry.get(ticker)

        approved = ""
        isin = ""
        currency = ""
        identity_status = "MISSING_FROM_REGISTRY"

        if entry is not None:
            identity_status = norm(entry.get("status"))
            isin = norm(entry.get("isin"))
            currency = quote_unit(entry.get("xtb_currency"))
            catalog_status = norm(entry.get("xtb_catalog_status"))
            yahoo_currency = quote_unit(entry.get("yahoo_currency"))
            candidate_approved = norm(
                entry.get("approved_yahoo_ticker")
            )

            is_approved = (
                identity_status == "VERIFIED"
                and catalog_status == "FOUND"
                and bool(candidate_approved)
                and bool(isin)
                and bool(currency)
                and (
                    not yahoo_currency
                    or yahoo_currency == currency
                )
            )

            if is_approved:
                approved = candidate_approved

            for current_field, expected in [
                ("isin", isin),
                ("quote_currency", currency),
            ]:
                if quote_unit(row.get(current_field)) != expected:
                    raise ValueError(
                        f"{ticker}: stale {current_field}. "
                        "Regenerate current-position mapping."
                    )

        expected_readiness = (
            "MAPPING_APPROVED" if approved else "NEEDS_REVIEW"
        )

        if (
            norm(row.get("mapping_readiness")) != expected_readiness
            or norm(row.get("approved_yahoo_ticker")) != approved
        ):
            raise ValueError(
                f"{ticker}: current-position report differs "
                "from reviewed registry. Regenerate it using "
                "scripts.report_active_mapping --mapping "
                "data/private/portfolio_mapping/"
                "portfolio_mapping_reviewed.csv"
            )

        targets.append({
            "xtb_ticker": ticker,
            "yahoo_ticker": approved,
            "isin": isin,
            "expected_currency": currency,
            "identity_status": identity_status,
        })

    return sorted(targets, key=lambda item: item["xtb_ticker"])


def fetch_yahoo_history(
    symbol: str,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, Mapping]:
    """Use one Yahoo ticker object for history and its metadata."""
    ticker = yf.Ticker(symbol)

    history = ticker.history(
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=True,
    )

    metadata = ticker.get_history_metadata()

    return history, metadata


def positive_price(value: object, day: date) -> Decimal:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid closing price on {day}"
        ) from exc

    if not price.is_finite() or price <= 0:
        raise ValueError(f"Invalid closing price on {day}")

    return price


def assess_history(
    target: dict[str, str],
    start: date,
    end: date,
    history: pd.DataFrame,
    metadata: Mapping,
) -> dict[str, str]:
    """Validate a fetched history without writing or approving prices."""
    if history.empty:
        raise ValueError("Empty Yahoo history")

    if "Close" not in history.columns:
        raise ValueError("Missing Close column")

    if not isinstance(metadata, Mapping):
        raise ValueError("Invalid Yahoo history metadata")

    sessions = []
    seen = set()

    for timestamp, value in history["Close"].items():
        if not hasattr(timestamp, "date"):
            raise ValueError("Invalid session index")

        day = timestamp.date()

        if not start <= day < end:
            raise ValueError("Session outside requested range")

        if day in seen:
            raise ValueError(f"Duplicate trading session: {day}")

        seen.add(day)
        sessions.append((day, positive_price(value, day)))

    sessions.sort()
    days = [day for day, _ in sessions]

    yahoo_currency = quote_unit(metadata.get("currency"))
    expected_currency = target["expected_currency"]

    exchange_value = metadata.get("exchangeName")
    exchange = (
        exchange_value.strip()
        if isinstance(exchange_value, str)
        else ""
    )

    flags = set()

    if not yahoo_currency:
        flags.add("MISSING_CURRENCY")
    elif yahoo_currency != expected_currency:
        flags.add("CURRENCY_MISMATCH")

    if not exchange:
        flags.add("MISSING_EXCHANGE")

    if target["xtb_ticker"].endswith(".UK"):
        flags.add("UK_QUOTE_UNIT_REVIEW")

    if (days[0] - start).days > 30:
        flags.add("LATE_FIRST_SESSION")

    if (end - days[-1]).days > 10:
        flags.add("STALE_LAST_SESSION")

    if any(
        (current - previous).days > 10
        for previous, current in zip(days, days[1:])
    ):
        flags.add("LONG_SESSION_GAP")

    max_change = Decimal("0")

    for (_, previous), (_, current) in zip(
        sessions, sessions[1:]
    ):
        change = abs(
            (current - previous) / previous * Decimal("100")
        )
        max_change = max(max_change, change)

    if max_change > Decimal("40"):
        flags.add("LARGE_DAILY_MOVE")

    dividend_events = 0
    split_events = 0

    for column, flag in [
        ("Dividends", "DIVIDEND_EVENTS"),
        ("Stock Splits", "SPLIT_EVENTS"),
    ]:
        if column not in history.columns:
            flags.add("ACTIONS_COLUMN_MISSING")
            continue

        raw = history[column]
        numeric = pd.to_numeric(raw, errors="coerce")

        if (raw.notna() & numeric.isna()).any():
            raise ValueError(f"Invalid {column} data")

        count = int((numeric.fillna(0) != 0).sum())

        if column == "Dividends":
            dividend_events = count
        else:
            split_events = count

        if count:
            flags.add(flag)

    adjusted_diff_days = 0

    if "Adj Close" in history.columns:
        for timestamp, value in history["Adj Close"].items():
            day = timestamp.date()

            if pd.isna(value):
                flags.add("MISSING_ADJUSTED_CLOSE")
                continue

            adjusted = positive_price(value, day)
            close = positive_price(
                history.loc[timestamp, "Close"], day
            )

            relative_difference = abs(
                adjusted / close - Decimal("1")
            )

            if relative_difference > Decimal("0.0001"):
                adjusted_diff_days += 1

        if adjusted_diff_days:
            flags.add("ADJUSTED_CLOSE_DIFFERS")
    else:
        flags.add("ADJUSTED_CLOSE_NOT_REPORTED")

    return {
        "yahoo_currency": yahoo_currency,
        "exchange": exchange,
        "first_session": days[0].isoformat(),
        "last_session": days[-1].isoformat(),
        "sessions": str(len(sessions)),
        "dividend_events": str(dividend_events),
        "split_events": str(split_events),
        "adjusted_close_diff_days": str(adjusted_diff_days),
        "max_daily_change_pct": f"{max_change:.2f}",
        "status": (
            "REVIEW_REQUIRED" if flags else "PRELIMINARY_OK"
        ),
        "flags": "|".join(sorted(flags)),
        "error_type": "",
    }


def audit_portfolio(
    targets: list[dict[str, str]],
    start: date,
    end: date,
    fetcher: Callable = fetch_yahoo_history,
    delay: float = 0,
) -> list[dict[str, str]]:
    if start >= end:
        raise ValueError("End must be after start")

    if delay < 0:
        raise ValueError("Delay cannot be negative")

    report = []

    for target in targets:
        row = {field: "" for field in FIELDS}

        for field in (
            "xtb_ticker",
            "yahoo_ticker",
            "isin",
            "expected_currency",
        ):
            row[field] = target[field]

        row["start"] = start.isoformat()
        row["end_exclusive"] = end.isoformat()

        if not target["yahoo_ticker"]:
            row["status"] = "SKIPPED_UNAPPROVED"
            row["flags"] = (
                "UNAPPROVED_IDENTITY:"
                + target["identity_status"]
            )
            report.append(row)
            continue

        try:
            history, metadata = fetcher(
                target["yahoo_ticker"], start, end
            )
        except Exception as exc:
            row["status"] = "FETCH_ERROR"
            row["error_type"] = type(exc).__name__
        else:
            try:
                row.update(
                    assess_history(
                        target, start, end, history, metadata
                    )
                )
            except (ValueError, TypeError, KeyError) as exc:
                row["status"] = "INVALID_HISTORY"
                row["error_type"] = type(exc).__name__

        report.append(row)

        if delay:
            time.sleep(delay)

    return report


def write_report(
    rows: list[dict[str, str]],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")

    with temporary.open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=FIELDS
        )
        writer.writeheader()
        writer.writerows(rows)

    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit historical Yahoo quotes, without approving them."
    )
    parser.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY
    )
    parser.add_argument(
        "--positions", type=Path, default=DEFAULT_POSITIONS
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT
    )
    parser.add_argument(
        "--start", type=date.fromisoformat, required=True
    )
    parser.add_argument(
        "--end", type=date.fromisoformat, required=True
    )
    parser.add_argument(
        "--delay", type=float, default=0.5
    )

    args = parser.parse_args()

    for path in (args.registry, args.positions):
        if not path.is_file():
            parser.error(f"Required CSV not found: {path}")

    if args.start >= args.end:
        parser.error("--end must be later than --start")

    if args.delay < 0:
        parser.error("--delay cannot be negative")

    try:
        targets = prepare_targets(
            read_csv(args.positions),
            read_csv(args.registry),
        )
    except ValueError as exc:
        parser.error(str(exc))

    approved = sum(
        bool(row["yahoo_ticker"]) for row in targets
    )

    print("\nYAHOO PRICE HISTORY AUDIT")
    print(f"Current instruments: {len(targets)}")
    print(f"Identity-approved: {approved}")
    print(f"Unapproved: {len(targets) - approved}")
    print(f"Date range: [{args.start}, {args.end})")
    print("Price policy: Close, auto_adjust=False, actions=True")

    report = audit_portfolio(
        targets, args.start, args.end, delay=args.delay
    )

    write_report(report, args.output)

    print("\nRESULTS:")
    counts = Counter(row["status"] for row in report)

    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    print("\nItems requiring follow-up:")

    for row in report:
        if row["status"] != "PRELIMINARY_OK":
            print(
                f"  {row['xtb_ticker']}: {row['status']}"
                f" | {row['flags'] or row['error_type']}"
            )

    print(f"\nFull report: {args.output}")
    print(
        "No price histories were cached. "
        "PRELIMINARY_OK is not final price approval."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
