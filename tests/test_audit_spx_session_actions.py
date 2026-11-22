
import csv
from datetime import date

import pandas as pd
import pytest

from scripts.audit_spx_session_actions import (
    audit_sessions_actions,
    fetch_eodhd_action_dates,
    load_comparison,
    parse_eodhd_actions,
    parse_yahoo_actions,
    write_report,
)


START = date(2026, 7, 27)
END = date(2026, 7, 30)
BUY = START
MON = date(2026, 7, 27)
TUE = date(2026, 7, 28)
WED = date(2026, 7, 29)


def yahoo_frame(
    days=("2026-07-27", "2026-07-28"),
    dividends=None,
    splits=None,
):
    frame = pd.DataFrame(
        {
            "Close": [100] * len(days),
            "Dividends": (
                dividends
                if dividends is not None
                else [0] * len(days)
            ),
            "Stock Splits": (
                splits
                if splits is not None
                else [0] * len(days)
            ),
        },
        index=pd.to_datetime(days),
    )

    return frame, {
        "currency": "EUR",
        "exchangeName": "GER",
    }


def audit_with(
    *,
    eod=None,
    old_yahoo=None,
    remote_div=None,
    remote_split=None,
    remote_yahoo=None,
    failed_div=False,
):
    eod = eod if eod is not None else {
        MON, TUE, WED
    }

    old_yahoo = (
        old_yahoo if old_yahoo is not None
        else {MON, TUE}
    )

    remote_div = (
        remote_div if remote_div is not None
        else []
    )

    remote_split = (
        remote_split if remote_split is not None
        else []
    )

    remote_yahoo = (
        remote_yahoo if remote_yahoo is not None
        else yahoo_frame()
    )

    calls = []

    def fetch(path, params):
        calls.append(path)

        if path.startswith("div/"):
            if failed_div:
                raise RuntimeError("HTTP_403")
            return remote_div

        return remote_split

    result = audit_sessions_actions(
        eod,
        old_yahoo,
        BUY,
        START,
        END,
        eodhd_fetcher=fetch,
        yahoo_fetcher=lambda *args: remote_yahoo,
    )

    return result, calls


def write_source(path, rows):
    keys = [
        "date",
        "xtb_ticker",
        "eodhd_symbol",
        "yahoo_symbol",
        "eodhd_close_eur",
        "yahoo_close_eur",
        "status",
    ]

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=keys,
        )
        writer.writeheader()
        writer.writerows(rows)


def row(
    day,
    eod="100",
    yahoo="100",
    status="WITHIN_1PCT",
):
    return {
        "date": day,
        "xtb_ticker": "SPX.DE",
        "eodhd_symbol": "SPX.XETRA",
        "yahoo_symbol": "SPX.DE",
        "eodhd_close_eur": eod,
        "yahoo_close_eur": yahoo,
        "status": status,
    }


def test_load_comparison_retains_dates_and_prices(tmp_path):
    path = tmp_path / "comparison.csv"

    write_source(
        path,
        [
            row("2026-07-27"),
            row(
                "2026-07-28",
                "101",
                "",
                "EODHD_ONLY",
            ),
        ],
    )

    eodhd, yahoo = load_comparison(
        path,
        START,
        END,
    )

    assert eodhd == {MON, TUE}
    assert yahoo == {MON}


def test_load_comparison_rejects_bad_symbols(tmp_path):
    path = tmp_path / "comparison.csv"

    item = row("2026-07-27")
    item["eodhd_symbol"] = "WRONG.XETRA"

    write_source(path, [item])

    with pytest.raises(ValueError, match="instrument"):
        load_comparison(path, START, END)


def test_load_comparison_rejects_duplicated_sessions(
    tmp_path,
):
    path = tmp_path / "comparison.csv"

    write_source(
        path,
        [
            row("2026-07-27"),
            row("2026-07-27"),
        ],
    )

    with pytest.raises(ValueError, match="Duplicate"):
        load_comparison(path, START, END)


def test_load_comparison_rejects_bad_price(tmp_path):
    path = tmp_path / "comparison.csv"

    write_source(
        path,
        [row("2026-07-27", "NaN")],
    )

    with pytest.raises(ValueError, match="price"):
        load_comparison(path, START, END)


