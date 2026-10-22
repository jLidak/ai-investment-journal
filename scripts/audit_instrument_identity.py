"""Audit all XTB instrument identities in one resumable run."""

import argparse
from collections import Counter
from pathlib import Path

from app.ingestion.xtb_cash import read_cash_operations
from app.ingestion.xtb_trade_comments import parse_trade_details
from app.market_data.identity_audit import (
    get_yahoo_identity_cached,
    make_identity_audit_row,
    write_identity_reports,
)
from app.market_data.symbols import suggest_yahoo_ticker
from app.market_data.verified_instruments import VERIFIED_INSTRUMENTS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Ignore successful cached metadata")
    parser.add_argument("--ttl-days", type=int, default=30)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--limit", type=int, help="Debug only; normally audit ALL instruments")
    args = parser.parse_args()
    if args.ttl_days < 1 or args.delay < 0 or not 0 <= args.retries <= 5:
        parser.error("Invalid ttl/delay/retries")
    if args.limit is not None and args.limit < 1:
        parser.error("Limit must be positive")

    private_dir = Path("data/private")
    files = sorted(p for p in private_dir.glob("*.xlsx") if not p.name.startswith("~$"))
    if not files:
        print("No XTB XLSX files found in data/private")
        return 1

    # Deduplicate all trades across all imported accounts/exports.
    names: dict[str, set[str]] = {}
    for path in files:
        for operation in read_cash_operations(path):
            if operation.operation_type not in {"Stock purchase", "Stock sell"}:
                continue
            ticker = parse_trade_details(operation).ticker
            names.setdefault(ticker, set())
            if operation.instrument:
                names[ticker].add(operation.instrument.strip())

    symbols = sorted(names)
    if not symbols:
        print("No traded instruments found")
        return 1
    if args.limit is not None:
        symbols = symbols[:args.limit]
        print("DEBUG: report contains only a subset of instruments")

    cache_dir = private_dir / "market_audit" / "identity_cache"
    rows = []
    counters = Counter()
    print(f"Checking {len(symbols)} unique instruments (sequential, cached)")

    for i, xtb in enumerate(symbols, start=1):
        registered = VERIFIED_INSTRUMENTS.get(xtb)
        candidate = suggest_yahoo_ticker(xtb)
        # Reviewed mapping takes precedence over a rule-based suggestion.
        yahoo = registered.yahoo_ticker if registered else candidate.yahoo_ticker
        identity = None
        metadata_source = ""
        error_type = ""
        if yahoo:
            try:
                identity, metadata_source = get_yahoo_identity_cached(
                    yahoo,
                    cache_dir,
                    ttl_days=args.ttl_days,
                    refresh=args.refresh,
                    retries=args.retries,
                    delay=args.delay,
                )
            except Exception as exc:
                error_type = type(exc).__name__

        row = make_identity_audit_row(
            xtb, names[xtb], yahoo, identity,
            metadata_source=metadata_source,
            error_type=error_type,
        )
        rows.append(row)
        counters[row.status] += 1
        print(f"[{i}/{len(symbols)}] {xtb}: {row.status} ({metadata_source or error_type or '-'})")

    all_path, review_path = write_identity_reports(
        rows, private_dir / "market_audit",
        prefix="identity_debug" if args.limit is not None else "identity",
    )
    print("\nSUMMARY")
    for status, number in sorted(counters.items()):
        print(f"  {status}: {number}")
    print(f"Full report: {all_path}")
    print(f"Review queue: {review_path}")
    print("CANDIDATE does NOT mean confirmed instrument identity.")
    return 1 if counters["FETCH_ERROR"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
