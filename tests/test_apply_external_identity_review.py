
"""Offline tests for non-activating mapping review."""

import copy
import csv
import sys

import pytest

from scripts.apply_external_identity_review import (
    ADDITIONAL_FIELDS,
    REQUIRED_REGISTRY_FIELDS,
    STATUS,
    main,
    prepare_review,
    read_table,
    write_new,
)
from scripts.review_remaining_identity_evidence import EVIDENCE


def fixture_rows():
    registry = [
        {
            "xtb_ticker": "DRAM.DE",
            "xtb_catalog_status": "NOT_IN_CATALOG",
            "status": "NOT_IN_CATALOG",
            "isin": "",
            "kind": "",
            "xtb_currency": "",
            "approved_yahoo_ticker": "",
            "yahoo_candidate": "DRAM.DE",
            "yahoo_currency": "",
            "yahoo_exchange": "",
            "reason": "Missing in XTB",
            "source_url": "",
        },
        {
            "xtb_ticker": "RKLB.US",
            "xtb_catalog_status": "FOUND",
            "status": "CANDIDATE",
            "isin": "US7731221062",
            "kind": "EQUITY",
            "xtb_currency": "USD",
            "approved_yahoo_ticker": "",
            "yahoo_candidate": "RKLB",
            "yahoo_currency": "USD",
            "yahoo_exchange": "NMS",
            "reason": "Historical CUSIP",
            "source_url": "",
        },
        {
            "xtb_ticker": "MSFT.US",
            "xtb_catalog_status": "FOUND",
            "status": "VERIFIED",
            "isin": "US5949181045",
            "kind": "EQUITY",
            "xtb_currency": "USD",
            "approved_yahoo_ticker": "MSFT",
            "yahoo_candidate": "MSFT",
            "yahoo_currency": "USD",
            "yahoo_exchange": "NMS",
            "reason": "Already approved",
            "source_url": "https://example.org",
        },
    ]

    evidence = []
    for ticker, expected in EVIDENCE.items():
        evidence.append({
            "xtb_ticker": ticker,
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
        })

    return registry, evidence


def get(rows, ticker):
    return next(x for x in rows if x["xtb_ticker"] == ticker)


def test_external_review_creates_two_records_without_activation():
    registry, evidence = fixture_rows()
    result = prepare_review(registry, evidence)

    assert len(result) == 3

    for ticker in EVIDENCE:
        row = get(result, ticker)
        assert row["external_identity_status"] == STATUS
        assert row["approved_yahoo_ticker"] == ""
        assert row["status"] != "VERIFIED"

    assert get(result, "DRAM.DE")["reviewed_current_isin"] == "IE000CEUZ052"
    assert get(result, "RKLB.US")["reviewed_current_isin"] == "US7731211089"


def test_xtb_catalog_and_other_approvals_preserved():
    registry, evidence = fixture_rows()
    old = copy.deepcopy(registry)
    result = prepare_review(registry, evidence)

    assert registry == old

    for before, after in zip(old, result):
        assert all(after[k] == v for k, v in before.items())

    assert get(result, "DRAM.DE")["isin"] == ""
    assert get(result, "RKLB.US")["isin"] == "US7731221062"
    assert get(result, "MSFT.US")["approved_yahoo_ticker"] == "MSFT"
    assert get(result, "MSFT.US")["external_identity_status"] == ""


@pytest.mark.parametrize("ticker,column,value", [
    ("DRAM.DE", "current_isin", "IE9999999999"),
    ("RKLB.US", "xtb_catalog_isin", "US7731211089"),
    ("RKLB.US", "evidence_status", "VERIFIED"),
    ("DRAM.DE", "issuer_source", "https://untrusted.invalid"),
    ("RKLB.US", "explanation", "Different description"),
    ("RKLB.US", "eodhd_symbol", "FAKE.US"),
])
def test_rejects_modified_evidence(ticker, column, value):
    registry, evidence = fixture_rows()
    get(evidence, ticker)[column] = value

    with pytest.raises(ValueError, match="evidence mismatch"):
        prepare_review(registry, evidence)


