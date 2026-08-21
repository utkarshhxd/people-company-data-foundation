"""Consume records.normalized and validate that batch.

Offsets commit only after the batch is fully written (at-least-once).
Reprocessing is safe: results are keyed on
(record_id, observation_id, rule_id, ruleset_version) and re-run in place.
"""

import json
import logging
import signal
import sys
from datetime import UTC, datetime
from types import FrameType

from common import control, events
from common.config import settings
from common.db import connect
from common.heartbeat import beat, default_path, record
from common.kafka import EventProducer, ensure_topics
from common.logging import configure
from confluent_kafka import Consumer, KafkaError

from validation.pipeline import BatchNotValidatable, validate_batch
from validation.rules import RULESET_VERSION

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "pcdf-validation"
# Which pipeline_control row decides whether this consumer runs.
CONTROL_STAGE = "validation"
POLL_TIMEOUT_SECONDS = 1.0

_running = True


def _stop(signum: int, _frame: FrameType | None) -> None:
    global _running
    logger.info("received signal %s, shutting down", signum)
    _running = False


def _handle(payload: dict, producer: EventProducer) -> None:
    batch_id = payload.get("batch_id")
    if not batch_id:
        raise BatchNotValidatable(f"event has no batch_id: {payload}")

    result = validate_batch(batch_id)
    producer.publish(
        events.TOPIC_RECORDS_VALIDATED,
        key=result.batch_id,
        event=events.records_validated(
            batch_id=result.batch_id,
            source_id=payload.get("source_id", ""),
            entity_type=payload.get("entity_type", ""),
            records=result.records,
            judgements=result.judgements,
            counts=result.counts,
            ruleset_version=RULESET_VERSION,
            validated_at=datetime.now(UTC),
        ),
    )
    producer.flush()


def _apply_control(consumer: Consumer, currently_stopped: bool) -> bool:
    """Pause or resume this consumer's partitions to match pipeline_control.

    A database it cannot reach is not a pause: it leaves the assignment as it
    is and lets the next batch fail on its own, which is recorded, rather than
    inventing a stop nobody asked for.
    """
    try:
        with connect() as conn:
            paused = control.get(conn, CONTROL_STAGE).paused
    except Exception as exc:
        logger.warning("could not read pipeline control: %s", exc)
        return currently_stopped

    if paused == currently_stopped:
        return currently_stopped

    assignment = consumer.assignment()
    if paused:
        consumer.pause(assignment)
        logger.warning(
            "%s is paused; holding %d partition(s). Work stays in the topic.",
            CONTROL_STAGE, len(assignment),
        )
    else:
        consumer.resume(assignment)
        logger.warning("%s resumed; consuming %d partition(s) again",
                       CONTROL_STAGE, len(assignment))
    return paused


def run() -> int:
    configure("validation-consumer")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    ensure_topics()
    producer = EventProducer()
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": CONSUMER_GROUP,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([events.TOPIC_RECORDS_NORMALIZED])
    logger.info(
        "consuming %s as group %s (ruleset %s)",
        events.TOPIC_RECORDS_NORMALIZED, CONSUMER_GROUP, RULESET_VERSION,
    )

    heartbeat = default_path("validation-consumer")
    stopped_partitions = False
    try:
        while _running:
            # Before the poll, so an idle consumer and a busy one both
            # look alive; a stale file means the loop itself stopped.
            beat(heartbeat)
            record("validation-consumer", {"group": CONSUMER_GROUP})

            # Pausing the partitions rather than skipping the poll: the poll is
            # what keeps this consumer in its group, and a consumer that stops
            # polling for longer than max.poll.interval.ms is evicted and its
            # partitions handed to nobody. Paused, it keeps its assignment, the
            # backlog accumulates in the topic, and no offset moves -- so
            # resuming reads exactly what was left, in order.
            stopped_partitions = _apply_control(consumer, stopped_partitions)

            message = consumer.poll(POLL_TIMEOUT_SECONDS)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("consume error: %s", message.error())
                continue

            try:
                _handle(json.loads(message.value().decode("utf-8")), producer)
            except BatchNotValidatable as exc:
                # Permanent for this message; commit so it isn't retried forever.
                logger.warning("skipping message: %s", exc)
            except Exception as exc:
                # Likely transient. Don't commit; exit so the container restarts
                # and resumes from the last committed offset.
                logger.error("failed to process message, exiting to retry: %s", exc)
                return 1

            consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()

    logger.info("consumer stopped")
    return 0


if __name__ == "__main__":
    sys.exit(run())
