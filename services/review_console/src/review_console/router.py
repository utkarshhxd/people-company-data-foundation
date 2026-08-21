"""The four review queues, as data and as a console.

Everything here is a projection of state the pipeline already keeps, plus four
POSTs that delegate to the stage services' own decision functions. There is no
review state of its own: closing a mapping, a candidate, a quarantine item or
an enrichment proposal writes to the same tables the CLIs write to, and the
CLIs keep working.

Every route is a plain `def`. The queries are synchronous psycopg and FastAPI
runs sync handlers in a threadpool, so an `async def` here would either block the
event loop or need a second set of queries that could disagree with the first.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Path, Query, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from ingestion.readers import DEFAULT_BATCH_SIZE
from pydantic import BaseModel, Field

from review_console import decisions, entities, pipeline, review, stages, stream, uploads
from review_console.admin_page import ADMIN_PAGE
from review_console.auth import require_api_key
from review_console.dashboard_page import DASHBOARD_PAGE
from review_console.review_page import REVIEW_PAGE

router = APIRouter(
    prefix="/review",
    tags=["review"],
    dependencies=[Depends(require_api_key)],
)

dashboard_router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_api_key)],
)

entities_router = APIRouter(
    prefix="/entities",
    tags=["entities"],
    dependencies=[Depends(require_api_key)],
)

MAPPING_STATUSES = "^(needs_review|auto_accepted|approved|rejected|unmapped)$"
CANDIDATE_STATUSES = "^(open|accepted|rejected)$"
QUARANTINE_STATUSES = "^(open|released|rejected|resolved)$"
ENRICHMENT_STATUSES = "^(pending|accepted|rejected)$"


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


class EnrichmentDecision(BaseModel):
    """Confirm an AI guess as a real observation, or decline it."""

    accept: bool
    reviewed_by: str = Field(min_length=1)


def _decided(call) -> dict[str, Any]:
    """Run a decision, turning a refusal into a 409 rather than a 500.

    A refused decision is not a server fault: the reviewer asked for something
    the data does not allow -- a record already released, a candidate already
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
# AI enrichment proposals
# --------------------------------------------------------------------------


