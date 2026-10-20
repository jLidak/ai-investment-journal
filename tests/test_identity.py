
from collections import UserDict

import pytest

from app.market_data.identity import fetch_yahoo_identity


def mock_info(monkeypatch, info):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def get_info(self):
            return info

    monkeypatch.setattr(
        "app.market_data.identity.yf.Ticker",
        FakeTicker,
    )


def test_reads_instrument_identity(monkeypatch):
    mock_info(monkeypatch, {
        "longName": "Microsoft Corporation",
        "quoteType": "EQUITY",
        "currency": "USD",
        "fullExchangeName": "NasdaqGS",
    })

    result = fetch_yahoo_identity("MSFT")

    assert result.name == "Microsoft Corporation"
    assert result.quote_type == "EQUITY"
    assert result.currency == "USD"


def test_uses_short_name_as_fallback(monkeypatch):
    mock_info(monkeypatch, {
        "shortName": "Demo Company",
    })

    result = fetch_yahoo_identity("DEMO")

    assert result.name == "Demo Company"


def test_accepts_mapping_metadata(monkeypatch):
    mock_info(monkeypatch, UserDict({
        "longName": "Demo Company",
    }))

    result = fetch_yahoo_identity("DEMO")

    assert result.name == "Demo Company"


def test_rejects_missing_name(monkeypatch):
    mock_info(monkeypatch, {"currency": "USD"})

    with pytest.raises(ValueError, match="Missing Yahoo"):
        fetch_yahoo_identity("DEMO")


def test_rejects_invalid_metadata(monkeypatch):
    mock_info(monkeypatch, None)

    with pytest.raises(ValueError, match="Invalid Yahoo"):
        fetch_yahoo_identity("DEMO")


def test_rejects_empty_symbol():
    with pytest.raises(ValueError, match="Symbol cannot be empty"):
        fetch_yahoo_identity("")
