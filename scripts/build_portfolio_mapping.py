"""Build one portfolio-specific XTB->Yahoo mapping draft from offline evidence."""

import argparse
from pathlib import Path

from app.market_data.portfolio_mapping import make_draft, read_csv, summarize, write_reports


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--coverage", type=Path,
        default=Path("data/private/xtb_catalog/portfolio_coverage.csv"),
    )
    parser.add_argument(
        "--identity", type=Path,
        default=Path("data/private/market_audit/identity_all.csv"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/private/portfolio_mapping"),
    )
    args = parser.parse_args()
    if not args.coverage.is_file():
        parser.error(f"Coverage report not found: {args.coverage}")

    coverage = read_csv(args.coverage)
    if not coverage:
        raise ValueError("Coverage report has no rows")
    audits = read_csv(args.identity) if args.identity.is_file() else None
    if audits is None:
        print(f"Note: optional Yahoo audit not found: {args.identity}")
    rows = make_draft(coverage, audits)
    paths = write_reports(rows, args.output)

    print(f"Portfolio instruments: {len(rows)}")
    for status, count in sorted(summarize(rows).items()):
        print(f"  {status}: {count}")
    print(f"Draft: {paths[0]}")
    print(f"Review: {paths[1]}")
    print("Only approved_yahoo_ticker can eventually be used for valuation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
