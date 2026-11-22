
"""Read-only weekday and corporate-action audit for SPX.DE.

Uses the saved source-comparison CSV. EODHD action endpoints are optional
checks; a denied or invalid response is NEVER interpreted as zero events.
"""

import argparse
import csv
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.audit_eodhd_candidates import first_purchase_dates, http_json
from scripts.audit_verified_prices import fetch_yahoo_history
from scripts.compare_spx_price_sources import (
    EODHD_TICKER,
    XTB_TICKER,
    YAHOO_TICKER,
    parse_yahoo_closes,
)
from scripts.check_purchase_price_coverage import (
    DEFAULT_OUTPUT as PURCHASE_REPORT,
)


SOURCE_REPORT = Path(
    "data/private/market_audit/spx_price_sources_comparison.csv"
)
OUTPUT = Path(
    "data/private/market_audit/spx_session_actions_audit.csv"
)

FIELDS = [
    "xtb_ticker",
    "start",
    "end_exclusive",
    "first_purchase",
    "reference_weekdays",
    "eodhd_sessions",
    "yahoo_sessions_csv",
    "missing_weekdays",
    "missing_weekdays_after_buy",
    "weekend_quotes",
    "yahoo_status",
    "yahoo_session_drift",
    "eodhd_div_status",
    "eodhd_split_status",
    "yahoo_div_dates",
    "yahoo_split_dates",
    "eodhd_div_dates",
    "eodhd_split_dates",
    "flags",
    "status",
]


def valid_price(value: str) -> bool:
    if not value.strip():
        return False

    try:
        price = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError(
            "Invalid price in comparison report"
        ) from None

    if not price.is_finite() or price <= 0:
        raise ValueError(
            "Invalid price in comparison report"
        )

    return True


def load_comparison(
    path: Path,
    start: date,
    end: date,
) -> tuple[set[date], set[date]]:
    """Read and verify actual dates from the previous audit."""
    if (
        type(start) is not date
        or type(end) is not date
        or start >= end
    ):
        raise ValueError("Invalid date range")

    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        reader = csv.DictReader(stream)

        required = {
            "date",
            "xtb_ticker",
            "eodhd_symbol",
            "yahoo_symbol",
            "eodhd_close_eur",
            "yahoo_close_eur",
            "status",
        }

        if (
            not reader.fieldnames
            or not required <= set(reader.fieldnames)
        ):
            raise ValueError("Incomplete comparison report")

        eodhd = set()
        yahoo = set()
        seen = set()

        for row in reader:
            if (
                row["xtb_ticker"] != XTB_TICKER
                or row["eodhd_symbol"] != EODHD_TICKER
                or row["yahoo_symbol"] != YAHOO_TICKER
            ):
                raise ValueError(
                    "Unexpected comparison instrument"
                )

            try:
                day = date.fromisoformat(row["date"])
            except (TypeError, ValueError):
                raise ValueError(
                    "Invalid session date"
                ) from None

            if day in seen or not start <= day < end:
                raise ValueError(
                    "Duplicate or out-of-range session"
                )

            seen.add(day)

            has_eod = valid_price(
                row["eodhd_close_eur"] or ""
            )
            has_yahoo = valid_price(
                row["yahoo_close_eur"] or ""
            )

            if not (has_eod or has_yahoo):
                raise ValueError(
                    "Report row contains no prices"
                )

            if has_eod and has_yahoo:
                expected_status = {
                    "WITHIN_1PCT",
                    "REVIEW_DIFFERENCE",
                }
            elif has_eod:
                expected_status = {"EODHD_ONLY"}
            else:
                expected_status = {"YAHOO_ONLY"}

            if row["status"] not in expected_status:
                raise ValueError(
                    "Inconsistent comparison status"
                )

            if has_eod:
                eodhd.add(day)

            if has_yahoo:
                yahoo.add(day)

    if not eodhd:
        raise ValueError(
            "No EODHD sessions in comparison report"
        )

    return eodhd, yahoo


