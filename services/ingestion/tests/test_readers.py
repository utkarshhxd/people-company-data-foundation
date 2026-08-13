"""The core guarantee of ingestion: values arrive exactly as the source wrote them."""

from pathlib import Path

import pytest

from ingestion.readers import disambiguate, iter_file, read_file

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


# --------------------------------------------------------------------------
# batched reading
# --------------------------------------------------------------------------

def _write_csv(tmp_path, rows: int):
    path = tmp_path / "big.csv"
    lines = ["id,name,email"]
    lines += [f"{i},Person {i},p{i}@example.com" for i in range(1, rows + 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_batches_are_the_requested_size(tmp_path):
    path = _write_csv(tmp_path, 25)
    sizes = [len(rows) for _, rows in iter_file(path, batch_size=10)]
    assert sizes == [10, 10, 5]


def test_every_row_survives_batching(tmp_path):
    """Batching is a memory strategy; it must not change what is read."""
    path = _write_csv(tmp_path, 2500)
    batched = [row for _, rows in iter_file(path, batch_size=250) for row in rows]
    _, whole = read_file(path)
    assert batched == whole
    assert len(batched) == 2500


def test_columns_are_identical_in_every_batch(tmp_path):
    path = _write_csv(tmp_path, 30)
    seen = {tuple(columns) for columns, _ in iter_file(path, batch_size=7)}
    assert seen == {("id", "name", "email")}


def test_a_batch_larger_than_the_file_yields_one_batch(tmp_path):
    path = _write_csv(tmp_path, 5)
    assert [len(rows) for _, rows in iter_file(path, batch_size=1000)] == [5]


def test_a_header_only_file_yields_nothing(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("id,name,email\n", encoding="utf-8")
    assert list(iter_file(path, batch_size=10)) == []


def test_blank_rows_are_dropped_while_batching_too(tmp_path):
    """Trailing ,,,, rows are the norm in real vendor exports."""
    path = tmp_path / "trailing.csv"
    path.write_text("id,name,email\n1,A,a@x.com\n,,\n,,\n2,B,b@x.com\n", encoding="utf-8")
    rows = [row for _, batch in iter_file(path, batch_size=2) for row in batch]
    assert len(rows) == 2


def test_a_nonsense_batch_size_is_refused(tmp_path):
    path = _write_csv(tmp_path, 3)
    with pytest.raises(ValueError, match="at least 1"):
        list(iter_file(path, batch_size=0))
