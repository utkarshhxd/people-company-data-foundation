"""Read-only lineage: walk a trusted value back to the source cell it came from.

Every stage of the pipeline recorded why it did what it did, but each recorded it
in its own table. This module is the join that makes the chain answerable in one
question:

    golden value
      <- which observation won, and what it beat
      <- which source cell that observation came from   (raw_value, file, row)
      <- how that column was interpreted                (mapping confidence)
      <- whether the value passed its checks            (validation result)
      <- why that record was attached to this entity    (match confidence)
      <- how much the vendor is believed                (source reliability)

All five confidence-like dimensions the spec insists on keeping separate appear
side by side here — separate columns of one answer, never added together.

Lives in `common` rather than a service because both the read API and the
`golden explain` CLI need exactly this, and duplicating the joins would let the
two drift into disagreeing about provenance, which is the one thing that must
never happen. Everything here is a query; nothing writes.
"""

from typing import Any

import psycopg
from psycopg.rows import dict_row

from common.db import connect


def resolve_entity(conn: psycopg.Connection, entity_id: str) -> dict[str, Any] | None:
    """Follow merge tombstones so a retired id still answers questions."""
    current = entity_id
    for _ in range(16):
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT entity_id, entity_type, status, merged_into_entity_id, created_at
                FROM entity WHERE entity_id = %s
                """,
                (current,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        if row["status"] == "active":
            row["requested_entity_id"] = entity_id
            row["was_merged"] = str(row["entity_id"]) != entity_id
            return row
        current = str(row["merged_into_entity_id"])
    raise RuntimeError(f"merge chain from {entity_id} did not terminate")


def entity_summary(entity_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        entity = resolve_entity(conn, entity_id)
        if entity is None:
            return None
        resolved_id = str(entity["entity_id"])

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT canonical_field, value, confidence, strategy,
                       supporting_sources, competing_values, valid_from
                FROM golden_attribute
                WHERE entity_id = %s AND valid_to IS NULL
                ORDER BY canonical_field
                """,
                (resolved_id,),
            )
            attributes = cur.fetchall()

            cur.execute(
                """
                SELECT s.source_name, s.reliability, b.file_name, r.row_number,
                       l.match_method, l.match_confidence, l.match_status, l.record_id
                FROM record_entity_link l
                JOIN raw_record r ON r.record_id = l.record_id
                JOIN source s ON s.source_id = l.source_id
                JOIN batch b ON b.batch_id = l.batch_id
                WHERE l.entity_id = %s
                ORDER BY l.linked_at
                """,
                (resolved_id,),
            )
            sources = cur.fetchall()

    return {
        "entity_id": resolved_id,
        "requested_entity_id": entity["requested_entity_id"],
        "was_merged": entity["was_merged"],
        "entity_type": entity["entity_type"],
        "created_at": entity["created_at"],
        "attributes": {a["canonical_field"]: a for a in attributes},
        "observed_by": sources,
    }


_CONTRIBUTIONS_SQL = """
SELECT o.observation_id,
       o.record_id,
       o.raw_value,
       o.normalized_value,
       o.source_column,
       o.normalization_method,
       o.observed_at,
       r.row_number,
       b.file_name,
       s.source_name,
       -- Kept apart on purpose. Folding these into one score is exactly what
       -- the spec forbids, and it would also destroy the ability to answer
       -- "the value is fine, we just aren't sure it's the same company".
       s.reliability        AS source_reliability,
       cm.mapping_method,
       cm.mapping_confidence,
       cm.mapping_status,
       l.match_method,
       l.match_confidence,
       l.match_status,
       rv.status            AS record_validation_status
FROM record_entity_link l
-- The link's role picks the observations it is a link to, so explaining a
-- company's address never reaches for the address of the person whose row
-- named it as their employer.
JOIN attribute_observation o
  ON o.record_id = l.record_id AND o.subject = l.role
JOIN raw_record r ON r.record_id = o.record_id
JOIN batch b ON b.batch_id = o.batch_id
JOIN source s ON s.source_id = o.source_id
LEFT JOIN record_validation rv ON rv.record_id = o.record_id
LEFT JOIN source_schema ss
       ON ss.source_id = o.source_id AND ss.entity_type = o.entity_type
LEFT JOIN column_mapping cm
       ON cm.source_schema_id = ss.source_schema_id
      AND cm.source_column = o.source_column
WHERE l.entity_id = %s AND o.canonical_field = %s
ORDER BY o.observed_at
"""


