"""Search entities and read their golden record + provenance, browser-side.

Nothing here queries anything new. `common.lineage` already has every query --
`find_entities`, `entity_summary`, `explain_value`, `entity_relationships`,
`entity_timeline` -- built for the `golden explain` CLI, and before it was
deleted, the old read API. This module only shapes what the CLI already calls
into responses the browser can render, so a value's provenance answers the
same way here as it does in a terminal.
"""

from typing import Any

from common import lineage
from common.db import connect
from psycopg.rows import dict_row

# `lineage.find_entities` deliberately searches identity KEYS (an email, a
# domain, a vendor's own id) rather than golden values -- built for "is this
# the same thing a vendor already told us about", which is what the CLI it
# backs needs. A person typing into a search box expects a name to work too,
# so this adds that one extra path and merges the two -- without touching
# `lineage.py`, which the CLI still needs to behave exactly as it does today.
_NAME_FIELDS = ["full_name", "company_name", "legal_name"]

_NAME_SEARCH_SQL = """
SELECT DISTINCT e.entity_id, e.entity_type,
       (SELECT g2.value FROM golden_attribute g2
         WHERE g2.entity_id = e.entity_id AND g2.valid_to IS NULL
           AND g2.canonical_field IN ('company_name', 'full_name')
         LIMIT 1) AS display_name,
       (SELECT count(*) FROM record_entity_link l
         WHERE l.entity_id = e.entity_id) AS record_count
FROM entity e
JOIN golden_attribute g ON g.entity_id = e.entity_id AND g.valid_to IS NULL
WHERE e.status = 'active'
  AND g.canonical_field = ANY(%(fields)s)
  AND g.value ILIKE %(query)s
  AND (%(entity_type)s::text IS NULL OR e.entity_type = %(entity_type)s::text)
LIMIT %(limit)s
"""


def _search_by_name(
    entity_type: str | None, query: str, limit: int
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            _NAME_SEARCH_SQL,
            {
                "fields": _NAME_FIELDS,
                "query": f"%{query}%",
                "entity_type": entity_type,
                "limit": limit,
            },
        )
        return cur.fetchall()


def search(
    entity_type: str | None, query: str, limit: int = 25
) -> list[dict[str, Any]]:
    """Entities findable by name, or by any identity key they carry."""
    by_key = lineage.find_entities(entity_type, query, limit)
    seen = set()
    combined = []
    for row in by_key:
        row["entity_id"] = str(row["entity_id"])
        seen.add(row["entity_id"])
        combined.append(row)

    for row in _search_by_name(entity_type, query, max(0, limit - len(combined))):
        row["entity_id"] = str(row["entity_id"])
        if row["entity_id"] not in seen:
            seen.add(row["entity_id"])
            combined.append(row)
    return combined


def detail(entity_id: str) -> dict[str, Any] | None:
    """Golden record plus who works there / where they work, in one response."""
    summary = lineage.entity_summary(entity_id)
    if summary is None:
        return None
    resolved_id = summary["entity_id"]
    relationships = lineage.entity_relationships(resolved_id)
    return {
        **summary,
        "relationships": relationships["relationships"] if relationships else [],
    }


def explain(entity_id: str, canonical_field: str) -> dict[str, Any] | None:
    """Every source cell that had a say in one field's trusted value."""
    return lineage.explain_value(entity_id, canonical_field)


def timeline(entity_id: str) -> list[dict[str, Any]] | None:
    """Everything that ever happened to this entity, in order."""
    return lineage.entity_timeline(entity_id)
