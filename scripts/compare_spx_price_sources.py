
"""Read-only comparison of raw Close prices for SpaceX on Xetra.

EODHD listing is revalidated against ISIN, exchange and EUR; Yahoo prices
are fetched independently. Nothing is written to the shared price cache.
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
    choose_listing,
    first_purchase_dates,
    http_json,
    make_targets,
    validate_history,
)
from scripts.audit_verified_prices import (
    fetch_yahoo_history,
    positive_price,
    quote_unit,
)
from scripts.check_purchase_price_coverage import DEFAULT_OUTPUT as PURCHASE_REPORT


XTB_TICKER = "SPX.DE"
YAHOO_TICKER = "SPX.DE"
EODHD_TICKER = "SPX.XETRA"
EXPECTED_CURRENCY = "EUR"
THRESHOLD_PCT = Decimal("1")  # Screening threshold, NOT pricing approval.
DEFAULT_OUTPUT = Path("data/private/market_audit/spx_price_sources_comparison.csv")
FIELDS = [
    "date", "xtb_ticker", "eodhd_symbol", "yahoo_symbol",
    "eodhd_close_eur", "yahoo_close_eur", "difference_pct", "status",
]


def parse_eodhd_closes(payload: object, start: date, end: date) -> dict[date, Decimal]:
    """Reuse the existing EODHD validator and retain raw Close values."""
    validate_history(payload, start, end)
    return {
        date.fromisoformat(item["date"]): Decimal(str(item["close"]))
        for item in payload
    }


def parse_yahoo_closes(
    history: pd.DataFrame, metadata: Mapping, start: date, end: date,
) -> dict[date, Decimal]:
    """Require raw, non-adjusted Yahoo Close data in EUR."""
    if not isinstance(metadata, Mapping):
        raise ValueError("Invalid Yahoo metadata")
    if quote_unit(metadata.get("currency")) != EXPECTED_CURRENCY:
        raise ValueError("Yahoo quote currency is not EUR")
    if not str(metadata.get("exchangeName") or "").strip():
        raise ValueError("Missing Yahoo exchange")
    if not isinstance(history, pd.DataFrame):
        raise ValueError("Invalid Yahoo history")
    if history.empty:
        return {}
    if "Close" not in history.columns:
        raise ValueError("Missing Yahoo Close column")

    closes = {}
    for timestamp, value in history["Close"].items():
        if not hasattr(timestamp, "date"):
            raise ValueError("Invalid Yahoo session index")
        day = timestamp.date()
        if not start <= day < end or day in closes:
            raise ValueError("Duplicate or out-of-range Yahoo session")
        closes[day] = positive_price(value, day)
    return closes


def compare_closes(
    eodhd: Mapping[date, Decimal],
    yahoo: Mapping[date, Decimal],
    first_buy: date,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Compare same-date raw closes; never manufacture absent prices."""
    if not eodhd:
        raise ValueError("EODHD returned no closes")

    rows = []
    differences = []
    first_yahoo = min(yahoo) if yahoo else None
    early_after_buy = 0
    unmatched_after_yahoo = 0
    unmatched_yahoo = 0

    for day in sorted(set(eodhd) | set(yahoo)):
        eod = eodhd.get(day)
        yah = yahoo.get(day)
        row = {
            "date": day.isoformat(),
            "xtb_ticker": XTB_TICKER,
            "eodhd_symbol": EODHD_TICKER,
            "yahoo_symbol": YAHOO_TICKER,
            "eodhd_close_eur": str(eod) if eod is not None else "",
            "yahoo_close_eur": str(yah) if yah is not None else "",
            "difference_pct": "",
            "status": "",
        }
        if eod is not None and yah is not None:
            pct = (eod - yah) / yah * Decimal("100")
            differences.append((abs(pct), day))
            row["difference_pct"] = f"{pct:.4f}"
            row["status"] = (
                "WITHIN_1PCT" if abs(pct) <= THRESHOLD_PCT
                else "REVIEW_DIFFERENCE"
            )
        elif eod is not None:
            row["status"] = "EODHD_ONLY"
            if day >= first_buy and first_yahoo is not None:
                if day < first_yahoo:
                    early_after_buy += 1
                else:
                    unmatched_after_yahoo += 1
        else:
            row["status"] = "YAHOO_ONLY"
            if day >= first_buy:
                unmatched_yahoo += 1
        rows.append(row)

    common = len(differences)
    high = sum(value > THRESHOLD_PCT for value, _ in differences)
    maximum = max(differences, default=None)
    if not yahoo:
        status = "NO_YAHOO_PRICES"
    elif not common:
        status = "NO_COMMON_SESSIONS"
    elif first_buy not in eodhd:
        status = "NO_EODHD_QUOTE_ON_BUY_DAY"
    elif high:
        status = "PRICE_DIFFERENCES_REVIEW"
    elif unmatched_after_yahoo or unmatched_yahoo:
        status = "SESSION_MISMATCH_REVIEW"
    elif common < 5:
        status = "TOO_FEW_COMMON_SESSIONS"
    else:
        status = "PRELIMINARY_AGREEMENT"

    summary = {
        "status": status,
        "first_purchase": first_buy.isoformat(),
        "eodhd_sessions": str(len(eodhd)),
        "yahoo_sessions": str(len(yahoo)),
        "first_eodhd": min(eodhd).isoformat(),
        "first_yahoo": first_yahoo.isoformat() if first_yahoo else "",
        "shared_sessions": str(common),
        "differences_over_1pct": str(high),
        "largest_difference_pct": f"{maximum[0]:.4f}" if maximum else "",
        "largest_difference_day": maximum[1].isoformat() if maximum else "",
        "eodhd_quote_on_purchase_day": "YES" if first_buy in eodhd else "NO",
        "eodhd_only_before_yahoo_start_after_buy": str(early_after_buy),
        "eodhd_only_after_yahoo_start": str(unmatched_after_yahoo),
        "yahoo_only_after_buy": str(unmatched_yahoo),
    }
    return rows, summary


