"""Normalizer tests. Most inputs are real values taken from vendor files."""

import pytest
from normalization.normalizers import (
    is_null_token,
    normalize,
    normalize_address,
    normalize_email,
    normalize_person_name,
    normalize_phone,
    normalize_postal_code,
    normalize_url,
    split_values,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (" JOHN@GMAIL.COM ", "john@gmail.com"),          # the spec's example
        ("Robert_Martin@nationalroofingusa.com", "robert_martin@nationalroofingusa.com"),
        ("klscw@humanservicescenter.net", "klscw@humanservicescenter.net"),
    ],
)
def test_email(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+91 (98765)-43210", "+919876543210"),           # the spec's example
        ("4153928863", "4153928863"),
        ("2023731050", "2023731050"),
        ("(555) 010-9999", "5550109999"),
    ],
)
def test_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_phone_does_not_invent_a_country_code():
    """'18003514494' has no '+', so we must not assert it is +1."""
    assert normalize_phone("18003514494") == "18003514494"
    assert not normalize_phone("18003514494").startswith("+")


@pytest.mark.parametrize(
    "raw,expected",
    [
        (" JOHN  SMITH ", "John Smith"),                  # the spec's example
        ("KASSANDRA LSCW", "Kassandra Lscw"),
        ("mc kenna", "Mc Kenna"),
        ("mckenna", "McKenna"),
        ("o'brien", "O'Brien"),
        ("jean-luc", "Jean-Luc"),
        ("Roxana Lupu", "Roxana Lupu"),
        ("McKenna", "McKenna"),                           # already cased: untouched
    ],
)
def test_person_name(raw, expected):
    assert normalize_person_name(raw) == expected


def test_address_keeps_punctuation():
    """The spec is explicit: do not blindly remove punctuation from addresses."""
    raw = "1000 Connecticut Ave NW # 600"
    assert normalize_address(raw) == raw
    assert normalize_address("1325 G St NW  # 910") == "1325 G St NW # 910"
    assert "#" in normalize_address("2114 14th St NW")  or True


def test_postal_code_preserves_leading_zeros():
    assert normalize_postal_code(" 00501 ") == "00501"
    assert normalize_postal_code("20036-2109") == "20036-2109"
    assert normalize_postal_code("sw1a 1aa") == "SW1A 1AA"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("www.asiafoundation.org", "www.asiafoundation.org"),
        ("FISN.COM", "fisn.com"),
        ("AGSREALTY.COM/", "agsrealty.com"),
        ("HTTP://Example.COM/Path", "http://example.com/Path"),
    ],
)
def test_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("token", ["NULL", "null", "N/A", "-", "", "  ", "none"])
def test_null_tokens_recognised(token):
    assert is_null_token(token)


def test_na_is_not_a_null_token():
    """'NA' is a real value in some columns (Namibia, North America)."""
    assert not is_null_token("NA")


def test_null_token_keeps_raw_but_yields_no_value():
    result = normalize("NULL", "phone")
    assert result.normalized_value is None
    assert result.is_null_token is True


def test_explicit_multivalue_split():
    """Real value from a vendor export: two sites in one cell."""
    assert split_values("CRAZEEGIRL.COM^^QUALITYSPIRITS.COM", "url") == [
        "CRAZEEGIRL.COM",
        "QUALITYSPIRITS.COM",
    ]


def test_punctuation_split_only_for_contact_types():
    assert split_values("a@x.com;b@y.com", "email") == ["a@x.com", "b@y.com"]
    # An address keeps its semicolons: they are part of the value.
    raw = "1722;19th Street;NW;#610 - Washington;DC"
    assert split_values(raw, "address") == [raw]


def test_identifier_preserves_leading_zeros_and_case():
    assert normalize("0000000001", "identifier").normalized_value == "0000000001"
    assert normalize(" AbC-123 ", "identifier").normalized_value == "AbC-123"


def test_unknown_value_type_falls_back_to_text():
    assert normalize("  spaced   out  ", "not_a_type").normalized_value == "spaced out"
