"""The pure parts of the per-record unit.

These are the pieces where the per-record path could silently diverge from the
batch path — the in-memory reconstructions of things the batch path reads back
out of the database. Anything that needs a connection is covered by the
end-to-end run instead, where a real divergence would actually show up.
"""

import pytest

from record_pipeline.context import samples_from_rows
from record_pipeline.unit import (
    _confirmed_values,
    _has_role_email,
    _observation_dicts,
)
from validation.rules import FAIL, PASS


def observation_tuple(source_column, canonical_field, normalized, value_index=0):
    return (
        "record-1", "batch-1", "source-1", "person", source_column,
        canonical_field, "auto_accepted", value_index, "raw", normalized,
        "text", "text:trim", False, "2026-01-01",
    )


# --------------------------------------------------------------------------
# observation ids
# --------------------------------------------------------------------------

def test_every_observation_gets_its_own_id():
    dicts = _observation_dicts([
        observation_tuple("a", "email", "x@y.com"),
        observation_tuple("b", "phone", "+15551234"),
    ])
    ids = {obs["observation_id"] for obs in dicts}
    assert len(ids) == 2


def test_observation_dicts_carry_what_validation_reads():
    """validate_record must not be able to tell memory from a query."""
    (obs,) = _observation_dicts([observation_tuple("e_mail", "email", "x@y.com")])
    assert set(obs) >= {
        "observation_id", "record_id", "source_column", "canonical_field",
        "value_index", "raw_value", "normalized_value", "value_type",
        "normalization_method", "is_null_token",
    }
    assert obs["canonical_field"] == "email"
    assert obs["normalized_value"] == "x@y.com"


# --------------------------------------------------------------------------
# confirmed values — the input to identity keys
# --------------------------------------------------------------------------

def test_only_confirmed_canonical_values_are_used():
    """An unmapped or unreviewed column must never decide who someone is."""
    dicts = _observation_dicts([
        observation_tuple("e_mail", "email", "x@y.com"),
        observation_tuple("notes", None, "something"),
    ])
    assert _confirmed_values(dicts) == {"email": ["x@y.com"]}


def test_null_normalized_values_are_skipped():
    dicts = _observation_dicts([
        observation_tuple("e_mail", "email", None),
        observation_tuple("e_mail__2", "email", "real@y.com"),
    ])
    assert _confirmed_values(dicts) == {"email": ["real@y.com"]}


def test_values_are_ordered_by_source_column_then_index():
    """Resolution's own query orders this way. If the two disagree, the same
    record can build a different identity key depending on which path read it —
    and then the two paths produce different entities from the same file."""
    dicts = _observation_dicts([
        observation_tuple("zzz_email", "email", "third"),
        observation_tuple("aaa_email", "email", "first", value_index=0),
        observation_tuple("aaa_email", "email", "second", value_index=1),
    ])
    assert _confirmed_values(dicts) == {"email": ["first", "second", "third"]}


def test_multiple_fields_are_kept_apart():
    dicts = _observation_dicts([
        observation_tuple("e_mail", "email", "x@y.com"),
        observation_tuple("mobile", "phone", "+15551234"),
    ])
    assert _confirmed_values(dicts) == {
        "email": ["x@y.com"], "phone": ["+15551234"],
    }


# --------------------------------------------------------------------------
# role email — reused from validation rather than re-derived
# --------------------------------------------------------------------------

def result_row(rule_id, outcome):
    return (None, "record-1", "batch-1", "attribute", "email", rule_id,
            "warning", outcome, "msg", None, "2")


def test_role_email_is_read_off_the_judgement():
    assert _has_role_email([result_row("email.role_account", FAIL)]) is True


def test_a_passing_role_rule_is_not_a_role_email():
    assert _has_role_email([result_row("email.role_account", PASS)]) is False


def test_other_failures_are_not_role_emails():
    assert _has_role_email([result_row("email.syntax", FAIL)]) is False


def test_no_judgements_means_no_role_email():
    assert _has_role_email([]) is False


# --------------------------------------------------------------------------
# value-analysis samples
# --------------------------------------------------------------------------

def test_samples_skip_absent_cells_like_the_batch_path_does():
    rows = [{"a": "1", "b": None}, {"a": None, "b": "2"}]
    assert samples_from_rows(rows, ["a", "b"]) == {"a": ["1"], "b": ["2"]}


def test_every_column_appears_even_with_no_values():
    assert samples_from_rows([{"a": None}], ["a", "b"]) == {"a": [], "b": []}


@pytest.mark.parametrize("rows", [0, 1, 5])
def test_samples_survive_short_files(rows):
    data = [{"a": str(i)} for i in range(rows)]
    assert len(samples_from_rows(data, ["a"])["a"]) == rows
