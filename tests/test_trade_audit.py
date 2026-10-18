
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from app.ingestion.trade_audit import audit_trades
from app.ingestion.xtb_cash import CashOperation


def make_operation():
    return CashOperation(
        account_id="DEMO",
        operation_type="Stock purchase",
        instrument="Demo Company",
        ticker="DEMO.US",
        category="STOCK",
        time_utc=datetime(2026, 10, 1, tzinfo=timezone.utc),
        amount=Decimal("-100"),
        external_id="DEMO_001",
        comment="OPEN BUY DEMO.US 5 @ 20.00",
        product="STOCK",
        position_id="POS_001",
    )


def test_audit_counts_valid_trades():
    purchase = make_operation()

    deposit = replace(
        purchase,
        operation_type="Deposit",
        comment="",
    )

    result = audit_trades([purchase, deposit])

    assert result.total_operations == 2
    assert result.trade_operations == 1
    assert result.parsed_trades == 1
    assert result.failed_trades == 0


def test_audit_counts_invalid_trades():
    purchase = make_operation()

    invalid = replace(
        purchase,
        comment="INVALID COMMENT",
    )

    result = audit_trades([purchase, invalid])

    assert result.trade_operations == 2
    assert result.parsed_trades == 1
    assert result.failed_trades == 1
    assert result.errors["Invalid XTB trade comment format"] == 1
