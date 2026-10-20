
from collections.abc import Mapping
from dataclasses import dataclass

import yfinance as yf


@dataclass(frozen=True)
class YahooIdentity:
    symbol: str
    name: str
    quote_type: str | None
    currency: str | None
    exchange: str | None


def clean_text(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def fetch_yahoo_identity(symbol: str) -> YahooIdentity:
    """Read descriptive Yahoo metadata, without verifying identity."""

    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol cannot be empty")

    info = yf.Ticker(symbol).get_info()

    if not isinstance(info, Mapping):
        raise ValueError("Invalid Yahoo metadata")

    name = (
        clean_text(info.get("longName"))
        or clean_text(info.get("shortName"))
    )

    if name is None:
        raise ValueError("Missing Yahoo instrument name")

    return YahooIdentity(
        symbol=symbol,
        name=name,
        quote_type=clean_text(info.get("quoteType")),
        currency=clean_text(info.get("currency")),
        exchange=(
            clean_text(info.get("fullExchangeName"))
            or clean_text(info.get("exchange"))
        ),
    )
