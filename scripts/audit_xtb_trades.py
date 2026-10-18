
from pathlib import Path

from app.ingestion.trade_audit import audit_trades
from app.ingestion.xtb_cash import read_cash_operations


def main():
    data_directory = Path("data/private")

    files = sorted(
        path for path in data_directory.glob("*.xlsx")
        if not path.name.startswith("~$")
    )

    if not files:
        print("Nie znaleziono plików XLSX w data/private/")
        return 1

    total_failed = 0

    for number, file_path in enumerate(files, start=1):
        operations = read_cash_operations(file_path)
        audit = audit_trades(operations)

        print(f"\nRaport {number}")
        print(f"Operacje: {audit.total_operations}")
        print(f"Zakupy i sprzedaże: {audit.trade_operations}")
        print(f"Poprawnie sparsowane: {audit.parsed_trades}")
        print(f"Błędy: {audit.failed_trades}")

        for reason, count in sorted(audit.errors.items()):
            print(f"  {reason}: {count}")

        total_failed += audit.failed_trades

    print(f"\nŁączna liczba błędów: {total_failed}")

    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
