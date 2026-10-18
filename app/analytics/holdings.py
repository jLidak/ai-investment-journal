
from decimal import Decimal

from app.domain.operation_types import OperationType
from app.domain.positions import PositionMovement


def rebuild_holdings(
    movements: list[PositionMovement],
) -> dict[tuple[str, str], Decimal]:
    """Reconstruct final share quantities per account and ticker."""

    seen_ids = set()

    for movement in movements:
        if movement.kind not in (OperationType.BUY, OperationType.SELL):
            raise ValueError("Unsupported position movement type")

        if (
            not movement.quantity.is_finite()
            or movement.quantity <= 0
        ):
            raise ValueError("Quantity must be positive and finite")

        if movement.executed_at.utcoffset() is None:
            raise ValueError("Timestamp must include timezone")

        if not movement.account_id.strip() or not movement.ticker.strip():
            raise ValueError("Missing account ID or ticker")

        operation_key = (movement.account_id, movement.external_id)

        if not movement.external_id.strip() or operation_key in seen_ids:
            raise ValueError("Missing or duplicate operation ID")

        seen_ids.add(operation_key)

    holdings = {}

    for movement in sorted(movements, key=lambda m: m.executed_at):
        key = (movement.account_id, movement.ticker)

        previous_quantity = holdings.get(key, Decimal("0"))

        if movement.kind == OperationType.BUY:
            new_quantity = previous_quantity + movement.quantity
        else:
            new_quantity = previous_quantity - movement.quantity

        if new_quantity < 0:
            raise ValueError("Sell exceeds available holdings")

        holdings[key] = new_quantity

    return {
        key: quantity
        for key, quantity in holdings.items()
        if quantity > 0
    }