@router.get("/enrichment")
def get_enrichment(
    status: Annotated[str | None, Query(pattern=ENRICHMENT_STATUSES)] = "pending",
    entity_type: Annotated[str | None, Query(pattern="^(person|company)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """AI guesses awaiting a human, each with the entity's other known values."""
    rows = review.enrichment_queue(status, entity_type, limit)
    return {"count": len(rows), "results": rows}


@router.post("/enrichment/{proposal_id}")
def decide_enrichment(
    proposal_id: str,
    body: Annotated[EnrichmentDecision, Body()],
) -> dict:
    """Confirm a proposal as a real observation, or decline it.

    Confirming also rebuilds the entity's golden record: a guess a reviewer
    just confirmed is human-confirmed evidence and should count as such in
    survivorship immediately, not after a separately remembered rebuild.
    """
    return _decided(
        lambda: decisions.decide_enrichment(
            proposal_id, body.accept, body.reviewed_by
        )
    )


# --------------------------------------------------------------------------
# pipeline dashboard
# --------------------------------------------------------------------------


@dashboard_router.get("/stream")
async def stream_dashboard(
    batch_id: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    """Push, not poll: an open connection carrying the summary, the batch
    list, review-queue depths, and (if given) one batch's detail, on every
    server-side tick. See stream.py for what "push" means here.

    `async def`, unlike every other route in this file, because holding a
    connection open without blocking a threadpool worker for it needs to be.
    """
    return StreamingResponse(
        stream.events(batch_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@dashboard_router.get("/summary")
def get_dashboard_summary() -> dict:
    """The funnel across every batch ever loaded: how many records reached
    each stage, and how many are sitting in a queue instead of moving."""
    return pipeline.global_summary()


@dashboard_router.get("/batches")
def get_dashboard_batches(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict:
    """Recent batches, most recent first, with live progress while running."""
    rows = pipeline.recent_batches(limit)
    return {"count": len(rows), "results": rows}


@dashboard_router.get("/batches/{batch_id}")
def get_dashboard_batch(batch_id: str) -> dict:
    """One batch: its own counters, plus the funnel of where its records are."""
    detail = pipeline.batch_detail(batch_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no batch {batch_id}")
    return detail


@dashboard_router.post("/ingest")
def ingest_upload(
    file: Annotated[UploadFile, File()],
    entity_type: Annotated[str, Form(pattern="^(person|company)$")],
    source_name: Annotated[str, Form(min_length=1)],
    source_type: Annotated[str | None, Form(pattern="^(csv|excel)$")] = None,
    record_id_column: Annotated[str | None, Form()] = None,
    reliability: Annotated[float, Form(ge=0, le=1)] = 0.5,
    describes: Annotated[str | None, Form(pattern="^(organisation|location)$")] = None,
    batch_size: Annotated[int, Form(ge=1)] = DEFAULT_BATCH_SIZE,
    allow_reingest: Annotated[bool, Form()] = False,
    sheet: Annotated[str | None, Form()] = None,
) -> dict:
    """Save the dropped file and start ingesting it; poll /ingest/{upload_id}.

    Runs in a background thread, same reasoning as stage runs: a 10-million-row
    file takes far longer than an HTTP request should stay open for.
    """
    try:
        state = uploads.start(
            file.file,
            file.filename or "upload",
            entity_type=entity_type,
            source_name=source_name,
            source_type=source_type,
            record_id_column=record_id_column or None,
            reliability=reliability,
            describes=describes,
            batch_size=batch_size,
            allow_reingest=allow_reingest,
            sheet=sheet or None,
        )
    except uploads.UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"upload_id": state.upload_id, "status": state.status}


@dashboard_router.get("/ingest/{upload_id}")
def get_ingest_status(upload_id: str) -> dict:
    state = uploads.status(upload_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no upload {upload_id}")
    return state


@dashboard_router.post("/batches/{batch_id}/stages/{stage}/run")
def run_stage(
    batch_id: str,
    stage: Annotated[str, Path(pattern="^(mapping|normalization|validation|resolution|golden)$")],
) -> dict:
    """Start that stage's whole-batch function; poll /batches/{batch_id} for progress.

    Runs in a background thread, not this request: a batch-sized run can take
    far longer than an HTTP request should stay open. Refuses a second start
    for the same (stage, batch) while one is already running.
    """
    try:
        state = stages.start(stage, batch_id)
    except stages.AlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"stage": stage, "batch_id": batch_id, "status": state.status}


# --------------------------------------------------------------------------
# entity browser
# --------------------------------------------------------------------------


@entities_router.get("/search")
def search_entities(
    q: Annotated[str, Query(min_length=1)],
    entity_type: Annotated[str | None, Query(pattern="^(person|company)$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict:
    """Entities findable by any identity key they carry -- an email, a
    domain, a vendor's own id -- not just their current golden name."""
    rows = entities.search(entity_type, q, limit)
    return {"count": len(rows), "results": rows}


@entities_router.get("/{entity_id}")
def get_entity(entity_id: str) -> dict:
    """The golden record, who observed it, and what it's related to."""
    detail = entities.detail(entity_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no entity {entity_id}")
    return detail


@entities_router.get("/{entity_id}/fields/{canonical_field}")
def get_entity_field(entity_id: str, canonical_field: str) -> dict:
    """Every source cell that had a say in this one field's trusted value."""
    explanation = entities.explain(entity_id, canonical_field)
    if explanation is None:
        raise HTTPException(
            status_code=404,
            detail=f"no {canonical_field!r} evidence for entity {entity_id}",
        )
    return explanation


@entities_router.get("/{entity_id}/timeline")
def get_entity_timeline(entity_id: str) -> dict:
    events = entities.timeline(entity_id)
    if events is None:
        raise HTTPException(status_code=404, detail=f"no entity {entity_id}")
    return {"count": len(events), "events": events}


# --------------------------------------------------------------------------
# the console(s)
# --------------------------------------------------------------------------

# Outside the router, so it is not behind the key: the page is a shell that
# fetches the endpoints above, and those requests carry the key. Gating the
# shell too would mean a browser could never reach the point of being asked.
page_router = APIRouter(tags=["review"])


@page_router.get("/review/page", response_class=HTMLResponse, include_in_schema=False)
def review_page() -> str:
    return REVIEW_PAGE


@page_router.get("/dashboard/page", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page() -> str:
    return DASHBOARD_PAGE


@page_router.get("/admin/page", response_class=HTMLResponse, include_in_schema=False)
def admin_page() -> str:
    """Pipeline progress and all four review queues, one page, tabbed.

    Combines /review/page and /dashboard/page rather than replacing either:
    both stay working for anything that already links to them.
    """
    return ADMIN_PAGE
