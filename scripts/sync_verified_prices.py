"""Synchronize reviewed Yahoo closes into the private SQLite cache.

Only identity-approved current holdings are fetched.

A cached Close is not final approval for portfolio valuation.
Price-quality warnings and incomplete history remain visible.
"""

import argparse
import csv
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.market_data.portfolio_mapping import read_csv
from app.market_data.price_cache import (
    PriceIdentity,
    open_cache,
    pending_windows,
    read_closes,
    record_fetch,
)
from scripts.audit_verified_prices import (
    DEFAULT_POSITIONS,
    DEFAULT_REGISTRY,
    assess_history,
    fetch_yahoo_history,
    positive_price,
    prepare_targets,
)


DEFAULT_CACHE = Path(
    "data/private/market_data/yahoo_prices.db"
)

DEFAULT_REPORT = Path(
    "data/private/market_audit/price_sync_report.csv"
)

REPORT_FIELDS = [
    "xtb_ticker",
    "yahoo_ticker",
    "start",
    "end_exclusive",
    "status",
    "fetched_windows",
    "cached_sessions",
    "first_session",
    "last_session",
    "pending_windows",
    "flags",
    "error_type",
]

# These problems prevent saving the fetched prices.
# Other audit flags remain visible but do not erase real quotes.
BLOCKING_FLAGS = {
    "CURRENCY_MISMATCH",
    "MISSING_CURRENCY",
    "MISSING_EXCHANGE",
    "ACTIONS_COLUMN_MISSING",
}


def make_identity(target: dict[str, str]) -> PriceIdentity:
    """Build a cache key from an approved instrument identity."""
    if not target["yahoo_ticker"]:
        raise ValueError("Unapproved target cannot be cached")

    return PriceIdentity(
        xtb_ticker=target["xtb_ticker"],
        source="YAHOO",
        source_symbol=target["yahoo_ticker"],
        isin=target["isin"],
        quote_currency=target["expected_currency"],
    )


def cached_quality_flags(
    connection: sqlite3.Connection,
    identity: PriceIdentity,
    start: date,
    end: date,
) -> set[str]:
    """Read warnings previously stored for this exact identity."""
    rows = connection.execute(
        """
        SELECT quality_flags
        FROM checked_price_windows
        WHERE xtb_ticker = ?
          AND source = ?
          AND source_symbol = ?
          AND isin = ?
          AND quote_currency = ?
          AND start_date < ?
          AND end_exclusive > ?
        """,
        (
            identity.xtb_ticker,
            identity.source,
            identity.source_symbol,
            identity.isin,
            identity.quote_currency,
            end.isoformat(),
            start.isoformat(),
        ),
    ).fetchall()

    flags = set()

    for (raw_flags,) in rows:
        flags.update(
            flag
            for flag in raw_flags.split("|")
            if flag
        )

    return flags


