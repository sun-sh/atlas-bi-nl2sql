from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import app


client = TestClient(app)


def test_health_and_examples():
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert health.headers["X-Content-Type-Options"] == "nosniff"
    assert health.headers["X-Frame-Options"] == "DENY"
    assert body["retrieval"]["tables"] == 5
    assert "fusion" in body["retrieval"]
    assert body["query_database"]["read_only"] is True
    assert body["query_database"]["database"].endswith(".db")
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


def test_evaluation_endpoint_reports_model_only_status(monkeypatch):
    default = client.get("/api/evaluation")
    assert default.status_code == 410
    monkeypatch.setenv("ATLAS_ENABLE_SYNC_EVALUATION", "1")
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
    import app.api.main as api_main

    called = {}

    def fake_models(config):
        called["base_url"] = config.base_url
        called["api_key"] = config.api_key
        return ["model-a", "model-b"]

    monkeypatch.setattr(api_main, "available_models", fake_models)
    monkeypatch.setattr(api_main, "get_settings", lambda: type(
        "S", (), {"admin_api_token": "unit-secret"}
    )())
    payload = {
        "provider": "custom",
        "base_url": "https://example.com/v1",
        "api_key": "temporary-key",
    }
    assert client.post("/api/model/models", json=payload).status_code == 401
    response = client.post(
        "/api/model/models",
        json=payload,
        headers={"X-Admin-Token": "unit-secret"},
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


def test_data_platform_api_never_returns_external_dsn(monkeypatch, tmp_path):
    monkeypatch.setenv('ATLAS_DATA_PLATFORM_DB_PATH', str(tmp_path / 'platform.db'))
    monkeypatch.setenv('ATLAS_PLATFORM_WAREHOUSE_DB_PATH', str(tmp_path / 'warehouse.db'))
    created = client.post(
        '/api/data-platform/sources',
        json={
            'workspace_id': 'default',
            'name': '外部 PostgreSQL',
            'kind': 'postgresql',
            'location': '',
            'dsn': 'postgresql://reader:super-secret@db.internal/atlas',
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body['dsn_configured'] is True
    assert 'super-secret' not in str(body)
    sources = client.get('/api/data-platform/sources').json()
    overview = client.get('/api/data-platform').json()
    for payload in (sources, overview):
        assert 'super-secret' not in str(payload)
        assert '_dsn' not in str(payload)


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

def test_ambiguous_and_write_questions_stop_before_sql():
    ambiguous = client.post("/api/query", json={"question": "最近生意怎么样？"}).json()
    assert ambiguous["outcome"] == "clarification"
    assert ambiguous["mode"] == "clarification"
    assert ambiguous["plan"]["reason_code"] == "missing_metric"
    assert ambiguous["plan"]["intent"] == ""
    assert ambiguous["plan"]["metrics"] == []
    assert ambiguous["sql"] == ""
    assert ambiguous["rows"] == []

    write = client.post("/api/query", json={"question": "帮我把订单表删掉"}).json()
    assert write["outcome"] == "unsupported"
    assert write["mode"] == "unsupported"
    assert write["plan"]["reason_code"] == "write_request"
    assert write["plan"]["intent"] == ""
    assert write["plan"]["metrics"] == []
    assert write["sql"] == ""
    assert write["rows"] == []


def test_follow_up_can_use_previous_metric_context():
    created = client.post("/api/sessions", json={}).json()
    first = client.post(
        "/api/query",
        json={"question": "上个月华东地区销售额是多少？", "session_id": created["id"]},
    ).json()
    assert first["outcome"] == "answered"
    follow_up = client.post(
        "/api/query",
        json={"question": "那华南呢？", "session_id": created["id"]},
    ).json()
    assert follow_up["outcome"] == "answered"
    assert follow_up["rows"]


def test_stream_clarification_does_not_call_model(monkeypatch, tmp_path):
    import app.api.main as api_main
    from tests.test_stream import parse_sse

    monkeypatch.setenv("ATLAS_SESSION_DB_PATH", str(tmp_path / "sessions.db"))
    called = {"narrative": False}

    def forbidden_narrative(*args, **kwargs):
        called["narrative"] = True
        yield {"type": "done"}

    monkeypatch.setattr(api_main, "stream_result_narrative", forbidden_narrative)
    created = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST",
        "/api/query/stream",
        json={"question": "用户满意度怎么样？", "session_id": created["id"]},
        headers={"Accept": "text/event-stream"},
    ) as response:
        events = parse_sse("".join(response.iter_text()))
    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert "delta" not in names
    assert called["narrative"] is False
    result = next(data for name, data in events if name == "result")["result"]
    assert result["outcome"] == "unsupported"
    assert result["sql"] == ""
    assert result["rows"] == []
def test_clarification_does_not_replace_followup_context():
    created = client.post("/api/sessions", json={}).json()
    first = client.post(
        "/api/query",
        json={"question": "上个月华东地区销售额是多少？", "session_id": created["id"]},
    ).json()
    assert first["outcome"] == "answered"
    ambiguous = client.post(
        "/api/query",
        json={"question": "用户满意度怎么样？", "session_id": created["id"]},
    ).json()
    assert ambiguous["outcome"] == "unsupported"
    session = client.get(f"/api/sessions/{created['id']}").json()
    assert session["turn_count"] == 2
    assert session["turns"][-1]["result"]["outcome"] == "unsupported"
    follow_up = client.post(
        "/api/query",
        json={"question": "那华南呢？", "session_id": created["id"]},
    ).json()
    assert follow_up["outcome"] == "answered"
    assert "o.region = '华南'" in follow_up["sql"]
    assert "2026-01-01" in follow_up["sql"]


def test_optional_admin_token_protects_write_routes(monkeypatch):
    import app.api.main as api_main

    monkeypatch.setattr(api_main, "get_settings", lambda: type(
        "S", (), {"admin_api_token": "unit-secret"}
    )())
    protected = [
        ("get", "/api/data-platform", None),
        ("get", "/api/data-platform/workspaces", None),
        ("get", "/api/data-platform/sources", None),
        ("get", "/api/data-platform/quality-rules", None),
        ("get", "/api/data-platform/metrics", None),
        ("get", "/api/model/config", None),
        ("post", "/api/data-platform/metrics", {"workspace_id": "default", "name": "测试指标", "grain": "订单", "definition": "SUM(paid_amount)", "owner": "test"}),
        ("post", "/api/data-platform/quality-rules", {"name": "测试规则", "rule_type": "row_min", "table_name": "orders", "expression": "1"}),
        ("post", "/api/evaluation/datasets", {"name": "令牌测试集", "version": "v1", "case_ids": ["sales-east"]}),
        ("post", "/api/evaluation/experiments", {"name": "令牌实验", "dataset_id": "dataset_v3_100"}),
        ("post", "/api/evaluation/feedback", {"task_id": "missing", "case_id": "missing", "category": "other"}),
        ("post", "/api/evaluation/regression-candidates/approve", {"candidate_ids": ["missing"], "name": "失败回流"}),
        ("post", "/api/evaluation/tasks/missing/cancel", None),
        ("post", "/api/evaluation/tasks/missing/retry", None),
        ("post", "/api/evaluation/queue/claim", None),
        ("post", "/api/evaluation/tasks", {"workers": 1}),
        ("post", "/api/model/models", {"provider": "custom", "base_url": "https://example.com/v1"}),
        ("put", "/api/model/config", {"enabled": False, "provider": "custom", "base_url": "https://example.com/v1", "model": "test"}),
        ("get", "/api/evaluation", None),
    ]
    for method, url, payload in protected:
        request = {"json": payload} if payload is not None else {}
        response = getattr(client, method)(url, **request)
        assert response.status_code == 401, (url, response.text)
    assert client.post("/api/data-platform/scheduler/run").status_code == 401
    authorized = client.post(
        "/api/data-platform/scheduler/run",
        headers={"X-Admin-Token": "unit-secret"},
    )
    assert authorized.status_code == 200


def test_activation_moves_query_to_selected_database(monkeypatch, tmp_path):
    import app.api.main as api_main

    selected = tmp_path / "selected.db"
    selected.touch()
    monkeypatch.setattr(api_main, "active_query_database", lambda workspace_id="default": {
        "source_id": "test-source",
        "workspace_id": workspace_id,
        "database": "selected.db",
        "path": selected,
        "target": __import__("app.nl2sql.sql_engine", fromlist=["QueryTarget"]).QueryTarget("sqlite", "sqlite", selected),
        "read_only": True,
        "fallback": False,
    })
    captured = {}

    def fake_run(question, *, context=None, db_path=None, query_target=None, **kwargs):
        captured["db_path"] = db_path
        captured["query_target"] = query_target
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(api_main, "run_query", fake_run)
    response = client.post("/api/query", json={"question": "上个月销售额是多少？"})
    assert response.status_code == 500
    assert captured["query_target"].path.name == "selected.db"


def test_runtime_metrics_endpoint_tracks_sync_and_stream_latency(monkeypatch, tmp_path):
    from tests.test_stream import parse_sse

    monkeypatch.setenv("ATLAS_SESSION_DB_PATH", str(tmp_path / "sessions.db"))
    response = client.get("/api/query")
    assert response.status_code == 405
    client.get("/api/metrics")
    client.post("/api/query", json={"question": "上个月销售额是多少？"})
    with client.stream(
        "POST",
        "/api/query/stream",
        json={"question": "上个月销售额是多少？"},
        headers={"Accept": "text/event-stream"},
    ) as streamed:
        assert streamed.status_code == 200
        assert parse_sse("".join(streamed.iter_text()))[-1][0] == "done"
    metrics = client.get("/api/metrics").json()
    assert metrics["sample_limit"] == 1000
    query_metrics = metrics["routes"].get("query")
    stream_metrics = metrics["routes"].get("query_stream")
    assert query_metrics is not None
    assert query_metrics["count"] >= 1
    assert stream_metrics is not None
    assert stream_metrics["count"] >= 1
    assert stream_metrics["errors"] == 0
    assert "p95_ms" in query_metrics
    assert metrics["model_calls"]["max_concurrency"] == 4
    assert "total_tokens" in metrics["model_calls"]
    assert "success_rate" in metrics["model_calls"]


def test_query_rate_limit_returns_429_after_bucket_exhausted(monkeypatch):
    from app.api import main as api_main

    monkeypatch.setattr(api_main, "_RATE_LIMIT_PER_MINUTE", 1)
    api_main._RATE_BUCKETS.clear()
    payload = {"question": "各渠道销售额", "request_id": "rate-check"}
    first = client.post("/api/query", json=payload)
    second = client.post("/api/query", json=payload)
    assert first.status_code == 200
    assert second.status_code == 429


def test_query_rate_bucket_map_is_bounded(monkeypatch):
    from app.api import main as api_main

    monkeypatch.setattr(api_main, "_RATE_LIMIT_PER_MINUTE", 10)
    monkeypatch.setattr(api_main, "_RATE_BUCKET_LIMIT", 2)
    api_main._RATE_BUCKETS.clear()
    api_main._check_query_rate("198.51.100.1")
    api_main._check_query_rate("198.51.100.2")
    api_main._check_query_rate("198.51.100.3")
    assert len(api_main._RATE_BUCKETS) == 2
    assert "198.51.100.1" not in api_main._RATE_BUCKETS
