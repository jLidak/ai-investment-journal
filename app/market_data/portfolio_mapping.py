"""Build a reviewable portfolio mapping draft; never promote a guessed Yahoo ticker."""

import csv
from collections import Counter
from pathlib import Path

from app.market_data.symbols import suggest_yahoo_ticker
from app.market_data.verified_instruments import VERIFIED_INSTRUMENTS


FIELDS = [
    "xtb_ticker", "xtb_catalog_status", "xtb_name", "isin", "kind",
    "xtb_currency", "yahoo_candidate", "approved_yahoo_ticker",
    "yahoo_name", "yahoo_currency", "yahoo_exchange", "status",
    "reason", "source_url",
]
REVIEW_STATUSES = {
    "CANDIDATE", "REVIEW_REQUIRED", "CONFLICT", "NOT_IN_CATALOG",
    "AMBIGUOUS", "UNMAPPED", "FETCH_ERROR",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def make_draft(
    coverage: list[dict[str, str]],
    identity_rows: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Merge two existing local reports with reviewed mappings, without networking.

    VERIFIED is reserved for an already manually reviewed mapping consistent
    with the official XTB snapshot. Yahoo metadata alone never confirms ISIN.
    """
    audits = {row["xtb_ticker"].strip().upper(): row for row in (identity_rows or [])}
    rows = []
    seen = set()

    for item in sorted(coverage, key=lambda r: r["xtb_ticker"]):
        ticker = item["xtb_ticker"].strip().upper()
        if not ticker or ticker in seen:
            raise ValueError(f"Empty or duplicated XTB ticker: {ticker!r}")
        seen.add(ticker)

        xtb_status = item["status"].strip().upper()
        if xtb_status not in {"FOUND", "NOT_IN_CATALOG", "AMBIGUOUS"}:
            raise ValueError(f"Unexpected coverage status for {ticker}: {xtb_status}")

        official_isin = item.get("isin", "").strip().upper()
        official_currency = item.get("quote_currency", "").strip().upper()
        official_kind = item.get("kind", "").strip().upper()
        audit = audits.get(ticker, {})
        registry = VERIFIED_INSTRUMENTS.get(ticker)

        # For missing/ambiguous symbols, don't create a usable Yahoo assignment.
        if registry:
            suggestion = registry.yahoo_ticker
        else:
            suggestion = suggest_yahoo_ticker(ticker).yahoo_ticker or ""
        audited_ticker = audit.get("yahoo_ticker", "").strip().upper()
        audited_currency = audit.get("yahoo_currency", "").strip().upper()
        audited_name = audit.get("yahoo_name", "").strip()
        audited_exchange = audit.get("yahoo_exchange", "").strip()
        audited_status = audit.get("status", "").strip().upper()

        approved = ""
        if xtb_status == "AMBIGUOUS":
            status, reason = "AMBIGUOUS", "XTB symbol identifies multiple securities; no ISIN selected"
        elif xtb_status == "NOT_IN_CATALOG":
            status, reason = "NOT_IN_CATALOG", "No identity in this XTB catalogue snapshot"
        elif not official_isin or not official_currency:
            status, reason = "REVIEW_REQUIRED", "Incomplete official XTB identity data"
        elif registry and (
            registry.isin != official_isin
            or registry.quote_currency != official_currency
            or registry.kind.value != official_kind
        ):
            status, reason = "CONFLICT", "XTB catalogue disagrees with reviewed ISIN/currency/type"
        elif not suggestion:
            status, reason = "UNMAPPED", "No proposed Yahoo ticker"
        elif audit and audited_ticker and audited_ticker != suggestion:
            status, reason = "CONFLICT", "Yahoo audit examined a different proposed ticker"
        elif audit and audited_currency and audited_currency != official_currency:
            status, reason = "CONFLICT", "Yahoo quote currency differs from XTB catalogue"
        elif registry:
            status, reason = "VERIFIED", "Existing reviewed mapping agrees with official XTB catalogue"
            approved = registry.yahoo_ticker
        elif audited_status == "FETCH_ERROR":
            status, reason = "FETCH_ERROR", "No Yahoo metadata; ticker identity remains unverified"
        elif official_kind == "UNKNOWN_FUND_KIND":
            status, reason = "REVIEW_REQUIRED", "Unrecognized XTB instrument type"
        elif audit and audited_currency == official_currency and audited_name:
            status, reason = "CANDIDATE", "Yahoo currency agrees, but listing/ISIN not independently confirmed"
        else:
            status, reason = "CANDIDATE", "Ticker proposed by naming rule; Yahoo identity not confirmed"

        rows.append({
            "xtb_ticker": ticker,
            "xtb_catalog_status": xtb_status,
            "xtb_name": item.get("name", "") or audit.get("xtb_names", ""),
            "isin": official_isin if xtb_status == "FOUND" else "",
            "kind": official_kind if xtb_status == "FOUND" else "",
            "xtb_currency": official_currency if xtb_status == "FOUND" else "",
            "yahoo_candidate": suggestion,
            "approved_yahoo_ticker": approved,
            "yahoo_name": audited_name if audited_ticker == suggestion else "",
            "yahoo_currency": audited_currency if audited_ticker == suggestion else "",
            "yahoo_exchange": audited_exchange if audited_ticker == suggestion else "",
            "status": status,
            "reason": reason,
            "source_url": registry.source_url if registry else "",
        })

    return rows


def write_reports(rows: list[dict[str, str]], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = [
        (output_dir / "portfolio_mapping_draft.csv", rows),
        (output_dir / "portfolio_mapping_review.csv", [r for r in rows if r["status"] in REVIEW_STATUSES]),
    ]
    for path, selected in targets:
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(selected)
        temp.replace(path)
    return targets[0][0], targets[1][0]


def summarize(rows: list[dict[str, str]]) -> Counter:
    return Counter(r["status"] for r in rows)
