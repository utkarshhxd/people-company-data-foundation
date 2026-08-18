"""The screen must reject less than Postgres does, never more.

A row it wrongly lets through is caught by the write a moment later and lands in
record_error regardless. A row it wrongly rejects is diverted from the entity
graph with nothing to say so. So these tests are mostly about what is *not*
flagged: every case below that returns None is a row the database would have
accepted, and flagging it would be silent data loss.
"""

from record_pipeline.screening import unstorable

NUL = "\x00"
LONE_SURROGATE = "\ud800"


def test_a_clean_row_passes():
    assert unstorable({"company_name": "Acme", "city": "Boston"}) is None


def test_nul_in_a_value_names_its_column():
    fault = unstorable({"name": f"Ac{NUL}me"})
    assert fault is not None
    assert "NUL byte" in fault
    assert "'name'" in fault


def test_nul_in_a_column_name_is_caught_too():
    """Vendor headers are source data as much as the cells are."""
    fault = unstorable({f"na{NUL}me": "Acme"})
    assert fault is not None
    assert "column name" in fault


def test_unpaired_surrogate_is_caught():
    fault = unstorable({"name": f"Ac{LONE_SURROGATE}me"})
    assert fault is not None
    assert "surrogate" in fault


def test_nulls_and_non_strings_pass():
    assert unstorable({"a": None, "b": 3, "c": True}) is None


def test_an_empty_row_passes():
    assert unstorable({}) is None


def test_unusual_but_storable_text_is_not_flagged():
    """The wide direction is the dangerous one: Postgres stores all of this."""
    assert unstorable({
        "name": "café — Ünicode «quoted» 🎯",
        "note": "tab\tnewline\ncarriage\rreturn",
        "math": "𝕬𝖓𝖙𝖎𝖖𝖚𝖆",
        "rtl": "شركة",
        "paired": "\U0001f600",
    }) is None


def test_a_very_long_value_is_not_flagged():
    """Size is not a certain refusal, so it is not screened on."""
    assert unstorable({"description": "x" * 2_000_000}) is None
