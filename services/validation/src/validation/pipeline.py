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


def _status_for(error_failures: int, warning_failures: int) -> str:
    # Errors dominate: a record with an unusable identifier is not "mostly fine".
    if error_failures:
        return STATUS_INVALID
    if warning_failures:
        return STATUS_WARNING
    return STATUS_VALID


def validate_record(
    record_id, observations: list[dict], entity_type: str, batch_id: str, source_id: str
) -> tuple[list[tuple], tuple]:
    """One record -> its validation_result rows and its record_validation row."""
    result_rows: list[tuple] = []
    validated = unvalidated = 0
    errors = warnings = 0
    failed_rules: list[str] = []

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
                failed_rules.append(judgement.rule_id)
                if judgement.severity == SEVERITY_ERROR:
                    errors += 1
                elif judgement.severity == SEVERITY_WARNING:
                    warnings += 1
            result_rows.append((
                obs["observation_id"], record_id, batch_id, "attribute",
                obs["canonical_field"], judgement.rule_id, judgement.severity,
                judgement.outcome, judgement.message,
                repository.json_value(judgement.details), RULESET_VERSION,
            ))

    for judgement in judge_record(observations, entity_type):
        if judgement.outcome == FAIL:
            failed_rules.append(judgement.rule_id)
            if judgement.severity == SEVERITY_ERROR:
                errors += 1
            elif judgement.severity == SEVERITY_WARNING:
                warnings += 1
        result_rows.append((
            None, record_id, batch_id, "record", None, judgement.rule_id,
            judgement.severity, judgement.outcome, judgement.message,
            repository.json_value(judgement.details), RULESET_VERSION,
        ))

    record_row = (
        record_id, batch_id, source_id, entity_type,
        _status_for(errors, warnings), errors, warnings, validated, unvalidated,
        repository.json_value(sorted(set(failed_rules))), RULESET_VERSION,
    )
    return result_rows, record_row


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

        pending_results: list[tuple] = []
        pending_records: list[tuple] = []
        records = judgements = 0

        def flush() -> int:
            nonlocal pending_results, pending_records
            written = repository.insert_results(conn, pending_results)
            repository.upsert_record_validation(conn, pending_records)
            conn.commit()
            pending_results, pending_records = [], []
            return written

        # Separate connection for the stream, so the writes above cannot
        # invalidate the cursor mid-iteration.
        with connect() as read_conn:
            for record_id, observations in repository.iter_records(read_conn, batch_id):
                records += 1
                result_rows, record_row = validate_record(
                    record_id, observations, entity_type, batch_id, source_id
                )
                pending_results.extend(result_rows)
                pending_records.append(record_row)
                if len(pending_records) >= RECORD_FLUSH_SIZE:
                    judgements += flush()

        judgements += flush()

        counts = repository.status_counts(conn, batch_id)
        failures = repository.failure_breakdown(conn, batch_id)
        logger.info(
            "batch %s validated: %d record(s), %d judgement(s) %s",
            batch_id, records, judgements, counts,
        )
        return ValidateResult(batch_id, records, judgements, counts, failures)
