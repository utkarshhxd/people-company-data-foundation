import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import REGISTRY
from prometheus_fastapi_instrumentator import Instrumentator

from api_service.auth import describe_configuration
from api_service.pipeline_metrics import PipelineCollector
from api_service.routers import entities, health

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
        "Read-only access to the trusted canonical data and its provenance. "
        "Data enters through the ingestion pipeline, where it acquires the "
        "lineage these endpoints report."
    ),
)

app.include_router(health.router)
app.include_router(entities.router)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# The API is the only long-running HTTP process, so it carries the pipeline
# gauges too rather than adding an exporter container for a handful of queries.
REGISTRY.register(PipelineCollector())

