
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from app.market_data.yahoo import download_daily_closes


def fake_history(monkeypatch, frame):
    calls = []

    class FakeTicker:
        def __init__(self, symbol):
            calls.append(("ticker", symbol))

        def history(self, **kwargs):
            calls.append(("history", kwargs))
            return frame

    monkeypatch.setattr(
        "app.market_data.yahoo.yf.Ticker",
        FakeTicker,
    )
    return calls


def test_returns_session_closes(monkeypatch):
    frame = pd.DataFrame(
        {"Close": [123.45, 125.75]},
        index=pd.to_datetime([
            "2026-09-01", "2026-09-02"
        ]).tz_localize("America/New_York"),
    )

    calls = fake_history(monkeypatch, frame)

    result = download_daily_closes(
        "MSFT", date(2026, 9, 1), date(2026, 9, 3)
    )

    assert result == {
        date(2026, 9, 1): Decimal("123.45"),
        date(2026, 9, 2): Decimal("125.75"),
    }
    assert calls[0] == ("ticker", "MSFT")
    assert calls[1][1]["auto_adjust"] is False
    assert calls[1][1]["actions"] is False
    assert calls[1][1]["interval"] == "1d"


def test_non_trading_days_are_allowed(monkeypatch):
    frame = pd.DataFrame(
        {"Close": [100.0, 103.0]},
        index=pd.to_datetime([
            "2026-09-11", "2026-09-14"
        ]),
    )

    fake_history(monkeypatch, frame)

    result = download_daily_closes(
        "MSFT", date(2026, 9, 11), date(2026, 9, 15)
    )

    assert len(result) == 2


def test_rejects_empty_history(monkeypatch):
    fake_history(monkeypatch, pd.DataFrame())

    with pytest.raises(ValueError, match="No price data"):
        download_daily_closes(
            "MSFT", date(2026, 9, 1), date(2026, 9, 3)
        )


def test_rejects_missing_close_column(monkeypatch):
    frame = pd.DataFrame(
        {"Open": [10]},
        index=pd.to_datetime(["2026-09-01"]),
    )

    fake_history(monkeypatch, frame)

    with pytest.raises(ValueError, match="Missing Close"):
        download_daily_closes(
            "MSFT", date(2026, 9, 1), date(2026, 9, 3)
        )


def test_rejects_nan_price(monkeypatch):
    frame = pd.DataFrame(
        {"Close": [float("nan")]},
        index=pd.to_datetime(["2026-09-01"]),
    )

    fake_history(monkeypatch, frame)

    with pytest.raises(ValueError, match="Invalid close price"):
        download_daily_closes(
            "MSFT", date(2026, 9, 1), date(2026, 9, 3)
        )


def test_rejects_bad_dates():
    with pytest.raises(ValueError, match="End date"):
        download_daily_closes(
            "MSFT", date(2026, 9, 3), date(2026, 9, 3)
        )


def test_rejects_blank_symbol():
    with pytest.raises(ValueError, match="Symbol cannot be empty"):
        download_daily_closes(
            "  ", date(2026, 9, 1), date(2026, 9, 3)
        )
