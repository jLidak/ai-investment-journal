
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.accounts import Currency
from app.domain.operation_types import OperationType


@dataclass(frozen=True)
class NormalizedOperation:
    account_id: str
    currency: Currency
    operation_type: OperationType
    time_utc: datetime
    amount: Decimal
    external_id: str

    instrument: str | None
    ticker: str | None
    category: str | None
    product: str | None
    position_id: str | None
    comment: str

    raw_type: str
