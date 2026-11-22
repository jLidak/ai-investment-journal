"""Synthetic consistency tests for external identity source evidence."""
from copy import deepcopy
from decimal import Decimal
import pytest
from scripts.review_remaining_identity_evidence import check_sources, write_report


def data():
    registry = [
        {"xtb_ticker": "DRAM.DE", "xtb_catalog_status": "NOT_IN_CATALOG", "status": "NOT_IN_CATALOG", "isin": "", "approved_yahoo_ticker": ""},
        {"xtb_ticker": "RKLB.US", "xtb_catalog_status": "FOUND", "status": "CANDIDATE", "isin": "US7731221062", "xtb_currency": "USD", "approved_yahoo_ticker": ""},
    ]
    price = []
    action = []
    for ticker, isin, cc, yahoo, eodhd, exchange, note, sessions, market, gaps in (
        ("DRAM.DE", "IE000CEUZ052", "EUR", "DRAM.DE", "DRAM.XETRA", "GER", "XTB_CATALOG_ISIN_MISSING", 84, "XETRA", ""),
        ("RKLB.US", "US7731211089", "USD", "RKLB", "RKLB.US", "NMS", "XTB_LEGACY_CUSIP_REVIEW", 82, "US", "2026-07-03|2026-09-07"),
    ):
        price.append({"xtb_ticker": ticker, "official_isin": isin, "expected_currency": cc,
            "yahoo_symbol": yahoo, "eodhd_symbol": eodhd, "identity_note": note,
            "yahoo_exchange": exchange, "status": "PRELIMINARY_AGREEMENT", "eodhd_on_purchase_day": "YES",
            "differences_over_1pct": "0", "eodhd_only_after_yahoo_start": "0", "yahoo_only_after_eodhd_start": "0",
            "error_stage": "", "first_purchase": "2026-08-27", "max_abs_difference_pct": "0.000005",
            "common_sessions": str(sessions), "eodhd_sessions": str(sessions), "yahoo_sessions": str(sessions)})
        action.append({"xtb_ticker": ticker, "official_isin": isin, "identity_note": note,
            "market": market, "status": "PRELIMINARY_OK", "flags": "", "yahoo_status": "OK",
            "yahoo_session_drift": "NO", "eodhd_div_status": "OK", "eodhd_split_status": "OK",
            "yahoo_div_dates": "", "yahoo_split_dates": "", "eodhd_div_dates": "", "eodhd_split_dates": "",
            "weekend_quotes": "", "weekdays_without_quotes": gaps,
            "first_purchase": "2026-08-27", "snapshot_sessions": str(sessions)})
    return registry, price, action


def test_consistent_reports_make_two_unapproved_evidence_rows():
    rows = check_sources(*data())
    assert len(rows) == 2
    assert [r["evidence_status"] for r in rows] == ["SOURCE_EVIDENCE_READY_NOT_APPROVED"]*2
    assert rows[0]["current_isin"] == "IE000CEUZ052"
    assert rows[1]["xtb_catalog_isin"] == "US7731221062"


def test_rejects_approved_registry():
    a,b,c=data(); a[0]["approved_yahoo_ticker"]="DRAM.DE"
    with pytest.raises(ValueError, match="unexpectedly approved"):
        check_sources(a,b,c)


def test_rejects_wrong_xtb_legacy_isin():
    a,b,c=data(); a[1]["isin"]="US7731211089"
    with pytest.raises(ValueError, match="inconsistent isin"):
        check_sources(a,b,c)


def test_rejects_wrong_eodhd_current_isin():
    a,b,c=data(); b[0]["official_isin"]="IE0000000000"
    with pytest.raises(ValueError, match="official_isin"):
        check_sources(a,b,c)


def test_rejects_event_date_issues():
    a,b,c=data(); c[1]["yahoo_split_dates"]="2026-07-03"
    with pytest.raises(ValueError, match="yahoo_split_dates"):
        check_sources(a,b,c)


def test_rejects_unknown_weekday_gap():
    a,b,c=data(); c[1]["weekdays_without_quotes"]="2026-07-06"
    with pytest.raises(ValueError, match="weekdays_without_quotes"):
        check_sources(a,b,c)


def test_rejects_stale_session_count():
    a,b,c=data(); c[0]["snapshot_sessions"]="83"
    with pytest.raises(ValueError, match="session counts"):
        check_sources(a,b,c)


def test_rejects_material_price_difference():
    a,b,c=data(); b[1]["max_abs_difference_pct"]="0.50"
    with pytest.raises(ValueError, match="price divergence"):
        check_sources(a,b,c)


def test_rejects_missing_target():
    a,b,c=data(); del b[0]
    with pytest.raises(ValueError, match="Missing comparison"):
        check_sources(a,b,c)


def test_rejects_duplicate_target():
    a,b,c=data(); b.append(deepcopy(b[0]))
    with pytest.raises(ValueError, match="Duplicate comparison"):
        check_sources(a,b,c)


def test_private_output_has_no_account_information(tmp_path):
    rows=check_sources(*data())
    path=tmp_path/"private"/"evidence.csv"
    write_report(rows,path)
    t=path.read_text(encoding="utf-8-sig")
    assert "IE000CEUZ052" in t and "US7731211089" in t
    assert "account_id" not in t and "api_token" not in t
