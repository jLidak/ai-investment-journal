
"""Stage 3.5k: separate, non-activating external identity review.

Preserve original XTB identities and all existing approvals.
Preview by default; --apply creates a NEW CSV without overwriting.
"""

import argparse
import csv
from pathlib import Path

from scripts.review_remaining_identity_evidence import (
    EVIDENCE,
    OUTPUT as EVIDENCE_REPORT,
)

SOURCE = Path("data/private/portfolio_mapping/portfolio_mapping_reviewed.csv")
OUTPUT = Path("data/private/portfolio_mapping/portfolio_mapping_external_review.csv")
STATUS = "EXTERNAL_IDENTITY_REVIEWED"
KINDS = {"DRAM.DE": "ETF", "RKLB.US": "EQUITY"}
ALLOWED_REGISTRY_EXCHANGES = {
    "DRAM.DE": frozenset({"GER", "XETRA"}),
    "RKLB.US": frozenset({"NMS", "NASDAQGS"}),
}

ADDITIONAL_FIELDS = [
    "external_identity_status",
    "reviewed_current_isin", "reviewed_quote_currency", "reviewed_kind",
    "reviewed_market", "reviewed_yahoo_ticker", "reviewed_eodhd_symbol",
    "previous_catalog_isin", "identity_exception",
    "external_identity_explanation", "external_issuer_source",
    "external_market_source",
]

REQUIRED_REGISTRY_FIELDS = {
    "xtb_ticker", "xtb_catalog_status", "status", "isin", "kind",
    "xtb_currency", "approved_yahoo_ticker", "yahoo_candidate",
    "yahoo_currency", "yahoo_exchange", "reason", "source_url",
}

REQUIRED_EVIDENCE_FIELDS = {
    "xtb_ticker", "xtb_catalog_isin", "current_isin", "currency",
    "yahoo_symbol", "eodhd_symbol", "identity_exception",
    "evidence_status", "issuer_source", "market_source", "explanation",
}


def read_table(path: Path, required: set[str]):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not required <= set(fields):
            raise ValueError(f"Invalid CSV columns: {path.name}")
        rows = list(reader)

    if not rows or any(
        None in row or any(value is None for value in row.values())
        for row in rows
    ):
        raise ValueError(f"Invalid or empty CSV: {path.name}")

    return fields, rows


def keyed(rows: list[dict[str, str]], *, exact: bool):
    result = {}

    for row in rows:
        ticker = str(row.get("xtb_ticker") or "").strip().upper()
        if not ticker or ticker in result:
            raise ValueError("Empty or duplicate XTB ticker")
        result[ticker] = row

    if not set(EVIDENCE) <= set(result) or (
        exact and set(result) != set(EVIDENCE)
    ):
        raise ValueError("Expected exactly DRAM.DE and RKLB.US in evidence")

    return result


