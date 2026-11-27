"""Fully synthetic cash-flow tests: no real broker data or network."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.analytics.cash_ledger import daily_cash_changes
from app.domain.accounts import Currency
from app.domain.operation_types import OperationType
from app.domain.operations import NormalizedOperation


def op(kind, amount, *, account="A", currency=Currency.PLN,
       day=1, hour=12, external_id=None):
    return NormalizedOperation(
        account_id=account,
        currency=currency,
        operation_type=kind,
        time_utc=datetime(2026, 9, day, hour, tzinfo=timezone.utc),
        amount=Decimal(amount),
        external_id=external_id or f"{account}_{day}_{hour}_{kind}",
        instrument=None,
        ticker=None,
        category=None,
        product=None,
        position_id=None,
        comment="",
        raw_type=kind.value,
    )


def test_empty_is_valid():
    assert daily_cash_changes([]) == []


def test_signed_cash_flow_not_pnl_or_balance():
    rows = daily_cash_changes([
        op(OperationType.BUY, "-60.50", day=2),
        op(OperationType.DEPOSIT, "100", day=1),
        op(OperationType.DIVIDEND, "2.50", day=2, hour=13),
    ])
    assert [r.day.day for r in rows] == [1, 2]
    assert rows[0].cash_change == Decimal("100")
    assert rows[-1].cash_change == Decimal("-58")
    assert rows[-1].change_since_first_operation == Decimal("42")
    assert rows[-1].trade_change == Decimal("-60.50")
    assert rows[-1].income_change == Decimal("2.50")
    assert sum(r.operations for r in rows) == 3


def test_separate_same_currency_accounts():
    rows = daily_cash_changes([
        op(OperationType.DEPOSIT, "50", account="PLN"),
        op(OperationType.IKE_DEPOSIT, "70", account="IKE"),
    ])
    assert [(r.account_id, r.change_since_first_operation) for r in rows] == [
        ("IKE", Decimal("70")), ("PLN", Decimal("50"))
    ]


def test_separate_eur_usd_currencies():
    rows = daily_cash_changes([
        op(OperationType.DEPOSIT, "10", account="EUR", currency=Currency.EUR),
        op(OperationType.DEPOSIT, "11", account="USD", currency=Currency.USD),
    ])
    assert {r.currency for r in rows} == {Currency.EUR, Currency.USD}


def test_all_eight_categories_are_accounted_for():
    rows = daily_cash_changes([
        op(OperationType.DEPOSIT, "100", hour=1),
        op(OperationType.IKE_DEPOSIT, "10", hour=2),
        op(OperationType.BUY, "-60", hour=3),
        op(OperationType.SELL, "20", hour=4),
        op(OperationType.DIVIDEND, "4", hour=5),
        op(OperationType.INTEREST, "1", hour=6),
        op(OperationType.WITHHOLDING_TAX, "-1", hour=7),
        op(OperationType.INTEREST_TAX, "-0.2", hour=8),
    ])[0]
    assert rows.cash_change == Decimal("73.8")
    assert rows.deposit_change == Decimal("110")
    assert rows.trade_change == Decimal("-40")
    assert rows.income_change == Decimal("5")
    assert rows.tax_change == Decimal("-1.2")
    assert rows.operations == 8


def test_utc_normalization_at_day_boundary():
    initial = op(OperationType.DEPOSIT, "9")
    shifted = replace(initial,
        time_utc=datetime(2026, 9, 2, 0, 30,
                          tzinfo=timezone(timedelta(hours=2))))
    assert daily_cash_changes([shifted])[0].day.isoformat() == "2026-09-01"


def test_identical_external_id_allowed_across_accounts():
    a = op(OperationType.DEPOSIT, "5", account="A", external_id="SAME")
    b = op(OperationType.DEPOSIT, "9", account="B", external_id="SAME")
    assert len(daily_cash_changes([a, b])) == 2


@pytest.mark.parametrize("field,value,part", [
    ("account_id", "", "account ID"),
    ("external_id", "", "operation ID"),
    ("amount", Decimal("NaN"), "finite"),
    ("amount", Decimal("Infinity"), "finite"),
    ("amount", 1.5, "Decimal"),
    ("time_utc", datetime(2026, 9, 1), "timezone"),
    ("currency", "PLN", "currency"),
    ("operation_type", "BUY", "type"),
])
def test_rejects_invalid_inputs(field, value, part):
    value_op = replace(op(OperationType.DEPOSIT, "10"), **{field: value})
    with pytest.raises(ValueError, match=part):
        daily_cash_changes([value_op])


def test_duplicate_operation_within_account_fails():
    value = op(OperationType.DEPOSIT, "12", external_id="DUP")
    with pytest.raises(ValueError, match="Duplicate"):
        daily_cash_changes([value, value])


def test_account_currency_conflict_fails():
    rows = [
        op(OperationType.DEPOSIT, "10", external_id="1"),
        op(OperationType.BUY, "-2", external_id="2", currency=Currency.USD),
    ]
    with pytest.raises(ValueError, match="Currency conflict"):
        daily_cash_changes(rows)


def test_zero_amount_is_valid_operation():
    rows = daily_cash_changes([op(OperationType.INTEREST, "0")])
    assert rows[0].cash_change == Decimal("0")


def test_negative_cash_change_is_not_treated_as_invalid():
    rows = daily_cash_changes([op(OperationType.BUY, "-200")])
    assert rows[0].change_since_first_operation == Decimal("-200")


def test_wrong_class_is_rejected():
    with pytest.raises(TypeError, match="NormalizedOperation"):
        daily_cash_changes([object()])
