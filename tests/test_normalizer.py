
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.accounts import Currency, InvestmentAccount
from app.domain.operation_types import OperationType
from app.ingestion.normalizer import normalize_cash_operation
from app.ingestion.xtb_cash import CashOperation


@pytest.fixture
def raw_operation():
    return CashOperation(
        account_id="DEMO_ACCOUNT",
        operation_type="Stock purchase",
        instrument="Example Company",
        ticker="EXAMPLE.US",
        category="STC",
        time_utc=datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc),
        amount=Decimal("-125.50"),
        external_id="DEMO_001",
        comment="Test purchase",
        product="STOCK",
        position_id="POSITION_001",
    )


@pytest.mark.parametrize(
    "currency",
    [Currency.PLN, Currency.EUR, Currency.USD],
)
def test_normalizes_operation(raw_operation, currency):
    account = InvestmentAccount(
        account_id="DEMO_ACCOUNT",
        currency=currency,
    )

    result = normalize_cash_operation(raw_operation, account)

    assert result.operation_type == OperationType.BUY
    assert result.currency == currency
    assert result.amount == Decimal("-125.50")
    assert result.ticker == "EXAMPLE.US"
    assert result.external_id == "DEMO_001"
    assert result.raw_type == "Stock purchase"


def test_rejects_wrong_account(raw_operation):
    account = InvestmentAccount(
        account_id="OTHER_ACCOUNT",
        currency=Currency.PLN,
    )

    with pytest.raises(ValueError, match="account ID"):
        normalize_cash_operation(raw_operation, account)


def test_rejects_unknown_type(raw_operation):
    account = InvestmentAccount(
        account_id="DEMO_ACCOUNT",
        currency=Currency.PLN,
    )

    invalid_operation = replace(
        raw_operation,
        operation_type="Unknown operation",
    )

    with pytest.raises(ValueError, match="Nieobsługiwany typ"):
        normalize_cash_operation(invalid_operation, account)
