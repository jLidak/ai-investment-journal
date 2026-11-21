
import csv
from datetime import date
from decimal import Decimal

import pytest

from scripts.audit_eodhd_candidates import (
    Target, audit, choose_listing, validate_history, write_report,
)

START = date(2026, 7, 1)
END = date(2026, 10, 27)
BUY = date(2026, 7, 27)
TARGET = Target("SPX.DE", "US84615Q1031", "XETRA", "EUR")
PURCHASES = {"SPX.DE": BUY}


def listing(code="SPX", exchange="XETRA", currency="EUR", isin="US84615Q1031"):
    return {"Code": code, "Exchange": exchange, "Currency": currency, "ISIN": isin, "Name": "Synthetic asset"}


def eod(day, close="100"):
    return {"date": day, "close": close}


def test_matching_listing_checks_dates_and_inclusive_api_end():
    calls = []

    def fake_fetch(path, params):
        calls.append((path, params))
        if path.startswith("search/"):
            return [listing()]
        return [eod("2026-07-27"), eod("2026-08-21")]

    row = audit([TARGET], PURCHASES, START, END, fetch_json=fake_fetch)[0]
    assert row["status"] == "INITIAL_COVERAGE_OK"
    assert row["first_quote_after_purchase"] == "2026-07-27"
    assert row["sessions"] == "2"
    assert calls == [
        ("search/US84615Q1031", {"limit": "100"}),
        ("eod/SPX.XETRA", {"from": "2026-07-01", "to": "2026-10-26", "period": "d"}),
    ]


def test_buy_before_first_price_is_visible():
    def fake_fetch(path, params):
        return [listing()] if path.startswith("search/") else [eod("2026-08-21")]

    row = audit([TARGET], PURCHASES, START, END, fetch_json=fake_fetch)[0]
    assert row["status"] == "BUY_BEFORE_FIRST_QUOTE"
    assert row["days_to_first_quote"] == "25"


def test_other_exchange_does_not_trigger_prices():
    calls = []

    def fake_fetch(path, params):
        calls.append(path)
        return [listing(exchange="US", currency="USD")]

    row = audit([TARGET], PURCHASES, START, END, fetch_json=fake_fetch)[0]
    assert row["status"] == "OTHER_MARKET_ONLY"
    assert row["other_listings"] == "SPX.US"
    assert calls == ["search/US84615Q1031"]


def test_wrong_isin_does_not_trigger_prices():
    calls = []

    def fake_fetch(path, params):
        calls.append(path)
        return [listing(isin="US0000000001")]

    row = audit([TARGET], PURCHASES, START, END, fetch_json=fake_fetch)[0]
    assert row["status"] == "ISIN_NOT_FOUND"
    assert len(calls) == 1


def test_wrong_currency_does_not_trigger_prices():
    symbol, alternatives, status = choose_listing([listing(currency="USD")], TARGET)
    assert symbol == "" and status == "CURRENCY_MISMATCH"
    assert alternatives == "SPX.XETRA"


def test_ambiguous_exact_listings_rejected():
    _, _, status = choose_listing([listing(code="AAA"), listing(code="BBB")], TARGET)
    assert status == "AMBIGUOUS_LISTING"


def test_duplicate_prices_are_invalid():
    with pytest.raises(ValueError, match="Duplicate"):
        validate_history([eod("2026-07-27"), eod("2026-07-27")], START, END)


@pytest.mark.parametrize("bad", ["0", "NaN", "-1"])
def test_bad_prices_rejected(bad):
    with pytest.raises(ValueError):
        validate_history([eod("2026-07-27", bad)], START, END)


def test_transport_error_does_not_expose_url_or_token():
    def fake_fetch(path, params):
        raise RuntimeError("HTTP_403")

    row = audit([TARGET], PURCHASES, START, END, fetch_json=fake_fetch)[0]
    assert row["status"] == "SEARCH_ERROR"
    assert row["error_type"] == "HTTP_403"


def test_missing_purchase_rejected_before_network():
    with pytest.raises(ValueError, match="Missing first purchase"):
        audit([TARGET], {}, START, END, fetch_json=lambda *a: None)


def test_private_report_lacks_accounts_and_api_tokens(tmp_path):
    output = tmp_path / "private" / "audit.csv"
    rows = audit([TARGET], PURCHASES, START, END, fetch_json=lambda path, params: [])
    write_report(rows, output)

    with output.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        saved = list(reader)
        cols = reader.fieldnames

    assert saved[0]["status"] == "ISIN_NOT_FOUND"
    assert not {"account_id", "quantity", "api_token", "external_id"} & set(cols)
    assert "SECRET" not in output.read_text(encoding="utf-8-sig")