def sync_targets(
    targets: list[dict[str, str]],
    connection: sqlite3.Connection,
    start: date,
    end: date,
    *,
    fetcher: Callable = fetch_yahoo_history,
    now: datetime | None = None,
    missing_ttl_days: int = 7,
    delay: float = 0,
    progress: Callable[[dict[str, str]], None] | None = None,
) -> list[dict[str, str]]:
    """Fetch missing date windows and persist validated closes."""
    if type(start) is not date or type(end) is not date:
        raise ValueError("Expected date objects")

    if start >= end:
        raise ValueError("End must be later than start")

    if delay < 0:
        raise ValueError("Delay cannot be negative")

    checked_at = now or datetime.now(timezone.utc)
    report = []

    for target in targets:
        row = {field: "" for field in REPORT_FIELDS}

        row.update({
            "xtb_ticker": target["xtb_ticker"],
            "yahoo_ticker": target["yahoo_ticker"],
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "fetched_windows": "0",
            "cached_sessions": "0",
            "pending_windows": "0",
        })

        # Never fetch guessed or unapproved identities.
        if not target["yahoo_ticker"]:
            row["status"] = "SKIPPED_UNAPPROVED"
            report.append(row)

            if progress:
                progress(row)

            continue

        identity = make_identity(target)

        windows = pending_windows(
            connection,
            identity,
            start,
            end,
            now=checked_at,
            missing_ttl_days=missing_ttl_days,
        )

        fetched_count = 0
        failed_status = ""
        error_type = ""
        current_flags = set()

        for window_start, window_end in windows:
            try:
                history, metadata = fetcher(
                    target["yahoo_ticker"],
                    window_start,
                    window_end,
                )
            except Exception as exc:
                failed_status = "FETCH_ERROR"
                error_type = type(exc).__name__
                break

            try:
                assessment = assess_history(
                    target,
                    window_start,
                    window_end,
                    history,
                    metadata,
                )
            except (ValueError, TypeError, KeyError) as exc:
                # An empty or malformed response is not saved
                # as a successfully checked interval.
                failed_status = "INVALID_HISTORY"
                error_type = type(exc).__name__
                break

            response_flags = set(
                filter(None, assessment["flags"].split("|"))
            )

            current_flags.update(response_flags)

            if response_flags & BLOCKING_FLAGS:
                failed_status = "BLOCKED_QUALITY"
                break

            try:
                # assess_history has already checked duplicates,
                # date bounds, prices and corporate-action columns.
                closes = {
                    timestamp.date(): positive_price(
                        value,
                        timestamp.date(),
                    )
                    for timestamp, value
                    in history["Close"].items()
                }

                record_fetch(
                    connection,
                    identity,
                    window_start,
                    window_end,
                    closes,
                    checked_at=checked_at,
                    quality_flags="|".join(
                        sorted(response_flags)
                    ),
                )
            except (ValueError, TypeError, sqlite3.Error) as exc:
                # The SQLite cache rejects conflicting prices.
                # Its transaction rolls back the failed fetch.
                failed_status = "CACHE_ERROR"
                error_type = type(exc).__name__
                break

            fetched_count += 1

            if delay:
                time.sleep(delay)

        stored = read_closes(
            connection,
            identity,
            start,
            end,
        )

        flags = cached_quality_flags(
            connection,
            identity,
            start,
            end,
        )
        flags.update(current_flags)

        if stored:
            first = min(stored)
            last = max(stored)

            row["first_session"] = first.isoformat()
            row["last_session"] = last.isoformat()

            # These are broad edge warnings, not an exchange
            # calendar-based proof of missing trading sessions.
            if (first - start).days > 7:
                flags.add("LEFT_EDGE_GAP")

            if (
                end - (last + timedelta(days=1))
            ).days > 7:
                flags.add("RIGHT_EDGE_GAP")

        remaining = pending_windows(
            connection,
            identity,
            start,
            end,
            now=checked_at,
            missing_ttl_days=missing_ttl_days,
        )

        row["fetched_windows"] = str(fetched_count)
        row["cached_sessions"] = str(len(stored))
        row["pending_windows"] = str(len(remaining))
        row["flags"] = "|".join(sorted(flags))
        row["error_type"] = error_type

        if failed_status:
            row["status"] = failed_status
        elif not windows:
            row["status"] = "CACHE_REUSED"
        elif flags:
            row["status"] = "FETCHED_REVIEW"
        else:
            row["status"] = "FETCHED_PRELIMINARY"

        report.append(row)

        if progress:
            progress(row)

    return report


def write_sync_report(
    rows: list[dict[str, str]],
    output: Path,
) -> None:
    """Write a diagnostic report without position quantities."""
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
            "Synchronize approved Yahoo closes "
            "into the local SQLite cache."
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
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT,
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
        "--missing-ttl-days",
        type=int,
        default=7,
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
    )

    args = parser.parse_args()

    if args.start >= args.end:
        parser.error("--end must be later than --start")

    if args.missing_ttl_days < 1:
        parser.error("--missing-ttl-days must be positive")

    if args.delay < 0:
        parser.error("--delay cannot be negative")

    for path in (args.registry, args.positions):
        if not path.is_file():
            parser.error(f"Required CSV not found: {path}")

    try:
        targets = prepare_targets(
            read_csv(args.positions),
            read_csv(args.registry),
        )
    except ValueError as exc:
        parser.error(str(exc))

    approved = sum(
        bool(item["yahoo_ticker"])
        for item in targets
    )

    print("\nYAHOO PRICE CACHE SYNCHRONIZATION")
    print(f"Current instruments: {len(targets)}")
    print(f"Identity-approved: {approved}")
    print(f"Unapproved: {len(targets) - approved}")
    print(f"Date range: [{args.start}, {args.end})")
    print(f"Cache: {args.cache}")
    print()

    connection = open_cache(args.cache)

    try:
        rows = sync_targets(
            targets,
            connection,
            args.start,
            args.end,
            missing_ttl_days=args.missing_ttl_days,
            delay=args.delay,
            progress=lambda row: print(
                f"  {row['xtb_ticker']}: "
                f"{row['status']} | "
                f"cached sessions={row['cached_sessions']}",
                flush=True,
            ),
        )
    finally:
        connection.close()

    write_sync_report(rows, args.output)

    counts = Counter(
        row["status"]
        for row in rows
    )

    print("\nRESULTS:")

    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    print(f"\nFull report: {args.output}")

    print(
        "\nFETCHED and CACHE_REUSED do not guarantee "
        "complete exchange-session coverage."
    )
    print(
        "Cached Close prices are not yet approved "
        "for final portfolio P&L calculations."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())