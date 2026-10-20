
import pytest

from app.market_data.verified_instruments import (
    InstrumentKind,
    get_verified_instrument,
)


@pytest.mark.parametrize(
    "ticker, yahoo, isin",
    [
        ("4GLD.DE", "4GLD.DE", "DE000A0S9GB0"),
        ("ABE.PL", "ABE.WA", "PLAB00000019"),
        ("ABEA.DE", "ABEA.DE", "US02079K3059"),
    ],
)
def test_verified_mapping(ticker, yahoo, isin):
    result = get_verified_instrument(ticker)

    assert result.yahoo_ticker == yahoo
    assert result.isin == isin
    assert result.source_url.startswith("https://")


def test_gold_is_etc_not_equity():
    result = get_verified_instrument("4GLD.DE")

    assert result.kind == InstrumentKind.ETC


def test_normalizes_input():
    result = get_verified_instrument("  abe.pl  ")

    assert result.yahoo_ticker == "ABE.WA"


def test_rejects_unverified_instrument():
    with pytest.raises(ValueError, match="not verified"):
        get_verified_instrument("UNKNOWN.US")


@pytest.mark.parametrize(
    "xtb, yahoo, isin, kind, currency",
    [
        ("ALE.PL", "ALE.WA", "LU2237380790",
         InstrumentKind.EQUITY, "PLN"),
        ("AMZN.DE", "AMZ.DE", "US0231351067",
         InstrumentKind.EQUITY, "EUR"),
        ("ANAV.DE", "ANAV.DE", "IE000QDFFK00",
         InstrumentKind.ETF, "EUR"),
        ("CBF.PL", "CBF.WA", "PLR220000018",
         InstrumentKind.EQUITY, "PLN"),
        ("CDR.PL", "CDR.WA", "PLOPTTC00011",
         InstrumentKind.EQUITY, "PLN"),
    ],
)
def test_new_verified_instruments(
    xtb, yahoo, isin, kind, currency
):
    result = get_verified_instrument(xtb)

    assert result.yahoo_ticker == yahoo
    assert result.isin == isin
    assert result.kind == kind
    assert result.quote_currency == currency


def test_anav_is_eur_quoted_etf():
    result = get_verified_instrument("ANAV.DE")

    assert result.kind == InstrumentKind.ETF
    assert result.quote_currency == "EUR"
    assert "USD Acc" in result.name
