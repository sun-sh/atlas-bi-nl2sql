from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.nl2sql.analytics import overview_metrics
from app.nl2sql.data_assets import data_assets_overview
from app.nl2sql.data_platform import (
    DEFAULT_WORKSPACE,
    DataPlatformError,
    DataSourceNotFound,
    PermissionDenied,
    activate_query_source,
    active_query_database,
    create_metric,
    create_quality_rule,
    create_workspace,
    data_service_query,
    dispatch_due_syncs,
    ensure_data_platform,
    list_data_sources,
    list_metrics,
    list_quality_rules,
    list_workspaces,
    platform_overview,
    register_data_source,
    run_quality,
    sync_data_source,
)
from app.nl2sql.evaluation_tasks import (
    ActiveEvaluationTaskError,
    DatasetNotFound,
    ModelUnavailableError,
    TaskStateError,
    add_feedback,
    cancel_evaluation_task,
    claim_next_evaluation_task,
    compare_evaluation_tasks,
    create_dataset,
    create_evaluation_task,
    create_experiment,
    execute_evaluation_task,
    get_evaluation_task,
    list_datasets,
    list_evaluation_tasks,
    list_experiments,
    list_feedback,
    retry_evaluation_task,
)
from app.nl2sql.model_config import (
    ModelConfigurationError,
    available_models,
    candidate_config,
    get_model_config,
    model_error_message,
    save_model_config,
    test_model_chat,
)
from app.nl2sql.result_insight import (
    NarrativeResult,
    digest_from_result,
    narrative_warnings,
    narrate_result,
    rule_narrative,
    stream_result_narrative,
    validate_narrative,
)
from app.nl2sql.runtime_metrics import observe, snapshot as metrics_snapshot
from app.nl2sql.seed import generate_database
from app.nl2sql.session_store import append_turn, create_session, delete_session, get_session, list_sessions
from app.nl2sql.harness import is_cancelled, register_cancel, release_cancel, request_cancel
from app.nl2sql.settings import get_settings
from app.nl2sql.workflow import get_retriever, query_stream_events, run_query


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
settings = get_settings()

app = FastAPI(title=settings.app_name, version="1.3.0")


def _require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    """Optional operator gate; local default remains open for development."""
    expected = get_settings().admin_api_token
    if not expected:
        return
    if x_admin_token != expected:
        raise HTTPException(status_code=401, detail="需要有效的管理令牌")

app.mount("/static", StaticFiles(directory=STATIC), name="static")


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    session_id: str | None = Field(default=None, max_length=64)
    request_id: str | None = Field(default=None, max_length=64)


class SessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=40)


class ModelSettingsRequest(BaseModel):
    enabled: bool = False
    provider: str = "custom"
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    api_key: str | None = None
    clear_api_key: bool = False


class ModelProbeRequest(BaseModel):
    provider: str = "custom"
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    api_key: str | None = None


