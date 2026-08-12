"""The core guarantee of ingestion: values arrive exactly as the source wrote them."""

from pathlib import Path

import pytest

from ingestion.readers import disambiguate, read_file

FIXTURE = Path(__file__).parent / "fixtures" / "messy_people.csv"


@pytest.fixture(scope="module")
def parsed():
    return read_file(FIXTURE)


def test_duplicate_headers_are_disambiguated():
    assert disambiguate(["a", "b", "a", "a"]) == ["a", "b", "a__2", "a__3"]


def test_blank_header_becomes_unnamed():
    assert disambiguate(["a", "  "]) == ["a", "unnamed"]


def test_duplicate_columns_both_survive(parsed):
    columns, rows = parsed
    assert "notes" in columns and "notes__2" in columns
    assert rows[0]["notes"] == "first"
    assert rows[0]["notes__2"] == "second"


def test_fully_empty_row_is_dropped(parsed):
    _, rows = parsed
    assert len(rows) == 4


@pytest.mark.parametrize(
    "row_index,column,expected",
    [
        # Leading zeros survive: the classic postal-code corruption.
        (0, "postal_code", "00501"),
        (2, "customer_ref", "0000000001"),
        # A 20-digit id must not become a float or scientific notation.
        (0, "customer_ref", "12345678901234567890"),
        # Something that LOOKS like scientific notation stays a string.
        (3, "customer_ref", "9.99E10"),
        # Surrounding whitespace and casing are the normalizer's job, not ours.
        (0, "full_name", "  JOHN  SMITH  "),
        (0, "e_mail", "  JOHN@GMAIL.COM "),
        # Phone punctuation is preserved verbatim.
        (0, "mobile_no", "+91 (98765)-43210"),
        # Unicode round-trips.
        (1, "full_name", "José Ünicode"),
        (1, "notes", "café"),
        # Dates are not reformatted, including an invalid one.
        (0, "joined", "01/02/2026"),
        (2, "joined", "02/29/2026"),
        # Quoted embedded comma stays one field.
        (2, "notes", "quoted, comma"),
        # Apostrophes/hyphens are untouched.
        (3, "full_name", "O'Brien-Smith"),
    ],
)
def test_values_are_preserved_verbatim(parsed, row_index, column, expected):
    _, rows = parsed
    assert rows[row_index][column] == expected


def test_every_value_is_string_or_none(parsed):
    _, rows = parsed
    for row in rows:
        for value in row.values():
            assert value is None or isinstance(value, str)
