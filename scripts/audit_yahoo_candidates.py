
import argparse
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from app.ingestion.xtb_cash import read_cash_operations
from app.ingestion.xtb_movements import extract_position_movements
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

    files = sorted(Path("data/private").glob("*.xlsx"))
    files = [f for f in files if not f.name.startswith("~$")]

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

    # Exclude the current day to avoid an unfinished session.
    end = date.today()
    start = end - timedelta(days=45)

    counts = Counter()
    errors = Counter()

    print(f"Total candidates: {len(candidates)}")
    print(f"Selected: {len(batch)}")
    print(f"Period: {start} to {end} (end exclusive)")

    for index, candidate in enumerate(batch, start=1):
        if candidate.yahoo_ticker is None:
            assessment = review_quote(candidate, None)

        else:
            try:
                inspected = inspect_instrument(
                    candidate.yahoo_ticker,
                    start,
                    end,
                )
            except Exception as exc:
                # External provider boundary: report failures,
                # but never treat failed retrieval as valid prices.
                error_type = type(exc).__name__
                errors[error_type] += 1
                print(f"Candidate {index}: FETCH_ERROR ({error_type})")
                continue

            assessment = review_quote(candidate, inspected)

        counts[assessment.status.value] += 1

        print(
            f"Candidate {index}: {assessment.status.value}, "
            f"currency={assessment.currency or '-'}"
        )

    print("\nSummary:")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    print(f"  FETCH_ERROR: {sum(errors.values())}")
    print("Instrument identity has NOT been verified.")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
