"""Read-only corporate-action and calendar audit of DRAM.DE / RKLB.US.

Uses the previously verified local comparison CSVs. Weekday gaps are
candidates for market holidays, not automatically missing quotations.
No mappings, price cache or portfolio valuations are modified.
"""

import argparse
import csv
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.market_data.portfolio_mapping import read_csv
from scripts.audit_eodhd_candidates import first_purchase_dates, http_json
from scripts.audit_spx_session_actions import parse_eodhd_actions, parse_yahoo_actions
from scripts.audit_verified_prices import fetch_yahoo_history
from scripts.check_purchase_price_coverage import DEFAULT_OUTPUT as PURCHASE_REPORT
from scripts.compare_remaining_price_sources import (
    CANDIDATES, DAILY_OUTPUT, SUMMARY_OUTPUT, yahoo_closes,
)

OUTPUT = Path("data/private/market_audit/remaining_session_actions_audit.csv")
FIELDS = [
    "xtb_ticker", "official_isin", "identity_note", "market", "first_purchase",
    "reference_weekdays", "snapshot_sessions", "weekdays_without_quotes",
    "weekend_quotes", "yahoo_status", "yahoo_session_drift",
    "eodhd_div_status", "eodhd_split_status", "yahoo_div_dates",
    "yahoo_split_dates", "eodhd_div_dates", "eodhd_split_dates",
    "flags", "status",
]


def positive_price(raw: str) -> Decimal:
    try:
        result = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid comparison price") from None
    if not result.is_finite() or result <= 0:
        raise ValueError("Invalid comparison price")
    return result


def load_snapshots(
    daily_path: Path, summary_path: Path, start: date, end: date,
    purchases: dict[str, date],
) -> dict[str, dict]:
    """Fail closed on stale or contradictory source comparison CSVs."""
    if type(start) is not date or type(end) is not date or start >= end:
        raise ValueError("Invalid date range")
    rows = read_csv(summary_path)
    if len(rows) != len(CANDIDATES) or {r.get("xtb_ticker") for r in rows} != set(CANDIDATES):
        raise ValueError("Missing or duplicate comparison summary")
    snapshots = {}
    for r in rows:
        ticker = r["xtb_ticker"]
        yahoo_symbol, eodhd_symbol, note = CANDIDATES[ticker]
        if ticker not in purchases or not start <= purchases[ticker] < end:
            raise ValueError("Missing/out-of-range first purchase")
        if not r.get("official_isin") or r.get("identity_note") != note:
            raise ValueError("Unverified instrument identity note")
        if (r.get("status") != "PRELIMINARY_AGREEMENT"
            or r.get("first_purchase") != purchases[ticker].isoformat()
            or r.get("yahoo_symbol") != yahoo_symbol
            or r.get("eodhd_symbol") != eodhd_symbol
            or r.get("expected_currency") != ("EUR" if ticker.endswith(".DE") else "USD")
            or not r.get("yahoo_exchange")
            or r.get("differences_over_1pct") != "0"
            or r.get("eodhd_only_after_yahoo_start") != "0"
            or r.get("yahoo_only_after_eodhd_start") != "0"
            or r.get("eodhd_on_purchase_day") != "YES"
            or r.get("error_stage")):
            raise ValueError(f"Comparison summary not clean: {ticker}")
        snapshots[ticker] = {
            "dates": set(), "isin": r["official_isin"], "note": note,
            "yahoo_symbol": yahoo_symbol, "eodhd_symbol": eodhd_symbol,
            "currency": r["expected_currency"], "exchange": r["yahoo_exchange"],
            "first_buy": purchases[ticker], "summary": r,
        }

    with daily_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "xtb_ticker", "session", "currency", "eodhd_symbol", "yahoo_symbol",
            "eodhd_close", "yahoo_close", "status",
        }
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise ValueError("Invalid comparison daily columns")
        for r in reader:
            ticker = r["xtb_ticker"]
            if ticker not in snapshots:
                raise ValueError("Unknown comparison ticker")
            snap = snapshots[ticker]
            try:
                day = date.fromisoformat(r["session"])
            except (TypeError, ValueError):
                raise ValueError("Malformed comparison session") from None
            if (not start <= day < end or day in snap["dates"]
                or r["eodhd_symbol"] != snap["eodhd_symbol"]
                or r["yahoo_symbol"] != snap["yahoo_symbol"]
                or r["currency"] != snap["currency"]
                or r["status"] != "WITHIN_1PCT"):
                raise ValueError("Inconsistent comparison daily row")
            eod = positive_price(r["eodhd_close"])
            yah = positive_price(r["yahoo_close"])
            if abs((eod - yah) / yah * Decimal("100")) > Decimal("1"):
                raise ValueError("Price discrepancy in comparison report")
            snap["dates"].add(day)

    for ticker, snap in snapshots.items():
        r = snap["summary"]
        n = len(snap["dates"])
        if (n < 5 or snap["first_buy"] not in snap["dates"]
            or str(n) != r.get("common_sessions")
            or str(n) != r.get("eodhd_sessions")
            or str(n) != r.get("yahoo_sessions")):
            raise ValueError(f"Comparison daily/summary mismatch: {ticker}")
    return snapshots


