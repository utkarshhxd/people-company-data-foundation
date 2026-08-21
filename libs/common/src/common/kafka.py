import json
import logging
from typing import Any

from confluent_kafka import KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewTopic

from common.config import settings
from common.events import ALL_TOPICS

logger = logging.getLogger(__name__)

TOPIC_PARTITIONS = 3
TOPIC_REPLICATION_FACTOR = 1

# How long to let the local produce queue drain before trying again, and how
# many times. Only reached when producing faster than the broker accepts.
_QUEUE_DRAIN_TIMEOUT = 1.0
_PRODUCE_ATTEMPTS = 30


class DeliveryFailed(Exception):
    pass


def ensure_topics(timeout: float = 15.0) -> None:
    """Create topics explicitly so partitioning is a deliberate choice, not a broker default."""
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
    existing = set(admin.list_topics(timeout=timeout).topics)
    missing = [name for name in ALL_TOPICS if name not in existing]
    if not missing:
        return

    futures = admin.create_topics(
        [
            NewTopic(name, num_partitions=TOPIC_PARTITIONS,
                     replication_factor=TOPIC_REPLICATION_FACTOR)
            for name in missing
        ]
    )
    for name, future in futures.items():
        try:
            future.result(timeout=timeout)
            logger.info("created topic %s", name)
        except KafkaException as exc:
            # A concurrent creator winning the race is fine; anything else is not.
            if "TOPIC_ALREADY_EXISTS" not in str(exc):
                raise


class EventProducer:
    def __init__(self) -> None:
        self._producer = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers})
        self._failures: list[str] = []

    def _on_delivery(self, err: Any, _msg: Any) -> None:
        if err is not None:
            self._failures.append(str(err))

    def publish(self, topic: str, key: str, event: dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        encoded_key = key.encode("utf-8")
        for attempt in range(_PRODUCE_ATTEMPTS):
            try:
                self._producer.produce(
                    topic, key=encoded_key, value=payload,
                    on_delivery=self._on_delivery,
                )
            except BufferError:
                # The local queue is full, which a per-record producer reaches
                # long before a per-batch one does. Waiting for the broker to
                # drain is correct: dropping the event would leave a record
                # processed but unannounced, and growing the queue without
                # bound just moves the failure.
                if attempt == _PRODUCE_ATTEMPTS - 1:
                    raise DeliveryFailed(
                        f"producer queue still full after {_PRODUCE_ATTEMPTS} attempts; "
                        "the broker is not keeping up"
                    ) from None
                self._producer.poll(_QUEUE_DRAIN_TIMEOUT)
                continue
            break
        self._producer.poll(0)

    def flush(self, timeout: float = 30.0) -> None:
        remaining = self._producer.flush(timeout)
        if remaining:
            raise DeliveryFailed(f"{remaining} event(s) still undelivered after {timeout}s")
        if self._failures:
            raise DeliveryFailed(
                f"{len(self._failures)} event(s) failed to deliver: {self._failures[0]}"
            )