def parse_eodhd_actions(
    payload: object,
    start: date,
    end: date,
    kind: str,
) -> set[date]:
    """Read event dates, not adjusted amounts or split factors."""
    if (
        kind not in {"div", "splits"}
        or not isinstance(payload, list)
    ):
        raise ValueError(
            "Invalid corporate actions response"
        )

    dates = set()

    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(
                "Invalid corporate action row"
            )

        try:
            day = date.fromisoformat(item["date"])

            if kind == "div":
                raw = item.get("unadjustedValue")

                if raw is None:
                    raw = item.get("value")

                amount = Decimal(str(raw))

                if not amount.is_finite() or amount <= 0:
                    raise ValueError(
                        "Invalid dividend value"
                    )
            else:
                ratio = item["split"]

                if (
                    not isinstance(ratio, str)
                    or not ratio.strip()
                ):
                    raise ValueError(
                        "Missing split ratio"
                    )

        except (
            KeyError,
            TypeError,
            ValueError,
            InvalidOperation,
        ):
            raise ValueError(
                "Invalid corporate action row"
            ) from None

        if not start <= day < end or day in dates:
            raise ValueError(
                "Duplicate or out-of-range corporate action date"
            )

        dates.add(day)

    return dates


def parse_yahoo_actions(
    history,
    start: date,
    end: date,
) -> tuple[set[date], set[date]]:
    """Collect dates with nonzero Yahoo dividends and splits."""
    if (
        history.empty
        or not {"Dividends", "Stock Splits"}
        <= set(history.columns)
    ):
        raise ValueError(
            "Yahoo actions columns unavailable"
        )

    events = []

    for column in ("Dividends", "Stock Splits"):
        dates = set()

        for timestamp, raw in history[column].items():
            try:
                day = timestamp.date()
                amount = Decimal(str(raw))
            except (
                AttributeError,
                TypeError,
                ValueError,
                InvalidOperation,
            ):
                raise ValueError(
                    "Invalid Yahoo corporate action"
                ) from None

            if (
                not start <= day < end
                or not amount.is_finite()
                or amount < 0
            ):
                raise ValueError(
                    "Invalid Yahoo corporate action"
                )

            if amount != 0:
                if day in dates:
                    raise ValueError(
                        "Duplicate Yahoo corporate action"
                    )

                dates.add(day)

        events.append(dates)

    return events[0], events[1]


def fetch_eodhd_action_dates(
    fetcher,
    kind: str,
    start: date,
    end: date,
):
    """Unavailable API access is not evidence of no events."""
    try:
        payload = fetcher(
            f"{kind}/{EODHD_TICKER}",
            {
                "from": start.isoformat(),
                "to": (
                    end - timedelta(days=1)
                ).isoformat(),
            },
        )

    except RuntimeError as exc:
        name = str(exc)

        return (
            None,
            name if name.startswith("HTTP_")
            else "FETCH_ERROR",
        )

    except (ValueError, TypeError, KeyError):
        return None, "FETCH_ERROR"

    try:
        dates = parse_eodhd_actions(
            payload,
            start,
            end,
            kind,
        )
        return dates, "OK"

    except (ValueError, TypeError, KeyError):
        return None, "INVALID_RESPONSE"


def join_dates(days: set[date] | None) -> str:
    return "|".join(
        day.isoformat()
        for day in sorted(days or [])
    )


