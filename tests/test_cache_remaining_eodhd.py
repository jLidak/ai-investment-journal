"""Offline checks for guarded DRAM/RKLB EODHD cache. No user data or network."""

import csv
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.market_data.price_cache import PriceIdentity, open_cache, read_closes, record_fetch
from scripts.apply_external_identity_review import ADDITIONAL_FIELDS, prepare_review
from scripts.cache_remaining_eodhd import (
    END, QUALITY_FLAGS, START, prepare_snapshot, read_sources, store, verify_fresh,
)
from scripts.review_remaining_identity_evidence import EVIDENCE, FIELDS as EVIDENCE_FIELDS

BUY = date(2026, 8, 27)


def dates(ticker):
    weekdays = {START + timedelta(days=i) for i in range((END - START).days)
                if (START + timedelta(days=i)).weekday() < 5}
    holidays = {date.fromisoformat(s) for s in EVIDENCE[ticker]["weekday_gaps"].split("|") if s}
    return sorted(weekdays - holidays)


def prices():
    return {
        ticker: {day: Decimal(100 + i) for i, day in enumerate(dates(ticker))}
        for ticker in EVIDENCE
    }


def fetch_stub(original, *, wrong_listing=None, changed=None):
    calls = []

    def get(path, params):
        calls.append(path)
        if path.startswith("search/"):
            isin = path.split("/", 1)[1]
            ticker = next(x for x in EVIDENCE if EVIDENCE[x]["current_isin"] == isin)
            expected = EVIDENCE[ticker]
            code, exchange = expected["eodhd"].rsplit(".", 1)
            if ticker == wrong_listing:
                exchange = "OTHER"
            return [{"Code": code, "Exchange": exchange,
                     "Currency": expected["currency"], "ISIN": isin}]
        ticker = next(x for x in EVIDENCE if EVIDENCE[x]["eodhd"] == path[4:])
        quoted = dict(original[ticker])
        if ticker == changed:
            first = min(quoted)
            quoted[first] += Decimal("1")
        return [{"date": d.isoformat(), "close": str(p)} for d, p in sorted(quoted.items())]
    return get, calls


def _make_snapshot(monkeypatch, tmp_path, *, bad_action=None, bad_date=None, bad_price=None, duplicate=False):
    import scripts.cache_remaining_eodhd as module
    data = prices()
    d = {t: {
        "dates": set(data[t]), "isin": EVIDENCE[t]["current_isin"],
        "currency": EVIDENCE[t]["currency"], "first_buy": BUY,
    } for t in EVIDENCE}
    if bad_date:
        d[bad_date]["dates"].remove(min(d[bad_date]["dates"]))
    actions = [{"xtb_ticker": t, "weekdays_without_quotes": EVIDENCE[t]["weekday_gaps"],
                "reference_weekdays": "84", "snapshot_sessions": str(len(data[t])),
                "first_purchase": BUY.isoformat()} for t in EVIDENCE]
    if bad_action:
        actions[0]["first_purchase"] = "2026-08-28"
    monkeypatch.setattr(module, "read_sources", lambda _: ([], actions))
    monkeypatch.setattr(module, "load_snapshots", lambda *args: d)
    daily = tmp_path / "daily.csv"
    fieldnames = ["xtb_ticker", "session", "eodhd_close", "yahoo_close"]
    with daily.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        rows = []
        for ticker in EVIDENCE:
            for day, price in data[ticker].items():
                row = {"xtb_ticker": ticker, "session": day.isoformat(),
                       "eodhd_close": str(price), "yahoo_close": str(price)}
                if bad_price and ticker == "DRAM.DE" and day == min(data[ticker]):
                    row["eodhd_close"] = bad_price
                rows.append(row)
        writer.writerows(rows)
        if duplicate:
            writer.writerow(rows[0])
    return {"daily": daily, "summary": tmp_path / "summary.csv"}, {ticker: BUY for ticker in EVIDENCE}, data


def test_verify_both_live_quoted_snapshots_before_saving():
    expected = prices()
    fetch, calls = fetch_stub(expected)
    out = verify_fresh(expected, START, END, fetch)
    assert len(out) == 2
    assert out["DRAM.DE"][0] == PriceIdentity(
        "DRAM.DE", "EODHD", "DRAM.XETRA", "IE000CEUZ052", "EUR")
    assert out["RKLB.US"][0].isin == "US7731211089"
    assert out["RKLB.US"][1] == expected["RKLB.US"]
    assert calls == [
        "search/IE000CEUZ052", "eod/DRAM.XETRA",
        "search/US7731211089", "eod/RKLB.US",
    ]


def test_wrong_listing_stops_before_price_fetch():
    fetch, calls = fetch_stub(prices(), wrong_listing="DRAM.DE")
    with pytest.raises(ValueError, match="identity mismatch"):
        verify_fresh(prices(), START, END, fetch)
    assert calls == ["search/IE000CEUZ052"]


