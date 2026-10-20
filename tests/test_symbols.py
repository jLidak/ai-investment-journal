
import pytest

from app.market_data.symbols import suggest_yahoo_ticker


@pytest.mark.parametrize(
    "xtb, expected",
    [
        ("MSFT.US", "MSFT"),
        ("GPW.PL", "GPW.WA"),
        ("BMW.DE", "BMW.DE"),
        ("LSEG.UK", "LSEG.L"),
    ],
)
def test_maps_supported_markets(xtb, expected):
    result = suggest_yahoo_ticker(xtb)

    assert result.xtb_ticker == xtb
    assert result.yahoo_ticker == expected


def test_unknown_market_requires_review():
    result = suggest_yahoo_ticker("DEMO.BE")

    assert result.yahoo_ticker is None


def test_complex_share_class_requires_review():
    result = suggest_yahoo_ticker("BRK.B.US")

    assert result.yahoo_ticker is None

def test_msft_return_ticker():
    result = suggest_yahoo_ticker("MSFT.US")

    assert result.yahoo_ticker == "MSFT"

@pytest.mark.parametrize("invalid", ["", "DEMO"])
def test_rejects_invalid_ticker(invalid):
    with pytest.raises(ValueError):
        suggest_yahoo_ticker(invalid)



def test_german_amazon_symbol_override():
    result = suggest_yahoo_ticker("AMZN.DE")

    assert result.xtb_ticker == "AMZN.DE"
    assert result.yahoo_ticker == "AMZ.DE"
