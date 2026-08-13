from fastapi import FastAPI
from prometheus_client import REGISTRY
from prometheus_fastapi_instrumentator import Instrumentator

from api_service.pipeline_metrics import PipelineCollector
from api_service.routers import entities, health

app = FastAPI(
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
