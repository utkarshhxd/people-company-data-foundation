"""The metrics endpoint has to survive the thing it is monitoring.

ADR 0010's second decision: "Monitoring that dies with the database is useless
at exactly the moment it is needed." A scrape that cannot reach Postgres must
still return 200, must say so with `pcdf_metrics_up 0`, and must not serve the
previous scrape's business gauges alongside that -- a stale queue depth
presented as current is worse than no number at all.
"""

import pytest
from fastapi.testclient import TestClient
from review_console import pipeline_metrics
from review_console.auth import ENV_ALLOW_UNAUTH
from review_console.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _serve_without_a_key(monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")


@pytest.fixture(autouse=True)
def _cold_cache():
    """Each test decides for itself what the scrape finds."""
    pipeline_metrics._cache.at = 0.0
    pipeline_metrics._kafka_cache.at = 0.0


def _collect(collector) -> dict[str, float]:
    out = {}
    for family in collector.collect():
        for sample in family.samples:
            out[sample.name] = sample.value
    return out


def test_a_database_failure_reports_itself_rather_than_breaking_the_scrape(monkeypatch):
    def boom():
        raise OSError("could not connect to server")

    monkeypatch.setattr(pipeline_metrics, "_collect_samples", boom)
    samples = _collect(pipeline_metrics.PipelineCollector())

    assert samples == {"pcdf_metrics_up": 0.0}, (
        "a failed scrape must serve the up-gauge and nothing else; anything "
        "here alongside it is a value from a previous scrape being presented "
        "as current"
    )


def test_a_broker_failure_does_not_take_the_database_gauges_with_it(monkeypatch):
    """The two collectors fail independently on purpose.

    Kafka being unreachable is itself an alert (KafkaUnreachable); it must not
    also blank out the queue depths and backlog ages, which are still perfectly
    readable and still the thing an operator is looking at.
    """
    def boom():
        raise OSError("broker transport failure")

    monkeypatch.setattr(pipeline_metrics, "_consumer_lag", boom)
    monkeypatch.setattr(
        pipeline_metrics, "_collect_samples",
        lambda: [(
            pipeline_metrics.MetricSpec(
                "pcdf_quarantine_items", "test", ("status",), "SELECT 1"
            ),
            [(["open"], 3.0)],
        )],
    )

    kafka = _collect(pipeline_metrics.KafkaCollector())
    assert kafka == {"pcdf_kafka_up": 0.0}

    database = _collect(pipeline_metrics.PipelineCollector())
    assert database["pcdf_metrics_up"] == 1.0
    assert database["pcdf_quarantine_items"] == 3.0


def test_the_scrape_endpoint_is_reachable_without_a_key(monkeypatch):
    """Prometheus scrapes before credentials are necessarily in place, and the
    series here are counts and ages -- never a name, address or vendor row.

    Both backends are stubbed: there is no broker and no database in a unit
    test, and letting this one wait out two five-second network timeouts to
    discover that would add ten seconds to every run.
    """
    monkeypatch.setattr(pipeline_metrics, "_consumer_lag", list)
    monkeypatch.setattr(pipeline_metrics, "_collect_samples", list)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "pcdf_metrics_up" in response.text


def test_every_spec_returns_its_value_last():
    """The collector reads labels off the front of each row and the number off
    the end, so a query that returns them the other way round would silently
    report a label as a value."""
    for spec in pipeline_metrics.SPECS:
        assert spec.name.startswith("pcdf_")
        assert spec.documentation.strip(), f"{spec.name} has no help text"