def audit_sessions_actions(
    eodhd: set[date],
    yahoo_csv: set[date],
    first_buy: date,
    start: date,
    end: date,
    *,
    eodhd_fetcher,
    yahoo_fetcher,
) -> dict[str, str]:
    if (
        type(first_buy) is not date
        or not start <= first_buy < end
        or not eodhd
    ):
        raise ValueError(
            "Invalid dates or empty price history"
        )

    all_days = (
        start + timedelta(days=offset)
        for offset in range((end - start).days)
    )

    weekdays = {
        day for day in all_days
        if day.weekday() < 5
    }

    missing = weekdays - eodhd
    weekend = {
        day for day in eodhd
        if day.weekday() >= 5
    }

    flags = set()

    if missing:
        flags.add("MISSING_WEEKDAY_QUOTES")

    if weekend:
        flags.add("WEEKEND_QUOTES")

    if first_buy not in eodhd:
        flags.add("NO_PRICE_ON_PURCHASE_DAY")

    divs, div_status = fetch_eodhd_action_dates(
        eodhd_fetcher,
        "div",
        start,
        end,
    )

    splits, split_status = fetch_eodhd_action_dates(
        eodhd_fetcher,
        "splits",
        start,
        end,
    )

    yahoo_divs = None
    yahoo_splits = None
    yahoo_dates = None

    try:
        history, metadata = yahoo_fetcher(
            YAHOO_TICKER,
            start,
            end,
        )

        yahoo_dates = set(
            parse_yahoo_closes(
                history,
                metadata,
                start,
                end,
            )
        )

        yahoo_divs, yahoo_splits = (
            parse_yahoo_actions(
                history,
                start,
                end,
            )
        )

        yahoo_status = "OK"

    except Exception:
        # Provider failure must not mean zero actions.
        yahoo_status = "UNAVAILABLE_OR_INVALID"

    if yahoo_dates is None:
        drift = "UNKNOWN"
    else:
        drift = (
            "YES" if yahoo_dates != yahoo_csv
            else "NO"
        )

    if drift == "YES":
        flags.add("YAHOO_SESSION_DRIFT")

    if (
        yahoo_status != "OK"
        or div_status != "OK"
        or split_status != "OK"
    ):
        flags.add("ACTIONS_UNVERIFIED")

    if (
        yahoo_divs is not None
        and divs is not None
        and yahoo_divs != divs
    ):
        flags.add("DIVIDEND_DATES_DIFFER")

    if (
        yahoo_splits is not None
        and splits is not None
        and yahoo_splits != splits
    ):
        flags.add("SPLIT_DATES_DIFFER")

    if (
        divs
        or splits
        or yahoo_divs
        or yahoo_splits
    ):
        flags.add("ACTION_VALUES_REVIEW_REQUIRED")

    return {
        "xtb_ticker": XTB_TICKER,
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "first_purchase": first_buy.isoformat(),
        "reference_weekdays": str(len(weekdays)),
        "eodhd_sessions": str(len(eodhd)),
        "yahoo_sessions_csv": str(len(yahoo_csv)),
        "missing_weekdays": join_dates(missing),
        "missing_weekdays_after_buy": join_dates(
            {
                day for day in missing
                if day >= first_buy
            }
        ),
        "weekend_quotes": join_dates(weekend),
        "yahoo_status": yahoo_status,
        "yahoo_session_drift": drift,
        "eodhd_div_status": div_status,
        "eodhd_split_status": split_status,
        "yahoo_div_dates": join_dates(yahoo_divs),
        "yahoo_split_dates": join_dates(yahoo_splits),
        "eodhd_div_dates": join_dates(divs),
        "eodhd_split_dates": join_dates(splits),
        "flags": "|".join(sorted(flags)),
        "status": (
            "REVIEW_REQUIRED"
            if flags
            else "PRELIMINARY_OK"
        ),
    }


def write_report(
    row: dict[str, str],
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(".tmp")

    with temporary.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=FIELDS,
        )

        writer.writeheader()
        writer.writerow(row)

    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only SPX Xetra weekday/actions audit"
        )
    )

    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        required=True,
    )

    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        required=True,
    )

    parser.add_argument(
        "--comparison",
        type=Path,
        default=SOURCE_REPORT,
    )

    parser.add_argument(
        "--purchases",
        type=Path,
        default=PURCHASE_REPORT,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT,
    )

    args = parser.parse_args()

    if args.start >= args.end:
        parser.error(
            "--end must be later than --start"
        )

    for path in (
        args.comparison,
        args.purchases,
    ):
        if not path.is_file():
            parser.error(
                f"Private input missing: {path}"
            )

    token = os.environ.get(
        "EODHD_API_TOKEN",
        "",
    ).strip()

    if not token or token.lower() == "demo":
        parser.error(
            "Set EODHD_API_TOKEN in the current PowerShell session"
        )

    try:
        first_buys = first_purchase_dates(
            args.purchases
        )

        if XTB_TICKER not in first_buys:
            raise ValueError(
                "SPX.DE purchase date missing"
            )

        eod, yahoo = load_comparison(
            args.comparison,
            args.start,
            args.end,
        )

        row = audit_sessions_actions(
            eod,
            yahoo,
            first_buys[XTB_TICKER],
            args.start,
            args.end,
            eodhd_fetcher=lambda path, params: http_json(
                path,
                params,
                token,
            ),
            yahoo_fetcher=fetch_yahoo_history,
        )

    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
    ):
        parser.error(
            "Invalid audit input; no report written"
        )

    write_report(
        row,
        args.output,
    )

    print(
        "\nSPX.DE — XETRA WEEKDAY / CORPORATE ACTION AUDIT"
    )

    for key in FIELDS:
        print(
            f"  {key}: {row[key] or '-'}"
        )

    print(
        f"\nPrivate report: {args.output}"
    )

    print(
        "READ ONLY. Weekdays are not a certified Xetra "
        "session calendar; values/ratios are not approved."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
