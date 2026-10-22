
from app.market_data.isin import is_valid_isin


def test_accepts_valid_isin():
    assert is_valid_isin("US0378331005")


def test_rejects_wrong_check_digit():
    assert not is_valid_isin("US0378331006")


def test_rejects_invalid_format():
    assert not is_valid_isin("US037833100")
    assert not is_valid_isin("invalid")
    assert not is_valid_isin("")
    assert not is_valid_isin(None)
