"""Offline tests: no requests to Yahoo or EODHD."""
import csv
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from scripts.audit_remaining_sessions_actions import (
    FIELDS, audit, load_snapshots, request_events, save_report,
)
from scripts.compare_remaining_price_sources import (
    CANDIDATES, DAILY_FIELDS, SUMMARY_FIELDS,
)

START = date(2026, 8, 27)
END = date(2026, 9, 4)
BUY = START
DAYS = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31),
        date(2026, 9, 1), date(2026, 9, 2)]
PURCHASES = {"DRAM.DE": BUY, "RKLB.US": BUY}


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def make_files(tmp_path, *, mutate=None):
    summary, daily = [], []
    for ticker, (ys, es, note) in CANDIDATES.items():
        currency = "EUR" if ticker == "DRAM.DE" else "USD"
        isin = "IE000CEUZ052" if ticker == "DRAM.DE" else "US7731211089"
        r = {f: "" for f in SUMMARY_FIELDS}
        r.update(xtb_ticker=ticker, official_isin=isin, identity_note=note,
                 expected_currency=currency, eodhd_symbol=es, yahoo_symbol=ys,
                 yahoo_exchange="GER" if ticker == "DRAM.DE" else "NMS",
                 first_purchase=BUY.isoformat(), eodhd_sessions="5",
                 yahoo_sessions="5", common_sessions="5",
                 differences_over_1pct="0", eodhd_only_after_yahoo_start="0",
                 yahoo_only_after_eodhd_start="0", eodhd_on_purchase_day="YES",
                 status="PRELIMINARY_AGREEMENT")
        summary.append(r)
        for day in DAYS:
            row = {f: "" for f in DAILY_FIELDS}
            row.update(xtb_ticker=ticker, session=day.isoformat(), currency=currency,
                       eodhd_symbol=es, yahoo_symbol=ys, eodhd_close="100",
                       yahoo_close="100", difference_pct="0.000000", status="WITHIN_1PCT")
            daily.append(row)
    if mutate:
        mutate(summary, daily)
    daily_path, summary_path = tmp_path / "daily.csv", tmp_path / "summary.csv"
    write_csv(daily_path, DAILY_FIELDS, daily)
    write_csv(summary_path, SUMMARY_FIELDS, summary)
    return daily_path, summary_path


def snapshots(tmp_path):
    daily, summary = make_files(tmp_path)
    return load_snapshots(daily, summary, START, END, PURCHASES)


def yahoo_hist(days=DAYS, *, currency="EUR", exchange="GER", div=None, splits=None):
    df = pd.DataFrame({"Close": [100] * len(days),
                       "Dividends": div if div is not None else [0] * len(days),
                       "Stock Splits": splits if splits is not None else [0] * len(days)},
                      index=pd.to_datetime(days))
    return df, {"currency": currency, "exchangeName": exchange}


def yahoo_fetch(symbol, start, end):
    return yahoo_hist(currency="EUR" if symbol == "DRAM.DE" else "USD",
                      exchange="GER" if symbol == "DRAM.DE" else "NMS")


def eod_fetch(path, params):
    return []


def test_clean_snapshot_dates_and_metadata(tmp_path):
    data = snapshots(tmp_path)
    assert set(data) == {"DRAM.DE", "RKLB.US"}
    assert data["DRAM.DE"]["dates"] == set(DAYS)
    assert data["RKLB.US"]["isin"] == "US7731211089"


def test_summary_identity_note_is_required(tmp_path):
    daily, summary = make_files(tmp_path, mutate=lambda summaries, _: summaries[1].update(identity_note=""))
    with pytest.raises(ValueError, match="identity"):
        load_snapshots(daily, summary, START, END, PURCHASES)


def test_summary_must_still_be_preliminary_agreement(tmp_path):
    daily, summary = make_files(tmp_path, mutate=lambda summaries, _: summaries[0].update(status="REVIEW_REQUIRED"))
    with pytest.raises(ValueError, match="not clean"):
        load_snapshots(daily, summary, START, END, PURCHASES)


def test_comparison_price_mismatch_is_rejected(tmp_path):
    daily, summary = make_files(tmp_path, mutate=lambda _, rows: rows[0].update(eodhd_close="108"))
    with pytest.raises(ValueError, match="Price discrepancy"):
        load_snapshots(daily, summary, START, END, PURCHASES)


def test_duplicate_daily_session_is_rejected(tmp_path):
    daily, summary = make_files(tmp_path, mutate=lambda _, rows: rows.append(dict(rows[0])))
    with pytest.raises(ValueError, match="daily row"):
        load_snapshots(daily, summary, START, END, PURCHASES)


def test_missing_daily_row_is_rejected(tmp_path):
    daily, summary = make_files(tmp_path, mutate=lambda _, rows: rows.pop(0))
    with pytest.raises(ValueError, match="mismatch"):
        load_snapshots(daily, summary, START, END, PURCHASES)


