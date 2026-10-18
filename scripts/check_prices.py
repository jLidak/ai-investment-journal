
import argparse
from datetime import date

from app.market_data.yahoo import download_daily_closes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("start")
    parser.add_argument("end")

    args = parser.parse_args()

    prices = download_daily_closes(
        args.symbol,
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
    )

    print(f"Symbol: {args.symbol}")
    print(f"Available sessions: {len(prices)}")

    for day, close in list(prices.items())[:5]:
        print(f"{day}: {close}")


if __name__ == "__main__":
    main()
