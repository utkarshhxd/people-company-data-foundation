"""Collector behaviour, especially when the database is unavailable.

The SQL itself is exercised against a real database in the end-to-end run; what
matters here is that a scrape never fails and never lies about its own health.
"""

import pytest
from api_service import pipeline_metrics
from api_service.pipeline_metrics import SPECS, PipelineCollector


@pytest.fixture(autouse=True)
def clear_cache():
    pipeline_metrics._cache.at = 0.0
    pipeline_metrics._cache.samples = []
    pipeline_metrics._cache.healthy = False
    yield
    pipeline_metrics._cache.at = 0.0


def families(collector):
    return {f.name: f for f in collector.collect()}


def test_every_spec_puts_its_labels_last_in_the_query(monkeypatch):
    """Each query returns label values first and the number last."""
    for spec in SPECS:
        assert spec.name.startswith("pcdf_")
        assert spec.documentation.endswith(".") or "." in spec.documentation


def test_a_database_failure_reports_itself_rather_than_breaking_the_scrape(monkeypatch):
    """Monitoring that dies with the database is useless exactly when needed."""
    def boom():
        raise ConnectionError("postgres is down")

    monkeypatch.setattr(pipeline_metrics, "_collect_samples", boom)
    result = families(PipelineCollector())

    assert result["pcdf_metrics_up"].samples[0].value == 0.0
    # No stale business gauges are served alongside a failed scrape.
    assert set(result) == {"pcdf_metrics_up"}


def test_a_successful_scrape_reports_healthy_and_yields_gauges(monkeypatch):
    spec = SPECS[0]
    monkeypatch.setattr(
        pipeline_metrics, "_collect_samples",
        lambda: [(spec, [(["completed"], 7.0), (["failed"], 1.0)])],
    )
    result = families(PipelineCollector())

    assert result["pcdf_metrics_up"].samples[0].value == 1.0
    values = {s.labels["status"]: s.value for s in result[spec.name].samples}
    assert values == {"completed": 7.0, "failed": 1.0}


def test_results_are_cached_so_a_scrape_storm_cannot_hammer_postgres(monkeypatch):
    calls = []

    def counted():
        calls.append(1)
        return []

    monkeypatch.setattr(pipeline_metrics, "_collect_samples", counted)
    collector = PipelineCollector()
    for _ in range(5):
        list(collector.collect())
    assert len(calls) == 1


def test_the_cache_expires(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pipeline_metrics, "_collect_samples", lambda: calls.append(1) or []
    )
    collector = PipelineCollector()
    list(collector.collect())
    # Pretend the cached read happened longer ago than the TTL allows.
    pipeline_metrics._cache.at -= pipeline_metrics.CACHE_TTL_SECONDS + 1
    list(collector.collect())
    assert len(calls) == 2


def test_an_unlabelled_metric_with_no_rows_still_reports_zero(monkeypatch):
    """Zero is an answer; a missing series is a gap in the graph."""
    unlabelled = next(s for s in SPECS if not s.labels)
    monkeypatch.setattr(
        pipeline_metrics, "_collect_samples", lambda: [(unlabelled, [([], 0.0)])]
    )
    result = families(PipelineCollector())
    assert result[unlabelled.name].samples[0].value == 0.0
