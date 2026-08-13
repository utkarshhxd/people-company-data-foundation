from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

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
