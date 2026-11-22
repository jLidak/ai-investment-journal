
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from scripts.compare_spx_price_sources import (
    audit_spx,
    compare_closes,
    parse_eodhd_closes,
    parse_yahoo_closes,
    write_report,
)

START = date(2026, 7, 1)
END = date(2026, 10, 27)
BUY = date(2026, 7, 27)


def listing(code="SPX", exchange="XETRA", currency="EUR"):
    return {"ISIN": "US84615Q1031", "Code": code,
            "Exchange": exchange, "Currency": currency}


def eod(day, close="100"):
    return {"date": day, "close": close}


def yahoo(days=("2026-08-21",), closes=(100,)):
    return (
        pd.DataFrame({"Close": closes}, index=pd.to_datetime(days)),
        {"currency": "EUR", "exchangeName": "GER"},
    )


def run(fake_eod=None, fake_yahoo=None):
    if fake_eod is None:
        fake_eod = [eod("2026-07-27"), eod("2026-08-21")]
    if fake_yahoo is None:
        fake_yahoo = yahoo()

    def fetch_json(path, params):
        return [listing()] if path.startswith("search/") else fake_eod

    return audit_spx(
        START, END, BUY,
        eodhd_fetcher=fetch_json,
        yahoo_fetcher=lambda symbol, start, end: fake_yahoo,
    )


def test_fetches_eodhd_exact_isin_and_yahoo_raw_close():
    calls = []

    def fetch_json(path, params):
        calls.append((path, params))
        return [listing()] if path.startswith("search/") else [
            eod("2026-07-27", "90"), eod("2026-08-21", "100"),
        ]

    def fetch_yahoo(symbol, start, end):
        assert (symbol, start, end) == ("SPX.DE", START, END)
        return yahoo()

    rows, result = audit_spx(
        START, END, BUY, eodhd_fetcher=fetch_json,
        yahoo_fetcher=fetch_yahoo,
    )
    assert calls == [
        ("search/US84615Q1031", {"limit": "100"}),
        ("eod/SPX.XETRA", {
            "from": "2026-07-01", "to": "2026-10-26", "period": "d",
        }),
    ]
    assert result["status"] == "TOO_FEW_COMMON_SESSIONS"
    assert result["eodhd_only_before_yahoo_start_after_buy"] == "1"
    assert rows[0]["status"] == "EODHD_ONLY"
    assert rows[1]["status"] == "WITHIN_1PCT"


def test_price_divergence_is_flagged():
    rows, summary = run(fake_eod=[eod("2026-07-27"), eod("2026-08-21", "105")])
    assert summary["status"] == "PRICE_DIFFERENCES_REVIEW"
    assert summary["largest_difference_pct"] == "5.0000"
    assert rows[-1]["status"] == "REVIEW_DIFFERENCE"


def test_one_percent_is_inclusive():
    rows, summary = run(fake_eod=[eod("2026-07-27"), eod("2026-08-21", "101")])
    assert rows[-1]["status"] == "WITHIN_1PCT"
    assert summary["differences_over_1pct"] == "0"


def test_flags_calendar_mismatch_after_yahoo_starts():
    rows, summary = run(fake_eod=[
        eod("2026-07-27"), eod("2026-08-21"), eod("2026-08-24")
    ])
    assert summary["eodhd_only_after_yahoo_start"] == "1"
    assert summary["status"] == "SESSION_MISMATCH_REVIEW"


def test_missing_purchase_day_quote_is_visible():
    rows, summary = run(fake_eod=[eod("2026-08-20"), eod("2026-08-21")])
    assert summary["status"] == "NO_EODHD_QUOTE_ON_BUY_DAY"
    assert summary["eodhd_quote_on_purchase_day"] == "NO"


def test_no_yahoo_quotes_is_explicit():
    rows, summary = run(fake_yahoo=yahoo(days=(), closes=()))
    assert summary["status"] == "NO_YAHOO_PRICES"
    assert len(rows) == 2


def test_no_overlapping_quotes_is_explicit():
    rows, summary = run(fake_yahoo=yahoo(days=("2026-09-01",), closes=(100,)))
    assert summary["status"] == "NO_COMMON_SESSIONS"
    assert rows[-1]["status"] == "YAHOO_ONLY"


def test_wrong_exchange_aborts_before_price_download():
    calls = []

    def fetch_json(path, params):
        calls.append(path)
        return [listing(exchange="US", currency="USD")]

    with pytest.raises(ValueError, match="not confirmed"):
        audit_spx(
            START, END, BUY, eodhd_fetcher=fetch_json,
            yahoo_fetcher=lambda *args: (_ for _ in ()).throw(
                AssertionError("Yahoo must not be fetched")
            ),
        )
    assert calls == ["search/US84615Q1031"]


def test_yahoo_currency_mismatch_is_rejected():
    frame, metadata = yahoo()
    with pytest.raises(ValueError, match="not EUR"):
        parse_yahoo_closes(frame, {**metadata, "currency": "USD"}, START, END)


def test_duplicate_yahoo_sessions_are_rejected():
    frame, metadata = yahoo(
        days=("2026-08-21", "2026-08-21"), closes=(100, 101),
    )
    with pytest.raises(ValueError, match="Duplicate"):
        parse_yahoo_closes(frame, metadata, START, END)


def test_invalid_eodhd_close_is_rejected():
    with pytest.raises(ValueError):
        parse_eodhd_closes([eod("2026-08-21", "0")], START, END)


def test_invalid_yahoo_close_is_rejected():
    frame, metadata = yahoo(closes=(-1,))
    with pytest.raises(ValueError):
        parse_yahoo_closes(frame, metadata, START, END)


def test_private_csv_does_not_contain_identifiers(tmp_path):
    rows, _ = run()
    output = tmp_path / "private" / "compare.csv"
    write_report(rows, output)
    content = output.read_text(encoding="utf-8-sig")
    assert "2026-07-27" in content
    assert "eodhd_close_eur" in content
    assert "account_id" not in content
    assert "api_token" not in content
    assert "quantity" not in content


def test_purchase_outside_range_rejected():
    with pytest.raises(ValueError, match="Invalid date range"):
        audit_spx(
            START, END, date(2026, 11, 1),
            eodhd_fetcher=lambda *args: [], yahoo_fetcher=lambda *args: yahoo(),
        )
