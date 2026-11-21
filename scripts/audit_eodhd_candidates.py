
"""Read-only EODHD audit of the three unresolved XTB instruments.

Search by documented ISIN; fetch EOD only for matching exchange AND currency.
Never approve mappings, change the Yahoo cache, or record API tokens.
"""

import argparse
import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.market_data.portfolio_mapping import read_csv
from scripts.audit_unapproved_candidates import CANDIDATES
from scripts.check_purchase_price_coverage import DEFAULT_OUTPUT as PURCHASE_REPORT


OUTPUT = Path("data/private/portfolio_mapping/eodhd_candidates_audit.csv")
EXCHANGES = {"DRAM.DE": "XETRA", "RKLB.US": "US", "SPX.DE": "XETRA"}
FIELDS = [
    "xtb_ticker", "isin", "expected_exchange", "expected_currency",
    "first_purchase", "matching_listing", "other_listings", "status",
    "first_session", "last_session", "first_quote_after_purchase",
    "sessions", "days_to_first_quote", "error_type",
]


@dataclass(frozen=True)
class Target:
    xtb_ticker: str
    isin: str
    exchange: str
    currency: str


def make_targets() -> list[Target]:
    """Reuse ISIN research from the earlier candidate audit."""
    result = []
    for ticker, exchange in EXCHANGES.items():
        identities = CANDIDATES[ticker]
        if len({(c.official_isin, c.expected_currency) for c in identities}) != 1:
            raise ValueError(f"Conflicting reviewed candidates for {ticker}")
        identity = identities[0]
        result.append(Target(ticker, identity.official_isin, exchange, identity.expected_currency))
    return result


def first_purchase_dates(path: Path) -> dict[str, date]:
    rows = read_csv(path)
    output = {}
    for row in rows:
        ticker = row["xtb_ticker"].strip().upper()
        if not ticker or ticker in output:
            raise ValueError("Duplicate/empty ticker in purchase report")
        raw = row["first_purchase_utc"].strip()
        if raw:
            output[ticker] = date.fromisoformat(raw)
    return output


def http_json(path: str, params: dict[str, str], token: str) -> object:
    """Transport with a sanitized error message (never print the token)."""
    address = "https://eodhd.com/api/" + quote(path, safe="/._-")
    query = urlencode({**params, "api_token": token, "fmt": "json"})
    request = Request(address + "?" + query, headers={"User-Agent": "AI-Investment-Journal/1.0"})
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"), parse_float=Decimal)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP_{exc.code}") from None
    except URLError:
        raise RuntimeError("CONNECTION_ERROR") from None
    except (ValueError, UnicodeError):
        raise RuntimeError("INVALID_JSON") from None


def choose_listing(payload: object, target: Target) -> tuple[str, str, str]:
    """Choose only an exact ISIN, exchange and quote-currency match."""
    if not isinstance(payload, list):
        raise ValueError("Invalid EODHD search response")
    exact_isin = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Invalid EODHD search item")
        if str(item.get("ISIN") or "").upper() != target.isin.upper():
            continue
        code = str(item.get("Code") or "").strip().upper()
        exchange = str(item.get("Exchange") or "").strip().upper()
        currency = str(item.get("Currency") or "").strip().upper()
        if not code or not exchange or not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]*", code):
            continue
        exact_isin.append((f"{code}.{exchange}", exchange, currency))

    alternatives = "|".join(sorted({symbol for symbol, _, _ in exact_isin}))
    exact_venue = [s for s, e, c in exact_isin if e == target.exchange.upper() and c == target.currency.upper()]
    wrong_ccy = any(e == target.exchange.upper() and c != target.currency.upper() for _, e, c in exact_isin)

    if len(set(exact_venue)) > 1:
        return "", alternatives, "AMBIGUOUS_LISTING"
    if exact_venue:
        return exact_venue[0], alternatives, "FOUND_EXACT_LISTING"
    if wrong_ccy:
        return "", alternatives, "CURRENCY_MISMATCH"
    return "", alternatives, ("OTHER_MARKET_ONLY" if exact_isin else "ISIN_NOT_FOUND")


