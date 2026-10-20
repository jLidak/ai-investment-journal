
import argparse
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from app.ingestion.xtb_cash import read_cash_operations
from app.ingestion.xtb_movements import extract_position_movements
from app.market_data.audit_export import AuditRow, save_audit_csv
from app.market_data.inspection import inspect_instrument
from app.market_data.quote_review import review_quote
from app.market_data.symbols import suggest_yahoo_ticker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    if args.offset < 0 or not 1 <= args.limit <= 10:
        parser.error("Offset must be non-negative and limit 1-10")

    files = sorted(
        path for path in Path("data/private").glob("*.xlsx")
        if not path.name.startswith("~$")
    )

    if not files:
        print("No XTB files found")
        return 1

    symbols = set()

    for file_path in files:
        operations = read_cash_operations(file_path)
        movements = extract_position_movements(operations)
        symbols.update(m.ticker for m in movements)

    candidates = [
        suggest_yahoo_ticker(symbol)
        for symbol in sorted(symbols)
    ]

    batch = candidates[args.offset:args.offset + args.limit]

    if not batch:
        print("No candidates in selected range")
        return 1

    end = date.today()
    start = end - timedelta(days=45)

    rows = []
    counts = Counter()

    print(f"Total candidates: {len(candidates)}")
    print(f"Selected: {len(batch)}")

    for index, candidate in enumerate(batch, start=1):
        inspected = None
        error_type = None

        if candidate.yahoo_ticker is not None:
            try:
                inspected = inspect_instrument(
                    candidate.yahoo_ticker,
                    start,
                    end,
                )
            except Exception as exc:
                error_type = type(exc).__name__

        if error_type is not None:
            status = "FETCH_ERROR"
            currency = None
            reason = "Market data inspection failed"
        else:
            assessment = review_quote(candidate, inspected)
            status = assessment.status.value
            currency = assessment.currency
            reason = assessment.reason

        row = AuditRow(
            xtb_ticker=candidate.xtb_ticker,
            yahoo_ticker=candidate.yahoo_ticker,
            status=status,
            quote_currency=currency,
            exchange=inspected.exchange if inspected else None,
            sessions=inspected.session_count if inspected else None,
            date_from=start,
            date_to_exclusive=end,
            reason=reason,
            error_type=error_type,
        )

        rows.append(row)
        counts[status] += 1

        print(
            f"Candidate {index}: {status}, "
            f"currency={currency or '-'}"
        )

    last_index = args.offset + len(batch) - 1

    output_path = (
        Path("data/private/market_audit")
        / f"audit_{end.isoformat()}_"
          f"{args.offset:03d}_{last_index:03d}.csv"
    )

    save_audit_csv(rows, output_path)

    print("\nSummary:")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    print(f"\nSaved to: {output_path}")
    print("Instrument identity has NOT been verified.")

    return 1 if counts["FETCH_ERROR"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
