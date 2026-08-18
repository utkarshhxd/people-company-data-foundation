"""Validate a batch's attribute observations and summarize each record.

What this module will not do, by design:

  * change a value            - the observation is evidence; evidence is not edited
  * drop a record             - 'invalid' is a label, not a delete
  * decide which record wins  - that is the golden record's job, later

It only attaches judgements, and records which rules ran so the absence of a
judgement can be told apart from a clean bill of health.
"""

import logging
from dataclasses import dataclass

from common.db import connect

from validation import repository
from validation.quarantine import route
from validation.record_rules import judge_record
from validation.rules import (
    FAIL,
    RULESET_VERSION,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    AttributeContext,
    judge_attribute,
)

logger = logging.getLogger(__name__)

RECORD_FLUSH_SIZE = 500

STATUS_VALID = "valid"
STATUS_WARNING = "warning"
STATUS_INVALID = "invalid"


class BatchNotValidatable(Exception):
    pass


@dataclass
class ValidateResult:
    batch_id: str
    records: int
    judgements: int
    counts: dict[str, int]
    failures: list[dict]


def _status_for(
    record_errors: int, attribute_errors: int, warning_failures: int
) -> str:
    """Is this record safe to resolve to an entity?

    Only a *record*-scope error says no. Those are the rules that ask whether
    the row could ever identify anybody — no identifying attribute, nothing but
    null tokens — and a row failing one of them has nothing to resolve on.

    An *attribute* error says a value cannot serve as the field it was mapped
    to. That is a verdict on the value, not on the row. Real vendor exports
    carry junk in one column routinely: a c_suite file shipped the literal
    string `[object Object]` in `Phone` for 907 rows out of 1,000, every one of
    which still had an email, a full name and a LinkedIn URL. Quarantining
    those records would have withheld 90% of a perfectly identifiable file over
    a column nobody needed, and a human reviewing them could only have said
    "yes, the vendor's phone column is broken" nine hundred times.

    The unusable value is still recorded as an error and still excluded from
    resolution — it simply does not condemn everything beside it. Whether the
    record retains an identifying attribute is a question the record rules ask
    directly, which is where it belongs.
    """
    if record_errors:
        return STATUS_INVALID
    if attribute_errors or warning_failures:
        return STATUS_WARNING
    return STATUS_VALID


@dataclass
class RecordVerdict:
    status: str
    result_rows: list[tuple]
    record_row: tuple
    # Only the error-severity failures. Warnings never quarantine a record, so
    # keeping the two apart is what stops quarantine from swallowing a whole file.
    error_rules: list[str]


def validate_record(
    record_id, observations: list[dict], entity_type: str, batch_id: str, source_id: str
) -> RecordVerdict:
    """One record -> its validation_result rows, summary row, and error reasons."""
    result_rows: list[tuple] = []
    validated = unvalidated = 0
    errors = warnings = 0
    failed_rules: list[str] = []
    error_rules: list[str] = []
    # Kept apart because they mean different things: one condemns a value, the
    # other condemns the row. See _status_for.
    record_errors = 0

    for obs in observations:
        # An unconfirmed mapping means we do not know what the value is supposed
        # to be, and validating it against a guessed field would produce a
        # judgement about our own guess rather than about the data.
        if obs["canonical_field"] is None:
            unvalidated += 1
            continue

        ctx = AttributeContext(
            canonical_field=obs["canonical_field"],
            entity_type=entity_type,
            value_type=obs["value_type"],
            raw_value=obs["raw_value"],
            normalization_method=obs["normalization_method"],
        )
        judgements = judge_attribute(obs["normalized_value"], ctx)
        if not judgements:
            unvalidated += 1
            continue
        validated += 1

        for judgement in judgements:
            if judgement.outcome == FAIL:
                # Info-severity failures are recorded in validation_result but
                # kept out of failed_rules, which is read as "what is wrong with
                # this record" — coverage notes would drown the real problems.
                if judgement.severity == SEVERITY_ERROR:
                    errors += 1
                    error_rules.append(judgement.rule_id)
                    failed_rules.append(judgement.rule_id)
                elif judgement.severity == SEVERITY_WARNING:
                    warnings += 1
                    failed_rules.append(judgement.rule_id)
            result_rows.append((
                obs["observation_id"], record_id, batch_id, "attribute",
                obs["canonical_field"], judgement.rule_id, judgement.severity,
                judgement.outcome, judgement.message,
                repository.json_value(judgement.details), RULESET_VERSION,
            ))

    for judgement in judge_record(observations, entity_type):
        if judgement.outcome == FAIL:
            if judgement.severity == SEVERITY_ERROR:
                errors += 1
                record_errors += 1
                error_rules.append(judgement.rule_id)
                failed_rules.append(judgement.rule_id)
            elif judgement.severity == SEVERITY_WARNING:
                warnings += 1
                failed_rules.append(judgement.rule_id)
        result_rows.append((
            None, record_id, batch_id, "record", None, judgement.rule_id,
            judgement.severity, judgement.outcome, judgement.message,
            repository.json_value(judgement.details), RULESET_VERSION,
        ))

    status = _status_for(record_errors, errors - record_errors, warnings)
    record_row = (
        record_id, batch_id, source_id, entity_type,
        status, errors, warnings, validated, unvalidated,
        repository.json_value(sorted(set(failed_rules))), RULESET_VERSION,
    )
    return RecordVerdict(status, result_rows, record_row, sorted(set(error_rules)))


