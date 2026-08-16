import pytest
from mapping.detectors import (
    is_email,
    is_integer,
    is_linkedin,
    is_phone,
    is_postal_code,
    is_url,
    is_year,
    match_ratio,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("john@gmail.com", True),
        ("jane.doe+tag@Example.COM", True),
        ("not-an-email", False),
        ("@nouser.com", False),
        ("missing@tld", False),
    ],
)
def test_is_email(value, expected):
    assert is_email(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("+91 (98765)-43210", True),
        ("+44 20 7946 0958", True),
        ("(555) 010-9999", True),
        ("12345", False),          # too few digits
        ("john@gmail.com", False),
    ],
)
def test_is_phone(value, expected):
    assert is_phone(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [("00501", True), ("90210", True), ("SW1A 1AA", True), ("K1A 0B1", True),
     ("560001", True), ("hello", False)],
)
def test_is_postal_code(value, expected):
    assert is_postal_code(value) is expected


def test_url_and_linkedin():
    assert is_url("https://example.com/x")
    assert is_url("example.co.uk")
    assert is_linkedin("https://www.linkedin.com/in/someone")
    assert not is_linkedin("https://example.com")


def test_integer_and_year():
    assert is_integer("4200")
    assert not is_integer("4.2")
    assert is_year("1998")
    assert not is_year("98")


def test_match_ratio_ignores_blanks():
    assert match_ratio("email", ["a@b.com", "", "   ", "c@d.com"]) == 1.0
    assert match_ratio("email", []) == 0.0
    assert match_ratio("email", ["a@b.com", "nope"]) == 0.5
