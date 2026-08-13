"""Consume entities.resolved and rebuild the golden record for that batch's entities.

Offsets commit only after the build completes (at-least-once). Reprocessing is
safe and cheap: an unchanged value is left alone, so a replay writes nothing and
history is not churned.
"""

import json
import logging
import signal
import sys
from datetime import datetime, timezone
from types import FrameType

from common import events
from common.config import settings
from common.kafka import EventProducer, ensure_topics
from confluent_kafka import Consumer, KafkaError

from golden.pipeline import NothingToBuild, build_batch

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "pcdf-golden"
POLL_TIMEOUT_SECONDS = 1.0

_running = True


def _stop(signum: int, _frame: FrameType | None) -> None:
    global _running
    logger.info("received signal %s, shutting down", signum)
    _running = False


def _handle(payload: dict, producer: EventProducer) -> None:
    batch_id = payload.get("batch_id")
    if not batch_id:
        raise NothingToBuild(f"event has no batch_id: {payload}")

    result = build_batch(batch_id)
    producer.publish(
        events.TOPIC_GOLDEN_UPDATED,
        key=batch_id,
        event=events.golden_updated(
            batch_id=batch_id,
            entity_type=payload.get("entity_type", ""),
            entities=result.entities,
            values_written=result.written,
            values_refreshed=result.refreshed,
            values_unchanged=result.unchanged,
            values_retired=result.retired,
            counts=result.counts,
            updated_at=datetime.now(timezone.utc),
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
    consumer.subscribe([events.TOPIC_ENTITIES_RESOLVED])
    logger.info(
        "consuming %s as group %s", events.TOPIC_ENTITIES_RESOLVED, CONSUMER_GROUP
    )

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
                _handle(json.loads(message.value().decode("utf-8")), producer)
            except NothingToBuild as exc:
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
