"""Reads and writes for AI enrichment.

Two tables, two different jobs:

  * enrichment_attempt -- has the model already been asked about this
    (entity, field)? Written for every attempt, whatever the outcome, so a
    scheduled run never re-pays a question already answered.
  * enrichment_proposal -- a confident answer, pending a human's decision.
    Nothing here is written as an attribute_observation until a human accepts
    it (see ADR 0014: AI may propose, never assert). Acceptance is what
    finally writes through the same batch / raw_record / attribute_observation
    / record_entity_link tables a vendor file would use, via a synthetic,
    low-reliability source -- at that point it is human-confirmed evidence,
    not an assertion wearing a confidence score.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import psycopg
from common.ids import uuid7
from psycopg.rows import dict_row
from psycopg.types.json import Json

AI_SOURCE_NAME = "ai_enrichment"
AI_SOURCE_TYPE = "ai_enrichment"


def get_or_create_ai_source(conn: psycopg.Connection, reliability: float) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT source_id FROM source WHERE source_name = %s", (AI_SOURCE_NAME,))
        row = cur.fetchone()
        if row:
            return str(row[0])
        cur.execute(
            """
            INSERT INTO source (source_name, source_type, reliability, description)
            VALUES (%s, %s, %s, %s)
            RETURNING source_id
            """,
            (AI_SOURCE_NAME, AI_SOURCE_TYPE, reliability,
             "Values a human accepted from a local model's enrichment proposal."),
        )
        return str(cur.fetchone()[0])


def known_values(conn: psycopg.Connection, entity_id: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_field, value FROM golden_attribute
            WHERE entity_id = %s AND valid_to IS NULL
            """,
            (entity_id,),
        )
        return dict(cur.fetchall())


def candidate_entities(
    conn: psycopg.Connection, entity_type: str, canonical_field: str, limit: int
) -> list[str]:
    """Entities missing this field, with other data to reason from, not yet
    asked about this field."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.entity_id
            FROM entity e
            WHERE e.entity_type = %s AND e.status = 'active'
              AND EXISTS (
                  SELECT 1 FROM golden_attribute g
                  WHERE g.entity_id = e.entity_id AND g.valid_to IS NULL
              )
              AND NOT EXISTS (
                  SELECT 1 FROM golden_attribute g
                  WHERE g.entity_id = e.entity_id AND g.valid_to IS NULL
                    AND g.canonical_field = %s
              )
              AND NOT EXISTS (
                  SELECT 1 FROM enrichment_attempt a
                  WHERE a.entity_id = e.entity_id AND a.canonical_field = %s
              )
            ORDER BY e.created_at
            LIMIT %s
            """,
            (entity_type, canonical_field, canonical_field, limit),
        )
        return [str(row[0]) for row in cur]


def record_attempt(
    conn: psycopg.Connection, entity_id: str, entity_type: str, canonical_field: str,
    outcome: str, model: str, stated_confidence: float | None,
) -> None:
    """One row per (entity, field) ever asked about -- the dedup gate for
    future runs. 'written' means a proposal was created, not that an
    observation was; see enrichment_proposal for what became of it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO enrichment_attempt (entity_id, entity_type, canonical_field,
                                            outcome, model, stated_confidence)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (entity_id, canonical_field) DO NOTHING
            """,
            (entity_id, entity_type, canonical_field, outcome, model, stated_confidence),
        )


def create_proposal(
    conn: psycopg.Connection, entity_id: str, entity_type: str, canonical_field: str,
    proposed_value: str, value_type: str, stated_confidence: float, model: str,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO enrichment_proposal (entity_id, entity_type, canonical_field,
                                             proposed_value, value_type, stated_confidence,
                                             model)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING proposal_id
            """,
            (entity_id, entity_type, canonical_field, proposed_value, value_type,
             stated_confidence, model),
        )
        return str(cur.fetchone()[0])


def list_proposals(
    conn: psycopg.Connection, status: str | None = None, entity_type: str | None = None
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if status:
        clauses.append("p.status = %s")
        params.append(status)
    if entity_type:
        clauses.append("p.entity_type = %s")
        params.append(entity_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT proposal_id, entity_id, entity_type, canonical_field, proposed_value,
                   value_type, stated_confidence, model, status, reviewed_by, created_at
            FROM enrichment_proposal p
            {where}
            ORDER BY created_at
            """,
            params,
        )
        return cur.fetchall()


def get_proposal(conn: psycopg.Connection, proposal_id: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM enrichment_proposal WHERE proposal_id = %s", (proposal_id,)
        )
        return cur.fetchone()


def set_proposal_status(
    conn: psycopg.Connection, proposal_id: str, status: str, reviewed_by: str,
    record_id: str | None = None,
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE enrichment_proposal
            SET status = %s, reviewed_at = now(), reviewed_by = %s, record_id = %s
            WHERE proposal_id = %s AND status = 'pending'
            """,
            (status, reviewed_by, record_id, proposal_id),
        )
        return cur.rowcount > 0


def create_run_batch(conn: psycopg.Connection, source_id: str, entity_type: str) -> str:
    """One batch per accepted proposal's write, tagged so it's identifiable in
    `batch` as an enrichment acceptance rather than a file load."""
    marker = f"ai-enrichment-accept-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO batch (source_id, entity_type, file_path, file_name, file_hash,
                               file_size_bytes, status)
            VALUES (%s, %s, %s, %s, %s, 0, 'completed')
            RETURNING batch_id
            """,
            (source_id, entity_type, marker, marker,
             hashlib.sha256(f"{marker}:{uuid7()}".encode()).hexdigest()),
        )
        return str(cur.fetchone()[0])


def write_observation(
    conn: psycopg.Connection, *, batch_id: str, source_id: str, entity_id: str,
    entity_type: str, canonical_field: str, raw_value: str,
    normalized_value: str | None, value_type: str, normalization_method: str,
    is_null_token: bool,
) -> str:
    """One human-accepted fact, linked to the entity it's about.

    subject/role are always 'self': enrichment only ever answers about the
    entity it was asked about, never an employer named inside it.
    """
    record_id = uuid7()
    observed_at = datetime.now(UTC)
    payload: dict[str, Any] = {canonical_field: raw_value}
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_record (record_id, batch_id, source_id, entity_type,
                                    source_record_id, row_number, payload_hash, raw_payload)
            VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
            """,
            (record_id, batch_id, source_id, entity_type, entity_id,
             payload_hash, Json(payload)),
        )
        cur.execute(
            """
            INSERT INTO attribute_observation (
                record_id, batch_id, source_id, entity_type, source_column,
                canonical_field, mapping_status, value_index, raw_value,
                normalized_value, value_type, normalization_method, is_null_token,
                observed_at, subject
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'auto_accepted', 0, %s, %s, %s, %s, %s, %s, 'self')
            """,
            (record_id, batch_id, source_id, entity_type, canonical_field,
             canonical_field, raw_value, normalized_value, value_type,
             normalization_method, is_null_token, observed_at),
        )
        cur.execute(
            """
            INSERT INTO record_entity_link (record_id, entity_id, entity_type, batch_id,
                                            source_id, role, match_method, match_confidence,
                                            match_status, evidence)
            VALUES (%s, %s, %s, %s, %s, 'self', 'ai_enrichment', 1.0, 'auto_linked', %s)
            """,
            (record_id, entity_id, entity_type, batch_id, source_id,
             Json({"note": "written directly against a known entity_id after human "
                            "acceptance of an enrichment proposal"})),
        )
    return str(record_id)
