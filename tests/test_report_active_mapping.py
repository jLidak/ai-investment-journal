
from decimal import Decimal

import pytest

from scripts.report_active_mapping import build_rows


def mapping(ticker, status="CANDIDATE", approved=""):
    return {
        "xtb_ticker": ticker,
        "xtb_catalog_status": "FOUND",
        "xtb_name": "Test instrument",
        "isin": "US0231351067",
        "xtb_currency": "EUR",
        "yahoo_currency": "EUR",
        "yahoo_candidate": "AMZ.DE",
        "approved_yahoo_ticker": approved,
        "status": status,
    }


def test_only_open_positions_are_included():
    accounts = {
        "A": {
            "AMZN.DE": Decimal("2"),
            "TSLA.DE": Decimal("0"),
        }
    }

    rows = build_rows(
        accounts,
        [mapping("AMZN.DE", "VERIFIED", "AMZ.DE")],
    )

    assert len(rows) == 1
    assert rows[0]["xtb_ticker"] == "AMZN.DE"
    assert rows[0]["mapping_readiness"] == "MAPPING_APPROVED"


def test_quantities_are_combined_across_accounts():
    accounts = {
        "A": {"AMZN.DE": Decimal("2")},
        "B": {"AMZN.DE": Decimal("0.5")},
    }

    rows = build_rows(
        accounts,
        [mapping("AMZN.DE", "VERIFIED", "AMZ.DE")],
    )

    assert rows[0]["quantity"] == "2.5"
    assert rows[0]["accounts_with_position"] == "2"


def test_candidate_is_not_automatically_approved():
    accounts = {
        "A": {"TSLA.DE": Decimal("3")}
    }

    rows = build_rows(
        accounts,
        [mapping("TSLA.DE")],
    )

    assert rows[0]["mapping_readiness"] == "NEEDS_REVIEW"
    assert rows[0]["approved_yahoo_ticker"] == ""


def test_missing_mapping_is_reported():
    accounts = {
        "A": {"UNKNOWN.DE": Decimal("1")}
    }

    rows = build_rows(accounts, [])

    assert rows[0]["identity_status"] == "MISSING_FROM_MAPPING"
    assert rows[0]["mapping_readiness"] == "NEEDS_REVIEW"


def test_negative_position_is_rejected():
    accounts = {
        "A": {"AMZN.DE": Decimal("-1")}
    }

    with pytest.raises(ValueError, match="Invalid quantity"):
        build_rows(accounts, [])