def validate_batch(batch_id: str) -> ValidateResult:
    with connect() as conn:
        batch = repository.get_batch(conn, batch_id)
        if batch is None:
            raise BatchNotValidatable(f"batch {batch_id} not found")
        if batch["status"] != "completed":
            raise BatchNotValidatable(
                f"batch {batch_id} has status {batch['status']!r}, expected 'completed'"
            )
        if repository.observation_count(conn, batch_id) == 0:
            raise BatchNotValidatable(
                f"batch {batch_id} has no attribute observations; normalize it first"
            )

        entity_type = batch["entity_type"]
        source_id = str(batch["source_id"])
        # Existing quarantine state, so routing is a decision against known
        # state rather than a query per record.
        existing = repository.quarantine_items_for_batch(conn, batch_id)

        pending_results: list[tuple] = []
        pending_records: list[tuple] = []
        pending_transitions: list[tuple] = []
        records = judgements = 0

        def flush() -> int:
            nonlocal pending_results, pending_records, pending_transitions
            # Retract this ruleset's previous verdicts on these records before
            # writing the new ones. Without it a re-run leaves behind the
            # failures of rules that no longer fire — see clear_results.
            repository.clear_results(
                conn, [row[0] for row in pending_records], RULESET_VERSION
            )
            written = repository.insert_results(conn, pending_results)
            repository.upsert_record_validation(conn, pending_records)
            # Same transaction as the verdict it derives from: there is never a
            # moment when a record is known-invalid but not yet quarantined.
            repository.apply_transitions(conn, pending_transitions)
            conn.commit()
            pending_results, pending_records, pending_transitions = [], [], []
            return written

        # Separate connection for the stream, so the writes above cannot
        # invalidate the cursor mid-iteration.
        with connect() as read_conn:
            for record_id, observations in repository.iter_records(read_conn, batch_id):
                records += 1
                verdict = validate_record(
                    record_id, observations, entity_type, batch_id, source_id
                )
                pending_results.extend(verdict.result_rows)
                pending_records.append(verdict.record_row)

                transition = route(
                    verdict.status, verdict.error_rules, existing.get(record_id)
                )
                if transition is not None:
                    pending_transitions.append((
                        record_id, batch_id, source_id, entity_type,
                        transition.to_status,
                        repository.json_value(transition.reason_codes),
                        len(transition.reason_codes),
                        transition.action, transition.from_status,
                        transition.to_status,
                        repository.json_value(transition.reason_codes),
                        transition.actor, transition.note,
                    ))

                if len(pending_records) >= RECORD_FLUSH_SIZE:
                    judgements += flush()

        judgements += flush()

        counts = repository.status_counts(conn, batch_id)
        counts["quarantined"] = repository.quarantine_counts(conn, batch_id)["open"]
        failures = repository.failure_breakdown(conn, batch_id)
        logger.info(
            "batch %s validated: %d record(s), %d judgement(s) %s",
            batch_id, records, judgements, counts,
        )
        return ValidateResult(batch_id, records, judgements, counts, failures)
