
"""Synthetic tests; no real accounts or API tokens."""

import csv
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.market_data.price_cache import (
    PriceIdentity, open_cache, read_closes, record_fetch,
)
from scripts.cache_spx_eodhd import (
    QUALITY_FLAGS, load_snapshot, prepare_prices, store_prices,
)
from scripts.audit_spx_session_actions import FIELDS as QUALITY_FIELDS
from scripts.compare_spx_price_sources import FIELDS as COMPARISON_FIELDS

START = date(2026, 7, 27)
END = date(2026, 8, 5)
BUY = START
DAYS = [
    START + timedelta(days=i)
    for i in range((END - START).days)
    if (START + timedelta(days=i)).weekday() < 5
]
VALUES = {day: Decimal(100 + i) for i, day in enumerate(DAYS)}


def fixture_reports(
    tmp_path, *, one_bad_price=False, missing_day=False,
    quality_flag="", yahoo_count=5,
):
    comp = tmp_path / "comparison.csv"
    quality = tmp_path / "quality.csv"
    original = VALUES.copy()
    if missing_day:
        del original[DAYS[1]]

    with comp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COMPARISON_FIELDS)
        w.writeheader()
        for day, price in sorted(original.items()):
            y = price if day in DAYS[-yahoo_count:] else None
            if one_bad_price and y is not None:
                y = Decimal("200")
            w.writerow({
                "date": day.isoformat(),
                "xtb_ticker": "SPX.DE",
                "eodhd_symbol": "SPX.XETRA",
                "yahoo_symbol": "SPX.DE",
                "eodhd_close_eur": str(price),
                "yahoo_close_eur": str(y) if y is not None else "",
                "difference_pct": "",
                "status": "WITHIN_1PCT" if y is not None else "EODHD_ONLY",
            })

    row = dict.fromkeys(QUALITY_FIELDS, "")
    row.update({
        "xtb_ticker": "SPX.DE",
        "start": START.isoformat(),
        "end_exclusive": END.isoformat(),
        "first_purchase": BUY.isoformat(),
        "reference_weekdays": str(len(DAYS)),
        "eodhd_sessions": str(len(original)),
        "yahoo_sessions_csv": str(yahoo_count),
        "yahoo_status": "OK",
        "yahoo_session_drift": "NO",
        "eodhd_div_status": "OK",
        "eodhd_split_status": "OK",
        "status": "PRELIMINARY_OK" if not quality_flag else "REVIEW_REQUIRED",
        "flags": quality_flag,
    })
    with quality.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QUALITY_FIELDS)
        w.writeheader()
        w.writerow(row)

    return comp, quality


def fetcher(changes=None, bad_identity=False):
    calls = []

    def fake(path, params):
        calls.append(path)
        if path.startswith("search/"):
            return [{
                "Code": "SPX",
                "Exchange": "US" if bad_identity else "XETRA",
                "Currency": "USD" if bad_identity else "EUR",
                "ISIN": "US84615Q1031",
            }]
        return [
            {"date": day.isoformat(), "close": str((changes or VALUES)[day])}
            for day in sorted(changes or VALUES)
        ]

    return fake, calls


def test_preparation_verifies_identity_snapshot_and_quotes(tmp_path):
    comp, quality = fixture_reports(tmp_path)
    fake, calls = fetcher()
    identity, prices, count = prepare_prices(
        START, END, BUY, comp, quality, fake,
    )
    assert (
        identity.source, identity.source_symbol, identity.quote_currency
    ) == ("EODHD", "SPX.XETRA", "EUR")
    assert identity.isin == "US84615Q1031"
    assert prices == VALUES
    assert count == 5
    assert calls == ["search/US84615Q1031", "eod/SPX.XETRA"]


def test_listing_mismatch_blocks_before_eod_call(tmp_path):
    comp, quality = fixture_reports(tmp_path)
    fake, calls = fetcher(bad_identity=True)
    with pytest.raises(ValueError, match="identity"):
        prepare_prices(START, END, BUY, comp, quality, fake)
    assert calls == ["search/US84615Q1031"]


def test_changed_fresh_quote_blocks(tmp_path):
    comp, quality = fixture_reports(tmp_path)
    changed = VALUES.copy()
    changed[DAYS[0]] = Decimal("101")
    fake, calls = fetcher(changes=changed)
    with pytest.raises(ValueError, match="differ"):
        prepare_prices(START, END, BUY, comp, quality, fake)
    assert len(calls) == 2


