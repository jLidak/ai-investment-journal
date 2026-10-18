
from app.domain.positions import PositionMovement
from app.ingestion.xtb_cash import CashOperation
from app.ingestion.xtb_trade_comments import parse_trade_details


TRADE_TYPES = {"Stock purchase", "Stock sell"}


def extract_position_movements(
    operations: list[CashOperation],
) -> list[PositionMovement]:

    movements = []

    for raw in operations:
        if raw.operation_type not in TRADE_TYPES:
            continue

        execution = parse_trade_details(raw)

        movements.append(
            PositionMovement(
                account_id=raw.account_id,
                ticker=execution.ticker,
                executed_at=raw.time_utc,
                kind=execution.kind,
                quantity=execution.quantity,
                external_id=raw.external_id,
            )
        )

    return movements
