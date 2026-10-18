
from datetime import date
from decimal import Decimal, InvalidOperation

import yfinance as yf


def download_daily_closes(
    symbol: str,
    start: date,
    end: date,
) -> dict[date, Decimal]:
    """Fetch daily closing prices in [start, end)."""

    if not symbol.strip():
        raise ValueError("Symbol cannot be empty")

    if start >= end:
        raise ValueError("End date must be after start date")

    history = yf.Ticker(symbol).history(
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=False,
    )

    if history.empty:
        raise ValueError(f"No price data for {symbol}")

    if "Close" not in history.columns:
        raise ValueError("Missing Close column")

    closes: dict[date, Decimal] = {}

    for timestamp, row in history.iterrows():
        day = timestamp.date()

        if not (start <= day < end):
            raise ValueError("Received date outside requested range")

        try:
            price = Decimal(str(row["Close"]))
        except InvalidOperation as exc:
            raise ValueError(
                f"Invalid close price on {day}"
            ) from exc

        if not price.is_finite() or price <= 0:
            raise ValueError(f"Invalid close price on {day}")

        if day in closes:
            raise ValueError(f"Duplicate close date: {day}")

        closes[day] = price

    return dict(sorted(closes.items()))
