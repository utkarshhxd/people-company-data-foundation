"""Consume records.validated and resolve that batch's records to entities.

Offsets commit only after the batch is fully processed (at-least-once).
Reprocessing is safe: record_entity_link is unique per record and already-linked
records are excluded from the work list, so a replay resolves nothing twice.
"""

import json
import logging
import signal
import sys
from datetime import UTC, datetime
from types import FrameType

from common import events
from common.config import settings
from common.heartbeat import beat, default_path
from common.kafka import EventProducer, ensure_topics
from common.logging import configure
from confluent_kafka import Consumer, KafkaError

from resolution.pipeline import BatchNotResolvable, resolve_batch

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "pcdf-resolution"
POLL_TIMEOUT_SECONDS = 1.0

_running = True


def _stop(signum: int, _frame: FrameType | None) -> None:
    global _running
    logger.info("received signal %s, shutting down", signum)
    _running = False


def _handle(payload: dict, producer: EventProducer) -> None:
    batch_id = payload.get("batch_id")
    if not batch_id:
        raise BatchNotResolvable(f"event has no batch_id: {payload}")

    result = resolve_batch(batch_id)
    producer.publish(
        events.TOPIC_ENTITIES_RESOLVED,
        key=result.batch_id,
        event=events.entities_resolved(
            batch_id=result.batch_id,
            source_id=payload.get("source_id", ""),
            entity_type=payload.get("entity_type", ""),
            records=result.records,
            linked=result.linked,
            created=result.created,
            candidates=result.candidates,
            counts=result.counts,
            resolved_at=datetime.now(UTC),
        ),
    )
    producer.flush()


def run() -> int:
    configure("resolution-consumer")
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
    consumer.subscribe([events.TOPIC_RECORDS_VALIDATED])
    logger.info(
        "consuming %s as group %s", events.TOPIC_RECORDS_VALIDATED, CONSUMER_GROUP
    )

    heartbeat = default_path("resolution-consumer")
    try:
        while _running:
            # Before the poll, so an idle consumer and a busy one both
            # look alive; a stale file means the loop itself stopped.
            beat(heartbeat)
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
            except BatchNotResolvable as exc:
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
