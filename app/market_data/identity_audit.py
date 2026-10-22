"""Repeatable XTB/Yahoo identity audit with a persistent metadata cache.

A candidate is NOT an identity confirmation. Only an independently reviewed
registry entry can be reported as VERIFIED.
"""

import csv
import hashlib
import json
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from app.market_data.identity import YahooIdentity, fetch_yahoo_identity
from app.market_data.verified_instruments import VERIFIED_INSTRUMENTS


EXPECTED_CURRENCIES = {"US": "USD", "PL": "PLN", "DE": "EUR"}
REVIEW_STATUSES = {"CANDIDATE", "REVIEW_REQUIRED", "CONFLICT", "UNMAPPED", "FETCH_ERROR"}


@dataclass(frozen=True)
class IdentityAuditRow:
    xtb_ticker: str
    xtb_names: str
    yahoo_ticker: str
    yahoo_name: str
    yahoo_quote_type: str
    yahoo_currency: str
    yahoo_exchange: str
    registry_isin: str
    status: str
    reason: str
    error_type: str
    metadata_source: str


def _cache_path(cache_dir: Path, symbol: str) -> Path:
    # Digest rather than raw ticker: no path traversal and safe on Windows.
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


def _read_cache(cache_dir: Path, symbol: str, ttl_days: int) -> YahooIdentity | None:
    try:
        payload = json.loads(_cache_path(cache_dir, symbol).read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if fetched_at.tzinfo is None or payload["symbol"] != symbol:
            return None
        age = datetime.now(timezone.utc) - fetched_at
        if age < timedelta(0) or age >= timedelta(days=ttl_days):
            return None
        identity = YahooIdentity(**payload["identity"])
        return identity if identity.symbol == symbol else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _write_cache(cache_dir: Path, identity: YahooIdentity) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, identity.symbol)
    payload = {
        "symbol": identity.symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "identity": asdict(identity),
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def get_yahoo_identity_cached(
    symbol: str,
    cache_dir: Path,
    *,
    ttl_days: int = 30,
    refresh: bool = False,
    retries: int = 2,
    delay: float = 1.0,
    fetcher: Callable[[str], YahooIdentity] = fetch_yahoo_identity,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[YahooIdentity, str]:
    """Fetch once per TTL; retain successes across interrupted audit runs."""
    if ttl_days < 1 or retries < 0 or delay < 0:
        raise ValueError("Invalid cache/fetch settings")

    if not refresh:
        cached = _read_cache(cache_dir, symbol, ttl_days)
        if cached is not None:
            return cached, "CACHE"

    for attempt in range(retries + 1):
        # Sequential requests + exponential delay after failures.
        if delay:
            sleeper(delay * (2 ** attempt))
        try:
            identity = fetcher(symbol)
            if identity.symbol != symbol:
                raise ValueError("Yahoo returned another ticker")
            _write_cache(cache_dir, identity)
            return identity, "LIVE"
        except Exception:
            if attempt == retries:
                raise

    raise AssertionError("Unreachable")


def make_identity_audit_row(
    xtb_ticker: str,
    xtb_names: set[str],
    yahoo_ticker: str | None,
    identity: YahooIdentity | None,
    *,
    metadata_source: str = "",
    error_type: str = "",
) -> IdentityAuditRow:
    registered = VERIFIED_INSTRUMENTS.get(xtb_ticker)
    reason = ""

    if not yahoo_ticker:
        status = "UNMAPPED"
        reason = "No candidate symbol"
    elif identity is None:
        status = "FETCH_ERROR"
        reason = "Yahoo metadata unavailable"
    elif identity.symbol != yahoo_ticker:
        status = "CONFLICT"
        reason = "Yahoo returned a different symbol"
    elif registered is not None:
        if registered.yahoo_ticker != yahoo_ticker:
            status = "CONFLICT"
            reason = "Yahoo ticker differs from reviewed registry"
        elif identity.currency != registered.quote_currency:
            status = "CONFLICT"
            reason = "Yahoo currency differs from reviewed registry"
        else:
            status = "VERIFIED"
            reason = "Reviewed registry entry; quote currency matches"
    else:
        market = xtb_ticker.rpartition(".")[2]
        expected = EXPECTED_CURRENCIES.get(market)
        if not expected or identity.currency != expected or not identity.exchange:
            status = "REVIEW_REQUIRED"
            reason = "Missing/unexpected market currency or exchange (UK needs unit review)"
        else:
            status = "CANDIDATE"
            reason = "Plausible listing; ISIN and identity NOT confirmed"

    return IdentityAuditRow(
        xtb_ticker=xtb_ticker,
        xtb_names=" | ".join(sorted(xtb_names)),
        yahoo_ticker=yahoo_ticker or "",
        yahoo_name=identity.name if identity else "",
        yahoo_quote_type=(identity.quote_type or "") if identity else "",
        yahoo_currency=(identity.currency or "") if identity else "",
        yahoo_exchange=(identity.exchange or "") if identity else "",
        registry_isin=registered.isin if registered else "",
        status=status,
        reason=reason,
        error_type=error_type,
        metadata_source=metadata_source,
    )


def write_identity_reports(
    rows: list[IdentityAuditRow], report_dir: Path, *, prefix: str = "identity"
) -> tuple[Path, Path]:
    """Write one complete report and one review queue; replace atomically."""
    report_dir.mkdir(parents=True, exist_ok=True)
    columns = [field.name for field in fields(IdentityAuditRow)]
    destinations = [
        (report_dir / f"{prefix}_all.csv", rows),
        (report_dir / f"{prefix}_review.csv", [r for r in rows if r.status in REVIEW_STATUSES]),
    ]
    for path, selected_rows in destinations:
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(asdict(row) for row in selected_rows)
        temporary.replace(path)
    return destinations[0][0], destinations[1][0]
