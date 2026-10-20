
from datetime import date

import pandas as pd
import pytest

from app.market_data.inspection import inspect_instrument


def mock_yahoo(monkeypatch, history, metadata):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            return history

        def get_history_metadata(self):
            return metadata

    monkeypatch.setattr(
        "app.market_data.inspection.yf.Ticker",
        FakeTicker,
    )


@pytest.fixture
def prices():
    return pd.DataFrame(
        {"Close": [100.0, 105.0]},
        index=pd.to_datetime(["2026-09-01", "2026-09-02"]),
    )


def test_inspects_currency(monkeypatch, prices):
    mock_yahoo(
        monkeypatch, prices,
        {"currency": "USD", "exchangeName": "NMS"},
    )

    result = inspect_instrument(
        "MSFT", date(2026, 9, 1), date(2026, 9, 3)
    )

    assert result.currency == "USD"
    assert result.exchange == "NMS"
    assert result.session_count == 2


def test_preserves_gbp_pence(monkeypatch, prices):
    mock_yahoo(
        monkeypatch, prices,
        {"currency": "GBp", "exchangeName": "LSE"},
    )

    result = inspect_instrument(
        "DEMO.L", date(2026, 9, 1), date(2026, 9, 3)
    )

    assert result.currency == "GBp"


def test_missing_currency(monkeypatch, prices):
    mock_yahoo(monkeypatch, prices, {})

    with pytest.raises(ValueError, match="Missing quote currency"):
        inspect_instrument(
            "MSFT", date(2026, 9, 1), date(2026, 9, 3)
        )


def test_empty_history(monkeypatch):
    mock_yahoo(monkeypatch, pd.DataFrame(), {})

    with pytest.raises(ValueError, match="No historical prices"):
        inspect_instrument(
            "MSFT", date(2026, 9, 1), date(2026, 9, 3)
        )


def test_invalid_date_range():
    with pytest.raises(ValueError, match="Invalid date range"):
        inspect_instrument(
            "MSFT", date(2026, 9, 3), date(2026, 9, 1)
        )

def test_accepts_mapping_metadata(monkeypatch, prices):
    from collections import UserDict

    metadata = UserDict({
        "currency": "USD",
        "exchangeName": "NMS",
    })

    mock_yahoo(monkeypatch, prices, metadata)

    result = inspect_instrument(
        "MSFT",
        date(2026, 9, 1),
        date(2026, 9, 3),
    )

    assert result.currency == "USD"
    assert result.exchange == "NMS"