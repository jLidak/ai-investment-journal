from datetime import date

import csv
import pandas as pd
import pytest

from app.market_data.identity import YahooIdentity
from scripts.audit_unapproved_candidates import (
    audit_candidates,
    prepare_requests,
    write_report,
)


START = date(2026, 10, 10)
END = date(2026, 10, 16)

REQUEST = {
    "xtb_ticker": "RKLB.US",
    "xtb_name": "Rocket Lab USA Inc - class A",
    "registry_isin": "US7731221062",
    "official_isin": "US7731211089",
    "expected_currency": "USD",
    "expected_type": "EQUITY",
    "yahoo_symbol": "RKLB",
    "first_purchase_utc": "2026-10-12",
    "source_url": "https://www.nasdaqtrader.com/",
}


def yahoo_identity(
    symbol="RKLB",
    currency="USD",
    quote_type="EQUITY",
):
    return YahooIdentity(
        symbol=symbol,
        name="Rocket Lab Corporation",
        quote_type=quote_type,
        currency=currency,
        exchange="NasdaqGS",
    )


def history(
    days=("2026-10-12", "2026-10-13"),
    closes=(100, 101),
):
    size = len(days)

    return pd.DataFrame(
        {
            "Close": list(closes),
            "Adj Close": list(closes),
            "Dividends": [0] * size,
            "Stock Splits": [0] * size,
        },
        index=pd.to_datetime(days),
    )


def fetch_history(symbol, start, end):
    assert symbol == "RKLB"
    assert start == START
    assert end == END

    return (
        history(),
        {
            "currency": "USD",
            "exchangeName": "NMS",
        },
    )


def test_prepare_only_unapproved_current_tickers():
    registry = [{
        "xtb_ticker": "RKLB.US",
        "xtb_catalog_status": "FOUND",
        "xtb_name": "Rocket Lab USA Inc - class A",
        "isin": "US7731221062",
        "xtb_currency": "USD",
        "yahoo_currency": "USD",
        "approved_yahoo_ticker": "",
        "status": "CANDIDATE",
    }]

    positions = [{
        "xtb_ticker": "RKLB.US",
        "isin": "US7731221062",
        "quote_currency": "USD",
        "approved_yahoo_ticker": "",
        "mapping_readiness": "NEEDS_REVIEW",
    }]

    purchases = [{
        "xtb_ticker": "RKLB.US",
        "first_purchase_utc": "2026-10-12",
    }]

    requests = prepare_requests(
        positions,
        registry,
        purchases,
    )

    assert len(requests) == 1
    assert requests[0]["yahoo_symbol"] == "RKLB"
    assert requests[0]["official_isin"] == "US7731211089"
    assert requests[0]["registry_isin"] == "US7731221062"


def test_dram_has_two_candidates():
    registry = [{
        "xtb_ticker": "DRAM.DE",
        "xtb_catalog_status": "NOT_IN_CATALOG",
        "xtb_name": "Memory",
        "isin": "",
        "xtb_currency": "",
        "yahoo_currency": "EUR",
        "approved_yahoo_ticker": "",
        "status": "NOT_IN_CATALOG",
    }]

    positions = [{
        "xtb_ticker": "DRAM.DE",
        "isin": "",
        "quote_currency": "",
        "approved_yahoo_ticker": "",
        "mapping_readiness": "NEEDS_REVIEW",
    }]

    purchases = [{
        "xtb_ticker": "DRAM.DE",
        "first_purchase_utc": "2026-08-27",
    }]

    requests = prepare_requests(
        positions,
        registry,
        purchases,
    )

    assert {
        item["yahoo_symbol"]
        for item in requests
    } == {"DRAM.DE", "DRAMG.DE"}

    assert all(
        item["official_isin"] == "IE000CEUZ052"
        for item in requests
    )


def test_documented_cusip_change_remains_visible():
    rows = audit_candidates(
        [REQUEST],
        START,
        END,
        identity_fetcher=lambda symbol: yahoo_identity(),
        history_fetcher=fetch_history,
    )

    row = rows[0]

    assert row["status"] == "CANDIDATE_REVIEW_REQUIRED"
    assert "DOCUMENTED_CUSIP_CHANGE" in row["flags"]
    assert row["first_session"] == "2026-10-12"
    assert row["sessions"] == "2"