class EvaluationTaskCreateRequest(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    workers: int = Field(default=3, ge=1, le=3)
    dataset_id: str = "dataset_v3_100"
    experiment_id: str | None = None
    priority: int = Field(default=5, ge=1, le=9)


class DatasetCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    version: str = Field(default="v1", max_length=40)
    description: str = Field(default="", max_length=240)
    case_ids: list[str] = Field(min_length=1, max_length=500)


class ExperimentCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    dataset_id: str
    hypothesis: str = Field(default="", max_length=240)


class FeedbackCreateRequest(BaseModel):
    task_id: str
    case_id: str
    category: str
    note: str = Field(default="", max_length=500)


class SourceCreateRequest(BaseModel):
    workspace_id: str = DEFAULT_WORKSPACE
    name: str = Field(min_length=2, max_length=80)
    kind: str
    location: str
    sync_mode: str = "snapshot"
    sensitivity: str = "internal"
    schedule_minutes: int = Field(default=0, ge=0, le=10080)


class MetricCreateRequest(BaseModel):
    workspace_id: str = DEFAULT_WORKSPACE
    name: str = Field(min_length=2, max_length=60)
    grain: str = Field(default="", max_length=60)
    definition: str = Field(min_length=1, max_length=500)
    owner: str = Field(default="", max_length=60)
    status: str = "draft"


class QualityRuleCreateRequest(BaseModel):
    workspace_id: str = DEFAULT_WORKSPACE
    source_id: str
    name: str = Field(min_length=2, max_length=80)
    rule_type: str
    table_name: str
    column_name: str = ""
    expression: str = ""
    severity: str = "error"


EXAMPLES = [
    "上个月华东地区销售额是多少？",
    "近90天各品类销售额排名",
    "退款率最高的品类是什么？",
    "上个月各渠道 ROI",
    "按日看本月销售额趋势",
    "各渠道客单价",
]


def _prepare_query(payload: QueryRequest) -> tuple[str, str | None, dict, str]:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")
    session_id = payload.session_id
    session = get_session(session_id) if session_id else None
    if session_id and session is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期，请新建会话")

    # Resolve this request's database in one place. Only the demo fallback may
    # be created on demand; an explicitly activated source is never replaced.
    fallback_db = active_query_database(settings.workspace_id)
    if not fallback_db["path"].exists():
        if not fallback_db["fallback"]:
            raise HTTPException(status_code=503, detail="当前分析数据源文件不存在，请重新同步或切换数据源")
        generate_database(settings.db_path)

    context: dict = {}
    if session and session.get("turns"):
        latest_turn = session["turns"][-1]
        context = dict(session.get("context") or {})
        context.update({"question": latest_turn.get("question", "")})
    return question, session_id, context, str(fallback_db["path"])


def _persist_turn(session_id: str | None, question: str, result) -> dict | None:
    if not session_id:
        return None
    updated = append_turn(session_id, question, result)
    if updated is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期，请新建会话")
    return updated


@app.get("/")
@app.get("/workspace")
@app.get("/overview")
@app.get("/evidence")
@app.get("/evaluation")
@app.get("/models")
@app.get("/data-assets")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    retriever = get_retriever()
    model = get_model_config()
    query_db = active_query_database(settings.workspace_id)
    return {
        "status": "ok",
        "database": query_db["path"].exists(),
        "query_database": {
            "workspace_id": query_db["workspace_id"],
            "source_id": query_db["source_id"],
            "database": query_db["database"],
            "read_only": query_db["read_only"],
            "fallback": query_db["fallback"],
        },
        "retrieval": retriever.describe(),
        "llm_enabled": model.enabled,
        "llm_active": model.enabled and model.active,
        "model": model.public(),
        "max_repair_attempts": settings.max_repair_attempts,
        "streaming": {"query": True, "stages": True, "narrative": True},
    }


@app.get("/api/metrics")
def runtime_metrics() -> dict:
    return metrics_snapshot()


@app.get("/api/overview")
def overview() -> dict:
    query_db = active_query_database(settings.workspace_id)
    if not query_db["path"].exists():
        if query_db["fallback"]:
            generate_database(settings.db_path)
        else:
            raise HTTPException(status_code=503, detail="当前分析数据源文件不存在，请重新同步或切换数据源")
    return overview_metrics(query_db["path"])


@app.get("/api/examples")
def examples() -> dict:
    return {"examples": EXAMPLES}


@app.get("/api/sessions")
def sessions(limit: int = 30) -> dict:
    return {"sessions": list_sessions(limit)}


@app.post("/api/sessions")
def create_session_endpoint(payload: SessionCreateRequest) -> dict:
    return create_session(payload.title)


@app.get("/api/sessions/{session_id}")
def session_detail(session_id: str) -> dict:
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return session


@app.delete("/api/sessions/{session_id}")
def remove_session(session_id: str) -> dict:
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return {"deleted": True, "session_id": session_id}


@app.post("/api/query")
def query(payload: QueryRequest) -> dict:
    started = time.perf_counter()
    error = False
    try:
        question, session_id, context, db_path = _prepare_query(payload)
        result = run_query(question, context=context, db_path=db_path)
        payload_result = result.as_dict()
        payload_result["session_id"] = session_id
        updated = _persist_turn(session_id, question, result)
        if updated is not None:
            payload_result["session"] = updated
        return payload_result
    except HTTPException:
        error = True
        raise
    except Exception as exc:
        error = True
        raise HTTPException(status_code=500, detail=f"查询失败：{exc}") from exc
    finally:
        observe("query", (time.perf_counter() - started) * 1000, error=error)


def _sse(event: str, data: dict[str, Any]) -> str:
    import json

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


STREAM_STAGE_LABELS = {
    "retrieve": "正在查找相关数据表",
    "plan": "正在解析分析意图",
    "generate": "正在生成 SQL",
    "execute": "正在执行只读查询",
    "repair": "正在修复 SQL 并重试",
    "finalize": "正在校验结果并组织回答",
}


def _stream_stage(node: str, state: dict) -> dict:
    payload = {
        "stage": node,
        "message": STREAM_STAGE_LABELS.get(node, "正在分析"),
    }
    if node == "plan" and state.get("plan") is not None:
        plan = state["plan"]
        payload["time_range"] = plan.time_range
        payload["outcome"] = plan.outcome
        payload["reason_code"] = plan.reason_code
    if node in {"retrieve", "plan"} and state.get("hits"):
        payload["schema_count"] = len(state["hits"])
    if node in {"generate", "repair"} and state.get("mode"):
        payload["sql_mode"] = state["mode"]
    if node == "execute":
        attempts = state.get("attempts") or []
        if attempts:
            payload["attempt"] = attempts[-1].get("attempt")
            payload["attempt_status"] = attempts[-1].get("status")
            if attempts[-1].get("status") == "failed" and attempts[-1].get("error"):
                payload["error"] = attempts[-1]["error"]
    return payload


def _stream_turn(
    question: str,
    session_id: str | None,
    context: dict,
    trace_id: str,
    db_path: str,
) -> Iterator[str]:
    yield _sse("start", {"session_id": session_id, "trace_id": trace_id, "message": "正在分析问题"})
    result = None
    events = query_stream_events(
        question,
        context=context,
        narrate=False,
        trace_id=trace_id,
        db_path=db_path,
    )
    for event in events:
        state_data = event.get("state") or {}
        if trace_id and is_cancelled(trace_id):
            yield _sse("cancelled", {"message": "查询已取消", "trace_id": trace_id})
            return
        if event.get("type") == "cancelled":
            yield _sse("cancelled", {"message": "查询已取消", "trace_id": trace_id})
            return
        if event.get("type") == "stage":
            yield _sse(
                "status",
                _stream_stage(event["node"], state_data),
            )
        elif event.get("type") == "result":
            result = event["result"]
    if result is None:
        raise RuntimeError("查询工作流未返回结果")
    yield _sse("result", {"result": result.as_dict(), "session_id": session_id})
    if result.outcome != "answered":
        narrative = {
            "text": result.answer,
            "mode": "guard",
            "model": "",
            "retries": 0,
            "fallback_reason": "",
            "elapsed_ms": 0,
        }
        yield _sse("narrative", narrative)
        updated = _persist_turn(session_id, question, result)
        yield _sse(
            "done",
            {
                "session_id": session_id,
                "session": updated,
                "elapsed_ms": round(result.elapsed_ms, 1),
                "trace_id": result.trace_id,
            },
        )
        return
    digest = digest_from_result(result)
    narrative = None
    sent_thinking = False
    for event in stream_result_narrative(question, result.plan, digest, trace_id=trace_id):
        if trace_id and is_cancelled(trace_id):
            yield _sse("cancelled", {"message": "查询已取消", "trace_id": trace_id})
            return
        if event.get("type") == "cancelled":
            yield _sse("cancelled", {"message": "查询已取消", "trace_id": trace_id})
            return
        if event.get("type") == "delta":
            narrative = None
            yield _sse("delta", {"delta": event.get("delta", "")})
        elif event.get("type") == "reset":
            narrative = None
            yield _sse("reset", {"text": event.get("text", "")})
        elif event.get("type") == "thinking" and not sent_thinking:
            sent_thinking = True
            yield _sse("thinking", {"message": "模型正在组织分析结论"})
        elif event.get("type") == "done":
            narrative = event["narrative"]
    if narrative is None:
        narrative = narrate_result(question, result.plan, digest)
    check = validate_narrative(narrative.text, digest, mode=narrative.mode)
    if narrative.mode == "model" and not check["passed"]:
        narrative = NarrativeResult(
            text=rule_narrative(digest),
            mode="digest",
            fallback_reason="解读事实校验未通过：" + "；".join(check["errors"]),
            retries=narrative.retries,
            elapsed_ms=narrative.elapsed_ms,
        )
        check = validate_narrative(narrative.text, digest, mode="digest")
        check["fallback_from"] = "model"
    result.narrative_validation = check
    if narrative.mode == "model":
        result.answer = narrative.text
        result.narrative_mode = narrative.mode
        result.narrative_model = narrative.model
        result.narrative_retries = narrative.retries
    elif narrative.fallback_reason:
        result.warnings.extend(narrative_warnings(narrative))
    yield _sse("narrative", narrative.as_dict())
    updated = _persist_turn(session_id, question, result)
    yield _sse(
        "done",
        {
            "session_id": session_id,
            "session": updated,
            "elapsed_ms": round(result.elapsed_ms, 1),
            "trace_id": result.trace_id,
        },
    )


@app.post("/api/query/cancel/{trace_id}")
def cancel_query(trace_id: str) -> dict:
    accepted = request_cancel(trace_id)
    return {"cancelled": accepted, "trace_id": trace_id}


@app.post("/api/query/stream")
def query_stream(payload: QueryRequest) -> StreamingResponse:
    started = time.perf_counter()
    error = False
    try:
        question, session_id, context, db_path = _prepare_query(payload)
    except HTTPException:
        error = True
        raise
    except Exception as exc:
        error = True
        raise HTTPException(status_code=500, detail=f"查询失败：{exc}") from exc
    trace_id = payload.request_id or __import__("uuid").uuid4().hex[:12]
    register_cancel(trace_id)

    def stream():
        nonlocal error
        try:
            yield from _stream_turn(question, session_id, context, trace_id, db_path)
        except Exception:
            error = True
            raise
        finally:
            release_cancel(trace_id)
            observe("query_stream", (time.perf_counter() - started) * 1000, error=error)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@app.get("/api/data-assets")
def data_assets() -> dict:
    return data_assets_overview()


@app.get("/api/data-platform")
def data_platform(workspace_id: str = DEFAULT_WORKSPACE) -> dict:
    try:
        return platform_overview(workspace_id)
    except DataPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/data-platform/workspaces")
def data_platform_workspaces() -> dict:
    return {"workspaces": list_workspaces()}


@app.post("/api/data-platform/workspaces", status_code=201)
def create_data_workspace(payload: dict, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return create_workspace(name=str(payload.get("name", "")), description=str(payload.get("description", "")))
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DataPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/data-platform/sources")
def data_platform_sources(workspace_id: str = DEFAULT_WORKSPACE) -> dict:
    return {"sources": list_data_sources(workspace_id)}


@app.post("/api/data-platform/sources", status_code=201)
def register_platform_source(payload: SourceCreateRequest, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return register_data_source(**payload.model_dump())
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DataPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/data-platform/sources/{source_id}/sync")
def sync_platform_source(source_id: str, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return sync_data_source(source_id)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DataPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/data-platform/metrics")
def data_platform_metrics(workspace_id: str = DEFAULT_WORKSPACE) -> dict:
    return {"metrics": list_metrics(workspace_id)}


@app.post("/api/data-platform/metrics", status_code=201)
def add_data_platform_metric(payload: MetricCreateRequest, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return create_metric(**payload.model_dump())
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DataPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/data-platform/quality-rules")
def data_platform_quality_rules(workspace_id: str = DEFAULT_WORKSPACE) -> dict:
    return {"rules": list_quality_rules(workspace_id)}


@app.post("/api/data-platform/quality-rules", status_code=201)
def add_data_platform_quality_rule(payload: QualityRuleCreateRequest, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return create_quality_rule(**payload.model_dump())
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DataPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/data-platform/sources/{source_id}/quality")
def run_platform_quality(source_id: str, workspace_id: str = DEFAULT_WORKSPACE, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return run_quality(workspace_id, source_id)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DataPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/data-platform/sources/{source_id}/activate")
def activate_data_source(source_id: str, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return activate_query_source(source_id)
    except DataSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DataPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/data-platform/data-service")
def query_data_service(source_id: str, table: str, limit: int = 100, workspace_id: str = DEFAULT_WORKSPACE) -> dict:
    try:
        return data_service_query(workspace_id, source_id, table, limit=limit)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DataPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/data-platform/scheduler/run")
def run_data_scheduler(_: None = Depends(_require_admin_token)) -> dict:
    try:
        return dispatch_due_syncs()
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/evaluation/datasets")
def evaluation_datasets() -> dict:
    return {"datasets": list_datasets()}


@app.post("/api/evaluation/datasets", status_code=201)
def create_evaluation_dataset(payload: DatasetCreateRequest, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return create_dataset(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/evaluation/experiments")
def evaluation_experiments() -> dict:
    return {"experiments": list_experiments()}


@app.post("/api/evaluation/experiments", status_code=201)
def create_evaluation_experiment(payload: ExperimentCreateRequest, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return create_experiment(**payload.model_dump())
    except DatasetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/evaluation/feedback")
def evaluation_feedback(status: str | None = None) -> dict:
    return {"feedback": list_feedback(status)}


@app.post("/api/evaluation/feedback", status_code=201)
def create_evaluation_feedback(payload: FeedbackCreateRequest, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return add_feedback(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/evaluation/tasks/{task_id}/cancel")
def cancel_evaluation(task_id: str, _: None = Depends(_require_admin_token)) -> dict:
    try:
        return cancel_evaluation_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/evaluation/tasks/{task_id}/retry", status_code=202)
def retry_evaluation(task_id: str, background_tasks: BackgroundTasks, _: None = Depends(_require_admin_token)) -> dict:
    try:
        task = retry_evaluation_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(execute_evaluation_task, task["id"])
    return task


@app.post("/api/evaluation/queue/claim")
def claim_evaluation_queue(_: None = Depends(_require_admin_token)) -> dict:
    task = claim_next_evaluation_task()
    return {"task": task, "claimed": bool(task)}


@app.get("/api/evaluation")
def evaluation(_: None = Depends(_require_admin_token)) -> dict:
    """Compatibility endpoint: synchronously run one strict evaluation."""
    from app.nl2sql.evaluation import run_evaluation

    if not settings.db_path.exists():
        generate_database(settings.db_path)
    return run_evaluation()


@app.get("/api/evaluation/tasks")
def evaluation_tasks(limit: int = 20) -> dict:
    return list_evaluation_tasks(limit)


@app.post("/api/evaluation/tasks", status_code=202)
def start_evaluation_task(payload: EvaluationTaskCreateRequest, background_tasks: BackgroundTasks, _: None = Depends(_require_admin_token)) -> dict:
    try:
        task = create_evaluation_task(
            label=payload.label,
            workers=payload.workers,
            dataset_id=payload.dataset_id,
            experiment_id=payload.experiment_id,
            priority=payload.priority,
        )
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ActiveEvaluationTaskError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DatasetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The explicit id only executes this newly queued task. Other pending
    # tasks are consumed by the queue worker / claim endpoint.
    background_tasks.add_task(execute_evaluation_task, task["id"])
    return task


@app.get("/api/evaluation/tasks/{task_id}")
def evaluation_task_detail(task_id: str) -> dict:
    task = get_evaluation_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="评测任务不存在")
    return task


@app.get("/api/evaluation/compare")
def evaluation_compare(baseline: str, candidate: str) -> dict:
    try:
        return compare_evaluation_tasks(baseline, candidate)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"评测任务不存在：{exc.args[0]}") from exc

@app.get("/api/model/config")
def model_config() -> dict:
    return get_model_config().public()


@app.put("/api/model/config")
def update_model_config(payload: ModelSettingsRequest, _: None = Depends(_require_admin_token)) -> dict:
    try:
        saved = save_model_config(**payload.model_dump())
        return saved.public()
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/model/models")
def list_models(payload: ModelProbeRequest, _: None = Depends(_require_admin_token)) -> dict:
    try:
        config = candidate_config(**payload.model_dump())
        return {"models": available_models(config)}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=model_error_message(exc)) from exc


@app.post("/api/model/test")
def test_model(payload: ModelProbeRequest, _: None = Depends(_require_admin_token)) -> dict:
    try:
        config = candidate_config(**payload.model_dump())
        reply = test_model_chat(config)
        return {"status": "ok", "reply": reply}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=model_error_message(exc)) from exc
