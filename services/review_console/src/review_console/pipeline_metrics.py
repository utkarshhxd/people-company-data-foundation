"""Business metrics for the data foundation, queried from Postgres on scrape.

These are gauges derived from the database, not counters incremented inside each
service, and that is deliberate. The numbers worth watching here are *accumulated
state* — how deep is the quarantine queue, how many matches are waiting for a
human, how many trusted values are contested. An in-process counter answers a
different question and answers it badly: it resets on restart, and a Kafka replay
double-counts work that was only done once. A query against the source of truth
is correct at every scrape regardless of what the services have been through.

The cost is a handful of aggregate queries per scrape, which is why results are
cached for slightly less than the scrape interval.

Infrastructure health (is the process up, how many HTTP requests) is already
covered by the instrumentator. This is the layer that tells an operator whether
the *data* is drifting: review backlogs growing, records failing validation,
golden values in dispute.
"""

import logging
import time
from dataclasses import dataclass, field

import psycopg
from common.config import settings
from prometheus_client.core import GaugeMetricFamily

logger = logging.getLogger(__name__)

# Prometheus scrapes every 15s (infra/prometheus/prometheus.yml). Caching just
# under that means at most one round of queries per scrape without ever serving
# a value from the previous scrape interval.
CACHE_TTL_SECONDS = 12.0
QUERY_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class MetricSpec:
    name: str
    documentation: str
    labels: tuple[str, ...]
    sql: str


