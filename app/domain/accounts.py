
from dataclasses import dataclass
from enum import Enum


class Currency(str, Enum):
    PLN = "PLN"
    EUR = "EUR"
    USD = "USD"


@dataclass(frozen=True)
class InvestmentAccount:
    account_id: str
    currency: Currency

    def __post_init__(self):
        if not self.account_id.strip():
            raise ValueError("Account ID cannot be empty")

        if not isinstance(self.currency, Currency):
            raise TypeError("Currency must be a Currency enum")
