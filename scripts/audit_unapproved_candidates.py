"""Audit Yahoo candidates for unresolved current XTB holdings.

Official issuer/exchange references establish a proposed security
identity. Yahoo metadata and history are checked separately.

This script NEVER approves mappings or writes market prices.
"""

import argparse
import csv
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.market_data.identity import fetch_yahoo_identity
from app.market_data.portfolio_mapping import read_csv
from scripts.audit_verified_prices import (
    DEFAULT_POSITIONS,
    DEFAULT_REGISTRY,
    assess_history,
    fetch_yahoo_history,
    prepare_targets,
)


DEFAULT_PURCHASES = Path(
    "data/private/market_audit/purchase_price_coverage.csv"
)

DEFAULT_OUTPUT = Path(
    "data/private/portfolio_mapping/"
    "unapproved_candidates_audit.csv"
)


@dataclass(frozen=True)
class Candidate:
    yahoo_symbol: str
    official_isin: str
    expected_currency: str
    expected_type: str
    source_url: str


# Independently identified securities.
# These are candidates, NOT approved price mappings.
CANDIDATES = {
    "DRAM.DE": (
        Candidate(
            "DRAMG.DE",
            "IE000CEUZ052",
            "EUR",
            "ETF",
            "https://hanetf.com/fund/dram-defiance-memory-etf/",
        ),
        Candidate(
            "DRAM.DE",
            "IE000CEUZ052",
            "EUR",
            "ETF",
            "https://hanetf.com/fund/dram-defiance-memory-etf/",
        ),
    ),
    "RKLB.US": (
        Candidate(
            "RKLB",
            "US7731211089",
            "USD",
            "EQUITY",
            "https://www.nasdaqtrader.com/"
            "TraderNews.aspx?id=ECA2025-264",
        ),
    ),
    "SPX.DE": (
        Candidate(
            "SPX.DE",
            "US84615Q1031",
            "EUR",
            "EQUITY",
            "https://live.deutsche-boerse.com/equity/spacex",
        ),
    ),
}


FIELDS = [
    "xtb_ticker",
    "xtb_name",
    "registry_isin",
    "official_isin",
    "expected_currency",
    "expected_type",
    "yahoo_symbol",
    "first_purchase_utc",
    "yahoo_name",
    "yahoo_quote_type",
    "yahoo_currency",
    "yahoo_exchange",
    "first_session",
    "last_session",
    "sessions",
    "status",
    "flags",
    "error_type",
    "source_url",
]


BLOCKING_FLAGS = {
    "CURRENCY_MISMATCH",
    "MISSING_CURRENCY",
    "MISSING_EXCHANGE",
    "QUOTE_TYPE_MISMATCH",
    "MISSING_QUOTE_TYPE",
    "UNEXPLAINED_ISIN_MISMATCH",
    "FIRST_QUOTE_AFTER_PURCHASE",
    "ACTIONS_COLUMN_MISSING",
}


def normalized(value: object) -> str:
    return str(value or "").strip().upper()