def test_currency_mismatch_blocks_candidate():
    def forbidden_history(*args):
        raise AssertionError("History should not be fetched")

    rows = audit_candidates(
        [REQUEST],
        START,
        END,
        identity_fetcher=lambda symbol: yahoo_identity(
            currency="EUR"
        ),
        history_fetcher=forbidden_history,
    )

    assert rows[0]["status"] == "BLOCKED_METADATA"
    assert "CURRENCY_MISMATCH" in rows[0]["flags"]


def test_wrong_yahoo_symbol_is_rejected():
    rows = audit_candidates(
        [REQUEST],
        START,
        END,
        identity_fetcher=lambda symbol: yahoo_identity(
            symbol="OTHER"
        ),
        history_fetcher=fetch_history,
    )

    assert rows[0]["status"] == "BLOCKED_METADATA"
    assert "YAHOO_SYMBOL_MISMATCH" in rows[0]["flags"]


def test_wrong_instrument_type_is_rejected():
    rows = audit_candidates(
        [REQUEST],
        START,
        END,
        identity_fetcher=lambda symbol: yahoo_identity(
            quote_type="ETF"
        ),
        history_fetcher=fetch_history,
    )

    assert rows[0]["status"] == "BLOCKED_METADATA"
    assert "QUOTE_TYPE_MISMATCH" in rows[0]["flags"]


def test_quote_history_starting_after_buy_is_blocked():
    def late_history(*args):
        return (
            history(
                days=("2026-10-13", "2026-10-14"),
                closes=(100, 101),
            ),
            {
                "currency": "USD",
                "exchangeName": "NMS",
            },
        )

    rows = audit_candidates(
        [REQUEST],
        START,
        END,
        identity_fetcher=lambda symbol: yahoo_identity(),
        history_fetcher=late_history,
    )

    assert rows[0]["status"] == "BLOCKED_QUALITY"
    assert "FIRST_QUOTE_AFTER_PURCHASE" in rows[0]["flags"]


def test_duplicate_sessions_are_invalid():
    def duplicated_history(*args):
        return (
            history(
                days=("2026-10-12", "2026-10-12"),
                closes=(100, 101),
            ),
            {
                "currency": "USD",
                "exchangeName": "NMS",
            },
        )

    rows = audit_candidates(
        [REQUEST],
        START,
        END,
        identity_fetcher=lambda symbol: yahoo_identity(),
        history_fetcher=duplicated_history,
    )

    assert rows[0]["status"] == "INVALID_HISTORY"
    assert rows[0]["error_type"] == "ValueError"


def test_identity_network_error_is_reported():
    def failing_identity(symbol):
        raise RuntimeError("Simulated failure")

    rows = audit_candidates(
        [REQUEST],
        START,
        END,
        identity_fetcher=failing_identity,
        history_fetcher=fetch_history,
    )

    assert rows[0]["status"] == "IDENTITY_FETCH_ERROR"
    assert rows[0]["error_type"] == "RuntimeError"


def test_history_network_error_is_reported():
    def failing_history(*args):
        raise RuntimeError("Simulated history failure")

    rows = audit_candidates(
        [REQUEST],
        START,
        END,
        identity_fetcher=lambda symbol: yahoo_identity(),
        history_fetcher=failing_history,
    )

    assert rows[0]["status"] == "HISTORY_FETCH_ERROR"
    assert rows[0]["error_type"] == "RuntimeError"


def test_report_is_written_without_private_account_data(
    tmp_path,
):
    rows = audit_candidates(
        [REQUEST],
        START,
        END,
        identity_fetcher=lambda symbol: yahoo_identity(),
        history_fetcher=fetch_history,
    )

    output = tmp_path / "private" / "review.csv"

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
    assert saved[0]["xtb_ticker"] == "RKLB.US"

    assert "account_id" not in columns
    assert "quantity" not in columns
    assert "external_id" not in columns


def test_invalid_date_range_is_rejected():
    with pytest.raises(ValueError, match="End"):
        audit_candidates(
            [REQUEST],
            END,
            START,
            identity_fetcher=lambda symbol: yahoo_identity(),
            history_fetcher=fetch_history,
        )