def explain_value(entity_id: str, canonical_field: str) -> dict[str, Any] | None:
    """Why does this entity's field hold this value, and who is responsible?"""
    with connect() as conn:
        entity = resolve_entity(conn, entity_id)
        if entity is None:
            return None
        resolved_id = str(entity["entity_id"])

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT golden_id, value, raw_value, strategy, confidence,
                       supporting_sources, competing_values, evidence,
                       winning_record_id, valid_from
                FROM golden_attribute
                WHERE entity_id = %s AND canonical_field = %s AND valid_to IS NULL
                """,
                (resolved_id, canonical_field),
            )
            golden = cur.fetchone()

            cur.execute(_CONTRIBUTIONS_SQL, (resolved_id, canonical_field))
            contributions = cur.fetchall()

            cur.execute(
                """
                SELECT v.rule_id, v.severity, v.outcome, v.message, v.observation_id
                FROM validation_result v
                JOIN attribute_observation o ON o.observation_id = v.observation_id
                JOIN record_entity_link l
                  ON l.record_id = o.record_id AND l.role = o.subject
                WHERE l.entity_id = %s AND o.canonical_field = %s
                ORDER BY (v.severity = 'error') DESC, v.rule_id
                """,
                (resolved_id, canonical_field),
            )
            judgements = cur.fetchall()

            cur.execute(
                """
                SELECT value, strategy, confidence, valid_from, valid_to,
                       (valid_to IS NULL) AS is_current
                FROM golden_attribute
                WHERE entity_id = %s AND canonical_field = %s
                ORDER BY valid_from
                """,
                (resolved_id, canonical_field),
            )
            history = cur.fetchall()

    if golden is None and not contributions:
        return None

    by_observation: dict[Any, list[dict]] = {}
    for judgement in judgements:
        by_observation.setdefault(judgement.pop("observation_id"), []).append(judgement)

    winning_record = str(golden["winning_record_id"]) if golden else None
    for contribution in contributions:
        contribution["validation"] = by_observation.get(
            contribution["observation_id"], []
        )
        # Two different questions. Several sources can agree with the trusted
        # value while only one record is cited as having produced it, and
        # collapsing them into a single "won" flag misreads the second as the
        # first.
        contribution["agrees_with_golden"] = (
            golden is not None
            and contribution["normalized_value"] == golden["value"]
        )
        contribution["is_winning_record"] = (
            winning_record is not None
            and str(contribution["record_id"]) == winning_record
        )

    return {
        "entity_id": resolved_id,
        "requested_entity_id": entity["requested_entity_id"],
        "canonical_field": canonical_field,
        "golden": golden,
        "winning_record_id": winning_record,
        # Every source cell that had a say, whether or not it won.
        "contributions": contributions,
        "history": history,
    }


def entity_timeline(entity_id: str) -> list[dict[str, Any]] | None:
    """Everything that ever happened to this entity, in order.

    A union rather than one table: each stage owns its own history, and forcing
    them into a shared event log would make every stage depend on a format none
    of them controls.
    """
    with connect() as conn:
        entity = resolve_entity(conn, entity_id)
        if entity is None:
            return None
        resolved_id = str(entity["entity_id"])

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT r.ingested_at AS at, 'record_ingested' AS event,
                       s.source_name AS actor,
                       b.file_name || ' row ' || r.row_number AS detail
                FROM record_entity_link l
                JOIN raw_record r ON r.record_id = l.record_id
                JOIN source s ON s.source_id = r.source_id
                JOIN batch b ON b.batch_id = r.batch_id
                WHERE l.entity_id = %(entity)s

                UNION ALL
                SELECT l.linked_at, 'record_linked', s.source_name,
                       l.match_method || ' @ ' || l.match_confidence
                       || ' (' || l.match_status || ')'
                FROM record_entity_link l
                JOIN source s ON s.source_id = l.source_id
                WHERE l.entity_id = %(entity)s

                UNION ALL
                SELECT q.quarantined_at, 'record_quarantined', 'system',
                       q.status || ': ' || array_to_string(
                           ARRAY(SELECT jsonb_array_elements_text(q.reason_codes)), ', ')
                FROM record_entity_link l
                JOIN quarantine_item q ON q.record_id = l.record_id
                WHERE l.entity_id = %(entity)s

                UNION ALL
                SELECT m.created_at, 'entity_merged', m.actor,
                       m.merged_entity_id || ' absorbed ('
                       || m.records_moved || ' record(s))'
                FROM entity_merge m
                WHERE m.surviving_entity_id = %(entity)s

                UNION ALL
                SELECT g.valid_from, 'golden_value_set', s.source_name,
                       g.canonical_field || ' = ' || g.value
                       || ' [' || g.strategy || ' @ ' || g.confidence || ']'
                FROM golden_attribute g
                JOIN source s ON s.source_id = g.winning_source_id
                WHERE g.entity_id = %(entity)s

                UNION ALL
                SELECT g.valid_to, 'golden_value_superseded', s.source_name,
                       g.canonical_field || ' was ' || g.value
                FROM golden_attribute g
                JOIN source s ON s.source_id = g.winning_source_id
                WHERE g.entity_id = %(entity)s AND g.valid_to IS NOT NULL

                ORDER BY at
                """,
                {"entity": resolved_id},
            )
            return cur.fetchall()


