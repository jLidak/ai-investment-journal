
from datetime import date

import pandas as pd
import pytest

from scripts.audit_verified_prices import (
    assess_history,
    audit_portfolio,
    prepare_targets,
)


START = date(2026, 10, 20)
END = date(2026, 10, 28)


def registry_row(
    ticker="DEMO.US",
    yahoo="DEMO",
    currency="USD",
    status="VERIFIED",
):
    return {
        "xtb_ticker": ticker,
        "xtb_catalog_status": "FOUND",
        "isin": "US0000000001",
        "xtb_currency": currency,
        "yahoo_currency": currency,
        "approved_yahoo_ticker": (
            yahoo if status == "VERIFIED" else ""
        ),
        "status": status,
    }


def position_row(
    ticker="DEMO.US",
    yahoo="DEMO",
    currency="USD",
    approved=True,
):
    return {
        "xtb_ticker": ticker,
        "isin": "US0000000001",
        "quote_currency": currency,
        "approved_yahoo_ticker": yahoo if approved else "",
        "mapping_readiness": (
            "MAPPING_APPROVED" if approved else "NEEDS_REVIEW"
        ),
    }


def history(
    days=("2026-10-20", "2026-10-21"),
    closes=(100, 101),
    dividends=None,
    splits=None,
    adjusted=None,
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


def test_only_approved_positions_are_fetched():
    reviewed = [
        registry_row(),
        registry_row(
            "BLOCKED.US", "BLOCKED", status="CANDIDATE"
        ),
    ]

    positions = [
        position_row(),
        position_row(
            "BLOCKED.US", "BLOCKED", approved=False
        ),
    ]

    targets = prepare_targets(positions, reviewed)
    calls = []

    def fake_fetcher(symbol, start, end):
        calls.append(symbol)
        return history(), metadata()

    result = audit_portfolio(
        targets, START, END, fake_fetcher
    )

    assert calls == ["DEMO"]
    assert len(result) == 2
    assert {
        row["xtb_ticker"]: row["status"]
        for row in result
    } == {
        "BLOCKED.US": "SKIPPED_UNAPPROVED",
        "DEMO.US": "PRELIMINARY_OK",
    }


def test_stale_position_report_is_rejected():
    reviewed = [registry_row()]

    positions = [position_row(approved=False)]

    with pytest.raises(ValueError, match="differs"):
        prepare_targets(positions, reviewed)


def test_currency_mismatch_requires_review():
    target = prepare_targets(
        [position_row()], [registry_row()]
    )[0]

    result = assess_history(
        target, START, END, history(),
        metadata(currency="EUR"),
    )

    assert result["status"] == "REVIEW_REQUIRED"
    assert "CURRENCY_MISMATCH" in result["flags"]


def test_uk_listing_requires_unit_review():
    target = prepare_targets(
        [position_row("DEMO.UK", "DEMO.L")],
        [registry_row("DEMO.UK", "DEMO.L")],
    )[0]

    result = assess_history(
        target, START, END, history(), metadata()
    )

    assert "UK_QUOTE_UNIT_REVIEW" in result["flags"]
    assert result["status"] == "REVIEW_REQUIRED"


def test_pence_is_not_treated_as_pounds():
    target = prepare_targets(
        [position_row(currency="GBP")],
        [registry_row(currency="GBP")],
    )[0]

    result = assess_history(
        target, START, END, history(),
        metadata(currency="GBp"),
    )

    assert result["yahoo_currency"] == "GBp"
    assert "CURRENCY_MISMATCH" in result["flags"]


def test_dividends_splits_and_adjustment_are_visible():
    target = prepare_targets(
        [position_row()], [registry_row()]
    )[0]

    result = assess_history(
        target,
        START,
        END,
        history(
            closes=(100, 102),
            dividends=(0, 1),
            splits=(0, 2),
            adjusted=(99, 102),
        ),
        metadata(),
    )

    assert result["dividend_events"] == "1"
    assert result["split_events"] == "1"
    assert result["adjusted_close_diff_days"] == "1"
    assert "DIVIDEND_EVENTS" in result["flags"]
    assert "SPLIT_EVENTS" in result["flags"]
    assert "ADJUSTED_CLOSE_DIFFERS" in result["flags"]


def test_duplicate_sessions_are_invalid():
    target = prepare_targets(
        [position_row()], [registry_row()]
    )[0]

    duplicated = history(
        days=("2026-10-20", "2026-10-20"),
        closes=(100, 101),
    )

    with pytest.raises(
        ValueError, match="Duplicate trading session"
    ):
        assess_history(
            target, START, END, duplicated, metadata()
        )


def test_long_gap_and_large_move_are_flagged():
    target = prepare_targets(
        [position_row()], [registry_row()]
    )[0]

    wider_start = date(2026, 9, 1)

    result = assess_history(
        target,
        wider_start,
        END,
        history(
            days=("2026-10-01", "2026-10-20"),
            closes=(100, 180),
        ),
        metadata(),
    )

    assert "LONG_SESSION_GAP" in result["flags"]
    assert "LARGE_DAILY_MOVE" in result["flags"]


def test_network_error_does_not_hide_instrument():
    targets = prepare_targets(
        [position_row()], [registry_row()]
    )

    def failing_fetcher(symbol, start, end):
        raise RuntimeError("Simulated Yahoo failure")

    report = audit_portfolio(
        targets, START, END, failing_fetcher
    )

    assert len(report) == 1
    assert report[0]["status"] == "FETCH_ERROR"
    assert report[0]["error_type"] == "RuntimeError"