def request_events(fetcher, kind: str, symbol: str, start: date, end: date):
    """An unavailable EODHD endpoint never means zero corporate actions."""
    try:
        payload = fetcher(
            f"{kind}/{symbol}",
            {"from": start.isoformat(), "to": (end - timedelta(days=1)).isoformat()},
        )
    except RuntimeError as exc:
        name = str(exc)
        return None, name if name.startswith("HTTP_") and name[5:].isdigit() else "FETCH_ERROR"
    except Exception:
        return None, "FETCH_ERROR"
    try:
        return parse_eodhd_actions(payload, start, end, kind), "OK"
    except (ValueError, KeyError, TypeError):
        return None, "INVALID_RESPONSE"


def dates_text(values: set[date] | None) -> str:
    return "|".join(d.isoformat() for d in sorted(values or set()))


def audit(
    snapshots: dict[str, dict], start: date, end: date,
    *, eodhd_fetcher, yahoo_fetcher,
) -> list[dict[str, str]]:
    """At most four EODHD action calls across the two instruments."""
    weekdays = {
        start + timedelta(days=i)
        for i in range((end - start).days)
        if (start + timedelta(days=i)).weekday() < 5
    }
    result = []
    for ticker in CANDIDATES:
        snap = snapshots[ticker]
        days = snap["dates"]
        absent = weekdays - days
        weekends = {d for d in days if d.weekday() >= 5}
        flags = set()
        if weekends:
            flags.add("WEEKEND_QUOTES")
        if snap["first_buy"] not in days:
            flags.add("MISSING_PURCHASE_DAY_PRICE")

        divs, div_status = request_events(
            eodhd_fetcher, "div", snap["eodhd_symbol"], start, end,
        )
        splits, split_status = request_events(
            eodhd_fetcher, "splits", snap["eodhd_symbol"], start, end,
        )

        yahoo_status = "OK"
        yahoo_drift = "UNKNOWN"
        yahoo_divs = None
        yahoo_splits = None
        try:
            history, metadata = yahoo_fetcher(snap["yahoo_symbol"], start, end)
            actual, exchange = yahoo_closes(
                history, metadata, start, end, snap["currency"],
            )
            yahoo_drift = "YES" if set(actual) != days else "NO"
            if exchange != snap["exchange"]:
                flags.add("YAHOO_EXCHANGE_CHANGED")
            yahoo_divs, yahoo_splits = parse_yahoo_actions(history, start, end)
        except Exception:
            yahoo_status = "UNAVAILABLE_OR_INVALID"
        if yahoo_drift == "YES":
            flags.add("YAHOO_SESSION_DRIFT")
        if yahoo_status != "OK" or div_status != "OK" or split_status != "OK":
            flags.add("ACTIONS_UNVERIFIED")
        if yahoo_divs is not None and divs is not None and yahoo_divs != divs:
            flags.add("DIVIDEND_DATES_DIFFER")
        if yahoo_splits is not None and splits is not None and yahoo_splits != splits:
            flags.add("SPLIT_DATES_DIFFER")
        if any((divs, splits, yahoo_divs, yahoo_splits)):
            flags.add("ACTION_VALUES_REVIEW_REQUIRED")
        result.append({
            "xtb_ticker": ticker, "official_isin": snap["isin"],
            "identity_note": snap["note"],
            "market": "XETRA" if ticker.endswith(".DE") else "US",
            "first_purchase": snap["first_buy"].isoformat(),
            "reference_weekdays": str(len(weekdays)),
            "snapshot_sessions": str(len(days)),
            "weekdays_without_quotes": dates_text(absent),
            "weekend_quotes": dates_text(weekends),
            "yahoo_status": yahoo_status, "yahoo_session_drift": yahoo_drift,
            "eodhd_div_status": div_status, "eodhd_split_status": split_status,
            "yahoo_div_dates": dates_text(yahoo_divs),
            "yahoo_split_dates": dates_text(yahoo_splits),
            "eodhd_div_dates": dates_text(divs),
            "eodhd_split_dates": dates_text(splits),
            "flags": "|".join(sorted(flags)),
            "status": "REVIEW_REQUIRED" if flags else "PRELIMINARY_OK",
        })
    return result


