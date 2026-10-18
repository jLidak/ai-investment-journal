
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain.operation_types import (
    OperationType,
    normalize_operation_type,
)
from app.ingestion.xtb_cash import CashOperation


@dataclass(frozen=True)
class TradeExecution:
    kind: OperationType
    ticker: str
    quantity: Decimal
    quoted_unit_price: Decimal
    reported_order_quantity: Decimal | None
    external_id: str


def parse_trade_details(raw: CashOperation) -> TradeExecution:
    kind = normalize_operation_type(raw.operation_type)

    if kind not in (OperationType.BUY, OperationType.SELL):
        raise ValueError("Operation is not a stock trade")

    parts = raw.comment.strip().split()

    # With ticker: OPEN BUY ABC.US 5 @ 20.50
    # No ticker:   OPEN BUY 5 @ 20.50
    if len(parts) not in (5, 6) or parts[-2] != "@":
        raise ValueError("Invalid XTB trade comment format")

    expected_prefix = (
        ["OPEN", "BUY"]
        if kind == OperationType.BUY
        else ["CLOSE", "BUY"]
    )

    if parts[:2] != expected_prefix:
        raise ValueError("Trade direction does not match operation type")

    if len(parts) == 6:
        comment_ticker = parts[2]
        quantity_text = parts[3]
    else:
        comment_ticker = None
        quantity_text = parts[2]

    ticker = raw.ticker or comment_ticker

    if not ticker:
        raise ValueError("Missing ticker")

    if comment_ticker and raw.ticker and comment_ticker != raw.ticker:
        raise ValueError("Ticker mismatch")

    # Example: 1/7 means 1 executed share out of 7 ordered.
    quantity_parts = quantity_text.split("/")

    if len(quantity_parts) not in (1, 2):
        raise ValueError("Invalid quantity format")

    try:
        quantity = Decimal(quantity_parts[0])
        order_quantity = (
            Decimal(quantity_parts[1])
            if len(quantity_parts) == 2
            else None
        )
        price = Decimal(parts[-1])
    except InvalidOperation as exc:
        raise ValueError("Invalid numeric value") from exc

    values = [quantity, price]
    if order_quantity is not None:
        values.append(order_quantity)

    if any(not value.is_finite() or value <= 0 for value in values):
        raise ValueError("Quantity and price must be positive")

    if order_quantity is not None and quantity > order_quantity:
        raise ValueError("Executed quantity exceeds order quantity")

    return TradeExecution(
        kind=kind,
        ticker=ticker,
        quantity=quantity,
        quoted_unit_price=price,
        reported_order_quantity=order_quantity,
        external_id=raw.external_id,
    )