def test_missing_first_purchase_is_rejected_before_network(tmp_path):
    daily, summary = make_files(tmp_path)
    with pytest.raises(ValueError, match="purchase"):
        load_snapshots(daily, summary, START, END, {"DRAM.DE": BUY})


def test_successful_live_audit_uses_four_eodhd_action_calls(tmp_path):
    calls = []
    def eod(path, params):
        calls.append(path)
        return []
    result = audit(snapshots(tmp_path), START, END,
                   eodhd_fetcher=eod, yahoo_fetcher=yahoo_fetch)
    assert [x["status"] for x in result] == ["PRELIMINARY_OK", "PRELIMINARY_OK"]
    assert calls == ["div/DRAM.XETRA", "splits/DRAM.XETRA",
                     "div/RKLB.US", "splits/RKLB.US"]
    assert result[0]["yahoo_session_drift"] == "NO"


def test_weekday_gap_is_reported_but_not_automatically_flagged(tmp_path):
    result = audit(snapshots(tmp_path), START, END,
                   eodhd_fetcher=eod_fetch, yahoo_fetcher=yahoo_fetch)
    assert result[0]["weekdays_without_quotes"] == "2026-09-03"
    assert result[0]["flags"] == ""


def test_denied_eodhd_dividends_means_unverified(tmp_path):
    def eod(path, params):
        if path == "div/RKLB.US":
            raise RuntimeError("HTTP_403")
        return []
    result = audit(snapshots(tmp_path), START, END,
                   eodhd_fetcher=eod, yahoo_fetcher=yahoo_fetch)
    assert result[1]["eodhd_div_status"] == "HTTP_403"
    assert result[1]["status"] == "REVIEW_REQUIRED"
    assert "ACTIONS_UNVERIFIED" in result[1]["flags"]
    assert result[0]["status"] == "PRELIMINARY_OK"


def test_invalid_eodhd_response_is_not_zero():
    events, status = request_events(lambda *args: {"error": "forbidden"},
                                    "splits", "RKLB.US", START, END)
    assert events is None
    assert status == "INVALID_RESPONSE"


def test_dividend_dates_differ_is_flagged(tmp_path):
    def eod(path, params):
        if path == "div/DRAM.XETRA":
            return [{"date": "2026-08-27", "value": "0.5"}]
        return []
    result = audit(snapshots(tmp_path), START, END,
                   eodhd_fetcher=eod, yahoo_fetcher=yahoo_fetch)
    assert "DIVIDEND_DATES_DIFFER" in result[0]["flags"]
    assert "ACTION_VALUES_REVIEW_REQUIRED" in result[0]["flags"]


def test_yahoo_date_drift_is_flagged(tmp_path):
    def drift(symbol, start, end):
        currency = "EUR" if symbol == "DRAM.DE" else "USD"
        exchange = "GER" if symbol == "DRAM.DE" else "NMS"
        return yahoo_hist(days=DAYS[:-1], currency=currency, exchange=exchange)
    result = audit(snapshots(tmp_path), START, END,
                   eodhd_fetcher=eod_fetch, yahoo_fetcher=drift)
    assert all(r["yahoo_session_drift"] == "YES" for r in result)
    assert all("YAHOO_SESSION_DRIFT" in r["flags"] for r in result)


def test_yahoo_corporate_actions_unavailable(tmp_path):
    def missing_actions(symbol, start, end):
        frame, meta = yahoo_fetch(symbol, start, end)
        return frame.drop(columns=["Stock Splits"]), meta
    result = audit(snapshots(tmp_path), START, END,
                   eodhd_fetcher=eod_fetch, yahoo_fetcher=missing_actions)
    assert all(r["yahoo_status"] == "UNAVAILABLE_OR_INVALID" for r in result)
    assert all("ACTIONS_UNVERIFIED" in r["flags"] for r in result)


def test_yahoo_exchange_changed(tmp_path):
    def changed(symbol, start, end):
        currency = "EUR" if symbol == "DRAM.DE" else "USD"
        return yahoo_hist(currency=currency, exchange="OTHER")
    result = audit(snapshots(tmp_path), START, END,
                   eodhd_fetcher=eod_fetch, yahoo_fetcher=changed)
    assert all("YAHOO_EXCHANGE_CHANGED" in r["flags"] for r in result)


def test_private_csv_contains_no_accounts_or_keys(tmp_path):
    rows = audit(snapshots(tmp_path), START, END,
                 eodhd_fetcher=eod_fetch, yahoo_fetcher=yahoo_fetch)
    target = tmp_path / "private" / "audit.csv"
    save_report(rows, target)
    with target.open(encoding="utf-8-sig", newline="") as stream:
        saved = list(csv.DictReader(stream))
    assert len(saved) == 2
    assert set(saved[0]) == set(FIELDS)
    assert not {"account_id", "quantity", "api_token"} & set(saved[0])
