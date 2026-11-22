
"""Safely cache audited SpaceX Xetra closes as a separate EODHD source.

No identity approvals, Yahoo overwrites, or P&L calculations.
Dry-run by default; --apply is needed for any SQLite writes.
"""

import argparse
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.market_data.portfolio_mapping import read_csv
from app.market_data.price_cache import (
    PriceIdentity, open_cache, read_closes, record_fetch,
)
from scripts.audit_eodhd_candidates import (
    choose_listing, first_purchase_dates, http_json, make_targets,
)
from scripts.audit_spx_session_actions import (
    OUTPUT as QUALITY_REPORT, SOURCE_REPORT as COMPARISON_REPORT,
    load_comparison,
)
from scripts.check_purchase_price_coverage import DEFAULT_OUTPUT as PURCHASE_REPORT
from scripts.compare_spx_price_sources import (
    EODHD_TICKER, XTB_TICKER, parse_eodhd_closes,
)

CACHE = Path("data/private/market_data/yahoo_prices.db")
QUALITY_FLAGS = "PRELIMINARY_ONLY|XETRA_CALENDAR_NOT_CERTIFIED|NOT_PNL_APPROVED"


def load_snapshot(path: Path, start: date, end: date, buy: date):
    """Verify prior EODHD/Yahoo comparison, then return EODHD closes."""
    eod_days, yahoo_days = load_comparison(path, start, end)
    if buy not in eod_days or len(yahoo_days) < 5:
        raise ValueError("Comparison lacks purchase date or sufficient Yahoo overlap")
    if not yahoo_days <= eod_days:
        raise ValueError("Yahoo has sessions missing from EODHD")
    first_yahoo = min(yahoo_days)
    if any(day >= first_yahoo and day not in yahoo_days for day in eod_days):
        raise ValueError("Session mismatch in overlapping range")

    prices = {}
    for row in read_csv(path):
        day = date.fromisoformat(row["date"])
        if row["eodhd_close_eur"]:
            eod = Decimal(row["eodhd_close_eur"])
            prices[day] = eod
            if row["yahoo_close_eur"]:
                yahoo = Decimal(row["yahoo_close_eur"])
                if abs((eod - yahoo) / yahoo * 100) > Decimal("1"):
                    raise ValueError("Price discrepancy above 1%")
    if set(prices) != eod_days:
        raise ValueError("Incomplete EODHD comparison snapshot")
    return prices, len(yahoo_days)


def validate_quality(path: Path, start: date, end: date, buy: date,
                     prices: dict[date, Decimal], yahoo_count: int) -> None:
    """A reported preliminary OK is required, not a P&L approval."""
    reports = read_csv(path)
    if len(reports) != 1:
        raise ValueError("Expected one SPX quality report")
    r = reports[0]
    equal = {
        "xtb_ticker": XTB_TICKER,
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "first_purchase": buy.isoformat(),
        "status": "PRELIMINARY_OK",
        "eodhd_sessions": str(len(prices)),
        "yahoo_sessions_csv": str(yahoo_count),
        "yahoo_status": "OK",
        "yahoo_session_drift": "NO",
        "eodhd_div_status": "OK",
        "eodhd_split_status": "OK",
    }
    if any(r.get(field) != expected for field, expected in equal.items()):
        raise ValueError("Quality report does not match verified history")
    for field in (
        "flags", "missing_weekdays", "missing_weekdays_after_buy",
        "weekend_quotes", "yahoo_div_dates", "yahoo_split_dates",
        "eodhd_div_dates", "eodhd_split_dates",
    ):
        if r.get(field) != "":
            raise ValueError(f"Quality report has outstanding {field}")

    weekdays = {
        start + timedelta(days=i)
        for i in range((end - start).days)
        if (start + timedelta(days=i)).weekday() < 5
    }
    if (set(prices) != weekdays or
            r.get("reference_weekdays") != str(len(weekdays))):
        raise ValueError("EODHD dates do not match audited weekdays")