def test_empty_eodhd_actions_are_known_zero():
    assert parse_eodhd_actions(
        [], START, END, "div"
    ) == set()

    assert parse_eodhd_actions(
        [], START, END, "splits"
    ) == set()


def test_dividend_and_split_event_dates():
    dividends = [
        {
            "date": "2026-07-28",
            "value": 0.3,
        }
    ]

    splits = [
        {
            "date": "2026-07-29",
            "split": "2:1",
        }
    ]

    assert parse_eodhd_actions(
        dividends, START, END, "div"
    ) == {TUE}

    assert parse_eodhd_actions(
        splits, START, END, "splits"
    ) == {WED}


def test_invalid_action_payload_is_not_zero():
    with pytest.raises(ValueError):
        parse_eodhd_actions(
            {"error": "unauthorized"},
            START,
            END,
            "div",
        )


def test_yahoo_nonzero_events():
    frame, _ = yahoo_frame(
        dividends=[0, 0.15],
        splits=[2, 0],
    )

    assert parse_yahoo_actions(
        frame, START, END
    ) == ({TUE}, {MON})


def test_api_403_is_not_interpreted_as_no_dividends():
    def forbidden_fetch(*args):
        raise RuntimeError("HTTP_403")

    dates, status = fetch_eodhd_action_dates(
        forbidden_fetch,
        "div",
        START,
        END,
    )

    assert dates is None
    assert status == "HTTP_403"


def test_clean_weekdays_and_actions_preliminary_ok():
    report, calls = audit_with()

    assert report["status"] == "PRELIMINARY_OK"
    assert report["reference_weekdays"] == "3"
    assert report["missing_weekdays"] == ""
    assert calls == [
        "div/SPX.XETRA",
        "splits/SPX.XETRA",
    ]


def test_missing_weekday_and_buy_day_flagged():
    report, _ = audit_with(
        eod={TUE, WED}
    )

    assert report["missing_weekdays"] == "2026-07-27"
    assert "NO_PRICE_ON_PURCHASE_DAY" in report["flags"]
    assert report["status"] == "REVIEW_REQUIRED"


def test_weekend_quote_flagged():
    report, _ = audit_with(
        eod={
            MON,
            TUE,
            WED,
            date(2026, 8, 1),
        }
    )

    assert report["status"] == "REVIEW_REQUIRED"
    assert "WEEKEND_QUOTES" in report["flags"]


def test_denied_dividend_endpoint_explicitly_unverified():
    report, _ = audit_with(
        failed_div=True
    )

    assert report["eodhd_div_status"] == "HTTP_403"
    assert "ACTIONS_UNVERIFIED" in report["flags"]


def test_different_dividend_dates_flagged():
    report, _ = audit_with(
        remote_div=[
            {
                "date": "2026-07-29",
                "value": 0.5,
            }
        ],
        remote_yahoo=yahoo_frame(
            dividends=[0, 0.5],
        ),
    )

    assert "DIVIDEND_DATES_DIFFER" in report["flags"]
    assert "ACTION_VALUES_REVIEW_REQUIRED" in report["flags"]


def test_yahoo_history_session_drift_flagged():
    report, _ = audit_with(
        remote_yahoo=yahoo_frame(
            days=("2026-07-27",),
        )
    )

    assert report["yahoo_session_drift"] == "YES"
    assert "YAHOO_SESSION_DRIFT" in report["flags"]


def test_missing_yahoo_action_columns_unverified():
    frame, metadata = yahoo_frame()

    report, _ = audit_with(
        remote_yahoo=(
            frame.drop(
                columns=["Stock Splits"]
            ),
            metadata,
        )
    )

    assert report["yahoo_status"] == "UNAVAILABLE_OR_INVALID"
    assert report["status"] == "REVIEW_REQUIRED"


def test_private_report(tmp_path):
    report, _ = audit_with()

    path = (
        tmp_path
        / "private"
        / "audit.csv"
    )

    write_report(report, path)

    content = path.read_text(
        encoding="utf-8-sig"
    )

    assert "SPX.DE" in content
    assert "account_id" not in content
    assert "api_token" not in content
