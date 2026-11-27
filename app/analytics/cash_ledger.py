"""Daily *changes* in cash recorded in XTB Cash Operations.

Amounts are signed in the settlement currency of each account. The cumulative
sum assumes zero only as a reference point: it is NOT an actual cash balance
unless the imported history is complete and the opening balance is known.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal

from app.domain.accounts import Currency
from app.domain.operation_types import OperationType
from app.domain.operations import NormalizedOperation


DEPOSIT_TYPES = frozenset({OperationType.DEPOSIT, OperationType.IKE_DEPOSIT})
TRADE_TYPES = frozenset({OperationType.BUY, OperationType.SELL})
INCOME_TYPES = frozenset({OperationType.DIVIDEND, OperationType.INTEREST})
TAX_TYPES = frozenset({OperationType.WITHHOLDING_TAX, OperationType.INTEREST_TAX})


@dataclass(frozen=True)
class CashDay:
    account_id: str
    currency: Currency
    day: date  # UTC date from the source export
    operations: int
    cash_change: Decimal
    change_since_first_operation: Decimal
    deposit_change: Decimal
    trade_change: Decimal
    income_change: Decimal
    tax_change: Decimal


def daily_cash_changes(operations: list[NormalizedOperation]) -> list[CashDay]:
    """Aggregate broker-signed cash movements per account and UTC calendar day.

    No account opening balance, FX conversion, or P&L is inferred.
    Duplicate external IDs within an account and inconsistent currencies fail.
    """
    seen: set[tuple[str, str]] = set()
    account_currencies: dict[str, Currency] = {}
    buckets: dict[tuple[str, date], dict[str, Decimal | int]] = defaultdict(
        lambda: {
            "count": 0,
            "cash": Decimal("0"),
            "deposit": Decimal("0"),
            "trade": Decimal("0"),
            "income": Decimal("0"),
            "tax": Decimal("0"),
        }
    )

    for op in operations:
        if not isinstance(op, NormalizedOperation):
            raise TypeError("Expected NormalizedOperation")
        if not op.account_id or not op.account_id.strip():
            raise ValueError("Missing account ID")
        if not op.external_id or not op.external_id.strip():
            raise ValueError("Missing operation ID")
        if not isinstance(op.currency, Currency):
            raise ValueError("Unsupported account currency")
        if not isinstance(op.operation_type, OperationType):
            raise ValueError("Unsupported operation type")
        if not isinstance(op.amount, Decimal) or not op.amount.is_finite():
            raise ValueError("Cash amount must be a finite Decimal")
        if op.time_utc.tzinfo is None or op.time_utc.utcoffset() is None:
            raise ValueError("Operation timestamp must contain a timezone")

        key = (op.account_id, op.external_id)
        if key in seen:
            raise ValueError("Duplicate operation ID within account")
        seen.add(key)

        currency = account_currencies.setdefault(op.account_id, op.currency)
        if currency != op.currency:
            raise ValueError("Currency conflict within account")

        day = op.time_utc.astimezone(timezone.utc).date()
        bucket = buckets[(op.account_id, day)]
        bucket["count"] += 1
        bucket["cash"] += op.amount

        if op.operation_type in DEPOSIT_TYPES:
            bucket["deposit"] += op.amount
        elif op.operation_type in TRADE_TYPES:
            bucket["trade"] += op.amount
        elif op.operation_type in INCOME_TYPES:
            bucket["income"] += op.amount
        elif op.operation_type in TAX_TYPES:
            bucket["tax"] += op.amount
        else:
            raise ValueError("Unexpected operation category")

    running: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    result = []
    for (account, day), bucket in sorted(buckets.items()):
        running[account] += bucket["cash"]
        result.append(CashDay(
            account_id=account,
            currency=account_currencies[account],
            day=day,
            operations=bucket["count"],
            cash_change=bucket["cash"],
            change_since_first_operation=running[account],
            deposit_change=bucket["deposit"],
            trade_change=bucket["trade"],
            income_change=bucket["income"],
            tax_change=bucket["tax"],
        ))
    return result