@pytest.mark.parametrize("ticker,column,value", [
    ("DRAM.DE", "xtb_catalog_status", "FOUND"),
    ("RKLB.US", "isin", "US7731211089"),
    ("RKLB.US", "status", "VERIFIED"),
    ("RKLB.US", "approved_yahoo_ticker", "RKLB"),
    ("RKLB.US", "kind", "ETF"),
    ("DRAM.DE", "xtb_currency", "EUR"),
    ("RKLB.US", "yahoo_exchange", "GER"),
])
def test_rejects_modified_registry(ticker, column, value):
    registry, evidence = fixture_rows()
    get(registry, ticker)[column] = value

    with pytest.raises(ValueError):
        prepare_review(registry, evidence)


def test_missing_duplicate_or_extra_evidence_rejected():
    registry, evidence = fixture_rows()

    with pytest.raises(ValueError):
        prepare_review(registry, evidence[:1])

    with pytest.raises(ValueError):
        prepare_review(
            registry,
            evidence + [copy.deepcopy(evidence[0])],
        )

    with pytest.raises(ValueError):
        prepare_review(
            registry,
            evidence + [{"xtb_ticker": "EXTRA"}],
        )


def test_duplicate_registry_rejected():
    registry, evidence = fixture_rows()

    with pytest.raises(ValueError, match="duplicate"):
        prepare_review(
            registry + [copy.deepcopy(registry[0])],
            evidence,
        )


def test_new_csv_is_written_with_no_overwrite(tmp_path):
    registry, evidence = fixture_rows()
    rows = prepare_review(registry, evidence)
    path = tmp_path / "data" / "private" / "mapping.csv"
    fields = list(registry[0]) + ADDITIONAL_FIELDS

    write_new(path, fields, rows)

    with path.open(encoding="utf-8-sig", newline="") as handle:
        result = list(csv.DictReader(handle))

    assert result == rows

    with pytest.raises(FileExistsError):
        write_new(path, fields, rows)

    assert path.read_bytes()


def test_read_table_rejects_bad_columns_and_missing_values(tmp_path):
    bad = tmp_path / "bad.csv"

    bad.write_text(
        "xtb_ticker,xtb_ticker\nDRAM.DE,DRAM.DE\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="columns"):
        read_table(bad, {"xtb_ticker"})

    bad.write_text(
        "xtb_ticker,status\nDRAM.DE\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invalid or empty"):
        read_table(bad, {"xtb_ticker", "status"})


def test_preview_then_apply_creates_only_separate_registry(
    tmp_path,
    monkeypatch,
    capsys,
):
    registry, evidence = fixture_rows()
    regpath = tmp_path / "reviewed.csv"
    evpath = tmp_path / "evidence.csv"
    destination = tmp_path / "external.csv"

    for path, rows in (
        (regpath, registry),
        (evpath, evidence),
    ):
        with path.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(rows[0]),
            )
            writer.writeheader()
            writer.writerows(rows)

    args = [
        "audit",
        "--registry", str(regpath),
        "--evidence", str(evpath),
        "--output", str(destination),
    ]

    monkeypatch.setattr(sys, "argv", args)
    assert main() == 0
    assert not destination.exists()
    assert "PREVIEW ONLY" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", args + ["--apply"])
    assert main() == 0
    assert destination.exists()

    _, old = read_table(regpath, REQUIRED_REGISTRY_FIELDS)
    _, new = read_table(destination, REQUIRED_REGISTRY_FIELDS)

    assert len(old) == len(new)
    assert old == registry
    assert get(new, "RKLB.US")["isin"] == "US7731221062"

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2


def test_input_output_path_alias_blocked(tmp_path, monkeypatch):
    registry, _ = fixture_rows()
    regpath = tmp_path / "reviewed.csv"

    with regpath.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(registry[0]),
        )
        writer.writeheader()
        writer.writerows(registry)

    monkeypatch.setattr(sys, "argv", [
        "audit",
        "--registry", str(regpath),
        "--evidence", str(regpath),
        "--output", str(regpath),
        "--apply",
    ])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2

@pytest.mark.parametrize("ticker, exchange", [
    ("DRAM.DE", "XETRA"),
    ("RKLB.US", "NasdaqGS"),
])
def test_accepts_observed_registry_exchange_labels_without_rewriting(
    ticker, exchange
):
    registry, evidence = fixture_rows()
    get(registry, ticker)["yahoo_exchange"] = exchange

    reviewed = prepare_review(registry, evidence)

    assert get(reviewed, ticker)["external_identity_status"] == STATUS
    assert get(reviewed, ticker)["yahoo_exchange"] == exchange
    assert get(reviewed, ticker)["approved_yahoo_ticker"] == ""