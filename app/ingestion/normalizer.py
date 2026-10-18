
from app.domain.accounts import InvestmentAccount
from app.domain.operations import NormalizedOperation
from app.domain.operation_types import normalize_operation_type
from app.ingestion.xtb_cash import CashOperation


def normalize_cash_operation(
    raw: CashOperation,
    account: InvestmentAccount,
) -> NormalizedOperation:

    if raw.account_id != account.account_id:
        raise ValueError(
            "Operation account ID does not match investment account"
        )

    normalized_type = normalize_operation_type(
        raw.operation_type
    )

    return NormalizedOperation(
        account_id=account.account_id,
        currency=account.currency,
        operation_type=normalized_type,
        time_utc=raw.time_utc,
        amount=raw.amount,
        external_id=raw.external_id,
        instrument=raw.instrument,
        ticker=raw.ticker,
        category=raw.category,
        product=raw.product,
        position_id=raw.position_id,
        comment=raw.comment,
        raw_type=raw.operation_type,
    )
