"""Read-only source-evidence check for unresolved DRAM.DE and RKLB.US.

This script never approves portfolio mappings or changes the price cache.
The public URLs are analyst-reviewed evidence, not automatically fetched.
"""

import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

REGISTRY = Path("data/private/portfolio_mapping/portfolio_mapping_reviewed.csv")
PRICES = Path("data/private/market_audit/remaining_price_sources_summary.csv")
ACTIONS = Path("data/private/market_audit/remaining_session_actions_audit.csv")
OUTPUT = Path("data/private/portfolio_mapping/remaining_identity_evidence.csv")

# These are explicit exceptions to the historical XTB catalog, NOT auto-approvals.
EVIDENCE = {
    "DRAM.DE": {
        "current_isin": "IE000CEUZ052", "catalog_isin": "",
        "catalog_status": "NOT_IN_CATALOG", "registry_status": "NOT_IN_CATALOG",
        "currency": "EUR", "market": "XETRA", "exchange": "GER",
        "yahoo": "DRAM.DE", "eodhd": "DRAM.XETRA",
        "note": "XTB_CATALOG_ISIN_MISSING", "weekday_gaps": "",
        "explanation": "Issuer and Xetra confirm the ETF; XTB catalog has no ISIN",
        "issuer_source": "https://hanetf.com/fund/dram-defiance-memory-etf/",
        "market_source": "https://www.cashmarket.deutsche-boerse.com/cash-en/Stay-Informed/newsroom/press-releases/New-ETF-and-ETP-Listings-on-June-19-2026-on-Deutsche-B-rse-5334498",
    },
    "RKLB.US": {
        "current_isin": "US7731211089", "catalog_isin": "US7731221062",
        "catalog_status": "FOUND", "registry_status": "CANDIDATE",
        "currency": "USD", "market": "US", "exchange": "NMS",
        "yahoo": "RKLB", "eodhd": "RKLB.US",
        "note": "XTB_LEGACY_CUSIP_REVIEW",
        "weekday_gaps": "2026-07-03|2026-09-07",
        "explanation": "Nasdaq confirms CUSIP change; SEC confirms 1-for-1 successor shares",
        "issuer_source": "https://www.sec.gov/Archives/edgar/data/1819994/000162828025027473/rklb-20250523.htm",
        "market_source": "https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-264",
    },
}
FIELDS = [
    "xtb_ticker", "xtb_catalog_isin", "current_isin", "currency",
    "yahoo_symbol", "eodhd_symbol", "identity_exception", "evidence_status",
    "explanation", "issuer_source", "market_source",
]


def index_rows(rows: list[dict], label: str, *, exact: bool) -> dict[str, dict]:
    result = {}
    for row in rows:
        ticker = str(row.get("xtb_ticker") or "").strip().upper()
        if ticker not in EVIDENCE:
            if exact:
                raise ValueError(f"Unexpected {label} instrument: {ticker}")
            continue
        if ticker in result:
            raise ValueError(f"Duplicate {label}: {ticker}")
        result[ticker] = row
    if set(result) != set(EVIDENCE):
        raise ValueError(f"Missing {label} instrument")
    return result


