"""Refuse a payload before it reaches Postgres — but only when Postgres would.

This screen is deliberately a *subset* of what the database rejects, and the
asymmetry is the whole design:

  * Too narrow, and a poison row slips through to be caught by the write. That
    still works. It costs one round-trip and a rolled-back transaction, and the
    row lands in record_error either way.
  * Too wide, and a row Postgres would have accepted is diverted to
    record_error. It never becomes an entity, and nothing anywhere says so.

The second direction is silent data loss, so a check only belongs here when the
refusal is certain. Two things qualify:

  * a NUL byte, which Postgres accepts in neither text nor jsonb
  * an unpaired surrogate, which cannot be encoded as UTF-8 at all

Value size does not qualify. jsonb's limit is real, but the size at which a row
would actually be refused depends on compression, and guessing the threshold
errs in the direction that loses data.

Payloads from `iter_rows` are flat: one level of column name to value. Nesting
is not walked, which means a nested fault would fall through to the write and be
caught there — the safe direction, by the argument above.
"""

from typing import Any

NUL = "\x00"


class UnstorablePayload(Exception):
    """A row the database would refuse, caught before it was offered."""


def _fault(text: str) -> str | None:
    if NUL in text:
        return "NUL byte"
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return "unpaired surrogate"
    return None


def unstorable(payload: dict[str, Any]) -> str | None:
    """Why this payload cannot be stored, or None if it can.

    The reason names the column, because the operator reading `process errors`
    needs to find the character in the source file.
    """
    for key, value in payload.items():
        if isinstance(key, str):
            fault = _fault(key)
            if fault is not None:
                return f"{fault} in column name {key!r}"
        if isinstance(value, str):
            fault = _fault(value)
            if fault is not None:
                return f"{fault} in column {key!r}"
    return None
