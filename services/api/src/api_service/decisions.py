"""The write half of review: a person's decision, and what it sets in motion.

Every decision here is delegated. `set_mapping`, `accept_candidate` and
`review` are the same functions the three review CLIs call, so a decision made
in a browser is not a second implementation of a decision made in a terminal.

What this module adds is the *follow-through*, which the CLIs left to the
operator and which turned out to be the part people forgot:

  * accepting a merge leaves the surviving entity's golden record built from
    only half its observations until something rebuilds it;
  * releasing a record from quarantine makes it resolvable but does not resolve
    it, so it sits in a state no queue shows.

Both were documented as "re-run X afterwards". A documented manual step after an
irreversible decision is a step that gets missed, so the decision now carries it.
Follow-through failures are reported, never swallowed: the decision itself has
already committed, and a caller that is told it succeeded when the rebuild did
not would be worse off than one told exactly what happened.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from common.canonical import fields_for
from common.db import connect
from golden import pipeline as golden_pipeline
from mapping import repository as mapping_repository
from resolution import pipeline as resolution_pipeline
from validation import quarantine as quarantine_rules
from validation import repository as validation_repository

logger = logging.getLogger(__name__)

STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

FOLLOW_UP_REMAP = (
    "Values already stored keep their old attribution. Re-run normalization "
    "for the affected batch to apply this mapping to them."
)


def _built(result: golden_pipeline.BuildResult) -> dict[str, int]:
    """The parts of a build a reviewer cares about: what changed, and what did not."""
    return {
        "entities": result.entities,
        "written": result.written,
        "refreshed": result.refreshed,
        "unchanged": result.unchanged,
        "retired": result.retired,
    }


class DecisionRefused(Exception):
    """The decision cannot be made. The caller asked for something invalid."""


@dataclass
class Decision:
    """What was decided, and what followed from it."""

    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    follow_up: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "summary": self.summary,
            "detail": self.detail,
            "follow_up": self.follow_up,
        }


# --------------------------------------------------------------------------
# schema mapping
# --------------------------------------------------------------------------


def decide_mapping(
    mapping_id: str,
    canonical_field: str | None,
    reviewed_by: str,
) -> Decision:
    """Approve a column as a canonical field, or reject it as mapping to nothing.

    The correction is stored against the source_schema, so every future file
    with the same column layout inherits it without anyone deciding twice.
    """
    if canonical_field is not None:
        valid = {f.name for f in fields_for("person")} | {
            f.name for f in fields_for("company")
        }
        if canonical_field not in valid:
            raise DecisionRefused(f"{canonical_field!r} is not a canonical field")

    status = STATUS_REJECTED if canonical_field is None else STATUS_APPROVED

    with connect() as conn:
        updated = mapping_repository.set_mapping(
            conn, mapping_id, canonical_field, status, reviewed_by
        )
        if not updated:
            raise DecisionRefused(f"no mapping {mapping_id}")
        conn.commit()

    return Decision(
        summary=(
            f"column mapped to {canonical_field}"
            if canonical_field
            else "column marked as mapping to nothing"
        ),
        detail={
            "mapping_id": mapping_id,
            "canonical_field": canonical_field,
            "mapping_status": status,
            "reviewed_by": reviewed_by,
        },
        # Said rather than done: re-running normalization over stored
        # observations is a batch-sized job, and doing it silently inside a
        # click would be a surprise proportional to the file.
        follow_up=[FOLLOW_UP_REMAP],
    )


# --------------------------------------------------------------------------
# match candidates
# --------------------------------------------------------------------------


def decide_candidate(
    candidate_id: str,
    accept: bool,
    reviewed_by: str,
    note: str | None,
) -> Decision:
    """Merge the two entities, or confirm they are different and keep both."""
    try:
        if not accept:
            resolution_pipeline.reject_candidate(candidate_id, reviewed_by, note)
            return Decision(
                summary="rejected; both entities stay separate",
                detail={"candidate_id": candidate_id, "status": "rejected"},
            )
        result = resolution_pipeline.accept_candidate(candidate_id, reviewed_by, note)
    except resolution_pipeline.BatchNotResolvable as exc:
        raise DecisionRefused(str(exc)) from exc

    if result.get("already_merged"):
        return Decision(
            summary="already merged by an earlier decision",
            detail={"candidate_id": candidate_id, **result},
        )

    surviving = result["surviving_entity_id"]
    follow_up: list[str] = []
    try:
        built = golden_pipeline.build_one(surviving)
        rebuilt = _built(built)
    except golden_pipeline.NothingToBuild as exc:
        rebuilt = None
        follow_up.append(f"Golden record not rebuilt: {exc}")
    except Exception as exc:
        # The merge is committed. Reporting a failed rebuild is the honest
        # outcome; raising here would tell the caller the merge failed, which
        # is false and would invite them to try it again.
        logger.warning("golden rebuild after merge %s failed: %s", candidate_id, exc)
        rebuilt = None
        follow_up.append(
            f"Golden record rebuild failed ({exc}). Run: golden build "
            f"--entity-id {surviving}"
        )

    return Decision(
        summary=(
            f"merged {result['merged_entity_id']} into {surviving}; "
            "the absorbed id still resolves"
        ),
        detail={"candidate_id": candidate_id, **result, "golden_rebuilt": rebuilt},
        follow_up=follow_up,
    )


# --------------------------------------------------------------------------
# quarantine
# --------------------------------------------------------------------------

_ACTIONS = {
    "release": quarantine_rules.ACTION_RELEASED,
    "reject": quarantine_rules.ACTION_REJECTED,
    "reopen": quarantine_rules.ACTION_REOPENED,
}


def decide_quarantine(
    record_id: str,
    action: str,
    reviewed_by: str,
    note: str | None,
) -> Decision:
    """Release, reject or reopen one held-back record.

    A release is the only one with consequences downstream: the record becomes
    visible to entity resolution, which then has to actually run.
    """
    if action not in _ACTIONS:
        raise DecisionRefused(
            f"{action!r} is not a quarantine action; use one of {sorted(_ACTIONS)}"
        )

    with connect() as conn:
        item = validation_repository.get_quarantine_item(conn, record_id)
        if item is None:
            raise DecisionRefused(f"{record_id} is not quarantined")
        try:
            transition = quarantine_rules.review(
                _ACTIONS[action], item, reviewed_by, note
            )
        except ValueError as exc:
            raise DecisionRefused(str(exc)) from exc

        validation_repository.record_review(
            conn,
            str(item["quarantine_id"]),
            record_id,
            transition.to_status,
            transition.action,
            transition.from_status,
            transition.reason_codes,
            reviewed_by,
            note,
        )
        conn.commit()
        batch_id = str(item["batch_id"])

    detail: dict[str, Any] = {
        "record_id": record_id,
        "from_status": transition.from_status,
        "to_status": transition.to_status,
        "reviewed_by": reviewed_by,
    }
    summary = f"{transition.from_status} -> {transition.to_status}"

    if transition.to_status != quarantine_rules.STATUS_RELEASED:
        return Decision(summary=summary, detail=detail)

    resolved, follow_up = _carry_released_record_forward(batch_id)
    detail["downstream"] = resolved
    return Decision(
        summary=f"{summary}; now visible to entity resolution",
        detail=detail,
        follow_up=follow_up,
    )


def _carry_released_record_forward(batch_id: str) -> tuple[dict[str, Any], list[str]]:
    """Resolve and build what the release just made resolvable.

    Both calls are batch-scoped and both skip work already done — resolution
    reads only records with no `self` link, and the golden build reads only the
    entities that batch touched. Releasing one record out of a 190,000-row batch
    therefore costs one record, not the batch.
    """
    downstream: dict[str, Any] = {}
    follow_up: list[str] = []

    try:
        result = resolution_pipeline.resolve_batch(batch_id)
        downstream["resolution"] = {
            "records": result.records,
            "linked": result.linked,
            "created": result.created,
            "candidates": result.candidates,
        }
    except resolution_pipeline.BatchNotResolvable as exc:
        downstream["resolution"] = None
        follow_up.append(f"Nothing to resolve: {exc}")
        return downstream, follow_up
    except Exception as exc:
        logger.warning("resolution after release in batch %s failed: %s", batch_id, exc)
        downstream["resolution"] = None
        follow_up.append(
            f"Resolution failed ({exc}). The record is released; run: "
            f"resolve run --batch-id {batch_id}"
        )
        return downstream, follow_up

    try:
        built = golden_pipeline.build_batch(batch_id)
        downstream["golden"] = _built(built)
    except golden_pipeline.NothingToBuild as exc:
        downstream["golden"] = None
        follow_up.append(f"No golden record to build: {exc}")
    except Exception as exc:
        logger.warning("golden build after release in batch %s failed: %s", batch_id, exc)
        downstream["golden"] = None
        follow_up.append(
            f"Golden build failed ({exc}). Run: golden build --batch-id {batch_id}"
        )

    return downstream, follow_up
