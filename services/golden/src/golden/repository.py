from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from golden.strategies import Observation


def entities_for_batch(conn: psycopg.Connection, batch_id: str) -> list[dict[str, Any]]:
    """Entities this batch touched — including ones it merely added evidence to,
    since a new observation can change a golden value for an entity first seen
    years ago."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT DISTINCT e.entity_id, e.entity_type
            FROM record_entity_link l
            JOIN entity e ON e.entity_id = l.entity_id
            WHERE l.batch_id = %s AND e.status = 'active'
            """,
            (batch_id,),
        )
        return cur.fetchall()


def get_entity(conn: psycopg.Connection, entity_id: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT entity_id, entity_type, status, merged_into_entity_id
            FROM entity WHERE entity_id = %s
            """,
            (entity_id,),
        )
        return cur.fetchone()


def observations_for_entity(
    conn: psycopg.Connection, entity_id: str
) -> dict[str, list[Observation]]:
    """Every confirmed, non-null value linked to this entity, by canonical field.

    Only observations from records that are actually linked to the entity are
    considered, so a quarantined record contributes nothing to a trusted value
    even though it remains fully stored.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.canonical_field, o.normalized_value, o.raw_value,
                   o.source_id, s.source_name, s.reliability,
                   o.record_id, o.observed_at
            FROM record_entity_link l
            -- The link's role selects the observations it is a link to. A
            -- person row also carries its employer's city and phone; without
            -- this the person's golden record would be built from both, and
            -- the employer's address would compete to become the person's.
            JOIN attribute_observation o
              ON o.record_id = l.record_id AND o.subject = l.role
            JOIN source s ON s.source_id = o.source_id
            WHERE l.entity_id = %s
              AND o.canonical_field IS NOT NULL
              AND o.normalized_value IS NOT NULL
            ORDER BY o.canonical_field, o.observed_at
            """,
            (entity_id,),
        )
        by_field: dict[str, list[Observation]] = {}
        for (field, value, raw, source_id, source_name, reliability,
             record_id, observed_at) in cur:
            by_field.setdefault(field, []).append(
                Observation(
                    value=value,
                    raw_value=raw,
                    source_id=str(source_id),
                    source_name=source_name,
                    reliability=float(reliability),
                    record_id=str(record_id),
                    observed_at=observed_at,
                )
            )
        return by_field


def lock_entity(conn: psycopg.Connection, entity_id: str) -> None:
    """Take the entity's row so only one builder works on it at a time.

    Golden building reads the current values, decides what changed, then closes
    and reopens rows. Two builders interleaving in that gap both see the same
    current row, both close it, and both insert -- and the partial unique index
    that permits one current value per field correctly refuses the second, so
    one of them dies. Which is the safe failure, but it is still a failure, and
    it happens whenever a batch CLI is run while the consumer chain is
    processing the same entity.

    A row lock rather than an advisory lock: the entity row is exactly the thing
    being contended, it needs no hashing and cannot collide with an unrelated
    id, and it is self-evident to anyone reading this later. Readers do not take
    it, so nothing blocks on reporting.

    Held until the caller's transaction ends, which is also what makes it
    correct: the lock has to outlive the read it protects.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM entity WHERE entity_id = %s FOR UPDATE", (entity_id,))


def current_values(conn: psycopg.Connection, entity_id: str) -> dict[str, dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT golden_id, canonical_field, value, confidence, strategy,
                   supporting_sources, competing_values
            FROM golden_attribute
            WHERE entity_id = %s AND valid_to IS NULL
            """,
            (entity_id,),
        )
        return {row["canonical_field"]: row for row in cur}


def refresh_evidence(conn: psycopg.Connection, golden_id: str, choice) -> None:
    """Same value, changed justification.

    A fourth vendor agreeing does not make the value newly true, so this is not
    a history event and valid_from must not move — but leaving the confidence
    and the agreeing-source list stale would misreport how well supported the
    value is. The fact is unchanged; only our reasons for believing it grew.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE golden_attribute
            SET confidence         = %s,
                supporting_sources = %s,
                competing_values   = %s,
                evidence           = %s,
                winning_record_id  = %s,
                winning_source_id  = %s,
                raw_value          = %s
            WHERE golden_id = %s
            """,
            (choice.confidence, choice.supporting_sources, choice.competing_values,
             Json(choice.evidence), choice.winning_record_id, choice.winning_source_id,
             choice.raw_value, golden_id),
        )


def close_value(conn: psycopg.Connection, golden_id: str) -> None:
    """Close a value's validity window instead of overwriting it.

    Must happen BEFORE the replacement is inserted: exactly one row per
    (entity, field) may have valid_to IS NULL, and that partial unique index is
    what makes "golden" mean something. The old value stays queryable forever —
    that history is the difference between a trusted record and a merely current
    one.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE golden_attribute SET valid_to = now() WHERE golden_id = %s",
            (golden_id,),
        )


def link_supersession(
    conn: psycopg.Connection, old_golden_id: str, new_golden_id: str
) -> None:
    """Point a closed value at what replaced it, so the chain is walkable."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE golden_attribute SET superseded_by = %s WHERE golden_id = %s",
            (new_golden_id, old_golden_id),
        )


def insert_value(
    conn: psycopg.Connection, entity_id: str, entity_type: str,
    canonical_field: str, choice,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO golden_attribute (
                entity_id, entity_type, canonical_field, value, raw_value,
                strategy, confidence, winning_record_id, winning_source_id,
                supporting_sources, competing_values, evidence
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING golden_id
            """,
            (entity_id, entity_type, canonical_field, choice.value, choice.raw_value,
             choice.strategy, choice.confidence, choice.winning_record_id,
             choice.winning_source_id, choice.supporting_sources,
             choice.competing_values, Json(choice.evidence)),
        )
        return str(cur.fetchone()[0])


