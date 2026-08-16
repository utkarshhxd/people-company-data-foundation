"""Client-side UUIDv7, so a record's rows can be written in one round-trip.

Every id column in this schema defaults to Postgres' `uuidv7()`, and that stays
true — nothing here changes the tables. What this adds is the option of knowing
an id *before* the insert.

Why that matters for a record-at-a-time pipeline: a record's observations
reference the record, and its validation results reference the observations. If
ids come from the server, each level has to wait for the previous level's
`RETURNING` to come back before it can be sent — three sequential round-trips per
record, every record. Generating the ids here removes the dependency, so one
record's entire write can be sent in a single flush.

The generated value is a real RFC 9562 UUIDv7 with the same layout Postgres
produces, so ids from either source sort together by creation time and neither
can be told from the other after the fact.
"""

import secrets
import time
import uuid

# Layout, most significant bit first (RFC 9562 section 5.7):
#   48 bits  unix_ts_ms
#    4 bits  version (7)
#   12 bits  rand_a
#    2 bits  variant (0b10)
#   62 bits  rand_b
_TIMESTAMP_SHIFT = 80
_VERSION_SHIFT = 76
_VARIANT_SHIFT = 62
_TIMESTAMP_MASK = 0xFFFF_FFFF_FFFF << _TIMESTAMP_SHIFT
_VERSION_MASK = 0xF << _VERSION_SHIFT
_VARIANT_MASK = 0b11 << _VARIANT_SHIFT


def uuid7() -> uuid.UUID:
    """A time-ordered UUID: millisecond timestamp first, then randomness."""
    value = int.from_bytes(secrets.token_bytes(16), "big")
    value &= ~(_TIMESTAMP_MASK | _VERSION_MASK | _VARIANT_MASK)
    value |= (time.time_ns() // 1_000_000) << _TIMESTAMP_SHIFT
    value |= 0x7 << _VERSION_SHIFT
    value |= 0b10 << _VARIANT_SHIFT
    return uuid.UUID(int=value)


def timestamp_ms(value: uuid.UUID) -> int:
    """The creation time embedded in a UUIDv7, in unix milliseconds.

    Only meaningful for version 7; reading it off any other UUID returns
    whatever random bits happen to sit there, so callers should check.
    """
    return (value.int & _TIMESTAMP_MASK) >> _TIMESTAMP_SHIFT
