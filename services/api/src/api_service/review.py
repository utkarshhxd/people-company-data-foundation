"""Read projections for the three review queues.

Three stages in this pipeline stop and ask for a person: schema mapping when a
column is ambiguous, entity resolution when a match is plausible but not certain,
and quarantine when a record failed validation. Each already has a CLI. What none
of them had is a way to *see the thing being decided* — the sample values under an
ambiguous column, the two entities a merge would join, the failures that put a
record in quarantine — without running three commands and joining the output by eye.

This module only reads. Every decision still goes through the stage's own
functions (`mapping.repository.set_mapping`, `resolution.pipeline.accept_candidate`,
`validation.quarantine.review`), so a decision made in a browser and the same
decision made in a terminal are the same code path. Nothing here writes, and
nothing here reimplements a rule.
"""

from typing import Any

from common.canonical import fields_for
from common.db import connect
from psycopg.rows import dict_row

QUEUE_MAPPINGS = "mappings"
QUEUE_CANDIDATES = "candidates"
QUEUE_QUARANTINE = "quarantine"

# How many raw rows to read when showing what a column actually contains. Small
# on purpose: a reviewer decides from a handful of values, and the query runs on
# every page load.
SAMPLE_ROWS = 40
SAMPLE_SHOWN = 6


def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _one(conn, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------

_SUMMARY_SQL = """
SELECT
  (SELECT count(*) FROM column_mapping WHERE mapping_status = 'needs_review')
      AS mappings_open,
  (SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0)
     FROM column_mapping WHERE mapping_status = 'needs_review')
      AS mappings_oldest_seconds,
  (SELECT count(*) FROM match_candidate WHERE status = 'open')
      AS candidates_open,
  (SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0)
     FROM match_candidate WHERE status = 'open')
      AS candidates_oldest_seconds,
  (SELECT count(*) FROM quarantine_item WHERE status = 'open')
      AS quarantine_open,
  (SELECT coalesce(extract(epoch FROM now() - min(quarantined_at)), 0)
     FROM quarantine_item WHERE status = 'open')
      AS quarantine_oldest_seconds
"""


def queue_summary() -> dict[str, Any]:
    """Depth and age of all three queues.

    Age matters more than depth and is reported alongside it for that reason: a
    queue of three is fine, three that have not moved in a fortnight means
    reviewing stopped, and no count reveals that.
    """
    with connect() as conn:
        row = _one(conn, _SUMMARY_SQL) or {}

    def queue(prefix: str) -> dict[str, Any]:
        return {
            "open": int(row.get(f"{prefix}_open") or 0),
            "oldest_seconds": float(row.get(f"{prefix}_oldest_seconds") or 0.0),
        }

    queues = {
        QUEUE_MAPPINGS: queue("mappings"),
        QUEUE_CANDIDATES: queue("candidates"),
        QUEUE_QUARANTINE: queue("quarantine"),
    }
    return {
        "queues": queues,
        "total_open": sum(q["open"] for q in queues.values()),
    }


# --------------------------------------------------------------------------
# schema mapping queue
# --------------------------------------------------------------------------

_MAPPING_SQL = """
SELECT cm.mapping_id, cm.source_column, cm.canonical_field, cm.subject,
       cm.mapping_method, cm.mapping_confidence, cm.mapping_status,
       cm.evidence, cm.created_at, cm.reviewed_at, cm.reviewed_by,
       ss.source_schema_id, ss.entity_type, ss.first_seen_batch_id,
       s.source_name
FROM column_mapping cm
JOIN source_schema ss USING (source_schema_id)
JOIN source s ON s.source_id = ss.source_id
-- Every optional filter is cast explicitly. Postgres cannot infer the type of
-- a bare NULL parameter, so an unfiltered call fails with AmbiguousParameter
-- rather than returning everything, which is exactly the default case.
WHERE (%(status)s::text IS NULL OR cm.mapping_status = %(status)s::text)
  AND (%(source_schema_id)s::uuid IS NULL
       OR ss.source_schema_id = %(source_schema_id)s::uuid)
ORDER BY cm.mapping_confidence DESC, s.source_name, cm.source_column
LIMIT %(limit)s
"""


def _samples_for(conn, batch_id: str, columns: set[str]) -> dict[str, list[str]]:
    """A few real values per column, so a reviewer decides from data, not a name."""
    if not columns:
        return {}
    found: dict[str, list[str]] = {column: [] for column in columns}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT raw_payload FROM raw_record WHERE batch_id = %s "
            "ORDER BY row_number LIMIT %s",
            (batch_id, SAMPLE_ROWS),
        )
        for (payload,) in cur:
            for column in columns:
                value = payload.get(column)
                text = "" if value is None else str(value).strip()
                # Blank cells say nothing about what a column holds, and a
                # column of them is exactly the case a reviewer needs to see
                # is empty rather than see forty blank lines of.
                if text and text not in found[column] and len(found[column]) < SAMPLE_SHOWN:
                    found[column].append(text)
    return found