def audit_spx(
    start: date, end: date, first_buy: date, *, eodhd_fetcher, yahoo_fetcher,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    if type(start) is not date or type(end) is not date or not start <= first_buy < end:
        raise ValueError("Invalid date range or first purchase")
    target = next(t for t in make_targets() if t.xtb_ticker == XTB_TICKER)
    matches = eodhd_fetcher(f"search/{target.isin}", {"limit": "100"})
    symbol, _, status = choose_listing(matches, target)
    if status != "FOUND_EXACT_LISTING" or symbol != EODHD_TICKER:
        raise ValueError(f"EODHD listing not confirmed: {status}")

    eod_raw = eodhd_fetcher(
        f"eod/{symbol}",
        {"from": start.isoformat(),
         "to": (end - timedelta(days=1)).isoformat(), "period": "d"},
    )
    eodhd = parse_eodhd_closes(eod_raw, start, end)
    if not eodhd:
        raise ValueError("EODHD returned no closes")
    history, metadata = yahoo_fetcher(YAHOO_TICKER, start, end)
    yahoo = parse_yahoo_closes(history, metadata, start, end)
    return compare_closes(eodhd, yahoo, first_buy)


def write_report(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare EODHD and Yahoo SpaceX raw closes")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--purchases", type=Path, default=PURCHASE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.purchases.is_file():
        parser.error(f"Purchase report not found: {args.purchases}")
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token or token.lower() == "demo":
        parser.error("Set your EODHD_API_TOKEN in this PowerShell session")
    try:
        buys = first_purchase_dates(args.purchases)
        if XTB_TICKER not in buys:
            raise ValueError("SPX.DE first purchase is missing")
        rows, summary = audit_spx(
            args.start, args.end, buys[XTB_TICKER],
            eodhd_fetcher=lambda path, params: http_json(path, params, token),
            yahoo_fetcher=fetch_yahoo_history,
        )
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.error(f"Audit failed ({type(exc).__name__}); no report written")

    write_report(rows, args.output)
    print("\nSPX.DE: EODHD / YAHOO RAW CLOSE COMPARISON")
    for key, value in summary.items():
        print(f"  {key}: {value or '-'}")
    print("\nLargest shared-session differences:")
    shared = [r for r in rows if r["difference_pct"]]
    for row in sorted(shared, key=lambda r: abs(Decimal(r["difference_pct"])), reverse=True)[:5]:
        print(f"  {row['date']}: {row['difference_pct']}% | EODHD {row['eodhd_close_eur']} EUR | Yahoo {row['yahoo_close_eur']} EUR")
    print(f"\nPrivate daily report: {args.output}")
    print("READ ONLY. A 1% threshold is diagnostic, not final approval for valuation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
