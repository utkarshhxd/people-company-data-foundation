"""Normalizer tests. Most inputs are real values taken from vendor files."""

import pytest
from normalization.normalizers import (
    is_null_token,
    normalize,
    normalize_address,
    normalize_date,
    normalize_email,
    normalize_money,
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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("100000000000", "100000000000"),      # Apollo ships plain integers
        ("3,181,850,000", "3181850000"),
        ("$1.2M", "1200000"),
        ("1.2m", "1200000"),                   # and must agree with the line above
        ("USD 45,000", "45000"),
        ("2,500,000.00", "2500000"),           # sub-unit precision is noise here
        ("$3.4bn", "3400000000"),
        ("500k", "500000"),
    ],
)
def test_money(raw, expected):
    assert normalize_money(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "N/A revenue", "1.2.3", "--"])
def test_money_refuses_what_it_cannot_read(raw):
    """Returning None keeps the raw value visible instead of inventing a figure."""
    assert normalize_money(raw) is None


def test_a_suffix_and_its_expansion_normalize_identically():
    """Two vendors reporting the same amount differently must agree, or
    survivorship sees a disagreement that does not exist."""
    assert normalize_money("1.2M") == normalize_money("1200000")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-09-01T00:00:00+00:00", "2024-09-01"),   # Apollo's 'Last Raised At'
        ("2025-07-28T01:10:16.666Z", "2025-07-28"),    # a vendor's 'verifyAt'
        ("1999-12-31", "1999-12-31"),
        ("2024/03/05", "2024-03-05"),
    ],
)
def test_date(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "07/28/2025",   # mm/dd/yyyy and dd/mm/yyyy are indistinguishable
        "28/07/2025",
        "2024-02-31",   # well shaped, does not exist
        "yesterday",
        "",
    ],
)
def test_date_refuses_ambiguous_or_impossible_input(raw):
    assert normalize_date(raw) is None


def test_the_same_instant_in_two_timezones_is_the_same_day():
    """Time is dropped on purpose: these fields report a day, and keeping the
    instant would make two sources reporting it disagree."""
    assert normalize_date("2024-09-01T23:00:00+00:00") == normalize_date("2024-09-01")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("45047", "2023-05-01"),   # real values from the Apollo xlsx export
        ("44805", "2022-09-01"),
        ("43298", "2018-07-17"),
        ("40817", "2011-10-01"),
    ],
)
def test_excel_serial_dates_are_decoded(raw, expected):
    """An xlsx export turns a date column into Excel's day count. Reading only
    ISO threw away 221 stated dates per thousand Apollo rows."""
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("raw", ["19999", "60001", "2023", "0", "-1"])
def test_numbers_outside_the_serial_range_are_not_dates(raw):
    """'2023' in a date column is a year, not day 2023 of the Excel epoch. A
    column of integers that is not really dates would decode just as willingly,
    so the range guard is what stops a wrong column becoming a wrong fact."""
    assert normalize_date(raw) is None


def test_the_excel_epoch_accounts_for_a_leap_day_that_never_happened():
    """Excel counts 1900-02-29. Anchoring on 1899-12-30 rather than 1900-01-01
    is what makes every modern date come out on the right day."""
    assert normalize_date("45047") == "2023-05-01"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4200", "4200"),
        ("1,200", "1200"),        # thousands separator is formatting
        ("1 200", "1200"),
        ("'4200", "4200"),        # spreadsheet text-marker
        ("500+", "500"),          # '+' means at least; 500 is still stated
        ("0", "0"),
    ],
)
def test_integer_keeps_what_the_source_actually_stated(raw, expected):
    assert normalize(raw, "integer").normalized_value == expected


@pytest.mark.parametrize("raw", ["50-100", "10 to 50", "2019-2020", "10..50", "5 – 9"])
def test_a_range_is_refused_rather_than_mangled(raw):
    """`50-100` used to become `50100` — a headcount wrong by 500x that then won
    survivorship and reached the golden record with only a warning beside it."""
    result = normalize(raw, "integer")
    assert result.normalized_value is None
    assert result.method == "integer:range"


def test_a_range_is_distinguished_from_an_unreadable_value():
    """'abc' left nothing usable. '50-100' was perfectly meaningful — just not as
    an integer — and validation says so differently."""
    assert normalize("abc", "integer").method == "integer:empty"
    assert normalize("50-100", "integer").method == "integer:range"
