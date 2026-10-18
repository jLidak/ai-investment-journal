
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.operation_types import OperationType


@dataclass(frozen=True)
class Trade:
    executed_at: datetime
    kind: OperationType
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class PnlSnapshot:
    day: date
    realized: Decimal
    unrealized: Decimal
    total: Decimal
    daily_change: Decimal | None


def calculate_pnl_series(
    trades: list[Trade],
    closing_prices: dict[date, Decimal],
) -> list[PnlSnapshot]:
    """Calculate daily cumulative trading P&L using FIFO."""

    if not closing_prices:
        raise ValueError("At least one closing price is required")

    for trade in trades:
        if trade.kind not in (OperationType.BUY, OperationType.SELL):
            raise ValueError("Only BUY and SELL are supported")
        if trade.quantity <= 0 or trade.unit_price <= 0:
            raise ValueError("Quantity and price must be positive")
        if trade.executed_at.utcoffset() is None:
            raise ValueError("Trade timestamp must include timezone")

    ordered_trades = sorted(trades, key=lambda t: t.executed_at)

    # Each lot stores (remaining quantity, purchase price).
    lots: deque[tuple[Decimal, Decimal]] = deque()

    realized = Decimal("0")
    previous_total = None
    trade_index = 0
    snapshots = []

    for day in sorted(closing_prices):
        close = closing_prices[day]

        if close < 0:
            raise ValueError("Closing price cannot be negative")

        # Apply all trades up to this valuation date.
        while trade_index < len(ordered_trades):
            trade = ordered_trades[trade_index]
            trade_day = trade.executed_at.astimezone(timezone.utc).date()

            if trade_day > day:
                break

            if trade.kind == OperationType.BUY:
                lots.append((trade.quantity, trade.unit_price))

            else:
                remaining_to_sell = trade.quantity

                while remaining_to_sell > 0:
                    if not lots:
                        raise ValueError("Cannot sell more shares than owned")

                    lot_quantity, lot_price = lots.popleft()
                    matched = min(lot_quantity, remaining_to_sell)

                    realized += matched * (trade.unit_price - lot_price)

                    if lot_quantity > matched:
                        lots.appendleft(
                            (lot_quantity - matched, lot_price)
                        )

                    remaining_to_sell -= matched

            trade_index += 1

        unrealized = sum(
            (quantity * (close - cost) for quantity, cost in lots),
            Decimal("0"),
        )

        total = realized + unrealized

        daily_change = (
            None if previous_total is None
            else total - previous_total
        )

        snapshots.append(
            PnlSnapshot(
                day=day,
                realized=realized,
                unrealized=unrealized,
                total=total,
                daily_change=daily_change,
            )
        )

        previous_total = total

    if trade_index != len(ordered_trades):
        raise ValueError("Missing closing prices after some trades")

    return snapshots