def test_second_changed_quote_rejects_entire_batch():
    fetch, calls = fetch_stub(prices(), changed="RKLB.US")
    with pytest.raises(ValueError, match="fresh EODHD prices differ"):
        verify_fresh(prices(), START, END, fetch)
    assert len(calls) == 4


def test_target_isin_or_currency_mismatch_is_fatal(monkeypatch):
    import scripts.cache_remaining_eodhd as module
    targets = module.make_targets()
    altered = [replace(t, currency="CAD") if t.xtb_ticker == "DRAM.DE" else t for t in targets]
    monkeypatch.setattr(module, "make_targets", lambda: altered)
    fetch, calls = fetch_stub(prices())
    with pytest.raises(ValueError, match="parameters changed"):
        verify_fresh(prices(), START, END, fetch)
    assert not calls


def test_audited_snapshot_has_expected_holidays(monkeypatch, tmp_path):
    paths, buy, data = _make_snapshot(monkeypatch, tmp_path)
    assert prepare_snapshot(paths, buy, START, END) == data
    assert len(data["DRAM.DE"]) == 84 and len(data["RKLB.US"]) == 82


@pytest.mark.parametrize("ticker", ["DRAM.DE", "RKLB.US"])
def test_unexpected_calendar_gap_blocks(monkeypatch, tmp_path, ticker):
    paths, buy, _ = _make_snapshot(monkeypatch, tmp_path, bad_date=ticker)
    with pytest.raises(ValueError, match="calendar or identity"):
        prepare_snapshot(paths, buy, START, END)


def test_changed_purchase_metadata_blocks(monkeypatch, tmp_path):
    paths, buy, _ = _make_snapshot(monkeypatch, tmp_path, bad_action=True)
    with pytest.raises(ValueError, match="calendar or identity"):
        prepare_snapshot(paths, buy, START, END)


@pytest.mark.parametrize("value", ["0", "NaN", "1000"])
def test_bad_or_diverging_source_price_blocks(monkeypatch, tmp_path, value):
    paths, buy, _ = _make_snapshot(monkeypatch, tmp_path, bad_price=value)
    with pytest.raises(ValueError, match="invalid audited"):
        prepare_snapshot(paths, buy, START, END)


def test_duplicated_comparison_date_blocks(monkeypatch, tmp_path):
    paths, buy, _ = _make_snapshot(monkeypatch, tmp_path, duplicate=True)
    with pytest.raises(ValueError, match="invalid audited"):
        prepare_snapshot(paths, buy, START, END)


def test_other_date_range_blocked_before_io():
    with pytest.raises(ValueError, match="exact audited"):
        prepare_snapshot({}, {}, START + timedelta(days=1), END)


def write_csv(path, rows, fields=None):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        w = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        w.writeheader(); w.writerows(rows)


def test_source_check_rejects_modified_external_review(monkeypatch, tmp_path):
    import scripts.cache_remaining_eodhd as module
    # Self-contained synthetic source data: no cross-import of the tests package.
    originals = [
        {"xtb_ticker": "DRAM.DE", "xtb_catalog_status": "NOT_IN_CATALOG",
         "status": "NOT_IN_CATALOG", "isin": "", "kind": "", "xtb_currency": "",
         "approved_yahoo_ticker": "", "yahoo_candidate": "DRAM.DE",
         "yahoo_currency": "EUR", "yahoo_exchange": "XETRA",
         "reason": "", "source_url": ""},
        {"xtb_ticker": "RKLB.US", "xtb_catalog_status": "FOUND",
         "status": "CANDIDATE", "isin": "US7731221062", "kind": "EQUITY",
         "xtb_currency": "USD", "approved_yahoo_ticker": "",
         "yahoo_candidate": "RKLB", "yahoo_currency": "USD",
         "yahoo_exchange": "NasdaqGS", "reason": "", "source_url": ""},
    ]
    evidence = [{
        "xtb_ticker": t,
        "xtb_catalog_isin": e["catalog_isin"],
        "current_isin": e["current_isin"],
        "currency": e["currency"],
        "yahoo_symbol": e["yahoo"],
        "eodhd_symbol": e["eodhd"],
        "identity_exception": e["note"],
        "evidence_status": "SOURCE_EVIDENCE_READY_NOT_APPROVED",
        "explanation": e["explanation"],
        "issuer_source": e["issuer_source"],
        "market_source": e["market_source"],
    } for t, e in EVIDENCE.items()]
    reviewed = prepare_review(originals, evidence)
    paths = {name: tmp_path / f"{name}.csv" for name in
             ("registry", "external", "evidence", "summary", "actions")}
    write_csv(paths["registry"], originals)
    write_csv(paths["external"], reviewed)
    write_csv(paths["evidence"], evidence, EVIDENCE_FIELDS)
    write_csv(paths["summary"], [{"xtb_ticker": t, "status": "PRELIMINARY_AGREEMENT"}
                                 for t in EVIDENCE])
    write_csv(paths["actions"], [{"xtb_ticker": t, "status": "PRELIMINARY_OK"}
                                 for t in EVIDENCE])
    monkeypatch.setattr(module, "check_sources", lambda *args: evidence)
    out = read_sources(paths)
    assert len(out[0]) == len(EVIDENCE)

    changed = deepcopy(reviewed)
    changed[0]["approved_yahoo_ticker"] = "DRAM.DE"
    write_csv(paths["external"], changed)
    with pytest.raises(ValueError, match="External registry differs"):
        read_sources(paths)