def test_bad_quality_report_blocks_before_fetch(tmp_path):
    comp, quality = fixture_reports(
        tmp_path, quality_flag="ACTIONS_UNVERIFIED",
    )
    fake, calls = fetcher()
    with pytest.raises(ValueError, match="Quality"):
        prepare_prices(START, END, BUY, comp, quality, fake)
    assert calls == []


def test_missing_weekday_blocks_before_fetch(tmp_path):
    comp, quality = fixture_reports(tmp_path, missing_day=True)
    fake, calls = fetcher()
    with pytest.raises(ValueError, match="weekdays"):
        prepare_prices(START, END, BUY, comp, quality, fake)
    assert calls == []


def test_price_divergence_blocks_before_fetch(tmp_path):
    comp, quality = fixture_reports(tmp_path, one_bad_price=True)
    fake, calls = fetcher()
    with pytest.raises(ValueError, match="discrepancy"):
        prepare_prices(START, END, BUY, comp, quality, fake)
    assert calls == []


def test_too_few_shared_sessions_blocks(tmp_path):
    comp, quality = fixture_reports(tmp_path, yahoo_count=4)
    with pytest.raises(ValueError, match="overlap"):
        load_snapshot(comp, START, END, BUY)


def test_range_rejected_before_network(tmp_path):
    comp, quality = fixture_reports(tmp_path)
    fake, calls = fetcher()
    with pytest.raises(ValueError, match="dates"):
        prepare_prices(END, START, BUY, comp, quality, fake)
    assert calls == []


def test_storage_is_distinct_from_yahoo(tmp_path):
    db = tmp_path / "prices.db"
    yahoo = PriceIdentity(
        "SPX.DE", "YAHOO", "SPX.DE", "US84615Q1031", "EUR",
    )
    eodhd = PriceIdentity(
        "SPX.DE", "EODHD", "SPX.XETRA", "US84615Q1031", "EUR",
    )
    con = open_cache(db)
    try:
        record_fetch(
            con, yahoo, START, END, {DAYS[0]: Decimal("999")},
        )
    finally:
        con.close()

    assert store_prices(db, eodhd, VALUES, START, END) == "CACHED"

    con = open_cache(db)
    try:
        assert read_closes(con, yahoo, START, END) == {
            DAYS[0]: Decimal("999"),
        }
        assert read_closes(con, eodhd, START, END) == VALUES
        flag = con.execute(
            "SELECT quality_flags FROM checked_price_windows "
            "WHERE source='EODHD' AND status='FETCHED'"
        ).fetchone()[0]
        assert flag == QUALITY_FLAGS
    finally:
        con.close()


def test_storage_is_idempotent(tmp_path):
    db = tmp_path / "prices.db"
    identity = PriceIdentity(
        "SPX.DE", "EODHD", "SPX.XETRA", "US84615Q1031", "EUR",
    )
    assert store_prices(db, identity, VALUES, START, END) == "CACHED"
    assert store_prices(db, identity, VALUES, START, END) == "CACHE_REUSED"


def test_conflicting_cached_price_is_blocked(tmp_path):
    db = tmp_path / "prices.db"
    identity = PriceIdentity(
        "SPX.DE", "EODHD", "SPX.XETRA", "US84615Q1031", "EUR",
    )
    store_prices(db, identity, VALUES, START, END)

    changed = VALUES.copy()
    changed[DAYS[0]] = Decimal("555")

    with pytest.raises(ValueError, match="Conflicting"):
        store_prices(db, identity, changed, START, END)

    con = open_cache(db)
    try:
        assert read_closes(con, identity, START, END) == VALUES
    finally:
        con.close()


def test_conflicting_eodhd_identity_is_blocked(tmp_path):
    db = tmp_path / "prices.db"
    first = PriceIdentity(
        "SPX.DE", "EODHD", "OLD.XETRA", "US0000000001", "EUR",
    )
    expected = PriceIdentity(
        "SPX.DE", "EODHD", "SPX.XETRA", "US84615Q1031", "EUR",
    )
    store_prices(
        db, first, {DAYS[0]: Decimal("42")}, START, END,
    )
    with pytest.raises(ValueError, match="identity"):
        store_prices(db, expected, VALUES, START, END)
