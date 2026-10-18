
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.analytics.pnl import Trade, calculate_pnl_series
from app.domain.operation_types import OperationType


def test_daily_realized_and_unrealized_pnl():
    trades = [
        Trade(
            datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
            OperationType.BUY,
            Decimal("10"),
            Decimal("100"),
        ),
        Trade(
            datetime(2026, 10, 3, 12, tzinfo=timezone.utc),
            OperationType.SELL,
            Decimal("4"),
            Decimal("110"),
        ),
    ]

    prices = {
        date(2026, 10, 1): Decimal("100"),
        date(2026, 10, 2): Decimal("105"),
        date(2026, 10, 3): Decimal("108"),
    }

    result = calculate_pnl_series(trades, prices)

    assert len(result) == 3

    assert result[0].total == Decimal("0")
    assert result[0].daily_change is None

    assert result[1].realized == Decimal("0")
    assert result[1].unrealized == Decimal("50")

    assert result[2].realized == Decimal("40")
    assert result[2].unrealized == Decimal("48")
    assert result[2].total == Decimal("88")
    assert result[2].daily_change == Decimal("38")


def test_fifo_with_two_purchase_prices():
    trades = [
        Trade(
            datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
            OperationType.BUY,
            Decimal("2"),
            Decimal("100"),
        ),
        Trade(
            datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
            OperationType.BUY,
            Decimal("2"),
            Decimal("120"),
        ),
        Trade(
            datetime(2026, 10, 3, 12, tzinfo=timezone.utc),
            OperationType.SELL,
            Decimal("3"),
            Decimal("130"),
        ),
    ]

    prices = {
        date(2026, 10, 3): Decimal("125"),
    }

    result = calculate_pnl_series(trades, prices)

    assert result[0].realized == Decimal("70")
    assert result[0].unrealized == Decimal("5")
    assert result[0].total == Decimal("75")


def test_cannot_sell_more_than_owned():
    trades = [
        Trade(
            datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
            OperationType.SELL,
            Decimal("5"),
            Decimal("100"),
        ),
    ]

    prices = {
        date(2026, 10, 1): Decimal("100"),
    }

    with pytest.raises(ValueError, match="Cannot sell"):
        calculate_pnl_series(trades, prices)
