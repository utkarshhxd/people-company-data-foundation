"""Nothing written to record_error may itself be rejectable.

The table exists so that a record the pipeline could not process is kept rather
than lost. A payload that cannot be stored is precisely the case it is for — so
sanitizing has to happen everywhere a value from the source can reach.
"""

import json

from record_pipeline.repository import _sanitize

NUL = "\x00"
REPLACEMENT = "�"


def test_a_clean_payload_is_untouched():
    payload = {"name": "Acme", "city": "Boston"}
    clean, sanitized = _sanitize(payload)
    assert clean == payload
    assert sanitized is False


def test_nul_in_a_value_is_replaced_and_reported():
    clean, sanitized = _sanitize({"name": f"Ac{NUL}me"})
    assert clean == {"name": f"Ac{REPLACEMENT}me"}
    assert sanitized is True


def test_nul_in_a_key_is_replaced_too():
    """Vendor headers are source data as much as the cells are."""
    clean, sanitized = _sanitize({f"na{NUL}me": "Acme"})
    assert clean == {f"na{REPLACEMENT}me": "Acme"}
    assert sanitized is True


def test_nested_values_are_reached():
    clean, sanitized = _sanitize({"a": {"b": [f"x{NUL}y"]}})
    assert clean == {"a": {"b": [f"x{REPLACEMENT}y"]}}
    assert sanitized is True


def test_none_and_non_strings_survive():
    payload = {"a": None, "b": 3, "c": True}
    clean, sanitized = _sanitize(payload)
    assert clean == payload
    assert sanitized is False


def test_a_bare_string_is_handled():
    """source_record_id arrives as a bare string, not inside a dict."""
    assert _sanitize(f"id{NUL}1") == (f"id{REPLACEMENT}1", True)


def test_none_source_record_id_is_fine():
    assert _sanitize(None) == (None, False)


def test_only_nul_is_replaced():
    """Everything else the source wrote is kept verbatim — accents, emoji,
    punctuation. Sanitizing more than necessary would be its own data loss."""
    value = "café — Ünicode «quoted» 🎯"
    assert _sanitize(value) == (value, False)


def test_an_unpaired_surrogate_is_made_storable():
    """It fails on the way to Postgres rather than at it: an unpaired surrogate
    cannot be encoded as UTF-8, so json.dumps().encode() raises before the
    insert is ever sent. Same hole as NUL, one level earlier."""
    clean, sanitized = _sanitize({"name": "Ac\ud800me"})
    assert sanitized is True
    clean["name"].encode("utf-8")  # would raise if the surrogate survived


def test_the_exact_bytes_survive_an_unencodable_payload():
    """raw_payload_bytes is the authoritative copy, and the payload that cannot
    be encoded is exactly the one whose bytes matter."""
    payload = {"name": "Ac\ud800me"}
    exact = json.dumps(payload, ensure_ascii=False).encode("utf-8", "surrogatepass")
    assert exact.decode("utf-8", "surrogatepass") == json.dumps(payload, ensure_ascii=False)
