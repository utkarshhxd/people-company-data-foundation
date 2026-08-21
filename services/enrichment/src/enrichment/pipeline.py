"""Two operations, deliberately not one.

`run()` is the batch job: ask the model about entities missing an eligible
field, in a bounded window, and file every confident answer as a pending
proposal. It never writes an observation -- ADR 0014 draws the line there: AI
may propose, never assert. See ELIGIBLE_FIELDS in enrichment.engine for what
"eligible" excludes and why.

`accept()` is the human decision, run through `enrich proposals accept`
(review-console.md's fourth queue). Only there does a proposal become a real
attribute_observation, through the same write path a vendor row would use.

Meant to run in a bounded window -- start the model, work through at most
`limit_per_field` entities per eligible field, stop -- not continuously. Each
(entity, field) pair is asked about at most once (see enrichment_attempt), so
a run that has caught up on a source costs nothing on the next one.
"""

import logging
from dataclasses import dataclass

from common.config import settings
from common.db import connect
from normalization.normalizers import normalize

from enrichment import repository
from enrichment.engine import ELIGIBLE_FIELDS, ask_for_field

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    entity_type: str
    attempted: int = 0
    proposed: int = 0
    declined: int = 0
    errors: int = 0


def run(entity_type: str, limit_per_field: int = 25) -> RunResult:
    result = RunResult(entity_type=entity_type)
    model = settings.ollama_model

    with connect() as conn:
        for field_name in ELIGIBLE_FIELDS.get(entity_type, ()):
            entity_ids = repository.candidate_entities(
                conn, entity_type, field_name, limit_per_field
            )
            for entity_id in entity_ids:
                result.attempted += 1
                known = repository.known_values(conn, entity_id)

                try:
                    answer = ask_for_field(entity_type, field_name, known)
                except Exception:
                    logger.exception(
                        "ai enrichment request failed for entity %s field %s",
                        entity_id, field_name,
                    )
                    repository.record_attempt(
                        conn, entity_id, entity_type, field_name, "error", model, None
                    )
                    conn.commit()
                    result.errors += 1
                    continue

                if answer is None:
                    repository.record_attempt(
                        conn, entity_id, entity_type, field_name, "declined", model, None
                    )
                    conn.commit()
                    result.declined += 1
                    continue

                value, stated_confidence, value_type = answer
                repository.create_proposal(
                    conn, entity_id, entity_type, field_name, value, value_type,
                    stated_confidence, model,
                )
                repository.record_attempt(
                    conn, entity_id, entity_type, field_name, "written", model,
                    stated_confidence,
                )
                conn.commit()
                result.proposed += 1

    logger.info(
        "enrichment run (%s): %d attempted, %d proposed, %d declined, %d error(s)",
        entity_type, result.attempted, result.proposed, result.declined, result.errors,
    )
    return result


class ProposalNotPending(Exception):
    pass


def accept(proposal_id: str, reviewed_by: str) -> str:
    """A human confirms a proposal. Only now does it become an observation --
    the accepted value goes through the exact same write path a vendor row
    would, via a synthetic, low-reliability source.

    Returns the new record_id. Does not rebuild the golden record: consistent
    with every other review decision in this project (see review-console.md,
    "What a decision sets in motion"), the caller runs
    `golden build --entity-id <id>` afterward.
    """
    with connect() as conn:
        proposal = repository.get_proposal(conn, proposal_id)
        if proposal is None or proposal["status"] != "pending":
            raise ProposalNotPending(f"no pending proposal {proposal_id}")

        source_id = repository.get_or_create_ai_source(
            conn, settings.ai_enrichment_reliability
        )
        batch_id = repository.create_run_batch(conn, source_id, proposal["entity_type"])

        normalized = normalize(proposal["proposed_value"], proposal["value_type"])
        record_id = repository.write_observation(
            conn, batch_id=batch_id, source_id=source_id,
            entity_id=str(proposal["entity_id"]), entity_type=proposal["entity_type"],
            canonical_field=proposal["canonical_field"],
            raw_value=proposal["proposed_value"],
            normalized_value=normalized.normalized_value,
            value_type=proposal["value_type"], normalization_method=normalized.method,
            is_null_token=normalized.is_null_token,
        )
        repository.set_proposal_status(conn, proposal_id, "accepted", reviewed_by, record_id)
        conn.commit()

    return record_id


def reject(proposal_id: str, reviewed_by: str) -> bool:
    with connect() as conn:
        updated = repository.set_proposal_status(conn, proposal_id, "rejected", reviewed_by)
        conn.commit()
    return updated
