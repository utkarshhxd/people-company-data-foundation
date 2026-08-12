import asyncio

from common import FOUNDATION_NAME
from fastapi import APIRouter, Response

from api_service.deps import check_kafka, check_postgres

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict:
    return {"status": "ok", "service": FOUNDATION_NAME}


@router.get("/ready")
async def ready(response: Response) -> dict:
    postgres_result, kafka_result = await asyncio.gather(
        check_postgres(), check_kafka(), return_exceptions=True
    )

    checks = {}
    for name, result in (("postgres", postgres_result), ("kafka", kafka_result)):
        checks[name] = "ok" if result is None else f"error: {result}"

    ok = all(v == "ok" for v in checks.values())
    response.status_code = 200 if ok else 503
    return {"status": "ok" if ok else "error", "checks": checks}
