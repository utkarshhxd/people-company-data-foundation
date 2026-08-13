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
        LEFT JOIN record_entity_link l ON l.record_id = rr.record_id
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
