from api_service.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_live() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_metrics_exposed() -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
