
"""SQLite cache for historical market prices.

A successful fetch is not proof of complete trading-session coverage.
Ranges with no returned prices remain explicitly distinguishable
from ranges where prices were obtained.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class PriceIdentity:
    xtb_ticker: str
    source: str
    source_symbol: str
    isin: str
    quote_currency: str

    def __post_init__(self) -> None:
        values = (
            self.xtb_ticker,
            self.source,
            self.source_symbol,
            self.isin,
            self.quote_currency,
        )

        if any(
            not isinstance(value, str) or not value.strip()
            for value in values
        ):
            raise ValueError("Price identity fields cannot be empty")


def _key(identity: PriceIdentity) -> tuple[str, ...]:
    return (
        identity.xtb_ticker,
        identity.source,
        identity.source_symbol,
        identity.isin,
        identity.quote_currency,
    )


def _validate_range(start: date, end: date) -> None:
    if type(start) is not date or type(end) is not date:
        raise ValueError("Expected date objects")

    if start >= end:
        raise ValueError("End must be later than start")


def _validate_timestamp(when: datetime) -> datetime:
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("Timestamp must have a timezone")

    return when.astimezone(timezone.utc)


def open_cache(path: Path) -> sqlite3.Connection:
    """Create or open the local SQLite database."""
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_closes (
                xtb_ticker TEXT NOT NULL,
                source TEXT NOT NULL,
                source_symbol TEXT NOT NULL,
                isin TEXT NOT NULL,
                quote_currency TEXT NOT NULL,
                session TEXT NOT NULL,
                close TEXT NOT NULL,

                PRIMARY KEY (
                    xtb_ticker,
                    source,
                    source_symbol,
                    isin,
                    quote_currency,
                    session
                )
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS checked_price_windows (
                xtb_ticker TEXT NOT NULL,
                source TEXT NOT NULL,
                source_symbol TEXT NOT NULL,
                isin TEXT NOT NULL,
                quote_currency TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_exclusive TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('FETCHED', 'NOT_RETURNED')
                ),
                checked_at TEXT NOT NULL,
                quality_flags TEXT NOT NULL,

                PRIMARY KEY (
                    xtb_ticker,
                    source,
                    source_symbol,
                    isin,
                    quote_currency,
                    start_date,
                    end_exclusive,
                    status
                )
            )
        """)

        connection.commit()
    except Exception:
        connection.close()
        raise

    return connection


def read_closes(
    connection: sqlite3.Connection,
    identity: PriceIdentity,
    start: date,
    end: date,
) -> dict[date, Decimal]:
    """Return actual stored prices, never synthetic placeholders."""
    _validate_range(start, end)

    rows = connection.execute(
        """
        SELECT session, close
        FROM historical_closes
        WHERE xtb_ticker = ?
          AND source = ?
          AND source_symbol = ?
          AND isin = ?
          AND quote_currency = ?
          AND session >= ?
          AND session < ?
        ORDER BY session
        """,
        (*_key(identity), start.isoformat(), end.isoformat()),
    ).fetchall()

    return {
        date.fromisoformat(session): Decimal(close)
        for session, close in rows
    }


def _write_window(
    connection: sqlite3.Connection,
    identity: PriceIdentity,
    start: date,
    end: date,
    status: str,
    checked_at: str,
    flags: str,
) -> None:
    if start >= end:
        return

    connection.execute(
        """
        INSERT INTO checked_price_windows (
            xtb_ticker,
            source,
            source_symbol,
            isin,
            quote_currency,
            start_date,
            end_exclusive,
            status,
            checked_at,
            quality_flags
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT (
            xtb_ticker,
            source,
            source_symbol,
            isin,
            quote_currency,
            start_date,
            end_exclusive,
            status
        )
        DO UPDATE SET
            checked_at = excluded.checked_at,
            quality_flags = excluded.quality_flags
        """,
        (
            *_key(identity),
            start.isoformat(),
            end.isoformat(),
            status,
            checked_at,
            flags,
        ),
    )


