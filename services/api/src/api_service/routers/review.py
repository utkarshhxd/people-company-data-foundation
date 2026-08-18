"""The review queues, as data and as a console.

Everything here is a projection of state the pipeline already keeps, plus four
POSTs that delegate to the stage services' own decision functions. There is no
review state of its own: closing a mapping, a candidate or a quarantine item
writes to the same tables the CLIs write to, and the CLIs keep working.

Every route is a plain `def`. The queries are synchronous psycopg and FastAPI
runs sync handlers in a threadpool, so an `async def` here would either block the
event loop or need a second set of queries that could disagree with the first.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from api_service import decisions, review
from api_service.auth import require_api_key
from api_service.review_page import REVIEW_PAGE

router = APIRouter(
    prefix="/review",
    tags=["review"],
    dependencies=[Depends(require_api_key)],
)

MAPPING_STATUSES = "^(needs_review|auto_accepted|approved|rejected|unmapped)$"
CANDIDATE_STATUSES = "^(open|accepted|rejected)$"
QUARANTINE_STATUSES = "^(open|released|rejected|resolved)$"


class MappingDecision(BaseModel):
    """Approve a column as a canonical field, or reject it as mapping to nothing."""

    canonical_field: str | None = Field(
        default=None,
        description="The canonical field this column represents. Null rejects it.",
    )
    reviewed_by: str = Field(min_length=1, description="Who decided. Recorded.")


class CandidateDecision(BaseModel):
    """Accept a proposed match (merging the entities) or reject it (keeping both)."""

    accept: bool
    reviewed_by: str = Field(min_length=1)
    note: str | None = Field(default=None, description="Why. Recorded permanently.")


class QuarantineDecision(BaseModel):
    """Release, reject or reopen a held-back record."""

    action: str = Field(pattern="^(release|reject|reopen)$")
    reviewed_by: str = Field(min_length=1)
    note: str | None = Field(default=None, description="Why. Recorded permanently.")


def _decided(call) -> dict[str, Any]:
    """Run a decision, turning a refusal into a 409 rather than a 500.

    A refused decision is not a server fault: the reviewer asked for something
    the data does not allow — a record already released, a candidate already
    closed, a field that is not in the vocabulary.
    """
    try:
        return call().as_dict()
    except decisions.DecisionRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------


@router.get("/summary")
def get_summary() -> dict:
    """How much is waiting for a human, and how long the oldest has waited.

    Age is reported next to depth because depth alone hides the failure that
    matters: a queue of three is fine, three that have not moved in a fortnight
    means reviewing has stopped.
    """
    return review.queue_summary()


# --------------------------------------------------------------------------
# schema mapping
# --------------------------------------------------------------------------


@router.get("/mappings")
def get_mappings(
    status: Annotated[str | None, Query(pattern=MAPPING_STATUSES)] = "needs_review",
    source_schema_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """Columns awaiting a decision, each with what the column actually contains."""
    rows = review.mapping_queue(status, source_schema_id, limit)
    return {"count": len(rows), "results": rows}


@router.get("/fields/{entity_type}")
def get_fields(
    entity_type: Annotated[str, Path(pattern="^(person|company)$")],
) -> dict:
    """The canonical vocabulary a column may be mapped to."""
    fields = review.canonical_fields(entity_type)
    return {"entity_type": entity_type, "count": len(fields), "fields": fields}


@router.post("/mappings/{mapping_id}")
def decide_mapping(
    mapping_id: str,
    body: Annotated[MappingDecision, Body()],
) -> dict:
    """Record a human mapping decision. It survives future files of this layout."""
    return _decided(
        lambda: decisions.decide_mapping(
            mapping_id, body.canonical_field, body.reviewed_by
        )
    )


# --------------------------------------------------------------------------
# match candidates
# --------------------------------------------------------------------------


@router.get("/candidates")
def get_candidates(
    status: Annotated[str, Query(pattern=CANDIDATE_STATUSES)] = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """Proposed matches, each as two sides to compare rather than two ids."""
    rows = review.candidate_queue(status, limit)
    return {"count": len(rows), "results": rows}


@router.post("/candidates/{candidate_id}")
def decide_candidate(
    candidate_id: str,
    body: Annotated[CandidateDecision, Body()],
) -> dict:
    """Merge two entities, or confirm they are different.

    Accepting also rebuilds the surviving entity's golden record, because a
    merge that is not followed by a rebuild leaves the trusted values computed
    from half the observations.
    """
    return _decided(
        lambda: decisions.decide_candidate(
            candidate_id, body.accept, body.reviewed_by, body.note
        )
    )


# --------------------------------------------------------------------------
# quarantine
# --------------------------------------------------------------------------


@router.get("/quarantine")
def get_quarantine(
    status: Annotated[str | None, Query(pattern=QUARANTINE_STATUSES)] = "open",
    entity_type: Annotated[str | None, Query(pattern="^(person|company)$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """Held-back records, oldest first, each with the failures holding it."""
    rows = review.quarantine_queue(status, entity_type, limit)
    return {"count": len(rows), "results": rows}


@router.get("/quarantine/{record_id}")
def get_quarantine_record(record_id: str) -> dict:
    """One record: the verbatim row, its failures, and every decision about it."""
    detail = review.quarantine_detail(record_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"{record_id} is not quarantined")
    return detail


@router.post("/quarantine/{record_id}")
def decide_quarantine(
    record_id: str,
    body: Annotated[QuarantineDecision, Body()],
) -> dict:
    """Release, reject or reopen a record.

    Releasing also runs resolution and the golden build for what the release
    made resolvable, so a released record does not sit in a state no queue shows.
    """
    return _decided(
        lambda: decisions.decide_quarantine(
            record_id, body.action, body.reviewed_by, body.note
        )
    )


# --------------------------------------------------------------------------
# the console
# --------------------------------------------------------------------------

# Outside the router, so it is not behind the key: the page is a shell that
# fetches the endpoints above, and those requests carry the key. Gating the
# shell too would mean a browser could never reach the point of being asked.
page_router = APIRouter(tags=["review"])


@page_router.get("/review/page", response_class=HTMLResponse, include_in_schema=False)
def review_page() -> str:
    return REVIEW_PAGE
