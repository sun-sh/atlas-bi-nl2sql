from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import app


client = TestClient(app)


def test_health_and_examples():
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["retrieval"]["tables"] == 5
    assert "fusion" in body["retrieval"]
    assert len(client.get("/api/examples").json()["examples"]) == 6


def test_overview_returns_business_metrics():
    body = client.get("/api/overview").json()
    assert body["revenue"] > 0
    assert body["orders"] > 0
    assert len(body["channel"]) == 4


def test_query_endpoint_returns_trace():
    response = client.post("/api/query", json={"question": "上个月App渠道ROI"})
    assert response.status_code == 200
    body = response.json()
    assert body["attempts"][-1]["status"] == "success"
    assert body["rows"][0]["渠道"] == "App"
    assert "m.spend_date" in body["sql"]


def test_invalid_query_is_rejected():
    response = client.post("/api/query", json={"question": "x"})
    assert response.status_code == 422
