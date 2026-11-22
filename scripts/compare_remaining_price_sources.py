
"""Read-only Yahoo/EODHD Close comparison for DRAM.DE and RKLB.US.

EODHD identity must match reviewed ISIN, exchange and currency.
Yahoo metadata is checked for quote currency and a non-empty exchange.
Neither price source is approved for portfolio P&L by this audit.
"""

import argparse
import csv
import os
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from scripts.audit_eodhd_candidates import (
    choose_listing, first_purchase_dates, http_json, make_targets,
)
from scripts.audit_verified_prices import (
    fetch_yahoo_history, positive_price, quote_unit,
)
from scripts.check_purchase_price_coverage import DEFAULT_OUTPUT as PURCHASE_REPORT
from scripts.compare_spx_price_sources import parse_eodhd_closes


# Audited candidates, not approved portfolio mappings.
CANDIDATES = {
    "DRAM.DE": ("DRAM.DE", "DRAM.XETRA", "XTB_CATALOG_ISIN_MISSING"),
    "RKLB.US": ("RKLB", "RKLB.US", "XTB_LEGACY_CUSIP_REVIEW"),
}
THRESHOLD_PCT = Decimal("1")  # Diagnostic screen, not an approval rule.
DAILY_OUTPUT = Path("data/private/market_audit/remaining_price_sources_daily.csv")
SUMMARY_OUTPUT = Path("data/private/market_audit/remaining_price_sources_summary.csv")

DAILY_FIELDS = [
    "xtb_ticker", "session", "currency", "eodhd_symbol", "yahoo_symbol",
    "eodhd_close", "yahoo_close", "difference_pct", "status",
]
SUMMARY_FIELDS = [
    "xtb_ticker", "official_isin", "identity_note", "expected_currency",
    "eodhd_symbol", "yahoo_symbol", "yahoo_exchange", "first_purchase",
    "eodhd_sessions", "yahoo_sessions", "first_eodhd", "first_yahoo",
    "common_sessions", "differences_over_1pct", "max_abs_difference_pct",
    "largest_difference_day", "eodhd_on_purchase_day",
    "eodhd_only_after_yahoo_start", "yahoo_only_after_eodhd_start",
    "status", "error_stage",
]


def yahoo_closes(
    history: pd.DataFrame, metadata: Mapping, start: date, end: date,
    currency: str,
) -> tuple[dict[date, Decimal], str]:
    """Return raw Yahoo Close, never auto-adjusted values."""
    if not isinstance(metadata, Mapping):
        raise ValueError("Invalid Yahoo metadata")
    if quote_unit(metadata.get("currency")) != currency:
        raise ValueError("Yahoo quote currency mismatch")
    exchange = str(metadata.get("exchangeName") or "").strip()
    if not exchange:
        raise ValueError("Yahoo exchange missing")
    if not isinstance(history, pd.DataFrame) or "Close" not in history.columns:
        raise ValueError("Missing Yahoo Close history")

    prices = {}
    for timestamp, value in history["Close"].items():
        if not hasattr(timestamp, "date"):
            raise ValueError("Invalid Yahoo session index")
        day = timestamp.date()
        if not start <= day < end or day in prices:
            raise ValueError("Duplicate/out-of-range Yahoo session")
        prices[day] = positive_price(value, day)
    return prices, exchange