# Each query returns label values first, then the numeric value last.
SPECS: tuple[MetricSpec, ...] = (
    # --- ingestion -------------------------------------------------------
    MetricSpec(
        "pcdf_batches", "Ingestion batches by status.", ("status",),
        "SELECT status, count(*) FROM batch GROUP BY 1",
    ),
    MetricSpec(
        "pcdf_batches_events_unpublished",
        "Completed batches whose Kafka events never published. "
        "Rows are committed regardless, so this is the visible gap, not data loss.",
        (),
        """
        SELECT count(*) FROM batch
        WHERE status = 'completed' AND events_published_at IS NULL
        """,
    ),
    MetricSpec(
        "pcdf_raw_records", "Source rows stored verbatim.", (),
        "SELECT count(*) FROM raw_record",
    ),

    # --- schema mapping --------------------------------------------------
    MetricSpec(
        "pcdf_column_mappings", "Column mappings by status.", ("status",),
        "SELECT mapping_status, count(*) FROM column_mapping GROUP BY 1",
    ),
    MetricSpec(
        "pcdf_oldest_unreviewed_mapping_age_seconds",
        "Age of the longest-waiting mapping review. Nothing ages this queue "
        "automatically, so a rising value means humans have stopped working it.",
        (),
        """
        SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0)
        FROM column_mapping WHERE mapping_status = 'needs_review'
        """,
    ),

    # --- normalization ---------------------------------------------------
    MetricSpec(
        "pcdf_attribute_observations", "Stored attribute observations.", (),
        "SELECT count(*) FROM attribute_observation",
    ),
    MetricSpec(
        "pcdf_observations_uncanonical",
        "Observations captured but not attributed to a canonical field. "
        "Nothing is lost, but these values cannot yet be used.",
        (),
        "SELECT count(*) FROM attribute_observation WHERE canonical_field IS NULL",
    ),

    # --- validation ------------------------------------------------------
    MetricSpec(
        "pcdf_records_validated", "Records by validation status.", ("status",),
        "SELECT status, count(*) FROM record_validation GROUP BY 1",
    ),
    MetricSpec(
        "pcdf_validation_failures", "Failing validation judgements by severity.",
        ("severity",),
        """
        SELECT severity, count(*) FROM validation_result
        WHERE outcome = 'fail' GROUP BY 1
        """,
    ),

    # --- quarantine ------------------------------------------------------
    MetricSpec(
        "pcdf_quarantine_items", "Quarantined records by disposition.", ("status",),
        "SELECT status, count(*) FROM quarantine_item GROUP BY 1",
    ),
    MetricSpec(
        "pcdf_oldest_open_quarantine_age_seconds",
        "Age of the longest-waiting quarantined record. A record can sit here "
        "indefinitely without anything being lost — and without anyone noticing.",
        (),
        """
        SELECT coalesce(extract(epoch FROM now() - min(quarantined_at)), 0)
        FROM quarantine_item WHERE status = 'open'
        """,
    ),

    # --- entity resolution -----------------------------------------------
    MetricSpec(
        "pcdf_entities", "Entities by type and status.", ("entity_type", "status"),
        "SELECT entity_type, status, count(*) FROM entity GROUP BY 1, 2",
    ),
    MetricSpec(
        "pcdf_match_candidates", "Proposed matches by review status.", ("status",),
        "SELECT status, count(*) FROM match_candidate GROUP BY 1",
    ),
    MetricSpec(
        "pcdf_oldest_open_match_candidate_age_seconds",
        "Age of the longest-waiting match decision. While it waits, one real "
        "entity is represented by two ids.",
        (),
        """
        SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0)
        FROM match_candidate WHERE status = 'open'
        """,
    ),
    MetricSpec(
        "pcdf_records_awaiting_resolution",
        "Records that passed validation but are not yet linked to an entity.",
        (),
        """
        SELECT count(*) FROM resolvable_record rr
        LEFT JOIN record_entity_link l
          ON l.record_id = rr.record_id AND l.role = 'self'
        WHERE l.link_id IS NULL
        """,
    ),

    # --- golden record ---------------------------------------------------
    MetricSpec(
        "pcdf_golden_values", "Golden attribute values by state.", ("state",),
        """
        SELECT CASE WHEN valid_to IS NULL THEN 'current' ELSE 'superseded' END,
               count(*)
        FROM golden_attribute GROUP BY 1
        """,
    ),
    MetricSpec(
        "pcdf_golden_contested_values",
        "Current golden values where sources disagreed. Not an error — but the "
        "fields most worth a human's attention.",
        (),
        """
        SELECT count(*) FROM golden_attribute
        WHERE valid_to IS NULL AND competing_values > 1
        """,
    ),
    MetricSpec(
        "pcdf_golden_mean_confidence",
        "Mean confidence of current golden values, by entity type.",
        ("entity_type",),
        """
        SELECT entity_type, avg(confidence)::float
        FROM golden_attribute WHERE valid_to IS NULL GROUP BY 1
        """,
    ),
    MetricSpec(
        "pcdf_golden_low_confidence_values",
        "Current golden values below 0.5 confidence.", (),
        "SELECT count(*) FROM golden_attribute WHERE valid_to IS NULL AND confidence < 0.5",
    ),

    # --- AI enrichment ---------------------------------------------------
    # The fourth review queue. It arrived after this file was first written,
    # and every other queue has both a depth and an age here; without these it
    # is the one backlog nothing can alert on.
    MetricSpec(
        "pcdf_enrichment_proposals",
        "AI enrichment proposals by status. Pending ones are a human decision "
        "nobody has made yet; until then the guess is not evidence of anything.",
        ("status",),
        "SELECT status, count(*) FROM enrichment_proposal GROUP BY 1",
    ),
    MetricSpec(
        "pcdf_oldest_pending_enrichment_age_seconds",
        "Age of the oldest undecided proposal.",
        (),
        """
        SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0)
        FROM enrichment_proposal WHERE status = 'pending'
        """,
    ),
    MetricSpec(
        "pcdf_enrichment_attempts",
        "Model calls by outcome. 'error' is an unreachable or unusable model, "
        "which enrichment_attempt records separately from 'declined' precisely "
        "so an outage cannot be mistaken for the model having no opinion.",
        ("outcome",),
        "SELECT outcome, count(*) FROM enrichment_attempt GROUP BY 1",
    ),

    # --- pipeline control ------------------------------------------------
    # A stopped stage is the loudest thing this system can be doing, and it is
    # invisible from outside: no container exits, no request fails, nothing
    # errors. It just quietly stops loading, which looks exactly like a quiet
    # week.
    MetricSpec(
        "pcdf_stage_paused",
        "1 when a stage has been stopped, by a person or by itself. "
        "Nothing is lost while this is 1; nothing moves either.",
        ("stage", "changed_by"),
        """
        SELECT stage, changed_by, CASE WHEN state = 'paused' THEN 1 ELSE 0 END
        FROM pipeline_control
        """,
    ),
    MetricSpec(
        "pcdf_stage_paused_seconds",
        "How long the stage has been stopped. The number that matters: a "
        "two-minute pause is somebody looking, an eight-hour one is somebody "
        "who forgot.",
        ("stage",),
        """
        SELECT stage, extract(epoch FROM now() - changed_at)
        FROM pipeline_control WHERE state = 'paused'
        """,
    ),

    # --- liveness --------------------------------------------------------
    # Age, not a boolean. `restart: unless-stopped` only acts on a process that
    # exited, so "the container is up" and "the loop is turning" are different
    # facts and only the second one matters. A service that has never beaten at
    # all has no row and therefore no sample, which the alert treats as absent
    # rather than as zero.
    MetricSpec(
        "pcdf_service_heartbeat_age_seconds",
        "Seconds since each long-running service last completed a loop.",
        ("service",),
        """
        SELECT service, extract(epoch FROM now() - beat_at)
        FROM service_heartbeat
        """,
    ),
)


@dataclass
class _Cache:
    at: float = 0.0
    samples: list = field(default_factory=list)
    healthy: bool = False


_cache = _Cache()


