
import pytest

from app.domain.accounts import (
    Currency,
    InvestmentAccount,
)


@pytest.mark.parametrize(
    "currency",
    [Currency.PLN, Currency.EUR, Currency.USD],
)
def test_supported_currencies(currency):
    account = InvestmentAccount(
        account_id="DEMO_ACCOUNT",
        currency=currency,
    )

    assert account.currency == currency


def test_rejects_empty_account_id():
    with pytest.raises(ValueError):
        InvestmentAccount(
            account_id="",
            currency=Currency.PLN,
        )


def test_rejects_invalid_currency():
    with pytest.raises(TypeError):
        InvestmentAccount(
            account_id="DEMO_ACCOUNT",
            currency="GBP",
        )