def compare(
    xtb_ticker: str, yahoo_symbol: str, eodhd_symbol: str, currency: str,
    buy: date, eodhd: dict[date, Decimal], yahoo: dict[date, Decimal],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Compare only same-session prices; preserve missing sessions."""
    rows = []
    overlaps = []
    first_eodhd = min(eodhd) if eodhd else None
    first_yahoo = min(yahoo) if yahoo else None
    eodhd_only_late = 0
    yahoo_only_late = 0

    for day in sorted(eodhd.keys() | yahoo.keys()):
        ev = eodhd.get(day)
        yv = yahoo.get(day)
        row = {
            "xtb_ticker": xtb_ticker, "session": day.isoformat(),
            "currency": currency, "eodhd_symbol": eodhd_symbol,
            "yahoo_symbol": yahoo_symbol,
            "eodhd_close": str(ev) if ev is not None else "",
            "yahoo_close": str(yv) if yv is not None else "",
            "difference_pct": "", "status": "",
        }
        if ev is not None and yv is not None:
            pct = (ev - yv) / yv * Decimal("100")
            overlaps.append((abs(pct), day))
            row["difference_pct"] = f"{pct:.6f}"
            row["status"] = (
                "WITHIN_1PCT" if abs(pct) <= THRESHOLD_PCT
                else "PRICE_DIFFERENCE_REVIEW"
            )
        elif ev is not None:
            row["status"] = "EODHD_ONLY"
            if first_yahoo is not None and day >= first_yahoo:
                eodhd_only_late += 1
        else:
            row["status"] = "YAHOO_ONLY"
            if first_eodhd is not None and day >= first_eodhd:
                yahoo_only_late += 1
        rows.append(row)

    largest = max(overlaps, default=None)
    over_threshold = sum(value > THRESHOLD_PCT for value, _ in overlaps)
    if not eodhd:
        status = "NO_EODHD_PRICES"
    elif not yahoo:
        status = "NO_YAHOO_PRICES"
    elif not overlaps:
        status = "NO_COMMON_SESSIONS"
    elif buy not in eodhd:
        status = "NO_EODHD_ON_PURCHASE_DAY"
    elif over_threshold:
        status = "PRICE_DIFFERENCES_REVIEW"
    elif eodhd_only_late or yahoo_only_late:
        status = "SESSION_MISMATCH_REVIEW"
    elif len(overlaps) < 5:
        status = "TOO_FEW_COMMON_SESSIONS"
    else:
        status = "PRELIMINARY_AGREEMENT"

    return rows, {
        "eodhd_sessions": str(len(eodhd)),
        "yahoo_sessions": str(len(yahoo)),
        "first_eodhd": first_eodhd.isoformat() if first_eodhd else "",
        "first_yahoo": first_yahoo.isoformat() if first_yahoo else "",
        "common_sessions": str(len(overlaps)),
        "differences_over_1pct": str(over_threshold),
        "max_abs_difference_pct": f"{largest[0]:.6f}" if largest else "",
        "largest_difference_day": largest[1].isoformat() if largest else "",
        "eodhd_on_purchase_day": "YES" if buy in eodhd else "NO",
        "eodhd_only_after_yahoo_start": str(eodhd_only_late),
        "yahoo_only_after_eodhd_start": str(yahoo_only_late),
        "status": status,
    }


def audit_remaining(
    start: date, end: date, purchases: dict[str, date], *,
    eodhd_fetcher, yahoo_fetcher,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """At most 4 EODHD calls; provider errors are scoped per ticker."""
    if type(start) is not date or type(end) is not date or start >= end:
        raise ValueError("Invalid date range")
    targets = {t.xtb_ticker: t for t in make_targets()}
    for ticker in CANDIDATES:
        if ticker not in purchases or not start <= purchases[ticker] < end:
            raise ValueError(f"Missing/out-of-range first purchase: {ticker}")
        if ticker not in targets:
            raise ValueError(f"Missing reviewed EODHD candidate: {ticker}")

    daily = []
    summaries = []
    for ticker, (yahoo_symbol, expected_eodhd, note) in CANDIDATES.items():
        target = targets[ticker]
        buy = purchases[ticker]
        summary = {field: "" for field in SUMMARY_FIELDS}
        summary.update({
            "xtb_ticker": ticker, "official_isin": target.isin,
            "identity_note": note, "expected_currency": target.currency,
            "eodhd_symbol": expected_eodhd, "yahoo_symbol": yahoo_symbol,
            "first_purchase": buy.isoformat(),
        })

        try:
            listings = eodhd_fetcher(f"search/{target.isin}", {"limit": "100"})
            selected, _, match_status = choose_listing(listings, target)
        except (RuntimeError, ValueError, TypeError, KeyError):
            summary.update(status="EODHD_SEARCH_ERROR", error_stage="SEARCH")
            summaries.append(summary)
            continue
        if match_status != "FOUND_EXACT_LISTING" or selected != expected_eodhd:
            summary.update(status="EODHD_IDENTITY_REVIEW", error_stage=match_status)
            summaries.append(summary)
            continue

        try:
            payload = eodhd_fetcher(
                f"eod/{selected}",
                {"from": start.isoformat(),
                 "to": (end - timedelta(days=1)).isoformat(), "period": "d"},
            )
            eodhd = parse_eodhd_closes(payload, start, end)
        except (RuntimeError, ValueError, TypeError, KeyError):
            summary.update(status="EODHD_PRICE_ERROR", error_stage="EOD_PRICES")
            summaries.append(summary)
            continue

        try:
            history, metadata = yahoo_fetcher(yahoo_symbol, start, end)
        except Exception:
            summary.update(status="YAHOO_FETCH_ERROR", error_stage="YAHOO_FETCH")
            summaries.append(summary)
            continue
        try:
            yahoo, exchange = yahoo_closes(history, metadata, start, end, target.currency)
        except (ValueError, TypeError, KeyError):
            summary.update(status="YAHOO_QUALITY_ERROR", error_stage="YAHOO_VALIDATION")
            summaries.append(summary)
            continue

        daily_rows, metrics = compare(
            ticker, yahoo_symbol, selected, target.currency, buy, eodhd, yahoo,
        )
        summary.update(metrics)
        summary["yahoo_exchange"] = exchange
        daily.extend(daily_rows)
        summaries.append(summary)
    return daily, summaries


def write_csv(rows: list[dict[str, str]], fields: list[str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare DRAM and RKLB Yahoo/EODHD prices")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--purchases", type=Path, default=PURCHASE_REPORT)
    parser.add_argument("--daily-output", type=Path, default=DAILY_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=SUMMARY_OUTPUT)
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--end must be later than --start")
    if not args.purchases.is_file():
        parser.error("Missing private purchase report")
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token or token.lower() == "demo":
        parser.error("Set EODHD_API_TOKEN in this PowerShell session")
    try:
        purchases = first_purchase_dates(args.purchases)
        daily, summaries = audit_remaining(
            args.start, args.end, purchases,
            eodhd_fetcher=lambda path, params: http_json(path, params, token),
            yahoo_fetcher=fetch_yahoo_history,
        )
    except (ValueError, KeyError, TypeError):
        parser.error("Invalid target dates or purchase report; no report written")
    write_csv(daily, DAILY_FIELDS, args.daily_output)
    write_csv(summaries, SUMMARY_FIELDS, args.summary_output)

    print("\nDRAM / RKLB — EODHD vs YAHOO RAW CLOSE AUDIT")
    for row in summaries:
        print(f"  {row['xtb_ticker']}: {row['status']}")
        print(f"    EODHD={row['eodhd_symbol']} ({row['eodhd_sessions'] or '-'} sessions) | "
              f"Yahoo={row['yahoo_symbol']} ({row['yahoo_sessions'] or '-'} sessions; "
              f"exchange={row['yahoo_exchange'] or '-'})")
        print(f"    common={row['common_sessions'] or '-'} | >1%={row['differences_over_1pct'] or '-'} | "
              f"max difference={row['max_abs_difference_pct'] or '-'}% "
              f"on {row['largest_difference_day'] or '-'}")
        print(f"    price on first purchase={row['eodhd_on_purchase_day'] or '-'} | "
              f"EODHD-only later={row['eodhd_only_after_yahoo_start'] or '-'} | "
              f"Yahoo-only later={row['yahoo_only_after_eodhd_start'] or '-'}")
        print(f"    identity_note={row['identity_note']}" +
              (f" | error_stage={row['error_stage']}" if row["error_stage"] else ""))
    print(f"\nPrivate daily report: {args.daily_output}")
    print(f"Private summary: {args.summary_output}")
    print("READ ONLY — no mapping, cached price, or P&L was changed.")
    print("One-percent screening and matching closes do NOT approve an instrument.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
