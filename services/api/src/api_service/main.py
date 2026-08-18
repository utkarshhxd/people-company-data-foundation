import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import REGISTRY
from prometheus_fastapi_instrumentator import Instrumentator

from api_service.auth import describe_configuration
from api_service.pipeline_metrics import PipelineCollector
from api_service.routers import alerts, entities, health, review

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Say how authentication is configured, every start.

    An API serving personal data without credentials should never be something
    anyone has to infer from a config file.
    """
    logger.warning("%s", describe_configuration())
    yield


app = FastAPI(
    lifespan=lifespan,
    title="People & Company Data Foundation API",
    description=(
        "Trusted canonical data and its provenance, plus the review queues. "
        "Data only ever *enters* through the ingestion pipeline, where it "
        "acquires the lineage these endpoints report — nothing here writes a "
        "value. The one thing it does write is a human's decision about a value "
        "the pipeline already stopped on, and those go through the same "
        "functions the review CLIs use."
    ),
)

app.include_router(health.router)
app.include_router(entities.router)
app.include_router(alerts.router)
app.include_router(review.router)
app.include_router(review.page_router)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# The API is the only long-running HTTP process, so it carries the pipeline
# gauges too rather than adding an exporter container for a handful of queries.
REGISTRY.register(PipelineCollector())