def insert_values(conn: psycopg.Connection, rows: list[tuple]) -> None:
    """Bulk-insert brand-new golden values that have no current row to
    supersede -- the common case on a first load, where every field is new.

    Safe to batch precisely because nothing downstream in the same build needs
    a golden_id back: link_supersession only runs when there WAS an existing
    value, and that path stays row-at-a-time in insert_value/link_supersession.

    Row shape matches insert_value's column order: (entity_id, entity_type,
    canonical_field, value, raw_value, strategy, confidence, winning_record_id,
    winning_source_id, supporting_sources, competing_values, evidence_as_Json).
    """
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO golden_attribute (
                entity_id, entity_type, canonical_field, value, raw_value,
                strategy, confidence, winning_record_id, winning_source_id,
                supporting_sources, competing_values, evidence
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def refresh_evidence_many(conn: psycopg.Connection, rows: list[tuple]) -> None:
    """Same as refresh_evidence, batched. Row shape: (confidence,
    supporting_sources, competing_values, evidence_as_Json, winning_record_id,
    winning_source_id, raw_value, golden_id) -- refresh_evidence's own
    parameter order.
    """
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE golden_attribute
            SET confidence         = %s,
                supporting_sources = %s,
                competing_values   = %s,
                evidence           = %s,
                winning_record_id  = %s,
                winning_source_id  = %s,
                raw_value          = %s
            WHERE golden_id = %s
            """,
            rows,
        )


def retire_fields(conn: psycopg.Connection, golden_ids: list[str]) -> None:
    """A field that loses every supporting observation stops being current but
    is not deleted: we once believed it, and that stays on the record. This is
    the same update close_value does; every retiring row gets the identical
    SET, so one UPDATE .. WHERE golden_id = ANY(...) covers them all in one
    round trip."""
    if not golden_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE golden_attribute SET valid_to = now() WHERE golden_id = ANY(%s)",
            (golden_ids,),
        )


def golden_for_entity(conn: psycopg.Connection, entity_id: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT g.canonical_field, g.value, g.strategy, g.confidence,
                   g.supporting_sources, g.competing_values, g.evidence,
                   s.source_name
            FROM golden_attribute g
            JOIN source s ON s.source_id = g.winning_source_id
            WHERE g.entity_id = %s AND g.valid_to IS NULL
            ORDER BY g.canonical_field
            """,
            (entity_id,),
        )
        return cur.fetchall()


def history_for_entity(
    conn: psycopg.Connection, entity_id: str, canonical_field: str | None
) -> list[dict[str, Any]]:
    clause = "AND g.canonical_field = %s" if canonical_field else ""
    params = [entity_id] + ([canonical_field] if canonical_field else [])
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT g.canonical_field, g.value, g.strategy, g.confidence,
                   g.valid_from, g.valid_to, (g.valid_to IS NULL) AS is_current,
                   s.source_name
            FROM golden_attribute g
            JOIN source s ON s.source_id = g.winning_source_id
            WHERE g.entity_id = %s {clause}
            ORDER BY g.canonical_field, g.valid_from
            """,
            params,
        )
        return cur.fetchall()


def golden_counts(conn: psycopg.Connection) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE valid_to IS NULL)  AS current_values,
                   count(*) FILTER (WHERE valid_to IS NOT NULL) AS superseded_values,
                   count(DISTINCT entity_id) FILTER (WHERE valid_to IS NULL) AS entities,
                   count(*) FILTER (WHERE valid_to IS NULL AND competing_values > 1)
                       AS contested_values
            FROM golden_attribute
            """
        )
        current, superseded, entities, contested = cur.fetchone()
        return {
            "current_values": current,
            "superseded_values": superseded,
            "entities": entities,
            "contested_values": contested,
        }
