from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import csv
import pytest

from app.ingestion.xtb_cash import CashOperation
from app.market_data.price_cache import (
    open_cache,
    record_fetch,
)
from scripts.check_purchase_price_coverage import (
    assess_purchase_coverage,
    load_first_purchases,
    write_report,
)
from scripts.sync_verified_prices import make_identity


START = date(2026, 7, 1)
END = date(2026, 7, 22)

NOW = datetime(
    2026, 10, 27, 12, 0, tzinfo=timezone.utc
)

TARGET = {
    "xtb_ticker": "KAP.UK",
    "yahoo_ticker": "KAP.L",
    "isin": "US0000000001",
    "expected_currency": "USD",
    "identity_status": "VERIFIED",
}


@pytest.fixture
def cache(tmp_path):
    connection = open_cache(
        tmp_path / "test_coverage.db"
    )

    yield connection

    connection.close()


def make_operation(
    account="A1",
    ticker="KAP.UK",
    day=date(2026, 7, 10),
    operation_type="Stock purchase",
    comment=None,
):
    if comment is None:
        comment = (
            f"OPEN BUY {ticker} 1 @ 10.00"
            if operation_type == "Stock purchase"
            else ""
        )

    return CashOperation(
        account_id=account,
        operation_type=operation_type,
        instrument="Synthetic instrument",
        ticker=ticker,
        category="STOCK",
        time_utc=datetime(
            day.year,
            day.month,
            day.day,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        amount=Decimal("-10"),
        external_id="TEST_OPERATION",
        comment=comment,
        product="TEST",
        position_id="TEST_POSITION",
    )


def save_prices(cache, prices):
    record_fetch(
        cache,
        make_identity(TARGET),
        START,
        END,
        prices,
        checked_at=NOW,
    )


def assess(cache, purchases):
    return assess_purchase_coverage(
        [TARGET],
        purchases,
        cache,
        START,
        END,
    )[0]


def test_first_purchase_across_multiple_accounts():
    sources = {
        "EUR_A1_2026.xlsx": [
            make_operation(
                account="A1",
                day=date(2026, 7, 15),
            ),
            make_operation(
                account="A1",
                day=date(2026, 7, 20),
            ),
        ],
        "IKE_A2_2026.xlsx": [
            make_operation(
                account="A2",
                day=date(2026, 7, 10),
            ),
            make_operation(
                account="A2",
                operation_type="Deposit",
                day=date(2026, 7, 1),
            ),
        ],
    }

    def fake_reader(path):
        return sources[path.name]

    result = load_first_purchases(
        [Path(name) for name in sources],
        reader=fake_reader,
    )

    assert result == {
        "KAP.UK": date(2026, 7, 10)
    }


def test_duplicate_account_exports_are_rejected():
    files = [
        Path("EUR_A1_2026.xlsx"),
        Path("PLN_A1_2026.xlsx"),
    ]

    def fake_reader(path):
        return [make_operation(account="A1")]

    with pytest.raises(
        ValueError,
        match="Multiple exports",
    ):
        load_first_purchases(
            files,
            reader=fake_reader,
        )


def test_invalid_purchase_comment_is_not_ignored():
    invalid = replace(
        make_operation(),
        comment="OPEN BUY OTHER.US 1 @ 10.00",
    )

    def fake_reader(path):
        return [invalid]

    with pytest.raises(
        ValueError,
        match="Ticker mismatch",
    ):
        load_first_purchases(
            [Path("EUR_A1_2026.xlsx")],
            reader=fake_reader,
        )


def test_buy_before_first_cached_quote(cache):
    save_prices(
        cache,
        {
            date(2026, 7, 17): Decimal("50"),
            date(2026, 7, 20): Decimal("51"),
        },
    )

    row = assess(
        cache,
        {"KAP.UK": date(2026, 7, 10)},
    )

    assert row["status"] == "BUY_BEFORE_FIRST_QUOTE"
    assert row["first_purchase_utc"] == "2026-07-10"
    assert row["first_cached_session"] == "2026-07-17"
    assert row["days_to_first_quote"] == "7"


def test_first_purchase_has_initial_price_coverage(cache):
    save_prices(
        cache,
        {
            date(2026, 7, 17): Decimal("50"),
            date(2026, 7, 20): Decimal("51"),
        },
    )

    row = assess(
        cache,
        {"KAP.UK": date(2026, 7, 20)},
    )

    assert row["status"] == "START_PRICE_AVAILABLE"
    assert row["first_quote_on_or_after_purchase"] == (
        "2026-07-20"
    )
    assert row["days_to_first_quote"] == "0"


def test_missing_cached_prices_are_reported(cache):
    row = assess(
        cache,
        {"KAP.UK": date(2026, 7, 10)},
    )

    assert row["status"] == "NO_CACHED_PRICES"
    assert row["cached_sessions"] == "0"


def test_missing_buy_and_unapproved_identity(cache):
    blocked = {
        **TARGET,
        "xtb_ticker": "UNKNOWN.US",
        "yahoo_ticker": "",
    }

    rows = assess_purchase_coverage(
        [TARGET, blocked],
        {},
        cache,
        START,
        END,
    )

    assert rows[0]["status"] == "NO_BUY_HISTORY"
    assert rows[1]["status"] == "UNAPPROVED_IDENTITY"


def test_buy_before_cache_start_is_reported(cache):
    save_prices(
        cache,
        {
            date(2026, 7, 1): Decimal("50"),
            date(2026, 7, 2): Decimal("51"),
        },
    )

    row = assess(
        cache,
        {"KAP.UK": date(2026, 6, 30)},
    )

    assert row["status"] == "BUY_BEFORE_CACHE_START"


def test_no_quote_after_purchase(cache):
    save_prices(
        cache,
        {
            date(2026, 7, 17): Decimal("50"),
        },
    )

    row = assess(
        cache,
        {"KAP.UK": date(2026, 7, 20)},
    )

    assert row["status"] == "NO_QUOTE_AFTER_BUY"
    assert row["first_quote_on_or_after_purchase"] == ""


def test_long_gap_during_holding_is_flagged(cache):
    save_prices(
        cache,
        {
            date(2026, 7, 2): Decimal("50"),
            date(2026, 7, 20): Decimal("51"),
        },
    )

    row = assess(
        cache,
        {"KAP.UK": date(2026, 7, 2)},
    )

    assert row["status"] == "POSSIBLE_SESSION_GAPS"
    assert "LONG_CACHED_GAP" in row["flags"]


def test_report_contains_no_account_or_quantity(cache, tmp_path):
    save_prices(
        cache,
        {
            date(2026, 7, 17): Decimal("50"),
            date(2026, 7, 20): Decimal("51"),
        },
    )

    rows = assess_purchase_coverage(
        [TARGET],
        {"KAP.UK": date(2026, 7, 10)},
        cache,
        START,
        END,
    )

    output = tmp_path / "private" / "coverage.csv"

    write_report(rows, output)

    with output.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)
        saved = list(reader)
        columns = reader.fieldnames

    assert len(saved) == 1
    assert saved[0]["xtb_ticker"] == "KAP.UK"
    assert "account_id" not in columns
    assert "quantity" not in columns
    assert "external_id" not in columns


def test_invalid_date_range_is_rejected(cache):
    with pytest.raises(ValueError, match="End"):
        assess_purchase_coverage(
            [TARGET],
            {},
            cache,
            END,
            START,
        )