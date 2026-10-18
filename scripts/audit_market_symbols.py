
from collections import Counter
from pathlib import Path

from app.ingestion.xtb_cash import read_cash_operations
from app.ingestion.xtb_movements import extract_position_movements
from app.market_data.symbols import suggest_yahoo_ticker


def main() -> int:
    files = sorted(
        path for path in Path("data/private").glob("*.xlsx")
        if not path.name.startswith("~$")
    )

    if not files:
        print("No XTB files found")
        return 1

    unique_tickers = set()

    for file_path in files:
        operations = read_cash_operations(file_path)
        movements = extract_position_movements(operations)

        unique_tickers.update(
            movement.ticker for movement in movements
        )

    if not unique_tickers:
        print("No traded instruments found")
        return 1

    candidates = [
        suggest_yahoo_ticker(ticker)
        for ticker in sorted(unique_tickers)
    ]

    markets = Counter(
        ticker.rpartition(".")[2]
        for ticker in unique_tickers
    )

    suggested = sum(
        candidate.yahoo_ticker is not None
        for candidate in candidates
    )

    print(f"XTB reports: {len(files)}")
    print(f"Unique instruments: {len(unique_tickers)}")

    print("\nMarkets:")
    for market, count in sorted(markets.items()):
        print(f"  {market}: {count}")

    print(f"\nYahoo symbol candidates: {suggested}")
    print(f"Manual mapping required: {len(candidates) - suggested}")
    print("Note: candidates have not been verified.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
