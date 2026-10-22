import pytest

from app.market_data.xtb_catalog import (
    build_coverage, parse_omi_pdf, parse_table_row
)


def test_parses_stock():
    row = ["ABE.PL", "AB SA", "PLAB00000019", "PLN", "1 PLN"]
    found = parse_table_row(row, page_number=3)
    assert found.xtb_ticker == "ABE.PL"
    assert found.kind == "EQUITY"
    assert found.isin == "PLAB00000019"


def test_parses_fund_and_close_only():
    row = ["4GLD.DE*", "Xetra-Gold", "DE000A0S9GB0", "EUR", "ETCs"]
    found = parse_table_row(row, page_number=100)
    assert found.xtb_ticker == "4GLD.DE"
    assert found.kind == "ETC"
    assert found.close_only


def test_malformed_isin_fails_loudly():
    with pytest.raises(ValueError, match="Invalid PDF row"):
        parse_table_row(["ABE.PL", "AB SA", "BAD", "PLN", "1 PLN"], 3)


def test_unknown_category_not_assumed_equity():
    found = parse_table_row(
        ["TEST.DE", "Demo Fund", "IE000QDFFK00", "EUR", "Something else"], 10
    )
    assert found.kind == "UNKNOWN_FUND_KIND"


def test_coverage_does_not_guess_yahoo_symbols():
    rec = parse_table_row(["AMZN.DE", "Amazon", "US0231351067", "EUR", "1 EUR"], 4)
    rows = build_coverage({"AMZN.DE", "NOTLISTED.US"}, {"AMZN.DE": (rec,)})
    assert rows[0]["xtb_ticker"] == "AMZN.DE"
    assert rows[0]["status"] == "FOUND"
    assert rows[0]["isin"] == "US0231351067"
    assert rows[1]["status"] == "NOT_IN_CATALOG"
    assert rows[1]["isin"] == ""


class FakePage:
    def __init__(self, num, rows, stop_at=None):
        self.page_number = num
        self.rows = rows
        self.stop_at = stop_at
        self.width = 100
        self.height = 100

    def search(self, text):
        return [{"top": self.stop_at}] if self.stop_at else []

    def crop(self, bbox):
        # Simulate a page that contains an OMI table followed by fractional rights.
        return FakePage(self.page_number, [self.rows[0]])

    def extract_tables(self, **kwargs):
        return [self.rows]


class FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_pdf_sections_and_fractional_exclusion(monkeypatch):
    header = ["Symbol", "Nazwa", "ISIN", "Waluta", "Minimalna wartość zlecenia"]
    stock = ["ABE.PL", "AB SA", "PLAB00000019", "PLN", "1 PLN"]
    etf = ["ANAV.DE", "Nasdaq 100 UCITS ETF", "IE000QDFFK00", "EUR", "ETFs"]
    fake = FakePDF([
        FakePage(1, []),
        FakePage(2, [header, stock]),
        FakePage(3, [etf, ["FRACTION.DE", "Fraction", "US0231351067", "EUR", "ETFs"]], stop_at=50),
    ])
    monkeypatch.setattr("app.market_data.xtb_catalog.pdfplumber.open", lambda _: fake)
    results = parse_omi_pdf("dummy.pdf")
    assert set(results) == {"ABE.PL", "ANAV.DE"}
    assert results["ANAV.DE"][0].kind == "ETF"


def test_pdf_conflicting_duplicate_keeps_both_ats(monkeypatch):
    header = ["Symbol", "Nazwa", "ISIN", "Waluta", "Minimalna wartość zlecenia"]
    air_lease = ["ATS.PL*", "CLOSE ONLY / Air Lease Corp - class A", "US00912X3026", "USD", "1 USD"]
    atlantis = ["ATS.PL*", "CLOSE ONLY / Atlantis Se", "EE3100063686", "PLN", "1 PLN"]
    fake = FakePDF([FakePage(13, [header, air_lease, atlantis])])
    monkeypatch.setattr("app.market_data.xtb_catalog.pdfplumber.open", lambda _: fake)

    catalog = parse_omi_pdf("dummy.pdf")
    assert len(catalog["ATS.PL"]) == 2
    assert all(r.close_only for r in catalog["ATS.PL"])
    coverage = build_coverage({"ATS.PL"}, catalog)
    assert coverage[0]["status"] == "AMBIGUOUS"
    assert coverage[0]["isin"] == ""
    assert coverage[0]["candidate_count"] == "2"
    assert "US00912X3026" in coverage[0]["candidate_isins"]
    assert "EE3100063686" in coverage[0]["candidate_isins"]


def test_same_isin_duplicate_is_unambiguous(monkeypatch):
    header = ["Symbol", "Nazwa", "ISIN", "Waluta", "Minimalna wartość zlecenia"]
    normal = ["ABC.PL", "Example SA", "PLAB00000019", "PLN", "1 PLN"]
    close = ["ABC.PL*", "CLOSE ONLY / Example SA", "PLAB00000019", "PLN", "1 PLN"]
    fake = FakePDF([FakePage(4, [header, normal, close, normal])])
    monkeypatch.setattr("app.market_data.xtb_catalog.pdfplumber.open", lambda _: fake)
    catalog = parse_omi_pdf("dummy.pdf")
    assert len(catalog["ABC.PL"]) == 2
    coverage = build_coverage({"ABC.PL"}, catalog)
    assert coverage[0]["status"] == "FOUND"
    assert coverage[0]["isin"] == "PLAB00000019"
    assert coverage[0]["close_only"] == "False"


def test_ambiguous_by_currency_with_same_isin():
    usd = parse_table_row(["TEST.DE", "Example", "US0231351067", "USD", "1 USD"], 4)
    eur = parse_table_row(["TEST.DE", "Example", "US0231351067", "EUR", "1 EUR"], 4)
    result = build_coverage({"TEST.DE"}, {"TEST.DE": (usd, eur)})[0]
    assert result["status"] == "AMBIGUOUS"
    assert result["isin"] == ""


def test_only_exact_missing_symbol_is_not_in_catalog():
    assert build_coverage({"XYZ.PL"}, {})[0]["status"] == "NOT_IN_CATALOG"
