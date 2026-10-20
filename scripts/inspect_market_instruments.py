
from datetime import date

from app.market_data.inspection import inspect_instrument


def main():
    symbols = ["MSFT", "BMW.DE", "GPW.WA"]

    for symbol in symbols:
        result = inspect_instrument(
            symbol,
            date(2026, 9, 1),
            date(2026, 10, 1),
        )

        print(
            f"{result.symbol}: "
            f"{result.currency}, "
            f"{result.exchange}, "
            f"{result.session_count} sessions"
        )


if __name__ == "__main__":
    main()
