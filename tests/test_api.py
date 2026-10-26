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


def test_secondary_routes_are_directly_accessible():
    for path in ("/workspace", "/evidence", "/evaluation", "/models"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Atlas BI" in response.text and "NL2SQL" in response.text


def test_evaluation_endpoint_exposes_acceptance_metadata():
    response = client.get("/api/evaluation")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "rule"
    assert body["dataset_range"] == "2025-10-01 至 2026-02-28"
    assert body["evaluated_at"]
    assert body["total"] == 6
    assert body["passed"] == 6
    assert all(
        {"question", "intent", "executed", "hit", "attempts", "elapsed_ms", "error"} <= set(case)
        for case in body["cases"]
    )


def test_evaluation_page_contains_acceptance_and_auto_load_markup():
    body = client.get("/evaluation").text
    assert "验收标准" in body
    assert "逐用例结果" in body
    assert 'id="eval-meta"' in body
    assert 'id="eval-count"' in body
    script = client.get("/static/app.js").text
    assert "if (view === 'evaluation') loadEvaluation();" in script
    assert "renderEvaluation(data)" in script
    assert "验收说明" in script

def test_model_config_endpoint_masks_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_MODEL_CONFIG_PATH", str(tmp_path / "model.json"))
    monkeypatch.setenv("ATLAS_DISABLE_MODEL", "0")
    response = client.put(
        "/api/model/config",
        json={
            "enabled": True,
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4.1-mini",
            "api_key": "very-secret-key",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert body["api_key_configured"] is True
    assert "very-secret-key" not in response.text


def test_model_list_endpoint_does_not_require_saved_config(monkeypatch):
    called = {}

    def fake_models(config):
        called["base_url"] = config.base_url
        called["api_key"] = config.api_key
        return ["model-a", "model-b"]

    monkeypatch.setattr("app.api.main.available_models", fake_models)
    response = client.post(
        "/api/model/models",
        json={
            "provider": "custom",
            "base_url": "https://example.com/v1",
            "api_key": "temporary-key",
        },
    )
    assert response.status_code == 200
    assert response.json()["models"] == ["model-a", "model-b"]
    assert called == {"base_url": "https://example.com/v1", "api_key": "temporary-key"}
