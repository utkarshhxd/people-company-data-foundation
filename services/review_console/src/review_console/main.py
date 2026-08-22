import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from common.config import settings
from common.logging import configure
from fastapi import FastAPI, Response
from prometheus_client import REGISTRY
from prometheus_fastapi_instrumentator import Instrumentator

from review_console import ingest_queue, pipeline_metrics, stream, supervisor
from review_console.alerts_router import router as alerts_router
from review_console.auth import describe_configuration
from review_console.router import (
    control_router,
    dashboard_router,
    entities_router,
    page_router,
    router,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Say how authentication is configured, every start; run the background
    loops for exactly as long as the app is up.

    A console serving personal data without credentials should never be
    something anyone has to infer from a config file.
    """
    # Before the first line is emitted. uvicorn configures its own loggers and
    # leaves the root logger bare, so without this everything below WARNING
    # from this application was discarded outright and the line below arrived
    # through logging.lastResort with no timestamp, level or logger name.
    configure("review_console")
    logger.warning("%s", describe_configuration())
    stream.start()
    # The queue worker and the supervisor are threads, not containers, because
    # both are single-instance by nature and this is the one long-running
    # process in the stack. Started here so they live exactly as long as the
    # app: no worker outliving the thing that can report on it.
    ingest_queue.worker.start()
    supervisor.supervisor.start()
    try:
        yield
    finally:
        supervisor.supervisor.stop()
        ingest_queue.worker.stop()
        await stream.stop()


app = FastAPI(
    lifespan=lifespan,
    title="Review console",
    description=(
        "The four review queues -- schema mappings, possible duplicates, "
        "quarantined records, AI enrichment proposals -- as a browser console. "
        "It writes nothing of its own: every decision goes through the same "
        "functions the review CLIs use, so a decision made here and one made "
        "in a terminal are the same code path. Also serves a read-only pipeline "
        "dashboard: batch progress and how far each batch's records got through "
        "normalization, validation, resolution and golden-record building."
    ),
)

# The console is the only long-running HTTP process in the stack, so it
# carries the pipeline gauges rather than adding an exporter container for a
# handful of aggregate queries. `/metrics` is outside the API-key dependency
# for the same reason the health endpoints are: Prometheus scrapes before
# credentials are necessarily in place, and the series here are counts and
# ages, never a name, an address or a vendor's row.
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
REGISTRY.register(pipeline_metrics.PipelineCollector())
REGISTRY.register(pipeline_metrics.KafkaCollector())

app.include_router(router)
app.include_router(dashboard_router)
app.include_router(entities_router)
app.include_router(page_router)
app.include_router(alerts_router)
app.include_router(control_router)


@app.get("/health/live")
async def live() -> dict:
    return {"status": "ok", "service": "review-console"}


@app.get("/health/ready")
async def ready(response: Response) -> dict:
    try:
        async with await psycopg.AsyncConnection.connect(
            settings.postgres_dsn, connect_timeout=5
        ) as conn, conn.cursor() as cur:
            await cur.execute("SELECT 1")
            await cur.fetchone()
        checks = {"postgres": "ok"}
    except Exception as exc:
        checks = {"postgres": f"error: {exc}"}

    ok = checks["postgres"] == "ok"
    response.status_code = 200 if ok else 503
    return {"status": "ok" if ok else "error", "checks": checks}
