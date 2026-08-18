"""UUIDv7 generated here has to be indistinguishable from the column default."""

import time
import uuid

from common.ids import timestamp_ms, uuid7


def test_version_is_7():
    assert uuid7().version == 7


def test_variant_is_rfc_4122():
    assert uuid7().variant == uuid.RFC_4122


def test_ids_are_unique():
    assert len({uuid7() for _ in range(10_000)}) == 10_000


def test_ids_sort_by_creation_time():
    """The reason for choosing v7 over v4: index locality on insert."""
    first = [uuid7() for _ in range(200)]
    time.sleep(0.005)
    second = [uuid7() for _ in range(200)]
    assert max(str(v) for v in first) < min(str(v) for v in second)


def test_embedded_timestamp_is_now():
    now = time.time_ns() // 1_000_000
    assert abs(timestamp_ms(uuid7()) - now) < 1000


def test_the_random_bits_actually_vary():
    """A generator that reused its randomness would still pass every test above
    until two ids landed in the same millisecond."""
    same_ms = [uuid7().int & ((1 << 74) - 1) for _ in range(500)]
    assert len(set(same_ms)) == 500
