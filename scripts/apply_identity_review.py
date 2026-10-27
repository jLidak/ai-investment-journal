"""Apply externally reviewed XTB -> Yahoo identity mappings.

This script creates a separate CSV. It never overwrites
the original portfolio_mapping_draft.csv.

VERIFIED means that instrument identity was reviewed.
It does not guarantee valid historical price data.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


SOURCE = Path(
    "data/private/portfolio_mapping/portfolio_mapping_draft.csv"
)

OUTPUT = Path(
    "data/private/portfolio_mapping/portfolio_mapping_reviewed.csv"
)


# XTB ticker: (expected ISIN, Yahoo ticker, identity evidence URL)
REVIEWED = {
    "CRI.PL": (
        "PLCRTCH00017", "CRI.WA",
        "https://creotech.pl/en/investors/stocks",
    ),
    "DEAM.DE": (
        "IE00BHJYDV33", "DEAM.DE",
        "https://live.deutsche-boerse.com/en/etf/invesco-mdax-ucits-etf",
    ),
    "DNP.PL": (
        "PLDINPL00011", "DNP.WA",
        "https://grupadino.pl/gpw/",
    ),
    "DUOL.US": (
        "US26603R1068", "DUOL",
        "https://investors.duolingo.com/resources",
    ),
    "ETFBM40TR.PL": (
        "PLBETF400025", "ETFBM40TR.WA",
        "https://betaetf.pl/en/our-funds",
    ),
    "ETFBS80TR.PL": (
        "PLBSWTR00014", "ETFBS80TR.WA",
        "https://betaetf.pl/en/our-funds",
    ),
    "ETFBW20TR.PL": (
        "PLBTETF00015", "ETFBW20TR.WA",
        "https://betaetf.pl/en/our-funds",
    ),
    "FB2A.DE": (
        "US30303M1027", "FB2A.DE",
        "https://live.deutsche-boerse.com/equity/meta-platforms?mic=XETR",
    ),
    "GPW.PL": (
        "PLGPW0000017", "GPW.WA",
        "https://www.mdm.pl/bm/g/D-QpHXGmbEaeN5d9RTdCVg",
    ),
    "KAP.UK": (
        "US63253R2013", "KAP.L",
        "https://www.londonstockexchange.com/en/quote/equity/KAP/joint-stock-company-national-atomic-company-kazatomprom",
    ),
    "LGN.US": (
        "US52476L1098", "LGN",
        "https://www.sec.gov/Archives/edgar/data/1938307/000119312526340346/xslSCHEDULE_13G_X02/primary_doc.xml",
    ),
    "MARA.US": (
        "US5657881067", "MARA",
        "https://www.sec.gov/Archives/edgar/data/1507605/000210012126000534/xslSCHEDULE_13G_X02/primary_doc.xml",
    ),
    "MSF.DE": (
        "US5949181045", "MSF.DE",
        "https://live.deutsche-boerse.com/equity/microsoft-corp",
    ),
    "MSFT.US": (
        "US5949181045", "MSFT",
        "https://www.microsoft.com/en-us/investor/FAQ",
    ),
    "NVD.DE": (
        "US67066G1040", "NVD.DE",
        "https://live.deutsche-boerse.com/equity/nvidia-corp",
    ),
    "ORCL.US": (
        "US68389X1054", "ORCL",
        "https://investor.oracle.com/faq/default.aspx",
    ),
    "PEO.PL": (
        "PLPEKAO00016", "PEO.WA",
        "https://bossa.pl/notowania/instrumenty/PEO",
    ),
    "PGE.PL": (
        "PLPGER000010", "PGE.WA",
        "https://www.gkpge.pl/dla-inwestorow/akcje",
    ),
    "PKO.PL": (
        "PLPKO0000016", "PKO.WA",
        "https://www.pkobp.pl/relacje-inwestorskie/faq",
    ),
    "PL.US": (
        "US72703X1063", "PL",
        "https://infomemo.theocc.com/infomemos?number=49697",
    ),
    "PTX.DE": (
        "US69608A1088", "PTX.DE",
        "https://live.deutsche-boerse.com/equity/palantir-technologies-inc",
    ),
    "PZU.PL": (
        "PLPZU0000011", "PZU.WA",
        "https://www.pzu.pl/relacje-inwestorskie/akcje-i-obligacje",
    ),
    "RDW.US": (
        "US75776W1036", "RDW",
        "https://www.sec.gov/Archives/edgar/data/1819810/000201238326002870/xslSCHEDULE_13G_X02/primary_doc.xml",
    ),
    "RRU.DE": (
        "GB00B63H8491", "RRU.DE",
        "https://live.deutsche-boerse.com/equity/rolls-royce-holdings-plc",
    ),
    "RRX.US": (
        "US7587501039", "RRX",
        "https://www.sec.gov/Archives/edgar/data/82811/000090514826004206/xslSCHEDULE_13G_X02/primary_doc.xml",
    ),
    "SCW.PL": (
        "PLSCWAY00018", "SCW.WA",
        "https://scanway.pl/relacje-inwestorskie/akcje/",
    ),
    "SMSN.UK": (
        "US7960508882", "SMSN.L",
        "https://www.samsung.com/global/ir/stock-information/listing-Info/",
    ),
    "SNT.PL": (
        "PLSNKTK00019", "SNT.WA",
        "https://www.money.pl/gielda/spolki-gpw/plsnktk00019,emitent,1.html",
    ),
    "SXRV.DE": (
        "IE00B53SZB19", "SXRV.DE",
        "https://live.deutsche-boerse.com/en/etf/ishares-nasdaq-100-ucits-etf-usd-acc",
    ),
    "TSLA.DE": (
        "US88160R1014", "TL0.DE",
        "https://live.deutsche-boerse.com/equity/tesla-inc",
    ),
    "TSLA.US": (
        "US88160R1014", "TSLA",
        "https://ir.tesla.com/contact-us",
    ),
    "VVSM.DE": (
        "IE00BMC38736", "VVSM.DE",
        "https://live.deutsche-boerse.com/en/etf/vaneck-semiconductor-ucits-etf-usd-acc",
    ),
    "XB0T.DE": (
        "IE00BLCHJB90", "XB0T.DE",
        "https://live.deutsche-boerse.com/en/etf/global-x-robotics-artificial-intelligence-ucits-etf",
    ),
    "XTB.PL": (
        "PLXTRDM00011", "XTB.WA",
        "https://esg.xtb.com/en/governance/",
    ),
    "ZPDU.DE": (
        "IE00BWBXMB69", "ZPDU.DE",
        "https://live.deutsche-boerse.com/etf/state-street-spdr-s-p-u-s-utilities-select-sector-ucits-etf-acc",
    ),
}


# These entries are deliberately not approved.
BLOCKED = {
    "DRAM.DE": (
        "NOT_IN_CATALOG",
        "Xetra listing found, but XTB catalogue identity is missing. "
        "Confirm XTB ISIN before approval.",
        "https://live.deutsche-boerse.com/en/etf/defiance-memory-ucits-etf?currency=EUR",
    ),
    "SPX.DE": (
        "NOT_IN_CATALOG",
        "Xetra listing found, but XTB catalogue identity is missing. "
        "Confirm XTB ISIN before approval.",
        "https://live.deutsche-boerse.com/equity/spacex",
    ),
    "RKLB.US": (
        "CANDIDATE",
        "XTB catalogue contains the pre-reorganization ISIN. "
        "Rocket Lab changed its CUSIP in May 2025 while retaining "
        "the RKLB ticker. Review identifier continuity.",
        "https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-264",
    ),
}


REQUIRED_COLUMNS = {
    "xtb_ticker",
    "xtb_catalog_status",
    "isin",
    "xtb_currency",
    "yahoo_candidate",
    "approved_yahoo_ticker",
    "yahoo_name",
    "yahoo_currency",
    "status",
    "reason",
    "source_url",
}


def norm(value: object) -> str:
    return str(value or "").strip().upper()


def expected_currency(ticker: str) -> str:
    if ticker.endswith(".PL"):
        return "PLN"
    if ticker.endswith(".DE"):
        return "EUR"
    if ticker.endswith(".US"):
        return "USD"
    if ticker.endswith(".UK"):
        return "USD"
    raise ValueError(f"Unsupported ticker suffix: {ticker}")


def apply_review(
    rows: list[dict[str, str]],
) -> tuple[int, int]:
    """Modify in-memory rows only; return (new, already_verified)."""

    by_ticker = {}

    for row in rows:
        ticker = norm(row.get("xtb_ticker"))

        if not ticker or ticker in by_ticker:
            raise ValueError(
                f"Empty or duplicate XTB ticker: {ticker!r}"
            )

        by_ticker[ticker] = row

    required_tickers = set(REVIEWED) | set(BLOCKED)
    missing = required_tickers - set(by_ticker)

    if missing:
        raise ValueError(
            f"Missing expected tickers: {sorted(missing)}"
        )

    new_approvals = 0
    existing_approvals = 0

    # Validate everything before changing any row.
    for ticker, (isin, yahoo, _) in REVIEWED.items():
        row = by_ticker[ticker]
        currency = expected_currency(ticker)

        checks = {
            "xtb_catalog_status": ("FOUND", norm(
                row.get("xtb_catalog_status")
            )),
            "isin": (isin, norm(row.get("isin"))),
            "xtb_currency": (
                currency, norm(row.get("xtb_currency"))
            ),
            "yahoo_candidate": (
                yahoo, norm(row.get("yahoo_candidate"))
            ),
            "yahoo_currency": (
                currency, norm(row.get("yahoo_currency"))
            ),
        }

        for field, (expected, actual) in checks.items():
            if expected != actual:
                raise ValueError(
                    f"{ticker}: {field} mismatch. "
                    f"Expected {expected!r}, got {actual!r}"
                )

        if not row.get("yahoo_name", "").strip():
            raise ValueError(
                f"{ticker}: missing Yahoo instrument name"
            )

        status = norm(row.get("status"))
        approved = norm(row.get("approved_yahoo_ticker"))

        if status not in {"CANDIDATE", "VERIFIED"}:
            raise ValueError(
                f"{ticker}: unexpected status {status!r}"
            )

        if approved and approved != yahoo:
            raise ValueError(
                f"{ticker}: conflicting approved ticker {approved}"
            )

        if status == "VERIFIED" and approved != yahoo:
            raise ValueError(
                f"{ticker}: VERIFIED without matching approval"
            )

    for ticker, (expected_status, _, _) in BLOCKED.items():
        row = by_ticker[ticker]

        if norm(row.get("status")) != expected_status:
            raise ValueError(
                f"{ticker}: blocker status changed; inspect manually"
            )

        if norm(row.get("approved_yahoo_ticker")):
            raise ValueError(
                f"{ticker}: blocked instrument is already approved"
            )

    if norm(by_ticker["RKLB.US"].get("isin")) != "US7731221062":
        raise ValueError(
            "RKLB.US: XTB ISIN changed; review the new identity first"
        )

    # All validation passed. Apply identity decisions.
    for ticker, (_, yahoo, source_url) in REVIEWED.items():
        row = by_ticker[ticker]

        if norm(row.get("status")) == "VERIFIED":
            existing_approvals += 1
            continue

        row["approved_yahoo_ticker"] = yahoo
        row["status"] = "VERIFIED"
        row["reason"] = (
            "Reviewed instrument identity, ISIN, listing and "
            "quote currency. Historical Yahoo prices not yet audited."
        )
        row["source_url"] = source_url
        new_approvals += 1

    for ticker, (_, reason, source_url) in BLOCKED.items():
        row = by_ticker[ticker]
        row["reason"] = reason
        row["source_url"] = source_url

    return new_approvals, existing_approvals


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--source",
        type=Path,
        default=SOURCE,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create reviewed CSV; without this flag only preview.",
    )

    args = parser.parse_args()

    if not args.source.is_file():
        parser.error(f"Source does not exist: {args.source}")

    if args.source.resolve() == args.output.resolve():
        parser.error("Source and output must be different files")

    with args.source.open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        reader = csv.DictReader(handle)

        fields = reader.fieldnames or []
        missing_columns = REQUIRED_COLUMNS - set(fields)

        if missing_columns:
            parser.error(
                f"Missing CSV columns: {sorted(missing_columns)}"
            )

        rows = list(reader)

    new_count, existing_count = apply_review(rows)

    approved = sum(
        norm(row.get("status")) == "VERIFIED"
        for row in rows
    )

    print("\nIDENTITY REVIEW")
    print(f"Registry rows: {len(rows)}")
    print(f"New approvals: {new_count}")
    print(f"Already approved in review set: {existing_count}")
    print(f"Total approved registry rows: {approved}")
    print(f"Blocked current holdings: {len(BLOCKED)}")

    if not args.apply:
        print("\nPREVIEW ONLY - no files changed.")
        print("Run again with --apply to create the reviewed CSV.")
        return 0

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Exclusive creation: existing output is never overwritten.
    try:
        with args.output.open(
            "x", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fields,
            )
            writer.writeheader()
            writer.writerows(rows)
    except FileExistsError:
        parser.error(
            f"Output already exists: {args.output}. "
            "Inspect it or choose another --output path."
        )

    print(f"\nCreated: {args.output}")
    print("Original mapping draft was not modified.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())