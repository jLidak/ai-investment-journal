
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.operation_types import OperationType
from app.ingestion.xtb_cash import CashOperation
from app.ingestion.xtb_trade_comments import parse_trade_details


@pytest.fixture
def demo_operation():
    return CashOperation(
        account_id="DEMO_EUR",
        operation_type="Stock purchase",
        instrument="Demo Company",
        ticker="DEMO.US",
        category="STOCK",
        time_utc=datetime(2026, 10, 1, tzinfo=timezone.utc),
        amount=Decimal("-100"),
        external_id="DEMO_001",
        comment="OPEN BUY DEMO.US 5 @ 20.00",
        product="My Trades",
        position_id="POS_001",
    )


@pytest.mark.parametrize(
    "raw_type, comment, kind, quantity, price, order_quantity",
    [
        (
            "Stock purchase",
            "OPEN BUY DEMO.US 5 @ 20.50",
            OperationType.BUY,
            "5", "20.50", None,
        ),
        (
            "Stock purchase",
            "OPEN BUY 2 @ 100.00",
            OperationType.BUY,
            "2", "100.00", None,
        ),
        (
            "Stock purchase",
            "OPEN BUY DEMO.US 1/7 @ 158.80",
            OperationType.BUY,
            "1", "158.80", "7",
        ),
        (
            "Stock sell",
            "CLOSE BUY 0.5/1.5 @ 150.00",
            OperationType.SELL,
            "0.5", "150.00", "1.5",
        ),
    ],
)
def test_parses_trade_comments(
    demo_operation,
    raw_type,
    comment,
    kind,
    quantity,
    price,
    order_quantity,
):
    raw = replace(
        demo_operation,
        operation_type=raw_type,
        comment=comment,
    )

    result = parse_trade_details(raw)

    assert result.kind == kind
    assert result.ticker == "DEMO.US"
    assert result.quantity == Decimal(quantity)
    assert result.quoted_unit_price == Decimal(price)

    expected_order_quantity = (
        Decimal(order_quantity)
        if order_quantity is not None
        else None
    )

    assert result.reported_order_quantity == expected_order_quantity


def test_rejects_ticker_mismatch(demo_operation):
    raw = replace(
        demo_operation,
        comment="OPEN BUY OTHER.US 5 @ 20.00",
    )

    with pytest.raises(ValueError, match="Ticker mismatch"):
        parse_trade_details(raw)


def test_rejects_invalid_format(demo_operation):
    raw = replace(
        demo_operation,
        comment="Bought five shares",
    )

    with pytest.raises(ValueError, match="Invalid XTB trade"):
        parse_trade_details(raw)


def test_rejects_oversized_execution(demo_operation):
    raw = replace(
        demo_operation,
        comment="OPEN BUY DEMO.US 8/7 @ 20.00",
    )

    with pytest.raises(ValueError, match="exceeds"):
        parse_trade_details(raw)


def test_rejects_non_trade(demo_operation):
    raw = replace(
        demo_operation,
        operation_type="Deposit",
    )

    with pytest.raises(ValueError, match="not a stock trade"):
        parse_trade_details(raw)


def test_rejects_missing_ticker(demo_operation):
    raw = replace(
        demo_operation,
        ticker=None,
        comment="OPEN BUY 5 @ 20.00",
    )

    with pytest.raises(ValueError, match="Missing ticker"):
        parse_trade_details(raw)
