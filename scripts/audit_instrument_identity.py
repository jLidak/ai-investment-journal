
import argparse
import csv
from datetime import date
from pathlib import Path

from app.ingestion.xtb_cash import read_cash_operations
from app.ingestion.xtb_trade_comments import parse_trade_details
from app.market_data.identity import fetch_yahoo_identity
from app.market_data.symbols import suggest_yahoo_ticker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    if args.offset < 0 or not 1 <= args.limit <= 5:
        parser.error("Offset must be >= 0 and limit must be 1-5")

    files = sorted(
        f for f in Path("data/private").glob("*.xlsx")
        if not f.name.startswith("~$")
    )

    if not files:
        print("No XTB exports found")
        return 1

    names = {}

    for path in files:
        operations = read_cash_operations(path)

        for operation in operations:
            if operation.operation_type not in (
                "Stock purchase", "Stock sell"
            ):
                continue

            ticker = parse_trade_details(operation).ticker
            names.setdefault(ticker, set())

            if operation.instrument:
                names[ticker].add(operation.instrument.strip())

    symbols = sorted(names)
    selected = symbols[args.offset:args.offset + args.limit]

    if not selected:
        print("No instruments in selected range")
        return 1

    records = []

    for xtb_ticker in selected:
        candidate = suggest_yahoo_ticker(xtb_ticker)
        identity = None
        error_type = ""

        if candidate.yahoo_ticker:
            try:
                identity = fetch_yahoo_identity(
                    candidate.yahoo_ticker
                )
            except Exception as exc:
                error_type = type(exc).__name__

        if identity:
            status = "REVIEW_REQUIRED"
        elif candidate.yahoo_ticker is None:
            status = "UNMAPPED"
        else:
            status = "FETCH_ERROR"

        records.append({
            "xtb_ticker": xtb_ticker,
            "xtb_names": " | ".join(sorted(names[xtb_ticker])),
            "yahoo_ticker": candidate.yahoo_ticker or "",
            "yahoo_name": identity.name if identity else "",
            "yahoo_quote_type": (
                identity.quote_type if identity else ""
            ),
            "yahoo_currency": (
                identity.currency if identity else ""
            ),
            "yahoo_exchange": (
                identity.exchange if identity else ""
            ),
            "status": status,
            "error_type": error_type,
        })

        print(f"{xtb_ticker}: {status}")

    folder = Path("data/private/market_audit")
    folder.mkdir(parents=True, exist_ok=True)

    last = args.offset + len(selected) - 1
    path = folder / (
        f"identity_{date.today()}_"
        f"{args.offset:03d}_{last:03d}.csv"
    )

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file, fieldnames=list(records[0])
        )
        writer.writeheader()
        writer.writerows(records)

    print(f"\nSaved to: {path}")
    print("All identity matches require manual verification.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
