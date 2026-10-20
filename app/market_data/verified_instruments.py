
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

    "ALE.PL": VerifiedInstrument(
        xtb_ticker="ALE.PL",
        yahoo_ticker="ALE.WA",
        name="Allegro.eu S.A.",
        isin="LU2237380790",
        kind=InstrumentKind.EQUITY,
        quote_currency="PLN",
        source_url=(
            "https://about.allegro.eu/static-files/"
            "9de50d8d-fee1-4681-84b5-1d6d668d22f6"
        ),
    ),
    "AMZN.DE": VerifiedInstrument(
        xtb_ticker="AMZN.DE",
        yahoo_ticker="AMZ.DE",
        name="Amazon.com Inc.",
        isin="US0231351067",
        kind=InstrumentKind.EQUITY,
        quote_currency="EUR",
        source_url=(
            "https://live.deutsche-boerse.com/"
            "equity/amazon-com-inc"
        ),
    ),
    "ANAV.DE": VerifiedInstrument(
        xtb_ticker="ANAV.DE",
        yahoo_ticker="ANAV.DE",
        name="BNP Paribas Easy II Nasdaq 100 UCITS ETF USD Acc",
        isin="IE000QDFFK00",
        kind=InstrumentKind.ETF,
        quote_currency="EUR",
        source_url=(
            "https://live.deutsche-boerse.com/en/etf/"
            "bnp-paribas-easy-ii-nasdaq-100-ucits-etf-usd-acc"
        ),
    ),
    "CBF.PL": VerifiedInstrument(
        xtb_ticker="CBF.PL",
        yahoo_ticker="CBF.WA",
        name="cyber_Folks S.A.",
        isin="PLR220000018",
        kind=InstrumentKind.EQUITY,
        quote_currency="PLN",
        source_url=(
            "https://investors.cyberfolks.pl/raporty/"
            "raport-biezacy-32-2026/"
        ),
    ),
    "CDR.PL": VerifiedInstrument(
        xtb_ticker="CDR.PL",
        yahoo_ticker="CDR.WA",
        name="CD PROJEKT RED S.A.",
        isin="PLOPTTC00011",
        kind=InstrumentKind.EQUITY,
        quote_currency="PLN",
        source_url=(
            "https://www.bmv.com.mx/en/issuers/"
            "profile/CDR-34818"
        ),
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
