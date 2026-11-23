"""Stage 3.5l: cache independently audited DRAM/RKLB EODHD closes.

Strictly preliminary and source-specific: no mapping, Yahoo or P&L approval.
An API-backed dry run is the default; --apply enables SQLite writes.
Only the exact audited [2026-07-01, 2026-10-27) interval is supported.
"""

import argparse
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.market_data.price_cache import PriceIdentity, open_cache, read_closes, record_fetch
from scripts.apply_external_identity_review import (
    EVIDENCE, REQUIRED_EVIDENCE_FIELDS, REQUIRED_REGISTRY_FIELDS,
    ADDITIONAL_FIELDS, OUTPUT as EXTERNAL, SOURCE as REGISTRY,
    prepare_review, read_table,
)
from scripts.audit_eodhd_candidates import (
    choose_listing, first_purchase_dates, http_json, make_targets,
)
from scripts.audit_remaining_sessions_actions import (
    OUTPUT as ACTIONS, load_snapshots,
)
from scripts.check_purchase_price_coverage import DEFAULT_OUTPUT as PURCHASES
from scripts.compare_remaining_price_sources import DAILY_OUTPUT as DAILY, SUMMARY_OUTPUT as SUMMARY
from scripts.compare_spx_price_sources import parse_eodhd_closes
from scripts.review_remaining_identity_evidence import check_sources, FIELDS as EVIDENCE_FIELDS, OUTPUT as EVIDENCE_CSV

START = date(2026, 7, 1)
END = date(2026, 10, 27)
CACHE = Path("data/private/market_data/yahoo_prices.db")
QUALITY_FLAGS = "PRELIMINARY_ONLY|EXTERNAL_IDENTITY_REVIEWED|CALENDAR_PRELIMINARY|NOT_PNL_APPROVED"


def read_sources(paths: dict[str, Path]):
    """Verify every existing review before accepting a preliminary price target."""
    old_fields, original = read_table(paths["registry"], REQUIRED_REGISTRY_FIELDS)
    ext_fields, external = read_table(
        paths["external"], REQUIRED_REGISTRY_FIELDS | set(ADDITIONAL_FIELDS)
    )
    evidence_fields, evidence = read_table(paths["evidence"], REQUIRED_EVIDENCE_FIELDS)
    _, summary = read_table(paths["summary"], {"xtb_ticker", "status"})
    _, actions = read_table(paths["actions"], {"xtb_ticker", "status"})

    # No changed/reordered legacy rows or extra approvals can slip through.
    if set(ext_fields) != set(old_fields) | set(ADDITIONAL_FIELDS):
        raise ValueError("External registry has unexpected columns")
    if evidence_fields != EVIDENCE_FIELDS:
        raise ValueError("Identity evidence has unexpected columns")
    if external != prepare_review(original, evidence):
        raise ValueError("External registry differs from reviewed inputs")
    if evidence != check_sources(original, summary, actions):
        raise ValueError("Source-evidence report differs from underlying audits")
    return summary, actions


def prepare_snapshot(paths: dict[str, Path], purchases: dict[str, date], start: date, end: date):
    """Verify audited prices, action dates and expected closed exchange days."""
    if type(start) is not date or type(end) is not date or (start, end) != (START, END):
        raise ValueError("Only the exact audited date range is supported")

    summary, actions = read_sources(paths)
    snapshots = load_snapshots(paths["daily"], paths["summary"], start, end, purchases)
    by_actions = {r["xtb_ticker"]: r for r in actions}

    weekdays = {
        start + timedelta(days=i) for i in range((end-start).days)
        if (start + timedelta(days=i)).weekday() < 5
    }

    for ticker, expected in EVIDENCE.items():
        snap = snapshots[ticker]
        dates = snap["dates"]
        row = by_actions[ticker]
        gaps = "|".join(d.isoformat() for d in sorted(weekdays - dates))
        if (gaps != expected["weekday_gaps"]
                or row.get("weekdays_without_quotes") != gaps
                or row.get("reference_weekdays") != str(len(weekdays))
                or row.get("snapshot_sessions") != str(len(dates))
                or snap["isin"] != expected["current_isin"]
                or snap["currency"] != expected["currency"]
                or row.get("first_purchase") != snap["first_buy"].isoformat()
                or any(day.weekday() >= 5 for day in dates)):
            raise ValueError(f"{ticker}: calendar or identity audit mismatch")

    prices = {ticker: {} for ticker in EVIDENCE}
    from app.market_data.portfolio_mapping import read_csv  # Local CSV only.
    for row in read_csv(paths["daily"]):
        ticker = row["xtb_ticker"]
        if ticker not in prices:
            raise ValueError("Unknown instrument in daily comparison")
        day = date.fromisoformat(row["session"])
        p = Decimal(row["eodhd_close"])
        y = Decimal(row["yahoo_close"])
        if (day not in snapshots[ticker]["dates"] or day in prices[ticker]
                or not p.is_finite() or p <= 0 or not y.is_finite() or y <= 0
                or abs((p-y)/y*100) > Decimal("0.01")):
            raise ValueError(f"{ticker}: invalid audited daily close")
        prices[ticker][day] = p
    for ticker in EVIDENCE:
        if set(prices[ticker]) != snapshots[ticker]["dates"]:
            raise ValueError(f"{ticker}: incomplete price comparison")
    return prices


