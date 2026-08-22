"""Drive a source of records through the per-record unit of work.

The runner owns three things the unit does not: which records exist, what
happens when one of them fails, and who gets told when one is done.

Failure handling is the reason this loop looks the way it does. A record that
throws is rolled back, written to record_error with its payload, and the run
continues with the next one. That is the property record-at-a-time buys and
batching cannot: one unprocessable row costs one row.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path

from common import control, events
from common.canonical import CANONICAL_SCHEMA_VERSION, column_fingerprint
from common.db import connect
from common.kafka import EventProducer, ensure_topics
from ingestion import repository as ingest_repo
from ingestion.pipeline import ReingestBlocked, file_hash
from ingestion.readers import DEFAULT_BATCH_SIZE, iter_rows
from validation import breaker

from record_pipeline import repository, screening
from record_pipeline.context import SAMPLE_SIZE, RunContext, prepare
from record_pipeline.unit import RecordOutcome, process

logger = logging.getLogger(__name__)

PROGRESS_EVERY = 1000

# How often the run asks whether validation should still be running.
# The breaker is a rate over a floor, so checking every record would
# cost a query per record to answer a question that cannot change that
# fast. This is the resolution at which a run stops -- at 20,000
# records/minute, within a second or two of the trip.
BREAKER_CHECK_EVERY = 200


class EmptySource(Exception):
    pass


class RunStopped(Exception):
    """The run stopped because a stage was paused, not because it finished.

    Distinct from a failure: every record already committed is complete and
    correct, the file has not been rejected, and the batch is left
    un-completed, which every downstream stage already treats as inert.
    """


@dataclass
class RunResult:
    batch_id: str
    source_id: str
    source_schema_id: str
    rows_read: int = 0
    processed: int = 0
    failed: int = 0
    quarantined: int = 0
    linked: int = 0
    new_entities: int = 0
    review: int = 0
    invalid: int = 0
    mapping_reused: bool = False
    events_published: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    failed_rule_counts: dict[str, int] = field(default_factory=dict)

    stopped: bool = False

    def observe(self, outcome: RecordOutcome) -> None:
        self.processed += 1
        for rule in outcome.failed_rules:
            self.failed_rule_counts[rule] = self.failed_rule_counts.get(rule, 0) + 1
        if outcome.quarantined:
            self.quarantined += 1
        if outcome.validation_status == "invalid":
            self.invalid += 1
        if outcome.match_decision == "link":
            self.linked += 1
        elif outcome.match_decision == "review":
            self.review += 1
        elif outcome.match_decision is not None:
            self.new_entities += 1


def _source_record_id(row: dict, column: str | None, row_number: int) -> str:
    if column is None:
        return str(row_number)
    return row.get(column) or str(row_number)


def run_file(
    path: Path,
    entity_type: str,
    source_name: str,
    source_type: str,
    reliability: float,
    record_id_column: str | None = None,
    allow_reingest: bool = False,
    read_ahead: int = DEFAULT_BATCH_SIZE,
    build_golden: bool = True,
    publish: bool = True,
    fail_fast: bool = False,
    async_commit: bool = False,
    describes: str | None = None,
) -> RunResult:
    digest = file_hash(path)
    size = path.stat().st_size

    with connect() as conn:
        # Before the batch row exists. Starting a batch that immediately stops
        # would leave a `running` batch nobody asked for, and the duplicate
        # guard would then block re-loading the file once the pause is lifted.
        #
        # Both stages, because they stop this for different reasons and either
        # is sufficient. `ingestion` means no new work should enter the system
        # at all -- a person's decision, or the console's when a service it
        # depends on has gone away. `validation` means the records coming back
        # do not look like data, which is the breaker.
        control.guard(conn, "ingestion")
        control.guard(conn, "validation")

        source_id = ingest_repo.get_or_create_source(
            conn, source_name, source_type, reliability, describes
        )
        conn.commit()

        # The same guards the batch path uses. Processing a record at a time
        # changes nothing about whether a file should be processed twice.
        previous = ingest_repo.find_completed_batch(conn, source_id, digest)
        if previous and not allow_reingest:
            raise ReingestBlocked(
                f"{path.name} was already processed for source {source_name!r} "
                f"as batch {previous}. Re-run with --allow-reingest to process it again."
            )
        in_flight = ingest_repo.find_in_flight_batch(conn, source_id, digest)
        if in_flight:
            raise ReingestBlocked(
                f"{path.name} is currently being processed for source {source_name!r} "
                f"as batch {in_flight}. Wait for it to finish, or mark it failed "
                f"if it is stuck."
            )
        elsewhere = ingest_repo.find_batches_from_other_sources(conn, source_id, digest)
        if elsewhere:
            logger.warning(
                "%s has identical content to %d batch(es) already processed under "
                "other source(s): %s. Continuing — but check --source-name is right.",
                path.name, len(elsewhere),
                ", ".join(f"{name} ({batch})" for name, batch in elsewhere),
            )

        batch_id = ingest_repo.create_batch(
            conn, source_id, entity_type, str(path), path.name, digest, size
        )
        conn.commit()
        logger.info("batch %s started for %s", batch_id, path.name)

        if async_commit:
            # Atomicity and isolation are untouched; only the fsync at commit is
            # deferred. Postgres' WAL is one ordered stream, so a crash can only
            # lose a suffix of committed records — and the batch is not marked
            # 'completed' until after every record, which means a lost suffix
            # always leaves the batch un-completed and therefore inert
            # downstream. Recovery is re-running the file.
            with conn.cursor() as cur:
                cur.execute("SET synchronous_commit = off")
            conn.commit()
            logger.warning(
                "synchronous_commit is off for this run: a crash can lose recently "
                "committed records, and the batch would stay un-completed"
            )

        rows = iter_rows(path, read_ahead)
        result = _drive(
            conn, rows, path, batch_id, source_id, entity_type,
            record_id_column, build_golden, publish, fail_fast,
            ingest_repo.source_describes(conn, source_id),
        )

        if result.stopped:
            # Not 'completed'. Every record already written is complete and
            # correct, but the file is not -- and 'completed' is exactly what
            # every downstream stage keys off, so marking it that way would
            # hand a half-read file to resolution as though it were whole.
            # 'failed' leaves the rows in place and the batch inert, which is
            # the same state a crash mid-load produces and the same recovery:
            # fix the cause, then re-run the file with --allow-reingest.
            ingest_repo.stop_batch(
                conn, batch_id, result.rows_read, result.processed, result.failed,
                f"stopped after {result.processed} record(s): validation was "
                f"paused because almost everything was coming back invalid. "
                f"See pipeline_control.",
            )
        else:
            ingest_repo.finish_batch(
                conn, batch_id, result.rows_read, result.processed, result.failed
            )
        conn.commit()

    return result


def _drive(
    conn, rows, path: Path, batch_id: str, source_id: str, entity_type: str,
    record_id_column: str | None, build_golden: bool, publish: bool,
    fail_fast: bool, describes: str = "organisation",
) -> RunResult:
    # pcdf.record.processed only -- deliberately never pcdf.batch.ingested.
    # That topic is what starts the batch consumer chain, and a file loaded
    # here has already been carried all the way to its golden values. Both
    # paths touching one batch is what produced the concurrent-golden-builder
    # collision in ADR 0012; keeping this path silent on that topic is what
    # stops it structurally rather than by convention.
    producer = None
    if publish:
        ensure_topics()
        producer = EventProducer()

    # Read ahead far enough to derive the mapping, then process every row
    # including the ones peeked at. The mapping has to exist before the first
    # record is written, because a record's observations record which canonical
    # field each column was taken to mean.
    head: list[tuple[list[str], dict]] = []
    columns: list[str] = []
    for item in rows:
        head.append(item)
        columns = item[0]
        if len(head) >= SAMPLE_SIZE:
            break

    if not head:
        raise EmptySource(f"{path.name} has no rows to process")

    # Recorded before the mapping is derived, because the mapping is identified
    # by a fingerprint of exactly this list and in exactly this order.
    ingest_repo.set_batch_columns(conn, batch_id, columns)
    conn.commit()

    ctx = prepare(
        conn, source_id, batch_id, entity_type, columns,
        [row for _, row in head], describes,
    )
    result = RunResult(
        batch_id=batch_id, source_id=source_id,
        source_schema_id=ctx.source_schema_id, mapping_reused=ctx.mapping_reused,
    )

    for _columns, row in chain(head, rows):
        result.rows_read += 1
        row_number = result.rows_read
        source_record_id = _source_record_id(row, record_id_column, row_number)

        # Screened before the database is touched at all. A row carrying a NUL
        # byte would be refused by the write anyway and end up in exactly this
        # table — this only spares it the round-trip and the rolled-back
        # transaction, and names the offending column while the row is still in
        # hand. The screen is narrower than what Postgres refuses on purpose;
        # anything it misses is still caught below.
        fault = screening.unstorable(row)
        if fault is not None:
            if fail_fast:
                raise screening.UnstorablePayload(fault)
            repository.record_failure(
                conn, batch_id, source_id, entity_type, row_number,
                source_record_id, row, "screen",
                screening.UnstorablePayload(fault),
            )
            conn.commit()
            result.failed += 1
            logger.error("row %d cannot be stored and was recorded: %s", row_number, fault)
            continue

        try:
            outcome = process(
                ctx, conn, source_record_id, row_number, row,
                build_golden=build_golden,
            )
        except Exception as exc:
            conn.rollback()
            if fail_fast:
                raise
            # The row is kept with its payload and its error. It has not been
            # processed, and it has not been lost either.
            repository.record_failure(
                conn, batch_id, source_id, entity_type, row_number,
                source_record_id, row, "process", exc,
            )
            conn.commit()
            result.failed += 1
            logger.error("row %d failed and was recorded: %s", row_number, exc)
            continue

        result.observe(outcome)
        if producer is not None:
            producer.publish(
                events.TOPIC_RECORD_PROCESSED,
                # Keyed by entity where there is one, so everything about an
                # entity lands on one partition and a consumer sees its records
                # in order. Records without an entity key on themselves.
                key=outcome.entity_id or outcome.record_id,
                event=events.record_processed(
                    outcome.record_id, batch_id, source_id, entity_type,
                    outcome.entity_id, outcome.validation_status,
                    outcome.quarantined, outcome.match_decision,
                    datetime.now(UTC),
                ),
            )

        if result.rows_read % PROGRESS_EVERY == 0:
            logger.info(
                "batch %s: %d row(s), %d processed, %d failed",
                batch_id, result.rows_read, result.processed, result.failed,
            )

        # The record-at-a-time path validates inline, so nothing else would
        # ever notice a feed that has stopped being usable -- there is no
        # per-batch verdict to inspect afterwards. This is where that gets
        # caught, and it is checked against this run's own counts rather than
        # the whole table so one bad file cannot be hidden by a good history.
        if result.processed % BREAKER_CHECK_EVERY == 0:
            verdict = breaker.trip_if_broken(
                conn, result.processed, result.invalid,
                result.failed_rule_counts, context=f"batch {batch_id}",
            )
            if verdict.tripped:
                result.stopped = True
                logger.error(
                    "batch %s stopped after %d record(s): %s",
                    batch_id, result.processed, verdict.reason(),
                )
                break

    if producer is not None:
        producer.flush()
        ingest_repo.mark_events_published(conn, batch_id)
        result.events_published = True

    result.counts = {
        "records": result.processed,
        "failed": result.failed,
        "quarantined": result.quarantined,
        "open_errors": repository.error_count(conn, batch_id),
    }
    return result


@dataclass
class ReprocessResult:
    attempted: int = 0
    succeeded: int = 0
    still_failing: int = 0
    skipped: int = 0


def reprocess_errors(
    batch_id: str | None = None, limit: int = 1000, actor: str = "reprocess",
    build_golden: bool = True,
) -> ReprocessResult:
    """Run rows from record_error through the pipeline again.

    The point of keeping a failed row byte-exact is being able to replay it once
    the reason it failed is gone — a fixed defect, a dropped index, a corrected
    mapping. Without this the payload was evidence and nothing more.

    Each row goes through the *same* `process()` every other record does. A
    replay path that did anything different would be a second implementation of
    what a record means, and the two would eventually disagree.

    A row that fails again is left `open` with its new error, because the reason
    it failed the second time is the one worth reading.
    """
    result = ReprocessResult()

    with connect() as conn:
        errors = repository.open_errors_for_reprocess(conn, batch_id, limit)
        if not errors:
            return result

        # Grouped by batch: the run context — column mapping, source, entity
        # type — is a property of the layout, and rebuilding it per row would
        # both be wasteful and risk two rows of one file being mapped
        # differently.
        by_batch: dict[str, list[dict]] = {}
        for error in errors:
            by_batch.setdefault(str(error["batch_id"]), []).append(error)

        for batch, rows in by_batch.items():
            ctx = _context_for_batch(conn, batch, rows[0])
            if ctx is None:
                result.skipped += len(rows)
                logger.warning(
                    "batch %s has no stored mapping; cannot reprocess %d row(s)",
                    batch, len(rows),
                )
                continue

            for error in rows:
                result.attempted += 1
                payload = repository.payload_from_bytes(error["raw_payload_bytes"])
                if payload is None:
                    # Written before migration 0010 added the exact copy. The
                    # readable column is sanitized, and replaying from it would
                    # feed the pipeline a row the vendor never sent.
                    result.skipped += 1
                    logger.warning(
                        "row %s has no byte-exact payload; skipped",
                        error["row_number"],
                    )
                    continue

                source_record_id = error["source_record_id"] or str(error["row_number"])
                try:
                    outcome = process(
                        ctx, conn, source_record_id, error["row_number"], payload,
                        build_golden=build_golden,
                    )
                except Exception as exc:
                    conn.rollback()
                    repository.record_failure(
                        conn, batch, str(error["source_id"]), error["entity_type"],
                        error["row_number"], source_record_id, payload,
                        "reprocess", exc,
                    )
                    conn.commit()
                    result.still_failing += 1
                    logger.error("row %s failed again: %s", error["row_number"], exc)
                    continue

                repository.mark_reprocessed(
                    conn, str(error["error_id"]), outcome.record_id, actor
                )
                # The batch now holds a row it did not before, and its counters
                # are read as what the batch contains rather than what one
                # attempt at it achieved.
                ingest_repo.count_reprocessed_row(conn, batch)
                conn.commit()
                result.succeeded += 1

    return result


def _context_for_batch(conn, batch_id: str, sample_error: dict):
    """Rebuild the run context for a batch already loaded.

    The layout is whatever the batch's own records used, so it is read back
    rather than re-derived: re-deriving could produce a different mapping than
    the rest of the file got, and then one row of a file would mean something
    different from its neighbours.
    """
    from mapping import repository as mapping_repo
    from normalization import repository as norm_repo

    columns = norm_repo.batch_columns(conn, batch_id)
    if not columns:
        return None

    source_id = str(sample_error["source_id"])
    source_schema_id = mapping_repo.find_source_schema(
        conn, source_id, column_fingerprint(columns), CANONICAL_SCHEMA_VERSION
    )
    if source_schema_id is None:
        # Batches loaded before migration 0015 have no stored column order, so
        # their fingerprint cannot be recomputed. The layout is still
        # identifiable: a source_schema holds its columns as a jsonb *array*,
        # which does keep order, so matching on the set of names finds it.
        source_schema_id = mapping_repo.find_source_schema_by_columns(
            conn, source_id, columns, CANONICAL_SCHEMA_VERSION
        )
    if source_schema_id is None:
        return None

    return RunContext(
        source_id=source_id,
        batch_id=batch_id,
        entity_type=sample_error["entity_type"],
        columns=columns,
        source_schema_id=source_schema_id,
        mappings=norm_repo.get_column_mappings(conn, source_schema_id),
        mapping_reused=True,
    )
