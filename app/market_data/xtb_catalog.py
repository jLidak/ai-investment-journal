"""Import the official XTB OMI specification table from an offline PDF.

Keep broker instrument identity separate from Yahoo ticker resolution.
The PDF is supplied explicitly and its version must be recorded by the caller.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber


SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9.+_-]*\.[A-Z]{2,3}\*?")
ISIN_RE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
CURRENCY_RE = re.compile(r"[A-Z]{3}")
SECTION_END = "Tabela Specyfikacji Praw Ułamkowych"


@dataclass(frozen=True)
class XtbCatalogRecord:
    xtb_ticker: str
    name: str
    isin: str
    quote_currency: str
    kind: str                 # EQUITY, ETF, ETN, ETC, or UNKNOWN_FUND_KIND
    close_only: bool
    pdf_page: int


def clean(value: object) -> str:
    """Whitespace/newline cleanup for cells returned by pdfplumber."""
    return " ".join(str(value or "").split()).strip()


def parse_table_row(row: list[object], page_number: int) -> XtbCatalogRecord | None:
    """Parse a data row; ignore headers and blank rows, fail on malformed identifiers."""
    if not row or len(row) < 4:
        return None

    raw_symbol = clean(row[0]).upper()
    if not SYMBOL_RE.fullmatch(raw_symbol):
        return None

    name = clean(row[1])
    isin = clean(row[2]).upper()
    currency = clean(row[3]).upper()

    if not name or not ISIN_RE.fullmatch(isin) or not CURRENCY_RE.fullmatch(currency):
        raise ValueError(f"Invalid PDF row on page {page_number}: {raw_symbol}: {row[:4]!r}")

    # The OMI table's fifth column is an explicit product class in ETF/ETN/ETC rows.
    # Stock rows put minimum-order size there (for example '1 EUR').
    class_cell = clean(row[4]).upper() if len(row) > 4 else ""
    if class_cell in {"ETF", "ETFS"}:
        kind = "ETF"
    elif class_cell in {"ETN", "ETNS"}:
        kind = "ETN"
    elif class_cell in {"ETC", "ETCS"}:
        kind = "ETC"
    elif class_cell.startswith("1 ") or re.match(r"^\d+(?:[,.]\d+)?\s+[A-Z]{3}$", class_cell):
        kind = "EQUITY"
    else:
        # Never silently classify unknown instrument types as equities.
        kind = "UNKNOWN_FUND_KIND"

    return XtbCatalogRecord(
        xtb_ticker=raw_symbol.rstrip("*"),
        name=name,
        isin=isin,
        quote_currency=currency,
        kind=kind,
        close_only=raw_symbol.endswith("*"),
        pdf_page=page_number,
    )


def identity_keys(records: tuple[XtbCatalogRecord, ...] | list[XtbCatalogRecord]) -> set[tuple[str, str, str]]:
    """An XTB ticker alone is NOT a permanent identifier.

    A security identity is an ISIN; currency and kind are extra listing checks.
    """
    return {(r.isin, r.quote_currency, r.kind) for r in records}


def parse_omi_pdf(path: str | Path) -> dict[str, tuple[XtbCatalogRecord, ...]]:
    """Read all OMI securities; preserve reused/ambiguous XTB tickers.

    For example ATS.PL* appears for two unrelated CLOSE ONLY instruments.
    The caller MUST NOT select one automatically.
    """
    results: dict[str, list[XtbCatalogRecord]] = {}
    saw_header = False

    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            # Exclude the subsequent fractional-rights specification.
            stop_here = False
            working_page = page
            if saw_header and page.page_number > 1:
                hits = page.search(SECTION_END)
                if hits:
                    top = min(hit["top"] for hit in hits)
                    stop_here = True
                    if top > 0:
                        working_page = page.crop((0, 0, page.width, top))

            tables = working_page.extract_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                }
            )
            for table in tables:
                for row in table:
                    normalized = [clean(cell).lower() for cell in (row or [])]
                    if len(normalized) >= 4 and normalized[:4] == [
                        "symbol", "nazwa", "isin", "waluta"
                    ]:
                        saw_header = True
                        continue
                    if not saw_header:
                        continue
                    entry = parse_table_row(row, page.page_number)
                    if entry is None:
                        continue
                    group = results.setdefault(entry.xtb_ticker, [])
                    # Drop exact repeated rows while retaining independent records
                    # with different ISIN, currency, kind, close-only or names.
                    if not any(
                        (r.name, r.isin, r.quote_currency, r.kind, r.close_only)
                        == (entry.name, entry.isin, entry.quote_currency,
                            entry.kind, entry.close_only)
                        for r in group
                    ):
                        group.append(entry)

            if stop_here:
                break

    if not results:
        raise ValueError("No OMI rows parsed: PDF format may have changed")
    return {ticker: tuple(entries) for ticker, entries in results.items()}


def build_coverage(
    symbols: set[str],
    catalog: dict[str, tuple[XtbCatalogRecord, ...]],
) -> list[dict[str, str]]:
    """Join by exact XTB ticker. Ambiguous symbols never get a single ISIN."""
    records = []
    for ticker in sorted(symbols):
        entries = catalog.get(ticker, ())
        identities = identity_keys(entries)
        if not entries:
            status = "NOT_IN_CATALOG"
            item = None
        elif len(identities) > 1:
            status = "AMBIGUOUS"
            item = None
        else:
            status = "FOUND"
            # Same instrument can occur in a table multiple times; prefer an
            # active listing when its ISIN/currency/type are identical.
            item = next((e for e in entries if not e.close_only), entries[0])
        records.append({
            "xtb_ticker": ticker,
            "status": status,
            "name": item.name if item else "",
            "isin": item.isin if item else "",
            "quote_currency": item.quote_currency if item else "",
            "kind": item.kind if item else "",
            "close_only": str(item.close_only) if item else "",
            "pdf_page": str(item.pdf_page) if item else "",
            "candidate_count": str(len(identities)),
            "candidate_isins": " | ".join(sorted({r.isin for r in entries})),
        })
    return records
