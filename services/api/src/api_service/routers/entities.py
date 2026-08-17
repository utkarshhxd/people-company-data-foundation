"""Read endpoints over the trusted canonical data and its provenance.

Every route is a plain `def`, not `async def`: the lineage queries are
synchronous psycopg, and FastAPI runs sync handlers in a threadpool. Wrapping
them in async would either block the event loop or need a second, divergent set
of queries.

This API is read-only by design. Data enters through the ingestion pipeline,
where it acquires provenance; an endpoint that could write a golden value would
create records nothing can explain.
"""

from typing import Annotated

from common import lineage
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from api_service.auth import require_api_key

# Applied to the router rather than to each route, so a route added later is
# protected by default. Health checks live on their own router and stay open.
router = APIRouter(tags=["entities"], dependencies=[Depends(require_api_key)])

EntityId = Annotated[str, Path(description="Person or company id (merged ids resolve)")]


@router.get("/entities")
def search_entities(
    entity_type: Annotated[str | None, Query(pattern="^(person|company)$")] = None,
    q: Annotated[str | None, Query(description="match any identity key")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
) -> dict:
    """Find entities by any identifier a vendor ever gave them.

    Searching identity keys rather than golden values means an entity is still
    findable by an email or domain that lost the survivorship contest.
    """
    results = lineage.find_entities(entity_type, q, limit)
    return {"count": len(results), "results": results}


@router.get("/entities/{entity_id}")
def get_entity(entity_id: EntityId) -> dict:
    """The trusted record, plus every source that observed it."""
    summary = lineage.entity_summary(entity_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"no entity {entity_id}")
    return summary


@router.get("/entities/{entity_id}/explain/{canonical_field}")
def explain_field(entity_id: EntityId, canonical_field: str) -> dict:
    """Why this field holds this value, all the way back to the source cell.

    Returns the golden value with the rule that chose it, every observation that
    had a say (won or not) with its raw cell, and — kept as separate numbers —
    the mapping confidence for the column, the validation verdicts on the value,
    the match confidence that attached the record to this entity, and the
    vendor's reliability.
    """
    explanation = lineage.explain_value(entity_id, canonical_field)
    if explanation is None:
        raise HTTPException(
            status_code=404,
            detail=f"entity {entity_id} has nothing recorded for {canonical_field}",
        )
    return explanation


@router.get("/entities/{entity_id}/timeline")
def get_timeline(entity_id: EntityId) -> dict:
    """Everything that ever happened to this entity, in order."""
    events = lineage.entity_timeline(entity_id)
    if events is None:
        raise HTTPException(status_code=404, detail=f"no entity {entity_id}")
    return {"entity_id": entity_id, "count": len(events), "events": events}


@router.get("/entities/{entity_id}/relationships")
def get_relationships(entity_id: EntityId) -> dict:
    """Who this entity is related to: a person's employer, a company's people.

    One stored row answers both questions, read from either end.
    """
    result = lineage.entity_relationships(entity_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no entity {entity_id}")
    return result


@router.get("/records/{record_id}")
def get_record(record_id: str) -> dict:
    """The other direction: what became of one source row.

    Includes the verbatim payload, every observation it produced, its validation
    and quarantine state, the entity it was linked to, and which golden values
    it actually won.
    """
    record = lineage.record_lineage(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no record {record_id}")
    return record