def mapping_queue(
    status: str | None = "needs_review",
    source_schema_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Ambiguous columns, worst-understood last, each with what it contains."""
    with connect() as conn:
        rows = _rows(
            conn,
            _MAPPING_SQL,
            {"status": status, "source_schema_id": source_schema_id, "limit": limit},
        )
        by_batch: dict[str, set[str]] = {}
        for row in rows:
            by_batch.setdefault(str(row["first_seen_batch_id"]), set()).add(
                row["source_column"]
            )
        samples = {
            batch_id: _samples_for(conn, batch_id, columns)
            for batch_id, columns in by_batch.items()
        }

    out = []
    for row in rows:
        evidence = row["evidence"] or {}
        out.append(
            {
                "mapping_id": str(row["mapping_id"]),
                "source_name": row["source_name"],
                "source_column": row["source_column"],
                "entity_type": row["entity_type"],
                "subject": row["subject"],
                "canonical_field": row["canonical_field"],
                "mapping_method": row["mapping_method"],
                "mapping_confidence": float(row["mapping_confidence"]),
                "mapping_status": row["mapping_status"],
                "source_schema_id": str(row["source_schema_id"]),
                "created_at": row["created_at"],
                "reviewed_at": row["reviewed_at"],
                "reviewed_by": row["reviewed_by"],
                # The alternatives the engine considered and rejected are the
                # single most useful thing on this screen: usually the right
                # answer is the second one.
                "alternatives": evidence.get("alternatives", []),
                "collision": evidence.get("collision"),
                "samples": samples.get(str(row["first_seen_batch_id"]), {}).get(
                    row["source_column"], []
                ),
            }
        )
    return out


def canonical_fields(entity_type: str) -> list[dict[str, str]]:
    """The vocabulary a reviewer may choose from, with what each field means."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "value_type": spec.value_type,
            "subject": spec.subject,
        }
        for spec in fields_for(entity_type)
    ]


# --------------------------------------------------------------------------
# match candidate queue
# --------------------------------------------------------------------------

_CANDIDATE_SQL = """
SELECT c.candidate_id, c.record_id, c.entity_id, c.entity_type,
       c.match_method, c.match_confidence, c.status, c.created_at,
       c.reviewed_at, c.reviewed_by, c.review_note,
       l.entity_id AS record_entity_id,
       s.source_name, r.row_number, b.file_name
FROM match_candidate c
JOIN raw_record r ON r.record_id = c.record_id
JOIN source s ON s.source_id = r.source_id
JOIN batch b ON b.batch_id = r.batch_id
LEFT JOIN record_entity_link l
  ON l.record_id = c.record_id AND l.role = 'self'
WHERE c.status = %(status)s
ORDER BY c.match_confidence DESC, c.created_at
LIMIT %(limit)s
"""

# Only the fields a human can actually judge two entities by. Showing all fifty
# would bury the three that decide it.
_COMPARE_FIELDS = (
    "full_name", "first_name", "last_name", "company_name", "legal_name",
    "email", "phone", "website", "linkedin_url", "job_title",
    "city", "state_region", "country", "address_line1",
)

_PROFILE_SQL = """
SELECT canonical_field, normalized_value, source_name
FROM entity_observation
WHERE entity_id = %s AND canonical_field = ANY(%s)
ORDER BY canonical_field, source_name
"""


def _side(conn, entity_id: str | None) -> dict[str, Any]:
    """One half of the comparison: what this entity is known to be."""
    if entity_id is None:
        return {"entity_id": None, "fields": {}}
    values: dict[str, list[str]] = {}
    for row in _rows(conn, _PROFILE_SQL, (entity_id, list(_COMPARE_FIELDS))):
        seen = values.setdefault(row["canonical_field"], [])
        if row["normalized_value"] not in seen:
            seen.append(row["normalized_value"])
    return {"entity_id": entity_id, "fields": values}


