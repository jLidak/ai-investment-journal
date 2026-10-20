
from dataclasses import dataclass
from datetime import date
from collections.abc import Mapping

import pandas as pd
import yfinance as yf


@dataclass(frozen=True)
class InstrumentInspection:
    symbol: str
    currency: str
    exchange: str | None
    session_count: int
    first_session: date
    last_session: date


def inspect_instrument(
    symbol: str,
    start: date,
    end: date,
) -> InstrumentInspection:

    if not symbol.strip():
        raise ValueError("Symbol cannot be empty")

    if start >= end:
        raise ValueError("Invalid date range")

    ticker = yf.Ticker(symbol)

    history = ticker.history(
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=False,
    )

    if history.empty:
        raise ValueError("No historical prices")

    if "Close" not in history.columns:
        raise ValueError("Missing Close column")

    closes = pd.to_numeric(history["Close"], errors="coerce")

    if closes.isna().any() or (closes <= 0).any():
        raise ValueError("Invalid closing prices")

    days = sorted(timestamp.date() for timestamp in history.index)

    if len(set(days)) != len(days):
        raise ValueError("Duplicate trading session")

    if any(not start <= day < end for day in days):
        raise ValueError("Session outside date range")

    metadata = ticker.get_history_metadata()

    if not isinstance(metadata, Mapping):
        raise ValueError("Invalid instrument metadata")

    currency = metadata.get("currency")
    exchange = metadata.get("exchangeName")

    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("Missing quote currency")

    return InstrumentInspection(
        symbol=symbol,
        currency=currency,
        exchange=exchange,
        session_count=len(days),
        first_session=days[0],
        last_session=days[-1],
    )
