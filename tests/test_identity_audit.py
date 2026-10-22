import csv

from app.market_data.identity import YahooIdentity
from app.market_data.identity_audit import (
    get_yahoo_identity_cached,
    make_identity_audit_row,
    write_identity_reports,
)


def fake_identity(symbol, currency="USD", exchange="NMS"):
    return YahooIdentity(
        symbol=symbol,
        name="Example Inc.",
        quote_type="EQUITY",
        currency=currency,
        exchange=exchange,
    )


def test_cache_avoids_repeated_requests(tmp_path):
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        return fake_identity(symbol)

    kwargs = dict(fetcher=fetch, delay=0, sleeper=lambda _: None)
    first, source1 = get_yahoo_identity_cached("MSFT", tmp_path, **kwargs)
    second, source2 = get_yahoo_identity_cached("MSFT", tmp_path, **kwargs)

    assert first == second
    assert (source1, source2) == ("LIVE", "CACHE")
    assert calls == ["MSFT"]


def test_refresh_reloads_cache(tmp_path):
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        return fake_identity(symbol)

    get_yahoo_identity_cached("MSFT", tmp_path, fetcher=fetch, delay=0)
    get_yahoo_identity_cached("MSFT", tmp_path, fetcher=fetch, delay=0, refresh=True)
    assert calls == ["MSFT", "MSFT"]


def test_retries_then_recovers(tmp_path):
    attempts = []

    def fetch(symbol):
        attempts.append(symbol)
        if len(attempts) == 1:
            raise ConnectionError("temporary")
        return fake_identity(symbol)

    result, source = get_yahoo_identity_cached(
        "MSFT", tmp_path, retries=1, delay=0,
        fetcher=fetch, sleeper=lambda _: None,
    )
    assert result.symbol == "MSFT"
    assert source == "LIVE"
    assert len(attempts) == 2


def test_registry_can_confirm_matching_quote():
    row = make_identity_audit_row(
        "ABE.PL", {"AB S.A."}, "ABE.WA",
        fake_identity("ABE.WA", "PLN", "WSE"),
    )
    assert row.status == "VERIFIED"
    assert row.registry_isin == "PLAB00000019"


def test_unregistered_symbol_stays_candidate():
    row = make_identity_audit_row(
        "MSFT.US", {"Microsoft"}, "MSFT", fake_identity("MSFT"),
    )
    assert row.status == "CANDIDATE"
    assert "NOT confirmed" in row.reason


def test_currency_mismatch_is_review_required():
    row = make_identity_audit_row(
        "DEMO.DE", {"Example"}, "DEMO.DE", fake_identity("DEMO.DE", "USD"),
    )
    assert row.status == "REVIEW_REQUIRED"


def test_registry_currency_conflict_is_not_verified():
    row = make_identity_audit_row(
        "ABE.PL", {"AB S.A."}, "ABE.WA", fake_identity("ABE.WA", "USD"),
    )
    assert row.status == "CONFLICT"


def test_unknown_or_failed_symbol():
    unmapped = make_identity_audit_row("DEMO.BE", set(), None, None)
    failed = make_identity_audit_row("DEMO.US", set(), "DEMO", None, error_type="ValueError")
    assert unmapped.status == "UNMAPPED"
    assert failed.status == "FETCH_ERROR"


def test_reports_include_full_and_review(tmp_path):
    verified = make_identity_audit_row(
        "ABE.PL", {"AB"}, "ABE.WA", fake_identity("ABE.WA", "PLN"),
    )
    candidate = make_identity_audit_row(
        "MSFT.US", {"Microsoft"}, "MSFT", fake_identity("MSFT"),
    )
    all_csv, review_csv = write_identity_reports([verified, candidate], tmp_path)

    def read(path):
        with path.open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))

    assert len(read(all_csv)) == 2
    assert [row["xtb_ticker"] for row in read(review_csv)] == ["MSFT.US"]
