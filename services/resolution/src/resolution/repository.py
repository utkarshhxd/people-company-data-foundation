from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json


def get_batch(conn: psycopg.Connection, batch_id: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT b.batch_id, b.source_id, b.entity_type, b.status, s.describes
            FROM batch b JOIN source s ON s.source_id = b.source_id
            WHERE b.batch_id = %s
            """,
            (batch_id,),
        )
        return cur.fetchone()


def resolvable_records(
    conn: psycopg.Connection, batch_id: str
) -> list[dict[str, Any]]:
    """Records this batch may resolve: cleared by validation or released from
    quarantine, and not already linked.

    Reading the view rather than raw_record is what gives quarantine its teeth —
    an 'open' or 'rejected' record simply is not here.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT rr.record_id, r.row_number, rr.was_quarantined
            FROM resolvable_record rr
            JOIN raw_record r ON r.record_id = rr.record_id
            -- role='self' in the join, not the WHERE: a record that has only
            -- been linked to its employer has not been resolved to its own
            -- entity yet, and must still appear as work to do.
            LEFT JOIN record_entity_link l
              ON l.record_id = rr.record_id AND l.role = 'self'
            WHERE rr.batch_id = %s AND l.link_id IS NULL
            ORDER BY r.row_number
            """,
            (batch_id,),
        )
        return cur.fetchall()


def record_values(
    conn: psycopg.Connection, record_id: str, subject: str = "self"
) -> dict[str, list[str]]:
    """Confirmed canonical field -> normalized values, in source column order.

    Only observations carrying a canonical_field appear: an unreviewed mapping
    must never decide who someone is.

    Scoped to one subject, because an employer's phone number is not a key to
    the person who works there — letting it in would build the same identity key
    for every colleague and resolve them all to one person.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_field, normalized_value
            FROM attribute_observation
            WHERE record_id = %s
              AND subject = %s
              AND canonical_field IS NOT NULL
              AND normalized_value IS NOT NULL
            ORDER BY source_column, value_index
            """,
            (record_id, subject),
        )
        values: dict[str, list[str]] = {}
        for field_name, value in cur:
            values.setdefault(field_name, []).append(value)
        return values


def has_role_email(conn: psycopg.Connection, record_id: str) -> bool:
    """Did validation judge this record's email to be a role mailbox?

    Reusing the stored verdict rather than re-deriving it keeps one definition
    of 'role account' in the system, and it lives with the rules.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM validation_result
            WHERE record_id = %s AND rule_id = 'email.role_account' AND outcome = 'fail'
            LIMIT 1
            """,
            (record_id,),
        )
        return cur.fetchone() is not None


def find_candidates(
    conn: psycopg.Connection, entity_type: str, keys: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """Entities sharing any of these keys. This is the only candidate source,
    so matching never scans the entity table."""
    if not keys:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT k.entity_id, k.key_type, k.key_value, k.strength
            FROM entity_identity_key k
            JOIN entity e ON e.entity_id = k.entity_id
            WHERE k.entity_type = %s
              AND e.status = 'active'
              AND (k.key_type, k.key_value) IN (
                  SELECT * FROM unnest(%s::text[], %s::text[])
              )
            """,
            (entity_type, [t for t, _ in keys], [v for _, v in keys]),
        )
        return cur.fetchall()


def create_entity(
    conn: psycopg.Connection, entity_type: str, first_record_id: str,
    entity_id: str | None = None,
) -> str:
    """Create an entity, optionally with an id the caller already chose.

    Supplying the id avoids waiting for RETURNING to come back, which is what
    lets the record-at-a-time pipeline send a record's whole write in one
    flush. Either way the id is a UUIDv7 — the column default and
    common.ids.uuid7 produce the same thing.
    """
    if entity_id is not None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entity (entity_id, entity_type, first_seen_record_id)
                VALUES (%s, %s, %s)
                """,
                (entity_id, entity_type, first_record_id),
            )
        return entity_id

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entity (entity_type, first_seen_record_id)
            VALUES (%s, %s) RETURNING entity_id
            """,
            (entity_type, first_record_id),
        )
        return str(cur.fetchone()[0])


