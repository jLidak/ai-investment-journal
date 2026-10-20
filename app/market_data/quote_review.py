
from dataclasses import dataclass
from enum import Enum

from app.market_data.inspection import InstrumentInspection
from app.market_data.symbols import SymbolCandidate


class QuoteCheckStatus(str, Enum):
    PRICES_AVAILABLE = "PRICES_AVAILABLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNMAPPED = "UNMAPPED"
    NOT_CHECKED = "NOT_CHECKED"


@dataclass(frozen=True)
class QuoteReview:
    xtb_ticker: str
    yahoo_ticker: str | None
    status: QuoteCheckStatus
    currency: str | None
    reason: str


EXPECTED_CURRENCIES = {
    "US": "USD",
    "PL": "PLN",
    "DE": "EUR",
}


def review_quote(
    candidate: SymbolCandidate,
    inspection: InstrumentInspection | None,
) -> QuoteReview:

    if candidate.yahoo_ticker is None:
        return QuoteReview(
            candidate.xtb_ticker,
            None,
            QuoteCheckStatus.UNMAPPED,
            None,
            "No Yahoo ticker candidate",
        )

    if inspection is None:
        return QuoteReview(
            candidate.xtb_ticker,
            candidate.yahoo_ticker,
            QuoteCheckStatus.NOT_CHECKED,
            None,
            "Market data not inspected",
        )

    if inspection.symbol != candidate.yahoo_ticker:
        raise ValueError("Inspection symbol mismatch")

    market = candidate.xtb_ticker.rpartition(".")[2]
    currency = inspection.currency.strip()

    if not inspection.exchange or not inspection.exchange.strip():
        status = QuoteCheckStatus.REVIEW_REQUIRED
        reason = "Missing exchange metadata"

    elif market == "UK":
        status = QuoteCheckStatus.REVIEW_REQUIRED
        reason = "UK quote currency and units require review"

    elif market not in EXPECTED_CURRENCIES:
        status = QuoteCheckStatus.REVIEW_REQUIRED
        reason = "Unknown market currency expectation"

    elif currency != EXPECTED_CURRENCIES[market]:
        status = QuoteCheckStatus.REVIEW_REQUIRED
        reason = "Quote currency differs from market expectation"

    else:
        status = QuoteCheckStatus.PRICES_AVAILABLE
        reason = "Prices available; identity not verified"

    return QuoteReview(
        xtb_ticker=candidate.xtb_ticker,
        yahoo_ticker=candidate.yahoo_ticker,
        status=status,
        currency=currency,
        reason=reason,
    )
