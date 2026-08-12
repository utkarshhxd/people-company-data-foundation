from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from api_service.routers import health

app = FastAPI(title="People & Company Data Foundation API")

app.include_router(health.router)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