def validate_history(payload: object, start: date, end: date) -> list[date]:
    """Require valid daily Close rows; [start, end) internally."""
    if not isinstance(payload, list):
        raise ValueError("Invalid EODHD historical response")
    days = set()
    for record in payload:
        if not isinstance(record, dict):
            raise ValueError("Invalid EODHD price row")
        try:
            day = date.fromisoformat(record["date"])
            price = Decimal(str(record["close"]))
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError("Malformed EODHD price row") from exc
        if day in days or not start <= day < end:
            raise ValueError("Duplicate/out-of-range EODHD session")
        if not price.is_finite() or price <= 0:
            raise ValueError("Invalid EODHD Close")
        days.add(day)
    return sorted(days)


def audit(
    targets: list[Target], purchases: dict[str, date], start: date, end: date,
    *, fetch_json,
) -> list[dict[str, str]]:
    if type(start) is not date or type(end) is not date or start >= end:
        raise ValueError("Invalid date range")
    result = []
    for target in targets:
        if target.xtb_ticker not in purchases:
            raise ValueError(f"Missing first purchase for {target.xtb_ticker}")
        buy = purchases[target.xtb_ticker]
        row = {key: "" for key in FIELDS}
        row.update({
            "xtb_ticker": target.xtb_ticker,
            "isin": target.isin, "expected_exchange": target.exchange,
            "expected_currency": target.currency, "first_purchase": buy.isoformat(),
        })
        try:
            matches = fetch_json(f"search/{target.isin}", {"limit": "100"})
            symbol, alternatives, status = choose_listing(matches, target)
            row["matching_listing"] = symbol
            row["other_listings"] = alternatives
            row["status"] = status
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            row["status"] = "SEARCH_ERROR"
            row["error_type"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            result.append(row)
            continue

        if row["status"] != "FOUND_EXACT_LISTING":
            result.append(row)
            continue

        try:
            records = fetch_json(
                f"eod/{row['matching_listing']}",
                {"from": start.isoformat(), "to": (end - timedelta(days=1)).isoformat(), "period": "d"},
            )
            days = validate_history(records, start, end)
            row["sessions"] = str(len(days))
            if not days:
                row["status"] = "NO_PRICES"
            else:
                row["first_session"] = days[0].isoformat()
                row["last_session"] = days[-1].isoformat()
                after = next((day for day in days if day >= buy), None)
                if after is not None:
                    row["first_quote_after_purchase"] = after.isoformat()
                    row["days_to_first_quote"] = str((after - buy).days)
                if days[0] > buy:
                    row["status"] = "BUY_BEFORE_FIRST_QUOTE"
                elif after is None:
                    row["status"] = "NO_QUOTE_AFTER_BUY"
                elif (after - buy).days > 7:
                    row["status"] = "DELAYED_QUOTE_AFTER_BUY"
                else:
                    row["status"] = "INITIAL_COVERAGE_OK"
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            row["status"] = "PRICE_ERROR"
            row["error_type"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        result.append(row)
    return result


def write_report(rows: list[dict[str, str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only audit of EODHD by ISIN")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="Exclusive end date")
    parser.add_argument("--purchases", type=Path, default=PURCHASE_REPORT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--end must be later than --start")
    if not args.purchases.is_file():
        parser.error(f"Missing purchase report: {args.purchases}")
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token or token.lower() == "demo":
        parser.error("Set EODHD_API_TOKEN in your PowerShell session (free personal token required)")
    try:
        purchases = first_purchase_dates(args.purchases)
        rows = audit(make_targets(), purchases, args.start, args.end,
                     fetch_json=lambda path, params: http_json(path, params, token))
    except ValueError as exc:
        parser.error(str(exc))
    write_report(rows, args.output)
    print("\nEODHD CANDIDATE AUDIT (READ-ONLY, <= 6 API REQUESTS)")
    for row in rows:
        print(f"  {row['xtb_ticker']}: {row['status']} | symbol={row['matching_listing'] or '-'} | first={row['first_session'] or '-'} | available={row['sessions'] or '-'}")
        if row['other_listings']:
            print(f"    ISIN listings: {row['other_listings']}")
        if row['error_type']:
            print(f"    Error: {row['error_type']}")
    print(f"\nPrivate report: {args.output}")
    print("No mapping or price cache was changed. INITIAL_COVERAGE_OK is not full session coverage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
