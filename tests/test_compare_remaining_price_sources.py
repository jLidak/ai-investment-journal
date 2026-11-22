
"""Offline regression tests with synthetic prices, no provider/API calls."""
import csv
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from scripts.compare_remaining_price_sources import (
    CANDIDATES, DAILY_FIELDS, SUMMARY_FIELDS,
    audit_remaining, compare, write_csv, yahoo_closes,
)

START = date(2026, 7, 1)
END = date(2026, 10, 27)
BUY_DRAM = date(2026, 8, 27)
BUY_RKLB = date(2026, 10, 12)
PURCHASES = {"DRAM.DE": BUY_DRAM, "RKLB.US": BUY_RKLB}


def yframe(days=("2026-08-27", "2026-10-12"), closes=(100, 100), currency="EUR", exchange="GER"):
    return pd.DataFrame({"Close": closes}, index=pd.to_datetime(days)), {"currency": currency, "exchangeName": exchange}


def listings(path, *, wrong_dram=False):
    isin = path.removeprefix("search/")
    return [{
        "ISIN": isin, "Code": "DRAM" if isin == "IE000CEUZ052" else "RKLB",
        "Exchange": "US" if isin == "US7731211089" or wrong_dram else "XETRA",
        "Currency": "USD" if isin == "US7731211089" or wrong_dram else "EUR",
    }]


def fake_eod(path, params):
    if path.startswith("search/"):
        return listings(path)
    if path == "eod/DRAM.XETRA":
        return [{"date": "2026-08-27", "close": "100"}, {"date": "2026-10-12", "close": "101"}]
    if path == "eod/RKLB.US":
        return [{"date": "2026-08-27", "close": "50"}, {"date": "2026-10-12", "close": "51"}]
    raise AssertionError(path)


def fake_yahoo(symbol, start, end):
    if symbol == "DRAM.DE":
        return yframe(closes=(100, 101))
    if symbol == "RKLB":
        return yframe(closes=(50, 51), currency="USD", exchange="NMS")
    raise AssertionError(symbol)


def test_both_targets_comparison_and_request_count():
    calls = []

    def fetch(path, params):
        calls.append((path, params))
        return fake_eod(path, params)

    daily, summaries = audit_remaining(START, END, PURCHASES, eodhd_fetcher=fetch, yahoo_fetcher=fake_yahoo)
    assert len(calls) == 4
    assert [x["xtb_ticker"] for x in summaries] == ["DRAM.DE", "RKLB.US"]
    assert [x["status"] for x in summaries] == ["TOO_FEW_COMMON_SESSIONS"] * 2
    assert summaries[0]["expected_currency"] == "EUR"
    assert summaries[1]["expected_currency"] == "USD"
    assert summaries[1]["identity_note"] == "XTB_LEGACY_CUSIP_REVIEW"
    assert len(daily) == 4
    assert calls[1] == ("eod/DRAM.XETRA", {"from": "2026-07-01", "to": "2026-10-26", "period": "d"})


def test_strong_agreement_when_five_common_sessions():
    days = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2)]
    prices = {d: Decimal("100") for d in days}
    _, summary = compare("DRAM.DE", "DRAM.DE", "DRAM.XETRA", "EUR", days[0], prices, prices)
    assert summary["status"] == "PRELIMINARY_AGREEMENT"
    assert summary["common_sessions"] == "5"


def test_price_difference_is_flagged_without_approval():
    d = BUY_DRAM
    rows, summary = compare("DRAM.DE", "DRAM.DE", "DRAM.XETRA", "EUR", d, {d: Decimal("107")}, {d: Decimal("100")})
    assert summary["status"] == "PRICE_DIFFERENCES_REVIEW"
    assert summary["max_abs_difference_pct"] == "7.000000"
    assert rows[0]["status"] == "PRICE_DIFFERENCE_REVIEW"


def test_one_percent_threshold_inclusive():
    d = BUY_DRAM
    rows, summary = compare("DRAM.DE", "DRAM.DE", "DRAM.XETRA", "EUR", d, {d: Decimal("101")}, {d: Decimal("100")})
    assert rows[0]["status"] == "WITHIN_1PCT"
    assert summary["differences_over_1pct"] == "0"


def test_calendar_mismatch_is_explicit():
    d1, d2 = BUY_DRAM, date(2026, 8, 28)
    _, metrics = compare("DRAM.DE", "DRAM.DE", "DRAM.XETRA", "EUR", d1, {d1: Decimal("100"), d2: Decimal("100")}, {d1: Decimal("100")})
    assert metrics["status"] == "SESSION_MISMATCH_REVIEW"
    assert metrics["eodhd_only_after_yahoo_start"] == "1"


def test_yahoo_later_missing_eodhd_explicit():
    d1, d2 = BUY_DRAM, date(2026, 8, 28)
    _, metrics = compare("DRAM.DE", "DRAM.DE", "DRAM.XETRA", "EUR", d1, {d1: Decimal("100")}, {d1: Decimal("100"), d2: Decimal("100")})
    assert metrics["yahoo_only_after_eodhd_start"] == "1"
    assert metrics["status"] == "SESSION_MISMATCH_REVIEW"


