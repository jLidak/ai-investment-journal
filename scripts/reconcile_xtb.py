
from decimal import Decimal
from pathlib import Path

from app.analytics.holdings import rebuild_holdings
from app.ingestion.xtb_cash import read_cash_operations
from app.ingestion.xtb_movements import extract_position_movements
from app.ingestion.xtb_open import read_open_positions


def main() -> int:
    files = sorted(
        path for path in Path("data/private").glob("*.xlsx")
        if not path.name.startswith("~$")
    )

    if not files:
        print("No XLSX files found")
        return 1

    total_mismatches = 0
    metadata_warnings = 0

    for index, file_path in enumerate(files, start=1):
        operations = read_cash_operations(file_path)

        account_ids = {op.account_id for op in operations}

        if len(account_ids) != 1:
            raise ValueError(
                "Expected one account per XTB export"
            )

        account_id = next(iter(account_ids))

        movements = extract_position_movements(operations)
        calculated = rebuild_holdings(movements)

        # This file represents one account.
        reconstructed = {
            ticker: quantity
            for (acc_id, ticker), quantity in calculated.items()
            if acc_id == account_id
        }

        snapshot = read_open_positions(file_path)
        reported = snapshot.quantities

        all_tickers = set(reconstructed) | set(reported)

        mismatches = [
            ticker
            for ticker in all_tickers
            if reconstructed.get(ticker, Decimal("0"))
            != reported.get(ticker, Decimal("0"))
        ]

        print(f"\nReport {index}")
        print(f"Reconstructed positions: {len(reconstructed)}")
        print(f"XTB open positions: {len(reported)}")
        print(f"Quantity mismatches: {len(mismatches)}")

        if snapshot.reported_account_id != account_id:
            metadata_warnings += 1
            print("Warning: inconsistent account metadata")

        total_mismatches += len(mismatches)

    print(f"\nTotal quantity mismatches: {total_mismatches}")
    print(f"Account metadata warnings: {metadata_warnings}")

    return 1 if total_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
