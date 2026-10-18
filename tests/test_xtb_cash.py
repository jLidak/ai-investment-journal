
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.ingestion.xtb_cash import (
    EXPECTED_HEADERS,
    read_cash_operations,
)


def create_test_workbook(
    tmp_path,
    invalid_headers=False,
    missing_amount=False,
):
    """Tworzy fikcyjny eksport XTB na potrzeby testów."""

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Cash Operations"

    sheet["B1"] = "DEMO_ACCOUNT"
    sheet["A3"] = "Date from (UTC)"

    headers = list(EXPECTED_HEADERS)

    if invalid_headers:
        headers[5] = "Wrong Amount Column"

    for column, header in enumerate(headers, start=1):
        sheet.cell(row=5, column=column, value=header)

    # Fikcyjny zakup akcji
    sheet.append([
        "Stock purchase",
        "Example Company",
        "EXAMPLE.US",
        "STC",
        datetime(2026, 9, 10, 12, 30),
        None if missing_amount else -125.5,
        "DEMO_001",
        "Test purchase",
        "STOCK",
        "POSITION_001",
    ])

    # Fikcyjna wpłata
    sheet.append([
        "Deposit",
        None,
        None,
        None,
        datetime(2026, 9, 11, 10, 0),
        200,
        "DEMO_002",
        "Test deposit",
        None,
        None,
    ])

    # Wiersz podsumowania nie jest operacją
    sheet.append(["Total"])

    file_path = tmp_path / "sample_xtb.xlsx"
    workbook.save(file_path)
    workbook.close()

    return file_path


def test_reads_operations(tmp_path):
    file_path = create_test_workbook(tmp_path)

    operations = read_cash_operations(file_path)

    assert len(operations) == 2

    purchase = operations[0]

    assert purchase.account_id == "DEMO_ACCOUNT"
    assert purchase.operation_type == "Stock purchase"
    assert purchase.ticker == "EXAMPLE.US"
    assert purchase.amount == Decimal("-125.5")
    assert purchase.time_utc.tzinfo == timezone.utc


def test_rejects_invalid_headers(tmp_path):
    file_path = create_test_workbook(
        tmp_path,
        invalid_headers=True,
    )

    with pytest.raises(
        ValueError,
        match="Nieoczekiwany format kolumn XTB",
    ):
        read_cash_operations(file_path)


def test_rejects_missing_amount(tmp_path):
    file_path = create_test_workbook(
        tmp_path,
        missing_amount=True,
    )

    with pytest.raises(
        ValueError,
        match="Brak ID lub kwoty",
    ):
        read_cash_operations(file_path)
