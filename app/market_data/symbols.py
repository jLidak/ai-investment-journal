
from dataclasses import dataclass


YAHOO_SYMBOL_OVERRIDES = {
    "AMZN.DE": "AMZ.DE",
}


@dataclass(frozen=True)
class SymbolCandidate:
    xtb_ticker: str
    yahoo_ticker: str | None


def suggest_yahoo_ticker(xtb_ticker: str) -> SymbolCandidate:
    """Suggest a Yahoo ticker for an XTB instrument.

    The result is not yet verified against market data.
    """

    if not isinstance(xtb_ticker, str) or not xtb_ticker.strip():
        raise ValueError("XTB ticker cannot be empty")

    ticker = xtb_ticker.strip().upper()
    base, separator, market = ticker.rpartition(".")

    if not separator or not base or not market:
        raise ValueError(
            "Expected XTB ticker format: SYMBOL.MARKET"
        )

    if ticker in YAHOO_SYMBOL_OVERRIDES:
        return SymbolCandidate(
            xtb_ticker=ticker,
            yahoo_ticker=YAHOO_SYMBOL_OVERRIDES[ticker],
        )

    yahoo_ticker = None

    if market == "US":
        # Some share classes require manual mapping.
        if "." not in base:
            yahoo_ticker = base

    elif market == "PL":
        yahoo_ticker = f"{base}.WA"

    elif market == "DE":
        yahoo_ticker = ticker

    elif market == "UK":
        yahoo_ticker = f"{base}.L"

    return SymbolCandidate(
        xtb_ticker=ticker,
        yahoo_ticker=yahoo_ticker,
    )