def prepare_review(
    registry: list[dict[str, str]],
    evidence: list[dict[str, str]],
):
    """Prepare an independent registry without changing input rows."""
    original = keyed(registry, exact=False)
    proofs = keyed(evidence, exact=True)
    updates = {}

    for ticker, expected in EVIDENCE.items():
        current = original[ticker]
        proof = proofs[ticker]

        expected_proof = {
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
        }

        for field, wanted in expected_proof.items():
            if proof.get(field) != wanted:
                raise ValueError(f"{ticker}: evidence mismatch in {field}")

        expected_registry = {
            "xtb_catalog_status": expected["catalog_status"],
            "status": expected["registry_status"],
            "isin": expected["catalog_isin"],
        }

        for field, wanted in expected_registry.items():
            if current.get(field) != wanted:
                raise ValueError(f"{ticker}: registry mismatch in {field}")

        if current.get("approved_yahoo_ticker", "").strip():
            raise ValueError(f"{ticker}: mapping already approved unexpectedly")

        if ticker == "RKLB.US":
            if current.get("xtb_currency") != "USD":
                raise ValueError("RKLB.US: historical XTB currency changed")
            if current.get("kind") != "EQUITY":
                raise ValueError("RKLB.US: historical XTB instrument kind changed")

        if ticker == "DRAM.DE" and (
            current.get("xtb_currency") or current.get("kind")
        ):
            raise ValueError("DRAM.DE: absent XTB catalogue identity was modified")

        if current.get("yahoo_currency", "") not in ("", expected["currency"]):
            raise ValueError(f"{ticker}: conflicting Yahoo currency")

        registry_exchange = str(
            current.get("yahoo_exchange") or ""
        ).strip().upper()

        if registry_exchange and registry_exchange not in ALLOWED_REGISTRY_EXCHANGES[ticker]:
            raise ValueError(f"{ticker}: conflicting Yahoo exchange")

        updates[ticker] = {
            "external_identity_status": STATUS,
            "reviewed_current_isin": expected["current_isin"],
            "reviewed_quote_currency": expected["currency"],
            "reviewed_kind": KINDS[ticker],
            "reviewed_market": expected["market"],
            "reviewed_yahoo_ticker": expected["yahoo"],
            "reviewed_eodhd_symbol": expected["eodhd"],
            "previous_catalog_isin": expected["catalog_isin"],
            "identity_exception": expected["note"],
            "external_identity_explanation": expected["explanation"],
            "external_issuer_source": expected["issuer_source"],
            "external_market_source": expected["market_source"],
        }

    output = []
    for old in registry:
        ticker = old["xtb_ticker"].strip().upper()
        item = dict(old)
        item.update({field: "" for field in ADDITIONAL_FIELDS})

        if ticker in updates:
            item.update(updates[ticker])

        output.append(item)

    return output


def write_new(
    path: Path,
    fields: list[str],
    rows: list[dict[str, str]],
):
    """Create only a new file; never overwrite a previous review."""
    if path.exists():
        raise FileExistsError(f"Output exists: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Non-activating external identity review"
    )
    parser.add_argument("--registry", type=Path, default=SOURCE)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE_REPORT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--apply", action="store_true",
        help="Create a new CSV; never overwrite",
    )
    args = parser.parse_args()

    if (
        args.registry.resolve() == args.output.resolve()
        or args.evidence.resolve() == args.output.resolve()
    ):
        parser.error("Output must differ from both input files")

    for input_path in (args.registry, args.evidence):
        if not input_path.is_file():
            parser.error(f"Missing private input: {input_path}")

    try:
        fields, originals = read_table(
            args.registry, REQUIRED_REGISTRY_FIELDS
        )
        _, proofs = read_table(
            args.evidence, REQUIRED_EVIDENCE_FIELDS
        )

        if set(fields) & set(ADDITIONAL_FIELDS):
            raise ValueError(
                "Registry already contains external review columns"
            )

        result = prepare_review(originals, proofs)

        if args.apply:
            write_new(
                args.output,
                fields + ADDITIONAL_FIELDS,
                result,
            )

    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    print("\nEXTERNAL IDENTITY REVIEW — DRAM / RKLB")

    for ticker in EVIDENCE:
        row = next(
            x for x in result
            if x["xtb_ticker"].upper() == ticker
        )
        print(
            f"  {ticker}: {row['external_identity_status']} | "
            f"current ISIN={row['reviewed_current_isin']} | "
            f"catalog ISIN={row['isin'] or '-'}"
        )

    print(f"Registry entries preserved: {len(result)}")
    print(
        "Existing VERIFIED mappings preserved: "
        f"{sum(r['status'] == 'VERIFIED' for r in result)}"
    )
    print(
        f"Output: {args.output if args.apply else 'PREVIEW ONLY'}"
    )
    print(
        "NOT ACTIVATED: approved_yahoo_ticker and original XTB "
        "fields unchanged; no SQLite/P&L writes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