def prepared():
    return {ticker: (
        PriceIdentity(ticker, "EODHD", EVIDENCE[ticker]["eodhd"],
                      EVIDENCE[ticker]["current_isin"], EVIDENCE[ticker]["currency"]),
        prices()[ticker],
    ) for ticker in EVIDENCE}


def test_cache_two_separate_identities_and_no_yahoo_changes(tmp_path):
    path = tmp_path / "cache.db"
    yahoo = PriceIdentity("DRAM.DE", "YAHOO", "DRAM.DE", "IE000CEUZ052", "EUR")
    c = open_cache(path)
    try:
        record_fetch(c, yahoo, START, END, {min(dates("DRAM.DE")): Decimal("999")})
    finally:
        c.close()
    assert store(path, prepared(), START, END) == {"DRAM.DE": "CACHED", "RKLB.US": "CACHED"}
    c = open_cache(path)
    try:
        for ticker, (ident, vals) in prepared().items():
            assert read_closes(c, ident, START, END) == vals
            flags = c.execute("SELECT DISTINCT quality_flags FROM checked_price_windows "
                              "WHERE xtb_ticker=? AND source='EODHD'", (ticker,)).fetchall()
            assert flags == [(QUALITY_FLAGS,)]
        assert read_closes(c, yahoo, START, END) == {min(dates("DRAM.DE")): Decimal("999")}
    finally:
        c.close()


def test_cache_is_idempotent(tmp_path):
    path = tmp_path / "cache.db"
    assert set(store(path, prepared(), START, END).values()) == {"CACHED"}
    assert set(store(path, prepared(), START, END).values()) == {"CACHE_REUSED"}


def test_prior_conflicting_eodhd_identity_blocks_all_writes(tmp_path):
    path = tmp_path / "cache.db"
    c = open_cache(path)
    try:
        record_fetch(c, PriceIdentity("DRAM.DE", "EODHD", "OLD.XETRA", "IE0000000001", "EUR"),
                     START, END, {min(dates("DRAM.DE")): Decimal("101")})
    finally:
        c.close()
    with pytest.raises(ValueError, match="conflicting cached EODHD identity"):
        store(path, prepared(), START, END)
    c = open_cache(path)
    try:
        assert read_closes(c, prepared()["RKLB.US"][0], START, END) == {}
    finally:
        c.close()


def test_bad_existing_flag_prevents_other_ticker_write(tmp_path):
    path = tmp_path / "cache.db"
    c = open_cache(path)
    try:
        identity, p = prepared()["RKLB.US"]
        record_fetch(c, identity, START, END, p, quality_flags="NOT_PNL_APPROVED")
    finally:
        c.close()
    with pytest.raises(ValueError, match="incompatible cached quality flags"):
        store(path, prepared(), START, END)
    c = open_cache(path)
    try:
        assert read_closes(c, prepared()["DRAM.DE"][0], START, END) == {}
    finally:
        c.close()


def test_conflicting_existing_close_blocks_other_ticker_write(tmp_path):
    path = tmp_path / "cache.db"
    c = open_cache(path)
    try:
        identity, p = prepared()["RKLB.US"]
        record_fetch(c, identity, START, END,
                     {min(p): Decimal("999")}, quality_flags=QUALITY_FLAGS)
    finally:
        c.close()
    with pytest.raises(ValueError, match="conflicting cached EODHD close"):
        store(path, prepared(), START, END)
    c = open_cache(path)
    try:
        assert read_closes(c, prepared()["DRAM.DE"][0], START, END) == {}
    finally:
        c.close()


def test_second_write_failure_rolls_back_first_ticker(tmp_path, monkeypatch):
    import scripts.cache_remaining_eodhd as module
    path = tmp_path / "cache.db"
    real_fetch = module.record_fetch

    def broken_fetch(conn, identity, *args, **kwargs):
        if identity.xtb_ticker == "RKLB.US":
            raise RuntimeError("simulated second-ticker write failure")
        return real_fetch(conn, identity, *args, **kwargs)

    monkeypatch.setattr(module, "record_fetch", broken_fetch)
    with pytest.raises(RuntimeError, match="write failure"):
        store(path, prepared(), START, END)
    c = open_cache(path)
    try:
        for ident, _ in prepared().values():
            assert read_closes(c, ident, START, END) == {}
    finally:
        c.close()