def test_no_quote_on_purchase_date():
    d1, d2 = BUY_DRAM, date(2026, 8, 28)
    _, metrics = compare("DRAM.DE", "DRAM.DE", "DRAM.XETRA", "EUR", d1, {d2: Decimal("100")}, {d2: Decimal("100")})
    assert metrics["status"] == "NO_EODHD_ON_PURCHASE_DAY"


def test_no_overlap_and_no_yahoo_stay_explicit():
    d1, d2 = BUY_DRAM, date(2026, 8, 28)
    _, m = compare("DRAM.DE", "DRAM.DE", "DRAM.XETRA", "EUR", d1, {d1: Decimal(100)}, {d2: Decimal(100)})
    assert m["status"] == "NO_COMMON_SESSIONS"
    _, m = compare("DRAM.DE", "DRAM.DE", "DRAM.XETRA", "EUR", d1, {d1: Decimal(100)}, {})
    assert m["status"] == "NO_YAHOO_PRICES"


def test_yahoo_wrong_currency_and_missing_exchange_block():
    df, meta = yframe(currency="USD")
    with pytest.raises(ValueError, match="currency"):
        yahoo_closes(df, meta, START, END, "EUR")
    df, meta = yframe(exchange="")
    with pytest.raises(ValueError, match="exchange"):
        yahoo_closes(df, meta, START, END, "EUR")


def test_yahoo_duplicate_and_bad_close_block():
    df, meta = yframe(days=("2026-08-27", "2026-08-27"))
    with pytest.raises(ValueError, match="Duplicate"):
        yahoo_closes(df, meta, START, END, "EUR")
    df, meta = yframe(closes=(0, 100))
    with pytest.raises(ValueError):
        yahoo_closes(df, meta, START, END, "EUR")


def test_search_identity_wrong_market_skips_price_request():
    calls = []

    def eod(path, params):
        calls.append(path)
        if path.startswith("search/"):
            return listings(path, wrong_dram=True)
        return fake_eod(path, params)

    _, summaries = audit_remaining(START, END, PURCHASES, eodhd_fetcher=eod, yahoo_fetcher=fake_yahoo)
    assert summaries[0]["status"] == "EODHD_IDENTITY_REVIEW"
    assert "eod/DRAM.XETRA" not in calls
    assert summaries[1]["status"] == "TOO_FEW_COMMON_SESSIONS"


def test_provider_error_does_not_skip_other_target():
    def eod(path, params):
        if path == "eod/DRAM.XETRA":
            raise RuntimeError("HTTP_403")
        return fake_eod(path, params)

    daily, summaries = audit_remaining(START, END, PURCHASES, eodhd_fetcher=eod, yahoo_fetcher=fake_yahoo)
    assert summaries[0]["status"] == "EODHD_PRICE_ERROR"
    assert summaries[0]["error_stage"] == "EOD_PRICES"
    assert summaries[1]["status"] == "TOO_FEW_COMMON_SESSIONS"
    assert all(r["xtb_ticker"] == "RKLB.US" for r in daily)


def test_yahoo_fetch_failure_scoped():
    def yf(symbol, *a):
        if symbol == "RKLB":
            raise RuntimeError("Provider blocked")
        return fake_yahoo(symbol, *a)

    _, summary = audit_remaining(START, END, PURCHASES, eodhd_fetcher=fake_eod, yahoo_fetcher=yf)
    assert summary[1]["status"] == "YAHOO_FETCH_ERROR"
    assert summary[0]["status"] == "TOO_FEW_COMMON_SESSIONS"


def test_yahoo_wrong_currency_per_ticker():
    def yf(symbol, *a):
        if symbol == "RKLB":
            return yframe(currency="EUR")
        return fake_yahoo(symbol, *a)

    _, summary = audit_remaining(START, END, PURCHASES, eodhd_fetcher=fake_eod, yahoo_fetcher=yf)
    assert summary[1]["status"] == "YAHOO_QUALITY_ERROR"


def test_duplicate_eodhd_days_rejected_and_error_scoped():
    def eod(path, params):
        if path == "eod/DRAM.XETRA":
            return [{"date": "2026-08-27", "close": "10"}] * 2
        return fake_eod(path, params)

    _, summary = audit_remaining(START, END, PURCHASES, eodhd_fetcher=eod, yahoo_fetcher=fake_yahoo)
    assert summary[0]["status"] == "EODHD_PRICE_ERROR"


def test_missing_purchase_prevents_any_network():
    with pytest.raises(ValueError, match="RKLB"):
        audit_remaining(
            START, END, {"DRAM.DE": BUY_DRAM},
            eodhd_fetcher=lambda *a: pytest.fail("network called"),
            yahoo_fetcher=lambda *a: pytest.fail("yahoo called"),
        )


def test_csv_outputs_do_not_export_accounts_or_tokens(tmp_path):
    daily, summary = audit_remaining(
        START, END, PURCHASES,
        eodhd_fetcher=fake_eod, yahoo_fetcher=fake_yahoo,
    )
    path1 = tmp_path / "private" / "daily.csv"
    path2 = tmp_path / "private" / "summary.csv"
    write_csv(daily, DAILY_FIELDS, path1)
    write_csv(summary, SUMMARY_FIELDS, path2)
    with path1.open(encoding="utf-8-sig", newline="") as f:
        assert len(list(csv.DictReader(f))) == 4
    for path in (path1, path2):
        t = path.read_text(encoding="utf-8-sig")
        assert "account_id" not in t and "api_token" not in t and "quantity" not in t