def verify_fresh(prices: dict[str, dict[date, Decimal]], start: date, end: date, fetcher):
    """All four external calls and both comparisons complete before any write."""
    targets = {t.xtb_ticker: t for t in make_targets()}
    prepared = {}
    for ticker, expected in EVIDENCE.items():
        target = targets[ticker]
        if (target.isin != expected["current_isin"]
                or target.currency != expected["currency"]
                or target.exchange != expected["market"]):
            raise ValueError(f"{ticker}: reviewed EODHD listing parameters changed")
        payload = fetcher(f"search/{target.isin}", {"limit": "100"})
        symbol, _, status = choose_listing(payload, target)
        if status != "FOUND_EXACT_LISTING" or symbol != expected["eodhd"]:
            raise ValueError(f"{ticker}: EODHD identity mismatch")
        rows = fetcher(
            f"eod/{symbol}",
            {"from": start.isoformat(), "to": (end-timedelta(days=1)).isoformat(),
             "period": "d"},
        )
        fresh = parse_eodhd_closes(rows, start, end)
        if fresh != prices[ticker]:
            raise ValueError(f"{ticker}: fresh EODHD prices differ from audit")
        prepared[ticker] = (
            PriceIdentity(ticker, "EODHD", symbol, target.isin, target.currency), fresh,
        )
    return prepared


class _DeferredConnection:
    """Let record_fetch participate in a single outer SQLite transaction."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # The batch owner commits or rolls back both instruments together.
        return False


def store(cache: Path, prepared: dict, start: date, end: date) -> dict[str, str]:
    """Precheck then atomically cache BOTH identities, or neither."""
    connection = open_cache(cache)
    try:
        connection.execute("BEGIN IMMEDIATE")
        states = {}
        for ticker, (identity, prices) in prepared.items():
            variants = connection.execute(
                """SELECT DISTINCT source_symbol, isin, quote_currency FROM historical_closes
                   WHERE xtb_ticker=? AND source='EODHD'
                   UNION
                   SELECT DISTINCT source_symbol, isin, quote_currency FROM checked_price_windows
                   WHERE xtb_ticker=? AND source='EODHD'""",
                (ticker, ticker),
            ).fetchall()
            current = (identity.source_symbol, identity.isin, identity.quote_currency)
            if any(tuple(x) != current for x in variants):
                raise ValueError(f"{ticker}: conflicting cached EODHD identity")
            previous_flags = connection.execute(
                "SELECT DISTINCT quality_flags FROM checked_price_windows "
                "WHERE xtb_ticker=? AND source='EODHD'", (ticker,),
            ).fetchall()
            if any(flag != QUALITY_FLAGS for (flag,) in previous_flags):
                raise ValueError(f"{ticker}: incompatible cached quality flags")
            existing = read_closes(connection, identity, start, end)
            if any(day not in prices or prices[day] != close for day, close in existing.items()):
                raise ValueError(f"{ticker}: conflicting cached EODHD close")
            states[ticker] = "CACHE_REUSED" if existing == prices else "CACHED"

        # record_fetch uses `with connection` internally. The proxy defers its
        # commits so that both ticker writes are one all-or-nothing transaction.
        deferred = _DeferredConnection(connection)
        for ticker, (identity, prices) in prepared.items():
            record_fetch(deferred, identity, start, end, prices, quality_flags=QUALITY_FLAGS)
            if read_closes(connection, identity, start, end) != prices:
                raise ValueError(f"{ticker}: cache readback mismatch")
        connection.commit()
        return states
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="DRAM/RKLB preliminary EODHD SQLite cache")
    parser.add_argument("--start", type=date.fromisoformat, default=START)
    parser.add_argument("--end", type=date.fromisoformat, default=END)
    for name, default in (
        ("registry", REGISTRY), ("external", EXTERNAL), ("evidence", EVIDENCE_CSV),
        ("daily", DAILY), ("summary", SUMMARY), ("actions", ACTIONS),
        ("purchases", PURCHASES), ("cache", CACHE),
    ):
        parser.add_argument(f"--{name}", type=Path, default=default)
    parser.add_argument("--apply", action="store_true", help="Write preliminary EODHD closes to SQLite")
    args = parser.parse_args()
    if (args.start, args.end) != (START, END):
        parser.error("Only the original audited date interval is allowed")
    paths = {k: getattr(args, k) for k in
             ("registry", "external", "evidence", "daily", "summary", "actions")}
    if any(not p.is_file() for p in [*paths.values(), args.purchases]):
        parser.error("Missing a required private audit/mapping CSV")
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token or token.lower() == "demo":
        parser.error("Set EODHD_API_TOKEN in this PowerShell session")
    try:
        purchases = first_purchase_dates(args.purchases)
        snapshot = prepare_snapshot(paths, purchases, args.start, args.end)
        prepared = verify_fresh(
            snapshot, args.start, args.end,
            lambda path, params: http_json(path, params, token),
        )
        state = (store(args.cache, prepared, args.start, args.end)
                 if args.apply else {ticker: "DRY_RUN" for ticker in prepared})
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        parser.error(f"Validation blocked: {type(exc).__name__}; no mapping/P&L approvals")

    print("\nDRAM / RKLB — PRELIMINARY EODHD SQLITE CACHE")
    for ticker, (identity, quotes) in prepared.items():
        print(f"  {ticker}: {state[ticker]} | {identity.source_symbol} | "
              f"ISIN={identity.isin} | sessions={len(quotes)}")
    print(f"Cache path: {args.cache}")
    print(f"Quality: {QUALITY_FLAGS}")
    print("No Yahoo prices, XTB mapping, or portfolio P&L approval changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
