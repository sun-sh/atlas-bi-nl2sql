from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.nl2sql.analytics import overview_metrics
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
    digest_from_result,
    narrative_warnings,
    narrate_result,
    stream_result_narrative,
)
from app.nl2sql.seed import generate_database
from app.nl2sql.session_store import append_turn, create_session, delete_session, get_session, list_sessions
from app.nl2sql.settings import get_settings
from app.nl2sql.workflow import get_retriever, run_query


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
settings = get_settings()

app = FastAPI(title=settings.app_name, version="1.2.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    session_id: str | None = Field(default=None, max_length=64)


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


EXAMPLES = [
    "上个月华东地区销售额是多少？",
    "近90天各品类销售额排名",
    "退款率最高的品类是什么？",
    "上个月各渠道 ROI",
    "按日看本月销售额趋势",
    "各渠道客单价",
]


def _prepare_query(payload: QueryRequest) -> tuple[str, str | None, dict]:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")
    session_id = payload.session_id
    session = get_session(session_id) if session_id else None
    if session_id and session is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期，请新建会话")
    if not settings.db_path.exists():
        generate_database(settings.db_path)
    context: dict = {}
    if session and session.get("turns"):
        latest_turn = session["turns"][-1]
        context = dict(session.get("context") or {})
        context.update({"question": latest_turn.get("question", "")})
    return question, session_id, context


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
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    retriever = get_retriever()
    model = get_model_config()
    return {
        "status": "ok",
        "database": settings.db_path.exists(),
        "retrieval": retriever.describe(),
        "llm_enabled": model.enabled,
        "llm_active": model.enabled and model.active,
        "model": model.public(),
        "max_repair_attempts": settings.max_repair_attempts,
        "streaming": {"query": True, "narrative": True},
    }


@app.get("/api/overview")
def overview() -> dict:
    if not settings.db_path.exists():
        generate_database(settings.db_path)
    return overview_metrics()


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
    try:
        question, session_id, context = _prepare_query(payload)
        result = run_query(question, context=context)
        payload_result = result.as_dict()
        payload_result["session_id"] = session_id
        updated = _persist_turn(session_id, question, result)
        if updated is not None:
            payload_result["session"] = updated
        return payload_result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询失败：{exc}") from exc


def _sse(event: str, data: dict[str, Any]) -> str:
    import json

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _stream_turn(question: str, session_id: str | None, context: dict) -> Iterator[str]:
    result = run_query(question, context=context, narrate=False)
    yield _sse("result", {"result": result.as_dict(), "session_id": session_id})
    digest = digest_from_result(result)
    narrative = None
    sent_thinking = False
    for event in stream_result_narrative(question, result.plan, digest):
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
    if narrative.mode == "model":
        result.answer = narrative.text
        result.narrative_mode = narrative.mode
        result.narrative_model = narrative.model
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


@app.post("/api/query/stream")
def query_stream(payload: QueryRequest) -> StreamingResponse:
    try:
        question, session_id, context = _prepare_query(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询失败：{exc}") from exc
    return StreamingResponse(
        _stream_turn(question, session_id, context),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/evaluation")
def evaluation() -> dict:
    from app.nl2sql.evaluation import run_evaluation

    if not settings.db_path.exists():
        generate_database(settings.db_path)
    return run_evaluation()


@app.get("/api/model/config")
def model_config() -> dict:
    return get_model_config().public()


@app.put("/api/model/config")
def update_model_config(payload: ModelSettingsRequest) -> dict:
    try:
        saved = save_model_config(**payload.model_dump())
        return saved.public()
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/model/models")
def list_models(payload: ModelProbeRequest) -> dict:
    try:
        config = candidate_config(**payload.model_dump())
        return {"models": available_models(config)}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=model_error_message(exc)) from exc


@app.post("/api/model/test")
def test_model(payload: ModelProbeRequest) -> dict:
    try:
        config = candidate_config(**payload.model_dump())
        reply = test_model_chat(config)
        return {"status": "ok", "reply": reply}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=model_error_message(exc)) from exc
