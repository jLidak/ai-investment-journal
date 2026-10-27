
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.market_data.price_cache import (
    PriceIdentity,
    open_cache,
    pending_windows,
    read_closes,
    record_fetch,
)


START = date(2026, 7, 1)
END = date(2026, 7, 22)

CHECKED_AT = datetime(
    2026, 7, 22, 12, 0, tzinfo=timezone.utc
)

KAP = PriceIdentity(
    xtb_ticker="KAP.UK",
    source="YAHOO",
    source_symbol="KAP.L",
    isin="US63253R2013",
    quote_currency="USD",
)


@pytest.fixture
def cache(tmp_path):
    connection = open_cache(tmp_path / "prices.db")

    yield connection

    connection.close()


def test_partial_history_keeps_missing_edges_visible(cache):
    prices = {
        date(2026, 7, 17): Decimal("50.25"),
        date(2026, 7, 20): Decimal("51.75"),
    }

    record_fetch(
        cache,
        KAP,
        START,
        END,
        prices,
        checked_at=CHECKED_AT,
    )

    assert read_closes(cache, KAP, START, END) == prices

    # All ranges have been checked recently, including
    # the ranges where Yahoo returned no quotes.
    assert pending_windows(
        cache, KAP, START, END, now=CHECKED_AT
    ) == []


def test_missing_history_becomes_retryable_after_ttl(cache):
    record_fetch(
        cache,
        KAP,
        START,
        END,
        {
            date(2026, 7, 17): Decimal("50"),
            date(2026, 7, 20): Decimal("51"),
        },
        checked_at=CHECKED_AT,
    )

    later = CHECKED_AT + timedelta(days=8)

    assert pending_windows(
        cache, KAP, START, END, now=later
    ) == [
        (START, date(2026, 7, 17)),
        (date(2026, 7, 21), END),
    ]


def test_empty_yahoo_response_is_not_permanently_complete(cache):
    record_fetch(
        cache,
        KAP,
        START,
        END,
        {},
        checked_at=CHECKED_AT,
    )

    assert read_closes(cache, KAP, START, END) == {}

    assert pending_windows(
        cache, KAP, START, END, now=CHECKED_AT
    ) == []

    later = CHECKED_AT + timedelta(days=8)

    assert pending_windows(
        cache, KAP, START, END, now=later
    ) == [(START, END)]


def test_identical_fetch_is_idempotent(cache):
    prices = {
        date(2026, 7, 17): Decimal("49.50")
    }

    record_fetch(
        cache,
        KAP,
        START,
        END,
        prices,
        checked_at=CHECKED_AT,
    )

    record_fetch(
        cache,
        KAP,
        START,
        END,
        prices,
        checked_at=CHECKED_AT,
    )

    result = read_closes(cache, KAP, START, END)

    assert result == prices
    assert len(result) == 1


def test_conflicting_price_rolls_back_entire_fetch(cache):
    record_fetch(
        cache,
        KAP,
        START,
        END,
        {
            date(2026, 7, 17): Decimal("50")
        },
        checked_at=CHECKED_AT,
    )

    with pytest.raises(ValueError, match="Conflicting"):
        record_fetch(
            cache,
            KAP,
            START,
            END,
            {
                date(2026, 7, 16): Decimal("48"),
                date(2026, 7, 17): Decimal("55"),
            },
            checked_at=CHECKED_AT,
        )

    result = read_closes(cache, KAP, START, END)

    assert result == {
        date(2026, 7, 17): Decimal("50")
    }

    assert date(2026, 7, 16) not in result


@pytest.mark.parametrize(
    "bad_price",
    [
        Decimal("0"),
        Decimal("-2"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_invalid_prices_are_never_saved(cache, bad_price):
    with pytest.raises(ValueError):
        record_fetch(
            cache,
            KAP,
            START,
            END,
            {date(2026, 7, 17): bad_price},
            checked_at=CHECKED_AT,
        )

    assert read_closes(cache, KAP, START, END) == {}


def test_price_identity_prevents_cross_instrument_mixups(cache):
    other = PriceIdentity(
        xtb_ticker="OTHER.UK",
        source="YAHOO",
        source_symbol="KAP.L",
        isin="US0000000001",
        quote_currency="USD",
    )

    record_fetch(
        cache,
        KAP,
        START,
        END,
        {date(2026, 7, 17): Decimal("50")},
        checked_at=CHECKED_AT,
    )

    assert read_closes(
        cache, other, START, END
    ) == {}

    assert pending_windows(
        cache, other, START, END, now=CHECKED_AT
    ) == [(START, END)]


def test_source_is_part_of_price_identity(cache):
    other_source = PriceIdentity(
        xtb_ticker="KAP.UK",
        source="OTHER_PROVIDER",
        source_symbol="KAP",
        isin=KAP.isin,
        quote_currency="USD",
    )

    record_fetch(
        cache,
        KAP,
        START,
        END,
        {date(2026, 7, 17): Decimal("50")},
        checked_at=CHECKED_AT,
    )

    assert read_closes(
        cache, other_source, START, END
    ) == {}


def test_invalid_range_and_naive_time_are_rejected(cache):
    with pytest.raises(ValueError, match="End"):
        pending_windows(cache, KAP, END, START)

    with pytest.raises(ValueError, match="timezone"):
        record_fetch(
            cache,
            KAP,
            START,
            END,
            {},
            checked_at=datetime(2026, 7, 22),
        )
