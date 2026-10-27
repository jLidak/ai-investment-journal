import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from app.market_data.price_cache import (
    open_cache,
    pending_windows,
    read_closes,
    record_fetch,
)
from scripts.sync_verified_prices import (
    make_identity,
    sync_targets,
    write_sync_report,
)


START = date(2026, 10, 20)
END = date(2026, 10, 22)

NOW = datetime(
    2026, 10, 27, 12, 0, tzinfo=timezone.utc
)

TARGET = {
    "xtb_ticker": "DEMO.US",
    "yahoo_ticker": "DEMO",
    "isin": "US0000000001",
    "expected_currency": "USD",
    "identity_status": "VERIFIED",
}


@pytest.fixture
def cache(tmp_path):
    connection = open_cache(
        tmp_path / "yahoo_test.db"
    )

    yield connection

    connection.close()


def history(
    days=("2026-10-20", "2026-10-21"),
    closes=(100, 101),
    *,
    adjusted=None,
    dividends=None,
    splits=None,
):
    size = len(days)

    return pd.DataFrame(
        {
            "Close": list(closes),
            "Adj Close": (
                list(adjusted)
                if adjusted is not None
                else list(closes)
            ),
            "Dividends": (
                list(dividends)
                if dividends is not None
                else [0] * size
            ),
            "Stock Splits": (
                list(splits)
                if splits is not None
                else [0] * size
            ),
        },
        index=pd.to_datetime(days),
    )


def metadata(currency="USD", exchange="NMS"):
    return {
        "currency": currency,
        "exchangeName": exchange,
    }


def fetch_valid(symbol, start, end):
    assert symbol == "DEMO"
    assert start == START
    assert end == END

    return history(), metadata()


def test_only_approved_targets_are_fetched(cache):
    blocked = {
        "xtb_ticker": "BLOCKED.US",
        "yahoo_ticker": "",
        "isin": "",
        "expected_currency": "",
        "identity_status": "CANDIDATE",
    }

    calls = []

    def fake_fetcher(symbol, start, end):
        calls.append(symbol)
        return history(), metadata()

    report = sync_targets(
        [TARGET, blocked],
        cache,
        START,
        END,
        fetcher=fake_fetcher,
        now=NOW,
    )

    assert calls == ["DEMO"]

    assert {
        row["xtb_ticker"]: row["status"]
        for row in report
    } == {
        "DEMO.US": "FETCHED_PRELIMINARY",
        "BLOCKED.US": "SKIPPED_UNAPPROVED",
    }

    prices = read_closes(
        cache,
        make_identity(TARGET),
        START,
        END,
    )

    assert prices == {
        date(2026, 10, 20): Decimal("100"),
        date(2026, 10, 21): Decimal("101"),
    }


def test_second_run_reuses_cache_without_yahoo_call(cache):
    first = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=fetch_valid,
        now=NOW,
    )

    assert first[0]["status"] == "FETCHED_PRELIMINARY"

    def forbidden_fetcher(*args):
        raise AssertionError(
            "Yahoo should not be called again"
        )

    second = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=forbidden_fetcher,
        now=NOW,
    )

    assert second[0]["status"] == "CACHE_REUSED"
    assert second[0]["cached_sessions"] == "2"
    assert second[0]["pending_windows"] == "0"


def test_partial_uk_history_keeps_left_gap_visible(cache):
    uk_target = {
        **TARGET,
        "xtb_ticker": "KAP.UK",
        "yahoo_ticker": "KAP.L",
    }

    start = date(2026, 7, 1)
    end = date(2026, 7, 22)

    def fake_fetcher(symbol, window_start, window_end):
        assert symbol == "KAP.L"
        assert window_start == start
        assert window_end == end

        return (
            history(
                days=("2026-07-17", "2026-07-20"),
                closes=(50, 51),
            ),
            metadata(exchange="LSE"),
        )

    report = sync_targets(
        [uk_target],
        cache,
        start,
        end,
        fetcher=fake_fetcher,
        now=NOW,
    )

    row = report[0]

    assert row["status"] == "FETCHED_REVIEW"
    assert row["first_session"] == "2026-07-17"
    assert row["last_session"] == "2026-07-20"
    assert row["cached_sessions"] == "2"
    assert "LEFT_EDGE_GAP" in row["flags"]
    assert "UK_QUOTE_UNIT_REVIEW" in row["flags"]

    # Recently checked missing edges are temporarily
    # cached as NOT_RETURNED.
    identity = make_identity(uk_target)

    assert pending_windows(
        cache,
        identity,
        start,
        end,
        now=NOW,
    ) == []

    # After the TTL, missing edges can be requested again.
    later = NOW + timedelta(days=8)

    assert pending_windows(
        cache,
        identity,
        start,
        end,
        now=later,
    ) == [
        (start, date(2026, 7, 17)),
        (date(2026, 7, 21), end),
    ]