def valid_count(row: dict, key: str) -> int:
    try:
        value = int(row[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Invalid count: {key}") from None
    if value < 5:
        raise ValueError(f"Insufficient sessions: {key}")
    return value


def check_sources(registry_rows: list[dict], price_rows: list[dict], action_rows: list[dict]) -> list[dict[str, str]]:
    registry = index_rows(registry_rows, "registry", exact=False)
    prices = index_rows(price_rows, "comparison", exact=True)
    actions = index_rows(action_rows, "actions", exact=True)
    output = []
    for ticker, expected in EVIDENCE.items():
        reg, cmp, act = registry[ticker], prices[ticker], actions[ticker]
        checks = [
            (reg, "xtb_catalog_status", expected["catalog_status"]),
            (reg, "status", expected["registry_status"]),
            (reg, "isin", expected["catalog_isin"]),
            (cmp, "official_isin", expected["current_isin"]),
            (cmp, "expected_currency", expected["currency"]),
            (cmp, "yahoo_symbol", expected["yahoo"]),
            (cmp, "eodhd_symbol", expected["eodhd"]),
            (cmp, "identity_note", expected["note"]),
            (cmp, "yahoo_exchange", expected["exchange"]),
            (cmp, "status", "PRELIMINARY_AGREEMENT"),
            (cmp, "eodhd_on_purchase_day", "YES"),
            (cmp, "differences_over_1pct", "0"),
            (cmp, "eodhd_only_after_yahoo_start", "0"),
            (cmp, "yahoo_only_after_eodhd_start", "0"),
            (cmp, "error_stage", ""),
            (act, "official_isin", expected["current_isin"]),
            (act, "identity_note", expected["note"]),
            (act, "market", expected["market"]),
            (act, "status", "PRELIMINARY_OK"),
            (act, "flags", ""),
            (act, "yahoo_status", "OK"),
            (act, "yahoo_session_drift", "NO"),
            (act, "eodhd_div_status", "OK"),
            (act, "eodhd_split_status", "OK"),
            (act, "yahoo_div_dates", ""),
            (act, "yahoo_split_dates", ""),
            (act, "eodhd_div_dates", ""),
            (act, "eodhd_split_dates", ""),
            (act, "weekend_quotes", ""),
            (act, "weekdays_without_quotes", expected["weekday_gaps"]),
        ]
        if ticker == "RKLB.US":
            checks.append((reg, "xtb_currency", "USD"))
        for row, field, value in checks:
            if str(row.get(field, "")).strip() != value:
                raise ValueError(f"{ticker}: inconsistent {field}")
        if reg.get("approved_yahoo_ticker", "").strip():
            raise ValueError(f"{ticker}: mapping unexpectedly approved")
        try:
            first_cmp = date.fromisoformat(cmp["first_purchase"])
            first_act = date.fromisoformat(act["first_purchase"])
            max_pct = Decimal(cmp["max_abs_difference_pct"])
        except (KeyError, ValueError, InvalidOperation, TypeError):
            raise ValueError(f"{ticker}: invalid comparison metadata") from None
        if first_cmp != first_act or not max_pct.is_finite() or max_pct < 0 or max_pct > Decimal("0.01"):
            raise ValueError(f"{ticker}: inconsistent purchase date or price divergence")
        sessions = valid_count(cmp, "common_sessions")
        if any(valid_count(cmp, k) != sessions for k in ("eodhd_sessions", "yahoo_sessions")):
            raise ValueError(f"{ticker}: source session counts disagree")
        if valid_count(act, "snapshot_sessions") != sessions:
            raise ValueError(f"{ticker}: action/session counts disagree")
        output.append({
            "xtb_ticker": ticker,
            "xtb_catalog_isin": expected["catalog_isin"],
            "current_isin": expected["current_isin"],
            "currency": expected["currency"],
            "yahoo_symbol": expected["yahoo"],
            "eodhd_symbol": expected["eodhd"],
            "identity_exception": expected["note"],
            "evidence_status": "SOURCE_EVIDENCE_READY_NOT_APPROVED",
            "explanation": expected["explanation"],
            "issuer_source": expected["issuer_source"],
            "market_source": expected["market_source"],
        })
    return output


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_report(rows: list[dict], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only evidence review of DRAM and RKLB")
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--prices", type=Path, default=PRICES)
    parser.add_argument("--actions", type=Path, default=ACTIONS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    for path in (args.registry, args.prices, args.actions):
        if not path.is_file():
            parser.error(f"Missing private report: {path}")
    try:
        results = check_sources(read_csv(args.registry), read_csv(args.prices), read_csv(args.actions))
    except (ValueError, OSError) as exc:
        parser.error(f"Evidence verification blocked: {exc}")
    write_report(results, args.output)
    print("\nDRAM / RKLB — INSTRUMENT IDENTITY EVIDENCE")
    for r in results:
        print(f"  {r['xtb_ticker']}: {r['evidence_status']} | ISIN={r['current_isin']} | note={r['identity_exception']}")
    print(f"\nPrivate evidence report: {args.output}")
    print("READ ONLY — no mappings or market price cache modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
