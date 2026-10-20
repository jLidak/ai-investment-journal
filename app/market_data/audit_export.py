
import csv
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class AuditRow:
    xtb_ticker: str
    yahoo_ticker: str | None
    status: str
    quote_currency: str | None
    exchange: str | None
    sessions: int | None
    date_from: date
    date_to_exclusive: date
    reason: str
    error_type: str | None


def save_audit_csv(
    rows: list[AuditRow],
    output_path: Path,
) -> Path:
    """Save a batch of quote reviews to CSV."""

    if not rows:
        raise ValueError("Cannot save an empty audit")

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[field.name for field in fields(AuditRow)],
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(asdict(row))

    return output_path
