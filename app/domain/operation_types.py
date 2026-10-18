
from enum import Enum


class OperationType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DEPOSIT = "DEPOSIT"
    IKE_DEPOSIT = "IKE_DEPOSIT"
    DIVIDEND = "DIVIDEND"
    WITHHOLDING_TAX = "WITHHOLDING_TAX"
    INTEREST = "INTEREST"
    INTEREST_TAX = "INTEREST_TAX"


XTB_TYPE_MAPPING = {
    "Stock purchase": OperationType.BUY,
    "Stock sell": OperationType.SELL,
    "Deposit": OperationType.DEPOSIT,
    "IKE deposit": OperationType.IKE_DEPOSIT,
    "Dividend": OperationType.DIVIDEND,
    "Withholding tax": OperationType.WITHHOLDING_TAX,
    "Free funds interest": OperationType.INTEREST,
    "Free funds interest tax": OperationType.INTEREST_TAX,
}


def normalize_operation_type(raw_type: str) -> OperationType:
    """Mapuje oryginalny typ operacji XTB na typ aplikacji."""

    cleaned_type = raw_type.strip()

    try:
        return XTB_TYPE_MAPPING[cleaned_type]
    except KeyError as exc:
        raise ValueError(
            f"Nieobsługiwany typ operacji XTB: {raw_type!r}"
        ) from exc
