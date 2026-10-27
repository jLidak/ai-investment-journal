
"""Diagnose account identifiers across XTB export worksheets."""

import re
from pathlib import Path

from app.ingestion.xtb_cash import read_cash_operations
from app.ingestion.xtb_open import read_open_positions


def main() -> int:
    files = sorted(
        path for path in Path("data/private").glob("*.xlsx")
        if not path.name.startswith("~$")
    )

    if not files:
        print("No XTB exports found")
        return 1

    print("\nXTB ACCOUNT ID DIAGNOSTICS\n")

    for file_path in files:
        filename_match = re.search(
            r"_(\d{8})_",
            file_path.name,
        )

        filename_id = (
            filename_match.group(1)
            if filename_match
            else "UNKNOWN"
        )

        cash_operations = read_cash_operations(file_path)
        open_positions = read_open_positions(file_path)

        cash_account_ids = sorted({
            operation.account_id
            for operation in cash_operations
        })

        open_account_id = open_positions.reported_account_id

        print(f"File: {file_path.name}")
        print(f"  Filename account ID: {filename_id}")
        print(f"  Cash Operations IDs: {cash_account_ids}")
        print(f"  Open Positions ID:   {open_account_id}")
        print(
            f"  Open instruments:    "
            f"{len(open_positions.quantities)}"
        )

        consistent = (
            len(cash_account_ids) == 1
            and cash_account_ids[0] == open_account_id
            and filename_id == open_account_id
        )

        print(
            f"  Status: {'CONSISTENT' if consistent else 'CHECK_REQUIRED'}"
        )
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