def candidate_queue(status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
    """Proposed matches, most confident first, each shown as two sides to compare."""
    with connect() as conn:
        rows = _rows(conn, _CANDIDATE_SQL, {"status": status, "limit": limit})
        out = []
        for row in rows:
            record_entity = (
                str(row["record_entity_id"]) if row["record_entity_id"] else None
            )
            out.append(
                {
                    "candidate_id": str(row["candidate_id"]),
                    "record_id": str(row["record_id"]),
                    "entity_type": row["entity_type"],
                    "match_method": row["match_method"],
                    "match_confidence": float(row["match_confidence"]),
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "reviewed_at": row["reviewed_at"],
                    "reviewed_by": row["reviewed_by"],
                    "review_note": row["review_note"],
                    "source_name": row["source_name"],
                    "file_name": row["file_name"],
                    "row_number": row["row_number"],
                    # "existing" is the entity already in the database; "incoming"
                    # is the one this record created for itself. Accepting merges
                    # incoming into existing.
                    "existing": _side(conn, str(row["entity_id"])),
                    "incoming": _side(conn, record_entity),
                }
            )
    return out


# --------------------------------------------------------------------------
# quarantine queue
# --------------------------------------------------------------------------

_QUARANTINE_SELECT = """
SELECT q.quarantine_id, q.record_id, q.status, q.reason_codes, q.quarantined_at,
       q.reviewed_by, q.review_note, q.reviewed_at, q.entity_type,
       r.row_number, r.batch_id,
       s.source_name, b.file_name
FROM quarantine_item q
JOIN raw_record r ON r.record_id = q.record_id
JOIN source s ON s.source_id = r.source_id
JOIN batch b ON b.batch_id = r.batch_id
"""

_QUARANTINE_SQL = _QUARANTINE_SELECT + """
WHERE (%(status)s::text IS NULL OR q.status = %(status)s::text)
  AND (%(entity_type)s::text IS NULL OR q.entity_type = %(entity_type)s::text)
ORDER BY q.quarantined_at
LIMIT %(limit)s
"""

_QUARANTINE_ONE_SQL = _QUARANTINE_SELECT + "WHERE q.record_id = %(record_id)s::uuid"

_FAILURE_SQL = """
SELECT v.rule_id, v.severity, v.message, v.canonical_field,
       o.raw_value, o.source_column
FROM validation_result v
LEFT JOIN attribute_observation o ON o.observation_id = v.observation_id
WHERE v.record_id = %s AND v.outcome = 'fail'
ORDER BY v.severity DESC, v.rule_id
"""

_HISTORY_SQL = """
SELECT action, from_status, to_status, actor, note, created_at
FROM quarantine_event
WHERE record_id = %s
ORDER BY created_at
"""


def quarantine_queue(
    status: str | None = "open",
    entity_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Held-back records, oldest first, each with the failures that held it."""
    with connect() as conn:
        rows = _rows(
            conn,
            _QUARANTINE_SQL,
            {"status": status, "entity_type": entity_type, "limit": limit},
        )
        out = []
        for row in rows:
            record_id = str(row["record_id"])
            out.append(
                {
                    "record_id": record_id,
                    "batch_id": str(row["batch_id"]),
                    "status": row["status"],
                    "reason_codes": list(row["reason_codes"] or []),
                    "quarantined_at": row["quarantined_at"],
                    "reviewed_by": row["reviewed_by"],
                    "review_note": row["review_note"],
                    "reviewed_at": row["reviewed_at"],
                    "entity_type": row["entity_type"],
                    "source_name": row["source_name"],
                    "file_name": row["file_name"],
                    "row_number": row["row_number"],
                    "failures": [
                        {
                            "rule_id": f["rule_id"],
                            "severity": f["severity"],
                            "message": f["message"],
                            "canonical_field": f["canonical_field"],
                            "source_column": f["source_column"],
                            "raw_value": f["raw_value"],
                        }
                        for f in _rows(conn, _FAILURE_SQL, (record_id,))
                    ],
                }
            )
    return out


def quarantine_detail(record_id: str) -> dict[str, Any] | None:
    """One record: why it is held, what it says, and every decision made about it."""
    with connect() as conn:
        item = _one(conn, _QUARANTINE_ONE_SQL, {"record_id": record_id})
        if item is None:
            return None
        payload = _one(
            conn,
            "SELECT raw_payload FROM raw_record WHERE record_id = %s",
            (record_id,),
        )
        return {
            "record_id": str(item["record_id"]),
            "batch_id": str(item["batch_id"]),
            "status": item["status"],
            "reason_codes": list(item["reason_codes"] or []),
            "quarantined_at": item["quarantined_at"],
            "reviewed_by": item["reviewed_by"],
            "review_note": item["review_note"],
            "entity_type": item["entity_type"],
            "source_name": item["source_name"],
            "file_name": item["file_name"],
            "row_number": item["row_number"],
            # The verbatim row. A reviewer deciding whether a record is usable
            # needs to see what the vendor actually wrote, not a cleaned form.
            "raw_payload": (payload or {}).get("raw_payload", {}),
            "failures": _rows(conn, _FAILURE_SQL, (record_id,)),
            "history": _rows(conn, _HISTORY_SQL, (record_id,)),
        }
