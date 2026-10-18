
import pytest

from app.domain.operation_types import (
    OperationType,
    normalize_operation_type,
)


@pytest.mark.parametrize(
    "raw_type, expected",
    [
        ("Stock purchase", OperationType.BUY),
        ("Stock sell", OperationType.SELL),
        ("Deposit", OperationType.DEPOSIT),
        ("IKE deposit", OperationType.IKE_DEPOSIT),
        ("Dividend", OperationType.DIVIDEND),
        ("Withholding tax", OperationType.WITHHOLDING_TAX),
        ("Free funds interest", OperationType.INTEREST),
        ("Free funds interest tax", OperationType.INTEREST_TAX),
    ],
)
def test_normalizes_operation_types(raw_type, expected):
    result = normalize_operation_type(raw_type)

    assert result == expected


def test_removes_extra_spaces():
    result = normalize_operation_type("  Stock purchase  ")

    assert result == OperationType.BUY


def test_rejects_unknown_operation_type():
    with pytest.raises(
        ValueError,
        match="Nieobsługiwany typ operacji XTB",
    ):
        normalize_operation_type("Unknown operation")
