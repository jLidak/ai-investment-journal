import csv

import pytest

from app.market_data.portfolio_mapping import make_draft, read_csv, write_reports


def row(xtb, status="FOUND", isin="US0231351067", currency="EUR", kind="EQUITY"):
    return {
        "xtb_ticker": xtb, "status": status, "name": "Demo",
        "isin": isin, "quote_currency": currency, "kind": kind,
    }


def test_registered_amazon_uses_correct_yahoo_ticker():
    result = make_draft([row("AMZN.DE")])[0]
    assert result["status"] == "VERIFIED"
    assert result["approved_yahoo_ticker"] == "AMZ.DE"
    assert result["isin"] == "US0231351067"


def test_xbt_catalog_conflict_never_gets_approved():
    result = make_draft([row("AMZN.DE", isin="US0000000000")])[0]
    assert result["status"] == "CONFLICT"
    assert not result["approved_yahoo_ticker"]


def test_new_ticker_only_candidate_even_if_yahoo_currency_matches():
    audits = [{"xtb_ticker": "DEMO.DE", "yahoo_ticker": "DEMO.DE",
               "yahoo_currency": "EUR", "yahoo_name": "Demo", "status": "CANDIDATE"}]
    result = make_draft([row("DEMO.DE")], audits)[0]
    assert result["status"] == "CANDIDATE"
    assert not result["approved_yahoo_ticker"]


def test_currency_conflict_is_flagged():
    audits = [{"xtb_ticker": "DEMO.DE", "yahoo_ticker": "DEMO.DE",
               "yahoo_currency": "USD", "yahoo_name": "Demo"}]
    result = make_draft([row("DEMO.DE")], audits)[0]
    assert result["status"] == "CONFLICT"


def test_three_missing_instruments_remain_unapproved():
    symbols = ["DRAM.DE", "ETFPZUGOLD.PL", "SPX.DE"]
    results = make_draft([row(s, "NOT_IN_CATALOG") for s in symbols])
    assert {r["xtb_ticker"] for r in results} == set(symbols)
    assert all(r["status"] == "NOT_IN_CATALOG" for r in results)
    assert all(not r["approved_yahoo_ticker"] and not r["isin"] for r in results)


def test_ambiguous_xbt_ticker_is_never_approved():
    result = make_draft([row("ATS.PL", "AMBIGUOUS")])[0]
    assert result["status"] == "AMBIGUOUS"
    assert result["isin"] == ""


def test_duplicate_ticker_rejected():
    with pytest.raises(ValueError, match="duplicated"):
        make_draft([row("AMZN.DE"), row("AMZN.DE")])


def test_report_contains_all_and_review_subset(tmp_path):
    rows = make_draft([row("AMZN.DE"), row("SPX.DE", "NOT_IN_CATALOG")])
    full, review = write_reports(rows, tmp_path)
    all_rows = read_csv(full)
    review_rows = read_csv(review)
    assert len(all_rows) == 2
    assert [r["xtb_ticker"] for r in review_rows] == ["SPX.DE"]
