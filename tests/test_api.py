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
    assert body["run_context"]["policy_version"] == "nl2sql-harness-v1"
    assert body["run_context"]["trace_id"] == body["trace_id"]
    assert body["run_context"]["max_result_rows"] >= len(body["rows"])


def test_query_cancel_endpoint_is_idempotent():
    response = client.post("/api/query/cancel/not-running")
    assert response.status_code == 200
    assert response.json() == {"cancelled": False, "trace_id": "not-running"}


def test_invalid_query_is_rejected():
    response = client.post("/api/query", json={"question": "x"})
    assert response.status_code == 422


def test_secondary_routes_are_directly_accessible():
    for path in ("/workspace", "/overview", "/evidence", "/data-assets", "/evaluation", "/models"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Atlas BI" in response.text and "电商经营分析" in response.text


def test_evaluation_endpoint_reports_model_only_status():
    response = client.get("/api/evaluation")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "model"
    assert body["dataset_range"] == "2025-10-01 至 2026-02-28"
    assert body["evaluated_at"]
    assert body["total"] == 100
    assert body["suite_version"] == "strict-model-v3-100"
    assert sum(bool(case["setup_question"]) for case in body["cases"]) == 11
    assert body["available"] is False
    assert body["passed"] == 0
    assert body["model_rate"] == 0
    assert all(
        {
            "question",
            "intent",
            "executed",
            "hit",
            "model_used",
            "narrative_mode",
            "attempts",
            "elapsed_ms",
            "sql_mode",
            "warnings",
            "error",
        }
        <= set(case)
        for case in body["cases"]
    )


def test_evaluation_page_explains_strict_model_policy():
    body = client.get('/evaluation').text
    assert '严格链路' in body
    assert '禁止规则与摘要补位' in body
    assert '数据集版本' in body
    assert '模型实验' in body
    assert '逐用例结果' in body
    assert 'id="eval-meta"' in body
    assert 'id="eval-count"' in body
    script = client.get('/static/app.js').text
    assert "if (view === 'evaluation') loadEvaluationTasks();" in script
    assert '任务队列' in body
    assert 'id="eval-baseline"' in body
    assert 'id="eval-task-list"' in body
    assert '提交评测任务' in body
    assert 'renderEvaluationTaskList(data)' in script
    assert 'SQL 执行率' in script
    evaluation_renderer = script.split('function renderEvaluationReport(task)', 1)[1].split('async function selectEvaluationTask', 1)[0]
    assert '规则回退' not in evaluation_renderer


def test_workspace_ui_contract_keeps_results_and_model_settings_consistent():
    page = client.get('/workspace').text
    script = client.get('/static/app.js').text
    styles = client.get('/static/styles.css').text

    assert 'id="model-name"' in page
    assert 'list="model-options"' in page
    assert 'id="model-name-manual"' not in page
    assert '数据依据与 SQL' in script
    assert "document.body.classList.toggle('workspace-active'" in script
    assert 'body.workspace-active' in styles
    assert '$(\'#model-name-manual\')' not in script

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



def test_session_endpoints_and_memory_query(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_SESSION_DB_PATH", str(tmp_path / "sessions.db"))
    created = client.post("/api/sessions", json={})
    assert created.status_code == 200
    session = created.json()
    assert session["id"].startswith("s_")
    assert session["turn_count"] == 0
    listed_empty = client.get("/api/sessions")
    assert listed_empty.status_code == 200
    assert any(item["id"] == session["id"] for item in listed_empty.json()["sessions"])

    first = client.post(
        "/api/query",
        json={"question": "上个月华东地区销售额是多少？", "session_id": session["id"]},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["session_id"] == session["id"]
    assert first_body["session"]["turn_count"] == 1
    assert first_body["session"]["context"]["region_label"] == "华东"

    second = client.post(
        "/api/query",
        json={"question": "那华南呢", "session_id": session["id"]},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["plan"]["context"]["region_label"] == "华南"
    assert any("时间范围" in item for item in second_body["plan"]["inherited"])
    assert first_body["plan"]["time_range"] == second_body["plan"]["time_range"]
    assert second_body["session"]["turn_count"] == 2

    listed = client.get("/api/sessions")
    assert listed.status_code == 200
    assert any(item["id"] == session["id"] for item in listed.json()["sessions"])

    deleted = client.delete(f"/api/sessions/{session['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/sessions/{session['id']}").status_code == 404


def test_data_assets_endpoint_reports_catalog_quality_and_relations():
    response = client.get('/api/data-assets')
    assert response.status_code == 200
    body = response.json()
    assert body['summary']['status'] == 'healthy'
    assert body['summary']['table_count'] == 5
    assert body['summary']['failed_checks'] == 0
    assert {table['name'] for table in body['tables']} == {
        'orders', 'order_items', 'refunds', 'marketing_spend', 'customers'
    }
    assert len(body['relations']) == 3
    assert all(relation['orphan_count'] == 0 for relation in body['relations'])
    assert {metric['name'] for metric in body['semantic_layer']['metrics']} >= {
        '销售额', '退款率', 'ROI', '客单价'
    }
    assert body['boundary']['level'] == 'project-level-data-asset-layer'
    assert client.get('/data-assets').status_code == 200


def test_evaluation_task_history_and_comparison(monkeypatch, tmp_path):
    monkeypatch.setenv('ATLAS_EVALUATION_DB_PATH', str(tmp_path / 'evaluation_tasks.db'))
    history = client.get('/api/evaluation/tasks')
    assert history.status_code == 200
    body = history.json()
    assert {task['id'] for task in body['tasks']} == {
        'ev_archive_v3_final', 'ev_archive_v3_initial', 'ev_archive_v2'
    }
    assert body['active'] is None
    detail = client.get('/api/evaluation/tasks/ev_archive_v3_final')
    assert detail.status_code == 200
    assert detail.json()['report']['passed'] == 100
    comparison = client.get(
        '/api/evaluation/compare',
        params={'baseline': 'ev_archive_v3_initial', 'candidate': 'ev_archive_v3_final'},
    )
    assert comparison.status_code == 200
    diff = comparison.json()
    assert diff['shared_cases'] == 100
    assert diff['delta']['passed'] == 21
    assert len(diff['fixed']) == 21
    assert diff['regressed'] == []


def test_evaluation_task_creation_rejects_disabled_model(monkeypatch, tmp_path):
    monkeypatch.setenv('ATLAS_EVALUATION_DB_PATH', str(tmp_path / 'evaluation_tasks.db'))
    response = client.post('/api/evaluation/tasks', json={'workers': 3})
    assert response.status_code == 409
    assert '模型未启用' in response.json()['detail']

def test_data_platform_control_plane_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv('ATLAS_DATA_PLATFORM_DB_PATH', str(tmp_path / 'platform.db'))
    monkeypatch.setenv('ATLAS_PLATFORM_WAREHOUSE_DB_PATH', str(tmp_path / 'warehouse.db'))
    overview = client.get('/api/data-platform')
    assert overview.status_code == 200
    body = overview.json()
    assert body['summary']['source_count'] == 1
    assert body['summary']['metric_count'] >= 5
    assert len(body['lineage']) > 20
    assert client.get('/api/data-platform/workspaces').status_code == 200
    assert client.get('/api/data-platform/sources').status_code == 200
    assert client.get('/api/data-platform/quality-rules').status_code == 200
    assert client.get('/api/data-platform/metrics').status_code == 200
    synced = client.post('/api/data-platform/sources/ds_commerce/sync')
    assert synced.status_code == 200
    assert synced.json()['row_count'] > 0


def test_evaluation_platform_registry_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv('ATLAS_EVALUATION_DB_PATH', str(tmp_path / 'evaluation_tasks.db'))
    datasets = client.get('/api/evaluation/datasets')
    assert datasets.status_code == 200
    assert any(item['id'] == 'dataset_v3_100' for item in datasets.json()['datasets'])
    created = client.post(
        '/api/evaluation/datasets',
        json={'name': 'API 小集', 'version': 'v1', 'case_ids': ['sales-east']},
    )
    assert created.status_code == 201
    dataset = created.json()
    assert dataset['case_count'] == 1
    experiment = client.post(
        '/api/evaluation/experiments',
        json={'name': 'API 实验', 'dataset_id': dataset['id'], 'hypothesis': '验证接口闭环'},
    )
    assert experiment.status_code == 201
    assert experiment.json()['dataset_id'] == dataset['id']
    assert client.get('/api/evaluation/experiments').status_code == 200
    assert client.get('/api/evaluation/feedback').status_code == 200