def record_fetch(
    connection: sqlite3.Connection,
    identity: PriceIdentity,
    start: date,
    end: date,
    prices: Mapping[date, Decimal],
    *,
    checked_at: datetime | None = None,
    quality_flags: str = "",
) -> None:
    """Save a validated fetch atomically.

    Prices must already have passed the market-data validator.
    Conflicting historical prices are never silently overwritten.
    """
    _validate_range(start, end)

    when = _validate_timestamp(
        checked_at or datetime.now(timezone.utc)
    )
    stamp = when.isoformat()

    normalized: dict[date, Decimal] = {}

    for day, raw_price in prices.items():
        if type(day) is not date or not start <= day < end:
            raise ValueError("Price date outside requested range")

        try:
            price = Decimal(str(raw_price))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError(
                f"Invalid closing price for {day}"
            ) from exc

        if not price.is_finite() or price <= 0:
            raise ValueError(
                f"Non-positive or non-finite close for {day}"
            )

        normalized[day] = price

    with connection:
        for day, price in sorted(normalized.items()):
            session = day.isoformat()

            existing = connection.execute(
                """
                SELECT close
                FROM historical_closes
                WHERE xtb_ticker = ?
                  AND source = ?
                  AND source_symbol = ?
                  AND isin = ?
                  AND quote_currency = ?
                  AND session = ?
                """,
                (*_key(identity), session),
            ).fetchone()

            if existing is not None:
                if Decimal(existing[0]) != price:
                    raise ValueError(
                        f"Conflicting cached price for "
                        f"{identity.source_symbol} on {session}"
                    )
                continue

            connection.execute(
                """
                INSERT INTO historical_closes (
                    xtb_ticker,
                    source,
                    source_symbol,
                    isin,
                    quote_currency,
                    session,
                    close
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (*_key(identity), session, str(price)),
            )

        if not normalized:
            _write_window(
                connection,
                identity,
                start,
                end,
                "NOT_RETURNED",
                stamp,
                quality_flags,
            )
            return

        first = min(normalized)
        last_exclusive = max(normalized) + timedelta(days=1)

        if start < first:
            _write_window(
                connection,
                identity,
                start,
                first,
                "NOT_RETURNED",
                stamp,
                quality_flags,
            )

        _write_window(
            connection,
            identity,
            first,
            last_exclusive,
            "FETCHED",
            stamp,
            quality_flags,
        )

        if last_exclusive < end:
            _write_window(
                connection,
                identity,
                last_exclusive,
                end,
                "NOT_RETURNED",
                stamp,
                quality_flags,
            )


def pending_windows(
    connection: sqlite3.Connection,
    identity: PriceIdentity,
    start: date,
    end: date,
    *,
    now: datetime | None = None,
    missing_ttl_days: int = 7,
) -> list[tuple[date, date]]:
    """Find ranges requiring a new request.

    FETCHED means that Yahoo was queried successfully in this range.
    It does NOT assert that every exchange session has a quote.

    NOT_RETURNED is a temporary negative cache. Once its TTL expires,
    the range can be requested again.
    """
    _validate_range(start, end)

    if missing_ttl_days < 1:
        raise ValueError("Missing-data TTL must be positive")

    current = _validate_timestamp(
        now or datetime.now(timezone.utc)
    )
    ttl = timedelta(days=missing_ttl_days)

    rows = connection.execute(
        """
        SELECT
            start_date,
            end_exclusive,
            status,
            checked_at
        FROM checked_price_windows
        WHERE xtb_ticker = ?
          AND source = ?
          AND source_symbol = ?
          AND isin = ?
          AND quote_currency = ?
          AND start_date < ?
          AND end_exclusive > ?
        ORDER BY start_date, end_exclusive
        """,
        (*_key(identity), end.isoformat(), start.isoformat()),
    ).fetchall()

    checked_intervals = []

    for raw_start, raw_end, status, raw_checked_at in rows:
        if status == "NOT_RETURNED":
            timestamp = datetime.fromisoformat(raw_checked_at)

            if timestamp.tzinfo is None:
                continue

            age = current - timestamp.astimezone(timezone.utc)

            if age >= ttl:
                continue

        covered_start = max(
            start, date.fromisoformat(raw_start)
        )
        covered_end = min(
            end, date.fromisoformat(raw_end)
        )

        if covered_start < covered_end:
            checked_intervals.append(
                (covered_start, covered_end)
            )

    checked_intervals.sort()
    pending = []
    cursor = start

    for covered_start, covered_end in checked_intervals:
        if covered_end <= cursor:
            continue

        if covered_start > cursor:
            pending.append((cursor, covered_start))

        cursor = max(cursor, covered_end)

    if cursor < end:
        pending.append((cursor, end))

    return pending
