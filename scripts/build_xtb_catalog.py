"""Build a source-backed XTB ticker -> ISIN catalogue and portfolio coverage report."""

import argparse
import csv
import hashlib
from collections import Counter
from datetime import date
from pathlib import Path

from app.ingestion.xtb_cash import read_cash_operations
from app.ingestion.xtb_movements import extract_position_movements
from app.market_data.xtb_catalog import build_coverage, identity_keys, parse_omi_pdf


DEFAULT_SOURCE = "https://www.xtb.com/pl/specyfikacja-instrumentow/dokumenty"


def collect_traded_symbols(export_folder: Path) -> set[str]:
    symbols: set[str] = set()
    files = sorted(
        p for p in export_folder.glob("*.xlsx") if not p.name.startswith("~$")
    )
    if not files:
        raise ValueError(f"No XTB XLSX exports in {export_folder}")
    for path in files:
        operations = read_cash_operations(path)
        movements = extract_position_movements(operations)
        symbols.update(m.ticker.strip().upper() for m in movements)
    return symbols


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--effective-from", type=date.fromisoformat, required=True)
    parser.add_argument("--exports", type=Path, default=Path("data/private"))
    parser.add_argument("--out", type=Path, default=Path("data/private/xtb_catalog"))
    parser.add_argument("--source-url", default=DEFAULT_SOURCE)
    args = parser.parse_args()

    if not args.pdf.is_file():
        parser.error(f"PDF not found: {args.pdf}")

    catalog = parse_omi_pdf(args.pdf)
    if len(catalog) < 100:
        raise ValueError(
            f"Only {len(catalog)} rows extracted; refusing to publish likely incomplete PDF parsing"
        )
    symbols = collect_traded_symbols(args.exports)
    coverage = build_coverage(symbols, catalog)
    sha = hashlib.sha256(args.pdf.read_bytes()).hexdigest()

    catalog_rows = [
        {
            "xtb_ticker": rec.xtb_ticker,
            "name": rec.name,
            "isin": rec.isin,
            "quote_currency": rec.quote_currency,
            "kind": rec.kind,
            "close_only": str(rec.close_only),
            "pdf_page": str(rec.pdf_page),
            "identity_status": (
                "AMBIGUOUS" if len(identity_keys(entries)) > 1 else "UNIQUE"
            ),
            "effective_from": args.effective_from.isoformat(),
            "source_url": args.source_url,
            "source_sha256": sha,
        }
        for ticker, entries in sorted(catalog.items())
        for rec in entries
    ]

    write_csv(
        args.out / "xtb_omi_catalog.csv", catalog_rows, list(catalog_rows[0])
    )
    write_csv(
        args.out / "xtb_omi_ambiguities.csv",
        [row for row in catalog_rows if row["identity_status"] == "AMBIGUOUS"],
        list(catalog_rows[0]),
    )
    write_csv(
        args.out / "portfolio_coverage.csv", coverage, list(coverage[0])
    )
    counts = Counter(row["status"] for row in coverage)
    ambiguous_symbols = sum(len(identity_keys(entries)) > 1 for entries in catalog.values())
    print(f"XTB catalog: {len(catalog)} unique ticker strings")
    print(f"Distinct candidate rows: {len(catalog_rows)}")
    print(f"Ambiguous catalog tickers: {ambiguous_symbols}")
    print(f"Historical instruments: {len(symbols)}")
    print(f"FOUND: {counts['FOUND']}")
    print(f"NOT_IN_CATALOG: {counts['NOT_IN_CATALOG']}")
    print(f"AMBIGUOUS: {counts['AMBIGUOUS']}")
    print(f"Unknown kind (full catalog): {sum(r.kind == 'UNKNOWN_FUND_KIND' for records in catalog.values() for r in records)}")
    print(f"Output: {args.out.resolve()}")
    print("Important: AMBIGUOUS tickers have NO resolved ISIN. FOUND is NOT a Yahoo mapping.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
