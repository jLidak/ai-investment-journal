
from collections import Counter
from dataclasses import dataclass

from app.ingestion.xtb_cash import CashOperation
from app.ingestion.xtb_trade_comments import parse_trade_details


TRADE_TYPES = {"Stock purchase", "Stock sell"}


@dataclass(frozen=True)
class TradeAudit:
    total_operations: int
    trade_operations: int
    parsed_trades: int
    failed_trades: int
    errors: dict[str, int]


def audit_trades(operations: list[CashOperation]) -> TradeAudit:
    parsed = 0
    trade_count = 0
    errors = Counter()

    for operation in operations:
        if operation.operation_type not in TRADE_TYPES:
            continue

        trade_count += 1

        try:
            parse_trade_details(operation)
            parsed += 1
        except ValueError as exc:
            errors[str(exc)] += 1

    return TradeAudit(
        total_operations=len(operations),
        trade_operations=trade_count,
        parsed_trades=parsed,
        failed_trades=trade_count - parsed,
        errors=dict(errors),
    )
