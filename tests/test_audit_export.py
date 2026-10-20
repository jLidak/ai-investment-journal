
import csv
from datetime import date

import pytest

from app.market_data.audit_export import AuditRow, save_audit_csv


def example_row(**changes):
    data = {
        "xtb_ticker": "DEMO.US",
        "yahoo_ticker": "DEMO",
        "status": "PRICES_AVAILABLE",
        "quote_currency": "USD",
        "exchange": "NMS",
        "sessions": 20,
        "date_from": date(2026, 9, 1),
        "date_to_exclusive": date(2026, 10, 1),
        "reason": "Prices available; identity not verified",
        "error_type": None,
    }

    data.update(changes)
    return AuditRow(**data)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def test_creates_csv_with_correct_values(tmp_path):
    path = tmp_path / "audits" / "batch.csv"

    save_audit_csv([example_row()], path)

    rows = read_csv(path)

    assert len(rows) == 1
    assert rows[0]["xtb_ticker"] == "DEMO.US"
    assert rows[0]["quote_currency"] == "USD"
    assert rows[0]["sessions"] == "20"
    assert rows[0]["date_from"] == "2026-09-01"


def test_saves_fetch_errors(tmp_path):
    path = tmp_path / "audit.csv"

    row = example_row(
        status="FETCH_ERROR",
        quote_currency=None,
        exchange=None,
        sessions=None,
        error_type="ValueError",
    )

    save_audit_csv([row], path)

    saved = read_csv(path)

    assert saved[0]["status"] == "FETCH_ERROR"
    assert saved[0]["error_type"] == "ValueError"


def test_rejects_empty_audit(tmp_path):
    with pytest.raises(ValueError, match="empty audit"):
        save_audit_csv([], tmp_path / "empty.csv")


def test_replaces_previous_batch(tmp_path):
    path = tmp_path / "audit.csv"

    save_audit_csv([example_row()], path)
    save_audit_csv([
        example_row(status="REVIEW_REQUIRED")
    ], path)

    saved = read_csv(path)

    assert len(saved) == 1
    assert saved[0]["status"] == "REVIEW_REQUIRED"