def find_entities(
    entity_type: str | None, key_value: str | None, limit: int = 25
) -> list[dict[str, Any]]:
    """Look an entity up by any identity key it is known by.

    Deliberately searches identity keys rather than golden values: an entity is
    findable by every identifier any vendor ever gave it, including ones that
    lost the survivorship contest.
    """
    clauses, params = ["e.status = 'active'"], {}
    if entity_type:
        clauses.append("e.entity_type = %(entity_type)s")
        params["entity_type"] = entity_type
    if key_value:
        clauses.append("k.key_value ILIKE %(key_value)s")
        params["key_value"] = f"%{key_value}%"
    params["limit"] = limit

    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT DISTINCT e.entity_id, e.entity_type,
                   (SELECT g.value FROM golden_attribute g
                     WHERE g.entity_id = e.entity_id AND g.valid_to IS NULL
                       AND g.canonical_field IN ('company_name', 'full_name')
                     LIMIT 1) AS display_name,
                   (SELECT count(*) FROM record_entity_link l
                     WHERE l.entity_id = e.entity_id) AS record_count
            FROM entity e
            JOIN entity_identity_key k ON k.entity_id = e.entity_id
            WHERE {' AND '.join(clauses)}
            ORDER BY display_name NULLS LAST
            LIMIT %(limit)s
            """,
            params,
        )
        return cur.fetchall()


def record_lineage(record_id: str) -> dict[str, Any] | None:
    """The other direction: what became of one source row."""
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.record_id, r.row_number, r.source_record_id, r.raw_payload,
                   r.ingested_at, b.file_name, s.source_name, s.reliability,
                   rv.status AS validation_status, rv.error_count, rv.warning_count,
                   rv.failed_rules, q.status AS quarantine_status, q.reason_codes,
                   l.entity_id, l.match_method, l.match_confidence, l.match_status
            FROM raw_record r
            JOIN batch b ON b.batch_id = r.batch_id
            JOIN source s ON s.source_id = r.source_id
            LEFT JOIN record_validation rv ON rv.record_id = r.record_id
            LEFT JOIN quarantine_item q ON q.record_id = r.record_id
            -- The record's own entity. Without the role this returns a second
            -- row for the employer link and the record appears twice.
            LEFT JOIN record_entity_link l
              ON l.record_id = r.record_id AND l.role = 'self'
            WHERE r.record_id = %s
            """,
            (record_id,),
        )
        record = cur.fetchone()
        if record is None:
            return None

        cur.execute(
            """
            SELECT source_column, canonical_field, raw_value, normalized_value,
                   value_index, mapping_status, is_null_token
            FROM attribute_observation
            WHERE record_id = %s
            ORDER BY source_column, value_index
            """,
            (record_id,),
        )
        record["observations"] = cur.fetchall()

        # Which of this record's values actually became trusted. A record can
        # contribute everything, something, or nothing.
        cur.execute(
            """
            SELECT canonical_field, value, strategy, confidence
            FROM golden_attribute
            WHERE winning_record_id = %s AND valid_to IS NULL
            ORDER BY canonical_field
            """,
            (record_id,),
        )
        record["golden_values_won"] = cur.fetchall()

    return record


def entity_relationships(entity_id: str) -> dict[str, Any] | None:
    """Which entities this one is related to, in both directions.

    Both directions because both questions are asked: "where does this person
    work" and "who works at this company". A relationship is stored once, from
    the person to the company, so answering the second from the first is a
    matter of reading the same row the other way round.

    Each related entity carries its own display name, taken from its golden
    record, so a caller does not have to fetch every id to render a list.
    """
    with connect() as conn:
        entity = resolve_entity(conn, entity_id)
        if entity is None:
            return None
        resolved_id = str(entity["entity_id"])

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                WITH related AS (
                    SELECT r.relationship_type, r.valid_from, r.evidence,
                           r.to_entity_id AS other_id, 'employer' AS direction,
                           r.record_id
                    FROM entity_relationship r
                    WHERE r.from_entity_id = %(entity)s AND r.valid_to IS NULL

                    UNION ALL

                    SELECT r.relationship_type, r.valid_from, r.evidence,
                           r.from_entity_id, 'employee', r.record_id
                    FROM entity_relationship r
                    WHERE r.to_entity_id = %(entity)s AND r.valid_to IS NULL
                )
                SELECT related.relationship_type, related.direction,
                       related.valid_from, related.evidence, related.record_id,
                       related.other_id AS entity_id, e.entity_type,
                       (SELECT g.value FROM golden_attribute g
                         WHERE g.entity_id = related.other_id
                           AND g.valid_to IS NULL
                           AND g.canonical_field IN ('company_name', 'full_name')
                         LIMIT 1) AS display_name
                FROM related
                JOIN entity e ON e.entity_id = related.other_id
                ORDER BY related.direction, display_name NULLS LAST
                """,
                {"entity": resolved_id},
            )
            relationships = cur.fetchall()

    return {
        "requested_entity_id": entity_id,
        "entity_id": resolved_id,
        "count": len(relationships),
        "relationships": relationships,
    }
