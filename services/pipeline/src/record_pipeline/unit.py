"""One record, carried from raw row to golden value, as a single unit of work.

The shape of a record's turn through here is deliberate:

  1. compute      — normalize and validate entirely in memory, no database
  2. one read     — which entities do this record's identity keys already reach?
  3. one write    — everything the record produces, including the entity's
                    rebuilt golden values, in one transaction and one commit

Steps 1 and 2 are what make step 3 a single flush. Nothing in the write depends
on a value that has to come back from the server first, because the ids are
chosen here rather than by the column defaults. That is the whole trick: the
record stays the unit of meaning *and* the unit of I/O.

Because golden is inside that same transaction, a record and everything derived
from it become visible together. When process() returns, there is nothing left
outstanding about that record — which is what makes it safe to hand straight to
another system.

Every decision below is made by the module that owns it. This file chooses the
order and the transaction boundary, and nothing else.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from common.ids import uuid7
from golden.pipeline import build_entity
from ingestion import repository as ingest_repo
from normalization import repository as norm_repo
from normalization.pipeline import observations_for_record
from resolution.employer import resolve_employer
from resolution.keys import keys_for
from resolution.pipeline import apply_decision, candidates_for
from validation import repository as validation_repo
from validation.pipeline import STATUS_INVALID, validate_record
from validation.quarantine import route
from validation.rules import FAIL

logger = logging.getLogger(__name__)

# The rule that decides an address is a shared mailbox rather than a person's.
# Resolution needs the answer; asking validation for it keeps one definition of
# "role account" in the system.
ROLE_EMAIL_RULE = "email.role_account"

# Index of these fields in the tuples validate_record produces.
_RESULT_RULE_ID = 5
_RESULT_OUTCOME = 7


@dataclass
class RecordOutcome:
    """What became of one record. This is also what gets published."""
    record_id: str
    row_number: int
    source_record_id: str
    validation_status: str
    quarantined: bool
    observations: int
    judgements: int
    entity_id: str | None = None
    match_decision: str | None = None
    failed_rules: list[str] = field(default_factory=list)
    golden_written: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _observation_dicts(tuples: list[tuple]) -> list[dict]:
    """Give each observation an id now, and shape it the way validation reads it.

    The keys match what validation.repository.iter_records yields, so
    validate_record cannot tell whether its input came from memory or from a
    query — which is what keeps the two paths honest about producing the same
    judgements.
    """
    out = []
    for row in tuples:
        (record_id, _batch_id, _source_id, _entity_type, source_column,
         canonical_field, _status, value_index, raw_value, normalized_value,
         value_type, method, is_null_token, _observed_at, subject) = row
        out.append({
            "observation_id": uuid7(),
            "record_id": record_id,
            "source_column": source_column,
            "canonical_field": canonical_field,
            "value_index": value_index,
            "raw_value": raw_value,
            "normalized_value": normalized_value,
            "value_type": value_type,
            "normalization_method": method,
            "is_null_token": is_null_token,
            "subject": subject,
        })
    return out


def _confirmed_values(
    observations: list[dict], subject: str = "self"
) -> dict[str, list[str]]:
    """Confirmed canonical field -> normalized values, for one subject.

    Sorted by (source_column, value_index) to match resolution's own query
    exactly. Identity keys are built from these in order, so a different order
    could build a different key from the same record — the per-record path
    producing different entities than the batch path is precisely the failure
    this ordering prevents.

    Filtering by subject is what keeps a person's identity keys built from the
    person's own attributes. An employer's phone number is not a key to the
    person, and letting it in would link colleagues to each other.
    """
    values: dict[str, list[str]] = {}
    for obs in sorted(observations, key=lambda o: (o["source_column"], o["value_index"])):
        if obs["canonical_field"] is None or obs["normalized_value"] is None:
            continue
        if obs.get("subject", "self") != subject:
            continue
        values.setdefault(obs["canonical_field"], []).append(obs["normalized_value"])
    return values


def _has_role_email(result_rows: list[tuple]) -> bool:
    return any(
        row[_RESULT_RULE_ID] == ROLE_EMAIL_RULE and row[_RESULT_OUTCOME] == FAIL
        for row in result_rows
    )


def process(
    ctx, conn, source_record_id: str, row_number: int,
    payload: dict[str, str | None], *, build_golden: bool = True,
) -> RecordOutcome:
    """Run one record the whole way through. Commits it, or raises."""
    record_id = uuid7()
    observed_at = datetime.now(UTC)

    # ---- 1. compute, in memory ------------------------------------------
    observation_tuples = observations_for_record(
        record_id, payload, ctx.mappings, ctx.entity_type, observed_at,
        ctx.batch_id, ctx.source_id,
    )
    observations = _observation_dicts(observation_tuples)

    verdict = validate_record(
        record_id, observations, ctx.entity_type, ctx.batch_id, ctx.source_id
    )
    # A record arriving here is new, so it has no quarantine history to weigh.
    transition = route(verdict.status, verdict.error_rules, None)

    # An invalid record is recorded in full and then stops: it keeps its raw
    # payload, every observation and every judgement, and sits in quarantine
    # until a human decides. What it must not do is silently become an entity.
    resolvable = verdict.status != STATUS_INVALID

    # ---- 2. one read -----------------------------------------------------
    keys: list = []
    decision = None
    entity_id: str | None = None
    employer_entity_id: str | None = None
    if resolvable:
        keys = keys_for(
            ctx.entity_type, _confirmed_values(observations), ctx.source_id,
            role_email=_has_role_email(verdict.result_rows),
        )
        # Reads the entities built by every record before this one, which is why
        # records must go through in file order: record 900 has to be able to
        # match the entity record 12 created.
        decision = candidates_for(conn, ctx.entity_type, keys) if keys else None
        entity_id = str(uuid7())

    # ---- 3. one write ----------------------------------------------------
    # Pipeline mode sends these without waiting for each result, and the commit
    # syncs. The record is still exactly one transaction: it lands whole or not
    # at all.
    with conn.pipeline():
        ingest_repo.insert_raw_record(
            conn, record_id, ctx.batch_id, ctx.source_id, ctx.entity_type,
            source_record_id, row_number, payload,
        )
        norm_repo.insert_observations_with_ids(conn, [
            (obs["observation_id"], record_id, ctx.batch_id, ctx.source_id,
             ctx.entity_type, obs["source_column"], obs["canonical_field"],
             _mapping_status(ctx, obs["source_column"]), obs["value_index"],
             obs["raw_value"], obs["normalized_value"], obs["value_type"],
             obs["normalization_method"], obs["is_null_token"], observed_at,
             obs["subject"])
            for obs in observations
        ])
        validation_repo.insert_results(conn, verdict.result_rows)
        validation_repo.upsert_record_validation(conn, [verdict.record_row])

        if transition is not None:
            validation_repo.apply_transitions(conn, [(
                record_id, ctx.batch_id, ctx.source_id, ctx.entity_type,
                transition.to_status,
                validation_repo.json_value(transition.reason_codes),
                len(transition.reason_codes),
                transition.action, transition.from_status, transition.to_status,
                validation_repo.json_value(transition.reason_codes),
                transition.actor, transition.note,
            )])

        if resolvable:
            _, entity_id = apply_decision(
                conn, record_id, ctx.entity_type, ctx.batch_id, ctx.source_id,
                keys, decision, new_entity_id=entity_id,
            )
            # The employer named inside a person row, resolved to a company of
            # its own. Inside the same transaction as everything else the record
            # produced, so a record and its employment land together.
            if ctx.entity_type == "person":
                employer_values = _confirmed_values(observations, "employer")
                if employer_values:
                    employer_entity_id = resolve_employer(
                        conn, record_id, ctx.batch_id, ctx.source_id,
                        employer_values, str(entity_id),
                        new_entity_id=str(uuid7()),
                    )

        # ---- 4. golden, in the same transaction --------------------------
        # Inside the record's own transaction, not after it. Two reasons, and
        # the second is why this is not merely an optimization:
        #
        #   * one commit per record instead of two. Profiling a 20,000-record
        #     run showed the pipeline was fsync-bound, so halving the commits
        #     halves the dominant cost.
        #   * there is no longer a window in which the record exists but the
        #     entity's golden values have not caught up with it. A record and
        #     its consequences land together or not at all.
        #
        # build_entity reads the observations this transaction just wrote, which
        # works because a transaction always sees its own uncommitted writes.
        golden_written = 0
        if resolvable and build_golden and entity_id:
            written, refreshed, _unchanged, _retired = build_entity(
                conn, str(entity_id), ctx.entity_type
            )
            golden_written = written + refreshed
            # The employer is an entity with trusted values of its own, and it
            # gains them from the same record in the same transaction. Skipping
            # it would leave a company that exists but says nothing.
            if employer_entity_id:
                written, refreshed, _unchanged, _retired = build_entity(
                    conn, employer_entity_id, "company"
                )
                golden_written += written + refreshed

        conn.commit()

    outcome = RecordOutcome(
        record_id=str(record_id),
        row_number=row_number,
        source_record_id=source_record_id,
        validation_status=verdict.status,
        quarantined=transition is not None and transition.to_status == "open",
        observations=len(observations),
        judgements=len(verdict.result_rows),
        entity_id=str(entity_id) if entity_id else None,
        match_decision=_decision_name(decision) if resolvable else None,
        failed_rules=verdict.error_rules,
        golden_written=golden_written,
    )
    return outcome


def _mapping_status(ctx, source_column: str) -> str:
    mapping = ctx.mappings.get(source_column)
    return mapping["mapping_status"] if mapping else "unmapped"


def _decision_name(decision) -> str:
    if decision is None:
        return "no_identity_keys"
    return decision.decision