def link_record(
    conn: psycopg.Connection, record_id: str, entity_id: str, entity_type: str,
    batch_id: str, source_id: str, method: str, confidence: float,
    status: str, evidence: dict, role: str = "self",
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO record_entity_link (
                record_id, entity_id, entity_type, batch_id, source_id,
                match_method, match_confidence, match_status, evidence, role
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            -- Per (record, role): a record links to its own entity once and to
            -- its employer once, and neither is a duplicate of the other.
            ON CONFLICT (record_id, role) DO NOTHING
            """,
            (record_id, entity_id, entity_type, batch_id, source_id,
             method, confidence, status, Json(evidence), role),
        )


def add_keys(
    conn: psycopg.Connection, entity_id: str, entity_type: str,
    record_id: str, keys: list,
) -> int:
    """Every key an entity is known by accumulates. A second vendor's spelling of
    a website or a second email address makes the entity findable more ways, and
    losing either would mean a later record fails to match."""
    if not keys:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO entity_identity_key (
                entity_id, entity_type, key_type, key_value, strength, source_record_id
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (entity_id, key_type, key_value) DO NOTHING
            """,
            [(entity_id, entity_type, k.key_type, k.key_value, k.strength, record_id)
             for k in keys],
        )
    return len(keys)


def record_candidate(
    conn: psycopg.Connection, record_id: str, entity_id: str, entity_type: str,
    method: str, confidence: float, evidence: dict,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO match_candidate (
                record_id, entity_id, entity_type, match_method,
                match_confidence, evidence
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (record_id, entity_id) DO UPDATE SET
                match_confidence = EXCLUDED.match_confidence,
                match_method     = EXCLUDED.match_method,
                evidence         = EXCLUDED.evidence
            WHERE match_candidate.status = 'open'
            """,
            (record_id, entity_id, entity_type, method, confidence, Json(evidence)),
        )


def resolution_counts(conn: psycopg.Connection, batch_id: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE match_status = 'auto_linked') AS linked,
                   count(*) FILTER (WHERE match_status = 'new_entity')  AS new_entities,
                   count(*) FILTER (WHERE match_status = 'manual')      AS manual
            -- Records resolved, not links written. An employer link is a
            -- consequence of resolving a record, never a second record.
            FROM record_entity_link WHERE batch_id = %s AND role = 'self'
            """,
            (batch_id,),
        )
        linked, new_entities, manual = cur.fetchone()
        cur.execute(
            """
            SELECT count(*) FROM match_candidate c
            JOIN raw_record r ON r.record_id = c.record_id
            WHERE r.batch_id = %s AND c.status = 'open'
            """,
            (batch_id,),
        )
        return {
            "linked": linked,
            "new_entities": new_entities,
            "manual": manual,
            "open_candidates": cur.fetchone()[0],
        }


# --------------------------------------------------------------------------
# merge
# --------------------------------------------------------------------------

def merge_entities(
    conn: psycopg.Connection, merged_id: str, surviving_id: str, entity_type: str,
    actor: str, reason: str | None, confidence: float | None,
) -> dict[str, int]:
    """Absorb one entity into another.

    The absorbed id is NOT deleted. It becomes a tombstone pointing at the
    survivor, so any id already handed out downstream still resolves — which is
    the whole promise of a stable entity id.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE record_entity_link SET entity_id = %s, updated_at = now()
            WHERE entity_id = %s
            """,
            (surviving_id, merged_id),
        )
        records_moved = cur.rowcount

        # Keys move too, or the survivor would be unfindable by the absorbed
        # entity's identifiers and the next record would create a third entity.
        cur.execute(
            """
            INSERT INTO entity_identity_key (
                entity_id, entity_type, key_type, key_value, strength, source_record_id
            )
            SELECT %s, entity_type, key_type, key_value, strength, source_record_id
            FROM entity_identity_key WHERE entity_id = %s
            ON CONFLICT (entity_id, key_type, key_value) DO NOTHING
            """,
            (surviving_id, merged_id),
        )
        keys_moved = cur.rowcount

        cur.execute(
            """
            UPDATE entity
            SET status = 'merged', merged_into_entity_id = %s, updated_at = now()
            WHERE entity_id = %s
            """,
            (surviving_id, merged_id),
        )
        cur.execute(
            """
            INSERT INTO entity_merge (
                merged_entity_id, surviving_entity_id, entity_type, match_confidence,
                records_moved, keys_moved, actor, reason
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (merged_id, surviving_id, entity_type, confidence,
             records_moved, keys_moved, actor, reason),
        )
    return {"records_moved": records_moved, "keys_moved": keys_moved}


def resolve_entity_id(conn: psycopg.Connection, entity_id: str) -> str | None:
    """Follow merge tombstones to the surviving entity.

    Bounded rather than recursive-until-done: a cycle would mean corrupt data,
    and hanging is a worse failure than reporting it.
    """
    current = entity_id
    for _ in range(16):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, merged_into_entity_id FROM entity WHERE entity_id = %s",
                (current,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            if row[0] == "active":
                return current
            current = str(row[1])
    raise RuntimeError(f"merge chain from {entity_id} did not terminate")


def get_candidate(conn: psycopg.Connection, candidate_id: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT c.*, l.entity_id AS record_entity_id
            FROM match_candidate c
            LEFT JOIN record_entity_link l
              ON l.record_id = c.record_id AND l.role = 'self'
            WHERE c.candidate_id = %s
            """,
            (candidate_id,),
        )
        return cur.fetchone()


def close_candidate(
    conn: psycopg.Connection, candidate_id: str, status: str,
    reviewed_by: str, note: str | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE match_candidate
            SET status = %s, reviewed_at = now(), reviewed_by = %s, review_note = %s
            WHERE candidate_id = %s
            """,
            (status, reviewed_by, note, candidate_id),
        )


def list_candidates(
    conn: psycopg.Connection, status: str, limit: int
) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT c.candidate_id, c.record_id, c.entity_id, c.entity_type,
                   c.match_method, c.match_confidence, c.status,
                   s.source_name, r.row_number
            FROM match_candidate c
            JOIN raw_record r ON r.record_id = c.record_id
            JOIN source s ON s.source_id = r.source_id
            WHERE c.status = %s
            ORDER BY c.match_confidence DESC, c.created_at
            LIMIT %s
            """,
            (status, limit),
        )
        return cur.fetchall()


def entity_profile(conn: psycopg.Connection, entity_id: str) -> list[dict[str, Any]]:
    """Everything known about an entity and who said it — the cross-dataset view."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT canonical_field, normalized_value, source_name, file_name
            FROM entity_observation
            WHERE entity_id = %s AND canonical_field IS NOT NULL
            ORDER BY canonical_field, source_name
            """,
            (entity_id,),
        )
        return cur.fetchall()


