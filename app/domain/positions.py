
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.operation_types import OperationType


@dataclass(frozen=True)
class PositionMovement:
    account_id: str
    ticker: str
    executed_at: datetime
    kind: OperationType
    quantity: Decimal
    external_id: str