def prepare_prices(start: date, end: date, buy: date,
                   comparison: Path, quality: Path, fetcher):
    """Two EODHD requests: verify listing, then fetch exact raw closes."""
    if (type(start) is not date or type(end) is not date
            or type(buy) is not date or not start <= buy < end):
        raise ValueError("Invalid dates")
    snapshot, count = load_snapshot(comparison, start, end, buy)
    validate_quality(quality, start, end, buy, snapshot, count)
    target = next(t for t in make_targets() if t.xtb_ticker == XTB_TICKER)
    matches = fetcher(f"search/{target.isin}", {"limit": "100"})
    symbol, _, status = choose_listing(matches, target)
    if status != "FOUND_EXACT_LISTING" or symbol != EODHD_TICKER:
        raise ValueError("EODHD identity no longer matches the audit")
    payload = fetcher(
        f"eod/{symbol}",
        {"from": start.isoformat(),
         "to": (end - timedelta(days=1)).isoformat(), "period": "d"},
    )
    fresh = parse_eodhd_closes(payload, start, end)
    if fresh != snapshot:
        raise ValueError("Fresh EODHD prices differ from the audited snapshot")
    identity = PriceIdentity(
        xtb_ticker=XTB_TICKER, source="EODHD", source_symbol=symbol,
        isin=target.isin, quote_currency=target.currency,
    )
    return identity, fresh, count


def store_prices(cache: Path, identity: PriceIdentity,
                 prices: dict[date, Decimal], start: date, end: date) -> str:
    """Save with existing transactional conflict checks; never touch YAHOO."""
    connection = open_cache(cache)
    try:
        variants = connection.execute(
            """SELECT DISTINCT source_symbol, isin, quote_currency
               FROM historical_closes WHERE xtb_ticker = ? AND source = ?
               UNION
               SELECT DISTINCT source_symbol, isin, quote_currency
               FROM checked_price_windows WHERE xtb_ticker = ? AND source = ?""",
            (XTB_TICKER, "EODHD", XTB_TICKER, "EODHD"),
        ).fetchall()
        expected = (identity.source_symbol, identity.isin, identity.quote_currency)
        if any(tuple(row) != expected for row in variants):
            raise ValueError("Conflicting EODHD identity already stored")
        existing = read_closes(connection, identity, start, end)
        if any(day not in prices or prices[day] != price
               for day, price in existing.items()):
            raise ValueError("Conflicting EODHD historical prices in SQLite")
        record_fetch(connection, identity, start, end, prices,
                     quality_flags=QUALITY_FLAGS)
        saved = read_closes(connection, identity, start, end)
        if saved != prices:
            raise ValueError("Cache verification failed")
        return "CACHE_REUSED" if len(existing) == len(prices) else "CACHED"
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Cache audited SPX.XETRA closes")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--comparison", type=Path, default=COMPARISON_REPORT)
    parser.add_argument("--quality", type=Path, default=QUALITY_REPORT)
    parser.add_argument("--purchases", type=Path, default=PURCHASE_REPORT)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--apply", action="store_true", help="Actually write validated prices")
    args = parser.parse_args()
    if any(not p.is_file() for p in (args.comparison, args.quality, args.purchases)):
        parser.error("Required private audit CSV is missing")
    token = os.getenv("EODHD_API_TOKEN", "").strip()
    if not token or token.lower() == "demo":
        parser.error("Set EODHD_API_TOKEN in this PowerShell session")
    try:
        purchases = first_purchase_dates(args.purchases)
        identity, prices, shared = prepare_prices(
            args.start, args.end, purchases[XTB_TICKER], args.comparison,
            args.quality, lambda path, params: http_json(path, params, token),
        )
        state = (store_prices(args.cache, identity, prices, args.start, args.end)
                 if args.apply else "DRY_RUN")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        parser.error(f"Validation blocked: {type(exc).__name__} (no approved mapping changed)")
    print("\nSPX.XETRA -> SQLITE EODHD SOURCE")
    print(f"  status: {state}")
    print(f"  source: {identity.source} | symbol: {identity.source_symbol}")
    print(f"  checked_eodhd_sessions: {len(prices)}")
    print(f"  confirmed_overlap_with_yahoo: {shared}")
    print(f"  cache: {args.cache}")
    print("  quality: PRELIMINARY_ONLY; NOT_PNL_APPROVED")
    print("YAHOO data and instrument mapping have not been modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
