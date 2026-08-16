"""Consume batch.ingested and map each new batch's schema.

Offsets are committed only after a batch is successfully processed
(at-least-once). Reprocessing is safe: map_batch reuses an existing
source_schema for a known column layout rather than duplicating it.
"""

import json
import logging
import signal
import sys
from datetime import UTC, datetime
from types import FrameType

from common import events
from common.config import settings
from common.db import connect
from common.kafka import EventProducer, ensure_topics
from confluent_kafka import Consumer, KafkaError

from mapping import repository
from mapping.pipeline import BatchNotMappable, map_batch

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "pcdf-mapping"
POLL_TIMEOUT_SECONDS = 1.0

_running = True


def _stop(signum: int, _frame: FrameType | None) -> None:
    global _running
    logger.info("received signal %s, shutting down", signum)
    _running = False


def _handle(payload: dict, producer: EventProducer) -> None:
    batch_id = payload.get("batch_id")
    if not batch_id:
        raise BatchNotMappable(f"event has no batch_id: {payload}")

    result = map_batch(batch_id)
    if result.reused:
        logger.info("batch %s reused source_schema %s", batch_id, result.source_schema_id)

    with connect() as conn:
        batch = repository.get_batch(conn, batch_id)

    producer.publish(
        events.TOPIC_SCHEMA_MAPPED,
        key=result.source_schema_id,
        event=events.schema_mapped(
            source_schema_id=result.source_schema_id,
            batch_id=batch_id,
            source_id=str(batch["source_id"]),
            entity_type=batch["entity_type"],
            counts=result.counts,
            schema_version=payload.get("schema_version", "1"),
            mapped_at=datetime.now(UTC),
        ),
    )
    producer.flush()


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
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
    consumer.subscribe([events.TOPIC_BATCH_INGESTED])
    logger.info("consuming %s as group %s", events.TOPIC_BATCH_INGESTED, CONSUMER_GROUP)

    try:
        while _running:
            message = consumer.poll(POLL_TIMEOUT_SECONDS)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("consume error: %s", message.error())
                continue

            try:
                payload = json.loads(message.value().decode("utf-8"))
                _handle(payload, producer)
            except BatchNotMappable as exc:
                # Permanent for this message — committing avoids an infinite retry loop.
                logger.warning("skipping message: %s", exc)
            except Exception as exc:
                # Likely transient (database or broker trouble). Do NOT commit;
                # exit so the container restarts and resumes from the last
                # committed offset rather than losing the message.
                logger.error("failed to process message, exiting to retry: %s", exc)
                return 1

            consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()

    logger.info("consumer stopped")
    return 0


if __name__ == "__main__":
    sys.exit(run())