def save_report(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only DRAM/RKLB action and calendar audit")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--daily", default=DAILY_OUTPUT, type=Path)
    parser.add_argument("--summary", default=SUMMARY_OUTPUT, type=Path)
    parser.add_argument("--purchases", default=PURCHASE_REPORT, type=Path)
    parser.add_argument("--output", default=OUTPUT, type=Path)
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--end must be later than --start")
    if any(not p.is_file() for p in (args.daily, args.summary, args.purchases)):
        parser.error("Required private comparison or purchase CSV missing")
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token or token.lower() == "demo":
        parser.error("Set EODHD_API_TOKEN in this PowerShell session")
    try:
        snapshots = load_snapshots(
            args.daily, args.summary, args.start, args.end,
            first_purchase_dates(args.purchases),
        )
        rows = audit(
            snapshots, args.start, args.end,
            eodhd_fetcher=lambda path, params: http_json(path, params, token),
            yahoo_fetcher=fetch_yahoo_history,
        )
    except (ValueError, KeyError, TypeError, OSError):
        parser.error("Invalid source comparison or purchase data; no report written")
    save_report(rows, args.output)
    print("\nDRAM / RKLB — SESSION AND CORPORATE ACTION AUDIT")
    for row in rows:
        print(f"  {row['xtb_ticker']}: {row['status']} | quotes={row['snapshot_sessions']} | "
              f"weekdays={row['reference_weekdays']}")
        print(f"    weekday gaps (holiday candidates): {row['weekdays_without_quotes'] or '-'}")
        print(f"    weekend quotes: {row['weekend_quotes'] or '-'} | "
              f"Yahoo drift: {row['yahoo_session_drift']}")
        print(f"    EODHD dividends={row['eodhd_div_status']}, splits={row['eodhd_split_status']} | "
              f"Yahoo={row['yahoo_status']}")
        print(f"    action dates Y(div/split)={row['yahoo_div_dates'] or '-'}/{row['yahoo_split_dates'] or '-'} "
              f"E(div/split)={row['eodhd_div_dates'] or '-'}/{row['eodhd_split_dates'] or '-'}")
        print(f"    identity_note={row['identity_note']} | flags={row['flags'] or '-'}")
    print(f"\nPrivate report: {args.output}")
    print("READ ONLY. Weekday gaps are NOT proof of missing exchange sessions.")
    print("PRELIMINARY_OK does not certify exchange calendars, identity or P&L.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