def entity_sources(conn: psycopg.Connection, entity_id: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT s.source_name, b.file_name, l.match_method, l.match_confidence,
                   l.match_status, l.record_id
            FROM record_entity_link l
            JOIN source s ON s.source_id = l.source_id
            JOIN batch b ON b.batch_id = l.batch_id
            WHERE l.entity_id = %s
            ORDER BY l.linked_at
            """,
            (entity_id,),
        )
        return cur.fetchall()


def assert_relationship(
    conn: psycopg.Connection, from_entity_id: str, to_entity_id: str,
    relationship_type: str, record_id: str, batch_id: str, source_id: str,
    evidence: dict,
) -> None:
    """Record that this record says these two entities are related.

    Idempotent per (from, to, type) while current, so a second vendor asserting
    the same employment refreshes the evidence rather than duplicating it. A
    person moving employer is not handled here: that closes the old row, which
    needs a decision about how a *disagreement* differs from a *change*, and
    both currently look identical in a batch of vendor files.
    """
    if from_entity_id == to_entity_id:
        # The check constraint would refuse it anyway; failing the whole record
        # over a vendor row that named a person as their own employer would not
        # be an improvement on ignoring it.
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entity_relationship (
                from_entity_id, to_entity_id, relationship_type,
                record_id, batch_id, source_id, evidence
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (from_entity_id, to_entity_id, relationship_type)
                WHERE valid_to IS NULL
            DO UPDATE SET evidence = EXCLUDED.evidence
            """,
            (from_entity_id, to_entity_id, relationship_type, record_id,
             batch_id, source_id, Json(evidence)),
        )


def entity_relationships(
    conn: psycopg.Connection, entity_id: str
) -> list[dict]:
    """Current relationships in both directions: where someone works, who works here."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.relationship_type, r.evidence, r.valid_from,
                   r.to_entity_id   AS other_entity_id, 'outgoing' AS direction,
                   e.entity_type    AS other_entity_type
            FROM entity_relationship r
            JOIN entity e ON e.entity_id = r.to_entity_id
            WHERE r.from_entity_id = %(entity)s AND r.valid_to IS NULL

            UNION ALL

            SELECT r.relationship_type, r.evidence, r.valid_from,
                   r.from_entity_id AS other_entity_id, 'incoming' AS direction,
                   e.entity_type    AS other_entity_type
            FROM entity_relationship r
            JOIN entity e ON e.entity_id = r.from_entity_id
            WHERE r.to_entity_id = %(entity)s AND r.valid_to IS NULL

            ORDER BY valid_from
            """,
            {"entity": entity_id},
        )
        return cur.fetchall()
