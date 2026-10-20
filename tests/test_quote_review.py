
from datetime import date

import pytest

from app.market_data.inspection import InstrumentInspection
from app.market_data.quote_review import (
    QuoteCheckStatus,
    review_quote,
)
from app.market_data.symbols import suggest_yahoo_ticker


def inspection(symbol, currency="USD", exchange="NMS"):
    return InstrumentInspection(
        symbol=symbol,
        currency=currency,
        exchange=exchange,
        session_count=2,
        first_session=date(2026, 9, 1),
        last_session=date(2026, 9, 2),
    )


@pytest.mark.parametrize(
    "xtb, yahoo, currency, exchange",
    [
        ("MSFT.US", "MSFT", "USD", "NMS"),
        ("GPW.PL", "GPW.WA", "PLN", "WSE"),
        ("BMW.DE", "BMW.DE", "EUR", "GER"),
    ],
)
def test_accepts_available_prices(xtb, yahoo, currency, exchange):
    candidate = suggest_yahoo_ticker(xtb)

    result = review_quote(
        candidate,
        inspection(yahoo, currency, exchange),
    )

    assert result.status == QuoteCheckStatus.PRICES_AVAILABLE
    assert result.currency == currency


def test_flags_unexpected_currency():
    candidate = suggest_yahoo_ticker("BMW.DE")

    result = review_quote(
        candidate,
        inspection("BMW.DE", "USD", "GER"),
    )

    assert result.status == QuoteCheckStatus.REVIEW_REQUIRED


def test_flags_uk_quote_units():
    candidate = suggest_yahoo_ticker("LSEG.UK")

    result = review_quote(
        candidate,
        inspection("LSEG.L", "GBp", "LSE"),
    )

    assert result.status == QuoteCheckStatus.REVIEW_REQUIRED
    assert result.currency == "GBp"


def test_flags_missing_exchange():
    candidate = suggest_yahoo_ticker("MSFT.US")

    result = review_quote(
        candidate,
        inspection("MSFT", "USD", None),
    )

    assert result.status == QuoteCheckStatus.REVIEW_REQUIRED


def test_handles_unmapped_symbol():
    candidate = suggest_yahoo_ticker("DEMO.BE")

    result = review_quote(candidate, None)

    assert result.status == QuoteCheckStatus.UNMAPPED


def test_handles_not_checked_symbol():
    candidate = suggest_yahoo_ticker("MSFT.US")

    result = review_quote(candidate, None)

    assert result.status == QuoteCheckStatus.NOT_CHECKED


def test_rejects_wrong_inspection():
    candidate = suggest_yahoo_ticker("MSFT.US")

    with pytest.raises(ValueError, match="symbol mismatch"):
        review_quote(
            candidate,
            inspection("AAPL", "USD", "NMS"),
        )
