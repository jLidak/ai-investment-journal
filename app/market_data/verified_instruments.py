
from dataclasses import dataclass
from enum import Enum


class InstrumentKind(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    ETC = "ETC"


@dataclass(frozen=True)
class VerifiedInstrument:
    xtb_ticker: str
    yahoo_ticker: str
    name: str
    isin: str
    kind: InstrumentKind
    quote_currency: str
    source_url: str


VERIFIED_INSTRUMENTS = {
    "4GLD.DE": VerifiedInstrument(
        xtb_ticker="4GLD.DE",
        yahoo_ticker="4GLD.DE",
        name="Xetra-Gold",
        isin="DE000A0S9GB0",
        kind=InstrumentKind.ETC,
        quote_currency="EUR",
        source_url="https://www.xetra-gold.com/en/investing-in-gold/",
    ),
    "ABE.PL": VerifiedInstrument(
        xtb_ticker="ABE.PL",
        yahoo_ticker="ABE.WA",
        name="AB S.A.",
        isin="PLAB00000019",
        kind=InstrumentKind.EQUITY,
        quote_currency="PLN",
        source_url="https://www.parkiet.com/firmy/228991-ab",
    ),
    "ABEA.DE": VerifiedInstrument(
        xtb_ticker="ABEA.DE",
        yahoo_ticker="ABEA.DE",
        name="Alphabet Inc. Class A",
        isin="US02079K3059",
        kind=InstrumentKind.EQUITY,
        quote_currency="EUR",
        source_url="https://live.deutsche-boerse.com/aktie/alphabet-inc-class-a",
    ),
}


def get_verified_instrument(xtb_ticker: str) -> VerifiedInstrument:
    ticker = xtb_ticker.strip().upper()

    try:
        return VERIFIED_INSTRUMENTS[ticker]
    except KeyError as exc:
        raise ValueError(
            f"Instrument not verified: {ticker}"
        ) from exc
