
"""Check which currently held XTB instruments have approved Yahoo mappings."""

import argparse
import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from app.ingestion.xtb_open import read_open_positions
from app.ingestion.xtb_cash import read_cash_operations
from app.market_data.portfolio_mapping import read_csv


FIELDS = [
    "xtb_ticker",
    "quantity",
    "accounts_with_position",
    "xtb_name",
    "isin",
    "quote_currency",
    "yahoo_candidate",
    "approved_yahoo_ticker",
    "identity_status",
    "mapping_readiness",
]


def normalize(value: str) -> str:
    return str(value or "").strip().upper()


def build_rows(
    positions_by_account: dict[str, dict[str, Decimal]],
    mapping_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Combine open positions and check mapping approval.

    No guessed Yahoo ticker is treated as approved.
    """

    if not positions_by_account:
        raise ValueError("No account snapshots provided")

    mapping = {}

    for row in mapping_rows:
        ticker = normalize(row.get("xtb_ticker"))

        if not ticker or ticker in mapping:
            raise ValueError(
                f"Empty or duplicated mapping ticker: {ticker!r}"
            )

        mapping[ticker] = row

    quantities = defaultdict(lambda: Decimal("0"))
    account_counts = defaultdict(int)

    for account_id, positions in positions_by_account.items():
        if not account_id.strip():
            raise ValueError("Empty account ID")

        seen = set()

        for raw_ticker, quantity in positions.items():
            ticker = normalize(raw_ticker)

            if not ticker or ticker in seen:
                raise ValueError(
                    f"Invalid or duplicated ticker in {account_id}"
                )

            seen.add(ticker)

            if (
                not quantity.is_finite()
                or quantity < Decimal("0")
            ):
                raise ValueError(
                    f"Invalid quantity for {ticker}"
                )

            if quantity == 0:
                continue

            quantities[ticker] += quantity
            account_counts[ticker] += 1

    results = []

    for ticker in sorted(quantities):
        entry = mapping.get(ticker)

        if entry is None:
            results.append({
                "xtb_ticker": ticker,
                "quantity": str(quantities[ticker]),
                "accounts_with_position": str(
                    account_counts[ticker]
                ),
                "xtb_name": "",
                "isin": "",
                "quote_currency": "",
                "yahoo_candidate": "",
                "approved_yahoo_ticker": "",
                "identity_status": "MISSING_FROM_MAPPING",
                "mapping_readiness": "NEEDS_REVIEW",
            })
            continue

        status = normalize(entry.get("status"))
        catalog_status = normalize(
            entry.get("xtb_catalog_status")
        )
        approved = normalize(
            entry.get("approved_yahoo_ticker")
        )
        isin = normalize(entry.get("isin"))
        currency = normalize(entry.get("xtb_currency"))
        yahoo_currency = normalize(
            entry.get("yahoo_currency")
        )

        approved_mapping = (
            status == "VERIFIED"
            and catalog_status == "FOUND"
            and bool(approved)
            and bool(isin)
            and bool(currency)
            and (
                not yahoo_currency
                or yahoo_currency == currency
            )
        )

        results.append({
            "xtb_ticker": ticker,
            "quantity": str(quantities[ticker]),
            "accounts_with_position": str(
                account_counts[ticker]
            ),
            "xtb_name": entry.get("xtb_name", ""),
            "isin": isin,
            "quote_currency": currency,
            "yahoo_candidate": entry.get(
                "yahoo_candidate", ""
            ),
            "approved_yahoo_ticker": (
                approved if approved_mapping else ""
            ),
            "identity_status": status,
            "mapping_readiness": (
                "MAPPING_APPROVED"
                if approved_mapping
                else "NEEDS_REVIEW"
            ),
        })

    return sorted(
        results,
        key=lambda row: (
            row["mapping_readiness"] == "MAPPING_APPROVED",
            row["xtb_ticker"],
        ),
    )


def write_reports(
    rows: list[dict[str, str]],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    full_path = output_dir / "current_positions_mapping.csv"
    review_path = output_dir / "current_positions_review.csv"

    reports = [
        (full_path, rows),
        (
            review_path,
            [
                row for row in rows
                if row["mapping_readiness"] == "NEEDS_REVIEW"
            ],
        ),
    ]

    for path, selected in reports:
        temporary = path.with_suffix(".tmp")

        with temporary.open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=FIELDS
            )
            writer.writeheader()
            writer.writerows(selected)

        temporary.replace(path)

    return full_path, review_path



def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--exports",
        type=Path,
        default=Path("data/private"),
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=Path(
            "data/private/portfolio_mapping/"
            "portfolio_mapping_draft.csv"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/private/portfolio_mapping"),
    )
    parser.add_argument(
        "--file",
        type=Path,
        action="append",
        dest="selected_files",
        help="Select one specific XLSX export. Repeat for multiple accounts.",
    )
    parser.add_argument(
        "--list-exports",
        action="store_true",
        help="List XTB exports and their accounts without building a report.",
    )

    args = parser.parse_args()

    if args.selected_files:
        files = args.selected_files
    else:
        files = sorted(
            path for path in args.exports.glob("*.xlsx")
            if not path.name.startswith("~$")
        )

    if not files:
        parser.error("No XTB XLSX exports found")


    snapshots = []

    for file_path in files:
        if not file_path.is_file():
            parser.error(f"File not found: {file_path}")

        if file_path.suffix.lower() != ".xlsx":
            parser.error(f"Expected XLSX: {file_path}")

        snapshot = read_open_positions(file_path)
        cash_operations = read_cash_operations(file_path)

        cash_account_ids = {
            operation.account_id
            for operation in cash_operations
        }

        if len(cash_account_ids) != 1:
            raise ValueError(
                f"Cannot determine a unique Cash Operations "
                f"account ID for {file_path.name}"
            )

        account_id = next(iter(cash_account_ids))
        open_account_id = snapshot.reported_account_id

        if account_id != open_account_id:
            # A filename is only corroborating evidence.
            # Never trust it as the sole account identifier.
            if f"_{account_id}_" not in file_path.name:
                raise ValueError(
                    f"Account identity conflict in {file_path.name}: "
                    f"Cash Operations={account_id}, "
                    f"Open Positions={open_account_id}. "
                    "Filename does not corroborate Cash Operations."
                )

            print(
                f"WARNING: Inconsistent account metadata "
                f"in {file_path.name}. "
                f"Using Cash Operations ID {account_id}; "
                f"Open Positions reports {open_account_id}."
            )

        snapshots.append(
            (file_path, snapshot, account_id)
        )


    if args.list_exports:
        print("\nAVAILABLE XTB EXPORTS")

        for file_path, snapshot, account_id in snapshots:
            print(
                f"Account: {account_id} | "
                f"Open Positions metadata: {snapshot.reported_account_id} | "
                f"Positions: {len(snapshot.quantities)} | "
                f"File: {file_path.name}"
            )

        return 0

    if not args.mapping.is_file():
        parser.error(
            f"Mapping report not found: {args.mapping}"
        )


    positions_by_account = {}
    source_files = {}

    for file_path, snapshot, account_id in snapshots:
        # account_id został wcześniej zweryfikowany
        # na podstawie Cash Operations i nazwy pliku.
        # NIE pobieramy go ponownie z Open Positions.

        if account_id in positions_by_account:
            previous = positions_by_account[account_id]

            if previous == snapshot.quantities:
                print(
                    f"Skipping identical position snapshot: "
                    f"{file_path.name}"
                )
                continue

            raise ValueError(
                f"Conflicting snapshots for account {account_id}.\n"
                f"First: {source_files[account_id]}\n"
                f"Second: {file_path}\n"
                "Different position snapshots for the same account."
            )

        positions_by_account[account_id] = snapshot.quantities
        source_files[account_id] = file_path

    mapping_rows = read_csv(args.mapping)


    rows = build_rows(
        positions_by_account,
        mapping_rows,
    )

    full_path, review_path = write_reports(
        rows, args.output
    )

    approved = sum(
        row["mapping_readiness"] == "MAPPING_APPROVED"
        for row in rows
    )

    unresolved = len(rows) - approved

    print("\nCURRENT POSITION MAPPING REPORT")
    print(f"Accounts: {len(positions_by_account)}")
    print(f"Open instruments: {len(rows)}")
    print(f"Approved mappings: {approved}")
    print(f"Mappings to review: {unresolved}")

    if unresolved:
        print("\nINSTRUMENTS TO REVIEW:")

        for row in rows:
            if row["mapping_readiness"] == "NEEDS_REVIEW":
                print(
                    f"  {row['xtb_ticker']}: "
                    f"{row['identity_status']}"
                )

    print(f"\nFull report: {full_path}")
    print(f"Review report: {review_path}")

    print(
        "\nMAPPING_APPROVED does not yet guarantee "
        "available or correct historical prices."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
