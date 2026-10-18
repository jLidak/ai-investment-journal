
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.analytics.holdings import rebuild_holdings
from app.domain.operation_types import OperationType
from app.domain.positions import PositionMovement
from app.ingestion.xtb_cash import CashOperation
from app.ingestion.xtb_movements import extract_position_movements


def movement(kind, quantity, day, account="PLN", operation_id=None):
    return PositionMovement(
        account_id=account,
        ticker="DEMO.US",
        executed_at=datetime(2026, 10, day, tzinfo=timezone.utc),
        kind=kind,
        quantity=Decimal(quantity),
        external_id=operation_id or f"{account}_{day}",
    )


def test_rebuilds_holdings_after_trades():
    trades = [
        movement(OperationType.BUY, "10", 1),
        movement(OperationType.BUY, "5", 2),
        movement(OperationType.SELL, "7", 3),
    ]

    result = rebuild_holdings(trades)

    assert result[("PLN", "DEMO.US")] == Decimal("8")


def test_separates_accounts():
    trades = [
        movement(OperationType.BUY, "5", 1, "PLN"),
        movement(OperationType.BUY, "3", 1, "IKE"),
    ]

    result = rebuild_holdings(trades)

    assert result[("PLN", "DEMO.US")] == Decimal("5")
    assert result[("IKE", "DEMO.US")] == Decimal("3")


def test_supports_fractional_shares():
    trades = [
        movement(OperationType.BUY, "0.75", 1),
        movement(OperationType.SELL, "0.25", 2),
    ]

    result = rebuild_holdings(trades)

    assert result[("PLN", "DEMO.US")] == Decimal("0.50")


def test_rejects_overselling():
    trades = [
        movement(OperationType.BUY, "1", 1),
        movement(OperationType.SELL, "2", 2),
    ]

    with pytest.raises(ValueError, match="Sell exceeds"):
        rebuild_holdings(trades)


def test_rejects_duplicate_operations():
    original = movement(OperationType.BUY, "5", 1)

    with pytest.raises(ValueError, match="duplicate"):
        rebuild_holdings([original, original])


def test_extracts_movements_from_xtb():
    raw = CashOperation(
        account_id="DEMO_PLN",
        operation_type="Stock purchase",
        instrument="Demo Company",
        ticker="DEMO.US",
        category="STOCK",
        time_utc=datetime(2026, 10, 1, tzinfo=timezone.utc),
        amount=Decimal("-200"),
        external_id="DEMO_001",
        comment="OPEN BUY DEMO.US 2 @ 100.00",
        product="STOCK",
        position_id="POS_001",
    )

    deposit = replace(
        raw,
        operation_type="Deposit",
        external_id="DEMO_002",
    )

    result = extract_position_movements([raw, deposit])

    assert len(result) == 1
    assert result[0].kind == OperationType.BUY
    assert result[0].quantity == Decimal("2")
    assert result[0].account_id == "DEMO_PLN"