@pytest.mark.parametrize(
    "bad_metadata",
    [
        metadata(currency="EUR"),
        metadata(exchange=""),
    ],
)
def test_invalid_market_metadata_blocks_cache(
    cache,
    bad_metadata,
):
    def fake_fetcher(*args):
        return history(), bad_metadata

    report = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=fake_fetcher,
        now=NOW,
    )

    row = report[0]

    assert row["status"] == "BLOCKED_QUALITY"
    assert row["cached_sessions"] == "0"
    assert row["pending_windows"] == "1"

    assert read_closes(
        cache,
        make_identity(TARGET),
        START,
        END,
    ) == {}


def test_duplicate_sessions_are_not_cached(cache):
    def fake_fetcher(*args):
        return (
            history(
                days=("2026-10-20", "2026-10-20"),
                closes=(100, 101),
            ),
            metadata(),
        )

    report = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=fake_fetcher,
        now=NOW,
    )

    assert report[0]["status"] == "INVALID_HISTORY"

    assert read_closes(
        cache,
        make_identity(TARGET),
        START,
        END,
    ) == {}


def test_empty_history_is_not_marked_as_fetched(cache):
    def fake_fetcher(*args):
        return (
            history(days=(), closes=()),
            metadata(),
        )

    report = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=fake_fetcher,
        now=NOW,
    )

    assert report[0]["status"] == "INVALID_HISTORY"

    assert pending_windows(
        cache,
        make_identity(TARGET),
        START,
        END,
        now=NOW,
    ) == [(START, END)]


def test_dividend_warnings_survive_cache_reuse(cache):
    def fake_fetcher(*args):
        return (
            history(
                adjusted=(99, 101),
                dividends=(0, 1),
            ),
            metadata(),
        )

    first = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=fake_fetcher,
        now=NOW,
    )

    assert first[0]["status"] == "FETCHED_REVIEW"
    assert "DIVIDEND_EVENTS" in first[0]["flags"]
    assert "ADJUSTED_CLOSE_DIFFERS" in first[0]["flags"]

    def forbidden_fetcher(*args):
        raise AssertionError("Unexpected Yahoo request")

    second = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=forbidden_fetcher,
        now=NOW,
    )

    assert second[0]["status"] == "CACHE_REUSED"
    assert "DIVIDEND_EVENTS" in second[0]["flags"]
    assert "ADJUSTED_CLOSE_DIFFERS" in second[0]["flags"]


def test_network_failure_does_not_modify_cache(cache):
    def failing_fetcher(*args):
        raise RuntimeError("Simulated network failure")

    report = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=failing_fetcher,
        now=NOW,
    )

    assert report[0]["status"] == "FETCH_ERROR"
    assert report[0]["error_type"] == "RuntimeError"
    assert report[0]["cached_sessions"] == "0"

    assert pending_windows(
        cache,
        make_identity(TARGET),
        START,
        END,
        now=NOW,
    ) == [(START, END)]


def test_conflicting_price_does_not_overwrite_cache(cache):
    identity = make_identity(TARGET)

    original = {
        date(2026, 10, 20): Decimal("100"),
        date(2026, 10, 21): Decimal("101"),
    }

    record_fetch(
        cache,
        identity,
        START,
        END,
        original,
        checked_at=NOW,
    )

    # Simulate lost window bookkeeping while retained
    # historical closes still exist.
    cache.execute(
        "DELETE FROM checked_price_windows"
    )
    cache.commit()

    def conflicting_fetcher(*args):
        return (
            history(closes=(120, 121)),
            metadata(),
        )

    report = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=conflicting_fetcher,
        now=NOW,
    )

    assert report[0]["status"] == "CACHE_ERROR"
    assert report[0]["error_type"] == "ValueError"

    assert read_closes(
        cache,
        identity,
        START,
        END,
    ) == original


def test_sync_report_excludes_private_positions(
    cache,
    tmp_path,
):
    rows = sync_targets(
        [TARGET],
        cache,
        START,
        END,
        fetcher=fetch_valid,
        now=NOW,
    )

    output = tmp_path / "reports" / "sync.csv"

    write_sync_report(rows, output)

    with output.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)
        saved = list(reader)
        columns = reader.fieldnames

    assert len(saved) == 1
    assert saved[0]["xtb_ticker"] == "DEMO.US"
    assert saved[0]["cached_sessions"] == "2"

    assert "quantity" not in columns
    assert "account_id" not in columns