def prepare_requests(
    position_rows: list[dict[str, str]],
    registry_rows: list[dict[str, str]],
    purchase_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Prepare only currently held, unresolved identities."""
    targets = prepare_targets(
        position_rows,
        registry_rows,
    )

    registry = {}

    for row in registry_rows:
        ticker = normalized(row.get("xtb_ticker"))

        if not ticker or ticker in registry:
            raise ValueError(
                f"Duplicate or empty registry ticker: {ticker!r}"
            )

        registry[ticker] = row

    purchases = {}

    for row in purchase_rows:
        ticker = normalized(row.get("xtb_ticker"))

        if not ticker or ticker in purchases:
            raise ValueError(
                f"Duplicate or empty purchase ticker: {ticker!r}"
            )

        purchases[ticker] = row

    requests = []

    for target in targets:
        if target["yahoo_ticker"]:
            continue

        ticker = target["xtb_ticker"]

        candidates = CANDIDATES.get(ticker)

        if not candidates:
            raise ValueError(
                f"No curated candidates for {ticker}"
            )

        purchase = purchases.get(ticker, {})
        purchase_text = str(
            purchase.get("first_purchase_utc") or ""
        ).strip()

        if not purchase_text:
            raise ValueError(
                f"{ticker}: first purchase date is missing"
            )

        # Reject malformed dates before any network access.
        date.fromisoformat(purchase_text)

        entry = registry[ticker]
        registry_isin = normalized(entry.get("isin"))

        for candidate in candidates:
            requests.append({
                "xtb_ticker": ticker,
                "xtb_name": str(
                    entry.get("xtb_name") or ""
                ).strip(),
                "registry_isin": registry_isin,
                "official_isin": candidate.official_isin,
                "expected_currency": candidate.expected_currency,
                "expected_type": candidate.expected_type,
                "yahoo_symbol": candidate.yahoo_symbol,
                "first_purchase_utc": purchase_text,
                "source_url": candidate.source_url,
            })

    return requests


def initial_flags(request: dict[str, str]) -> set[str]:
    """Keep older and missing XTB catalogue identifiers visible."""
    flags = set()

    ticker = request["xtb_ticker"]
    registry_isin = request["registry_isin"]
    official_isin = request["official_isin"]

    if not registry_isin:
        flags.add("XTB_CATALOG_ISIN_MISSING")

    elif registry_isin != official_isin:
        if (
            ticker == "RKLB.US"
            and registry_isin == "US7731221062"
            and official_isin == "US7731211089"
        ):
            flags.add("DOCUMENTED_CUSIP_CHANGE")
        else:
            flags.add("UNEXPLAINED_ISIN_MISMATCH")

    return flags


def audit_candidates(
    requests: list[dict[str, str]],
    start: date,
    end: date,
    *,
    identity_fetcher: Callable = fetch_yahoo_identity,
    history_fetcher: Callable = fetch_yahoo_history,
    delay: float = 0,
) -> list[dict[str, str]]:
    """Inspect proposed Yahoo symbols without approving them."""
    if type(start) is not date or type(end) is not date:
        raise ValueError("Expected date objects")

    if start >= end:
        raise ValueError("End must be later than start")

    if delay < 0:
        raise ValueError("Delay cannot be negative")

    report = []

    request_fields = (
        "xtb_ticker",
        "xtb_name",
        "registry_isin",
        "official_isin",
        "expected_currency",
        "expected_type",
        "yahoo_symbol",
        "first_purchase_utc",
        "source_url",
    )

    for request in requests:
        row = {field: "" for field in FIELDS}

        for field in request_fields:
            row[field] = request[field]

        flags = initial_flags(request)
        symbol = request["yahoo_symbol"]

        if "UNEXPLAINED_ISIN_MISMATCH" in flags:
            row["status"] = "BLOCKED_QUALITY"
            row["flags"] = "|".join(sorted(flags))
            report.append(row)
            continue

        try:
            identity = identity_fetcher(symbol)
        except Exception as exc:
            row["status"] = "IDENTITY_FETCH_ERROR"
            row["error_type"] = type(exc).__name__
            row["flags"] = "|".join(sorted(flags))
            report.append(row)
            continue

        row["yahoo_name"] = identity.name or ""
        row["yahoo_quote_type"] = normalized(
            identity.quote_type
        )
        row["yahoo_currency"] = normalized(
            identity.currency
        )
        row["yahoo_exchange"] = identity.exchange or ""

        if identity.symbol != symbol:
            flags.add("YAHOO_SYMBOL_MISMATCH")

        if not row["yahoo_quote_type"]:
            flags.add("MISSING_QUOTE_TYPE")
        elif (
            row["yahoo_quote_type"]
            != request["expected_type"]
        ):
            flags.add("QUOTE_TYPE_MISMATCH")

        if not row["yahoo_currency"]:
            flags.add("MISSING_CURRENCY")
        elif (
            row["yahoo_currency"]
            != request["expected_currency"]
        ):
            flags.add("CURRENCY_MISMATCH")

        if not row["yahoo_exchange"]:
            flags.add("MISSING_EXCHANGE")

        if (
            "YAHOO_SYMBOL_MISMATCH" in flags
            or flags & BLOCKING_FLAGS
        ):
            row["status"] = "BLOCKED_METADATA"
            row["flags"] = "|".join(sorted(flags))
            report.append(row)
            continue

        try:
            history, metadata = history_fetcher(
                symbol,
                start,
                end,
            )
        except Exception as exc:
            row["status"] = "HISTORY_FETCH_ERROR"
            row["error_type"] = type(exc).__name__
            row["flags"] = "|".join(sorted(flags))
            report.append(row)
            continue

        try:
            assessment = assess_history(
                {
                    "xtb_ticker": request["xtb_ticker"],
                    "expected_currency": request[
                        "expected_currency"
                    ],
                },
                start,
                end,
                history,
                metadata,
            )
        except (ValueError, TypeError, KeyError) as exc:
            row["status"] = "INVALID_HISTORY"
            row["error_type"] = type(exc).__name__
            row["flags"] = "|".join(sorted(flags))
            report.append(row)
            continue

        flags.update(
            filter(None, assessment["flags"].split("|"))
        )

        row["first_session"] = assessment["first_session"]
        row["last_session"] = assessment["last_session"]
        row["sessions"] = assessment["sessions"]

        purchase_day = date.fromisoformat(
            request["first_purchase_utc"]
        )
        first_session = date.fromisoformat(
            assessment["first_session"]
        )

        if first_session > purchase_day:
            flags.add("FIRST_QUOTE_AFTER_PURCHASE")

        if (
            assessment["yahoo_currency"]
            != row["yahoo_currency"]
        ):
            flags.add("YAHOO_METADATA_CURRENCY_CONFLICT")

        if (
            flags & BLOCKING_FLAGS
            or "YAHOO_METADATA_CURRENCY_CONFLICT" in flags
        ):
            row["status"] = "BLOCKED_QUALITY"
        elif flags:
            row["status"] = "CANDIDATE_REVIEW_REQUIRED"
        else:
            row["status"] = "CANDIDATE_METADATA_MATCH"

        row["flags"] = "|".join(sorted(flags))
        report.append(row)

        if delay:
            time.sleep(delay)

    return report


def write_report(
    rows: list[dict[str, str]],
    output: Path,
) -> None:
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
            fieldnames=FIELDS,
        )

        writer.writeheader()
        writer.writerows(rows)

    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit unresolved Yahoo candidates. "
            "No mappings or prices are approved."
        )
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
        "--purchases",
        type=Path,
        default=DEFAULT_PURCHASES,
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
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
    )

    args = parser.parse_args()

    if args.start >= args.end:
        parser.error("--end must be later than --start")

    if args.delay < 0:
        parser.error("--delay cannot be negative")

    for path in (
        args.registry,
        args.positions,
        args.purchases,
    ):
        if not path.is_file():
            parser.error(f"Required CSV not found: {path}")

    try:
        requests = prepare_requests(
            read_csv(args.positions),
            read_csv(args.registry),
            read_csv(args.purchases),
        )
    except ValueError as exc:
        parser.error(str(exc))

    print("\nUNAPPROVED IDENTITY CANDIDATE AUDIT")
    print(f"Yahoo candidates to inspect: {len(requests)}")
    print(f"Date range: [{args.start}, {args.end})")

    report = audit_candidates(
        requests,
        args.start,
        args.end,
        delay=args.delay,
    )

    write_report(report, args.output)

    print("\nRESULTS:")

    for row in report:
        print(
            f"  {row['xtb_ticker']} -> "
            f"{row['yahoo_symbol']}: "
            f"{row['status']} | "
            f"currency={row['yahoo_currency'] or '-'} | "
            f"first={row['first_session'] or '-'}"
        )

        if row["flags"]:
            print(f"    Flags: {row['flags']}")

        if row["error_type"]:
            print(f"    Error: {row['error_type']}")

    print("\nSUMMARY:")

    counts = Counter(
        row["status"]
        for row in report
    )

    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    print(f"\nFull report: {args.output}")

    print(
        "\nPREVIEW ONLY. No identity mappings, "
        "historical prices or cache entries were changed."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())