def _collect_samples() -> list[tuple[MetricSpec, list[tuple[list[str], float]]]]:
    out = []
    with psycopg.connect(
        settings.postgres_dsn, connect_timeout=QUERY_TIMEOUT_SECONDS
    ) as conn:
        for spec in SPECS:
            with conn.cursor() as cur:
                cur.execute(spec.sql)
                rows = [
                    ([str(v) for v in row[:-1]], float(row[-1] or 0))
                    for row in cur.fetchall()
                ]
            # A grouped query with no rows means zero of everything, which is a
            # real answer — but an ungrouped count always returns one row, so
            # only the labelled specs can legitimately come back empty.
            if not rows and not spec.labels:
                rows = [([], 0.0)]
            out.append((spec, rows))
    return out


class PipelineCollector:
    """Prometheus collector backed by the database.

    Never raises. A metrics endpoint that fails when the database blips takes
    out monitoring at exactly the moment it is needed, so a failed scrape
    reports `pcdf_metrics_up 0` and serves the process metrics regardless.
    """

    def collect(self):
        now = time.monotonic()
        if now - _cache.at > CACHE_TTL_SECONDS:
            try:
                _cache.samples = _collect_samples()
                _cache.healthy = True
            except Exception as exc:
                logger.warning("pipeline metrics scrape failed: %s", exc)
                _cache.samples = []
                _cache.healthy = False
            _cache.at = now

        yield GaugeMetricFamily(
            "pcdf_metrics_up",
            "1 when pipeline metrics were read from Postgres successfully.",
            value=1.0 if _cache.healthy else 0.0,
        )

        for spec, rows in _cache.samples:
            family = GaugeMetricFamily(
                spec.name, spec.documentation, labels=list(spec.labels)
            )
            for label_values, value in rows:
                family.add_metric(label_values, value)
            yield family


# --------------------------------------------------------------------------
# Kafka
# --------------------------------------------------------------------------

# Lag is the question the broker exists to answer: how much work is waiting
# because something is not keeping up or is not running. It cannot come from
# Postgres like everything else above, so it is a collector of its own -- and
# it fails the same way, reporting that it could not look rather than taking
# the scrape down.
KAFKA_TIMEOUT_SECONDS = 5.0

_kafka_cache = _Cache()


def _consumer_lag() -> list[tuple[str, str, int, float]]:
    """(group, topic, partition, lag) for every pcdf consumer group."""
    from confluent_kafka import (
        Consumer,
        ConsumerGroupTopicPartitions,
        TopicPartition,
    )
    from confluent_kafka.admin import AdminClient

    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
    groups = admin.list_consumer_groups(request_timeout=KAFKA_TIMEOUT_SECONDS).result()
    names = [g.group_id for g in groups.valid if g.group_id.startswith("pcdf-")]
    if not names:
        return []

    # One request per group: librdkafka refuses a batched one with "we support
    # listing offsets for a single consumer group only".
    #
    # The probe client exists only to read watermarks. It is configured into a
    # group it never joins -- it calls get_watermark_offsets and never
    # subscribe -- so it cannot take a partition away from the consumer that is
    # actually doing the work.
    probe = Consumer({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": "pcdf-metrics-probe",
        "enable.auto.commit": False,
    })
    out: list[tuple[str, str, int, float]] = []
    try:
        for name in names:
            futures = admin.list_consumer_group_offsets(
                [ConsumerGroupTopicPartitions(name, None)]
            )
            result = futures[name].result(timeout=KAFKA_TIMEOUT_SECONDS)
            for tp in result.topic_partitions:
                # A negative offset means the group has never committed this
                # partition. That is not lag; it is the absence of a reading.
                if tp.offset < 0:
                    continue
                _low, high = probe.get_watermark_offsets(
                    TopicPartition(tp.topic, tp.partition),
                    timeout=KAFKA_TIMEOUT_SECONDS, cached=False,
                )
                out.append((name, tp.topic, tp.partition, float(max(high - tp.offset, 0))))
    finally:
        probe.close()
    return out


class KafkaCollector:
    """Consumer lag per group, topic and partition.

    Same contract as PipelineCollector: never raises, and says so when it could
    not reach the broker instead of failing the scrape. A broker that is down
    is exactly when this endpoint needs to still answer.
    """

    def collect(self):
        now = time.monotonic()
        if now - _kafka_cache.at > CACHE_TTL_SECONDS:
            try:
                _kafka_cache.samples = _consumer_lag()
                _kafka_cache.healthy = True
            except Exception as exc:
                logger.warning("kafka lag scrape failed: %s", exc)
                _kafka_cache.samples = []
                _kafka_cache.healthy = False
            _kafka_cache.at = now

        yield GaugeMetricFamily(
            "pcdf_kafka_up",
            "1 when consumer lag was read from the broker successfully.",
            value=1.0 if _kafka_cache.healthy else 0.0,
        )
        family = GaugeMetricFamily(
            "pcdf_kafka_consumer_lag",
            "Messages published to a partition that this group has not committed.",
            labels=["group", "topic", "partition"],
        )
        for group, topic, partition, lag in _kafka_cache.samples:
            family.add_metric([group, topic, str(partition)], lag)
        yield family
