from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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
from app.nl2sql.seed import generate_database
from app.nl2sql.settings import get_settings
from app.nl2sql.workflow import get_retriever, run_query

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
settings = get_settings()

app = FastAPI(title=settings.app_name, version="1.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=300)


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
VIEWS = {"workspace", "evidence", "evaluation", "models"}


@app.get("/")
@app.get("/workspace")
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
    }


@app.get("/api/overview")
def overview() -> dict:
    if not settings.db_path.exists():
        generate_database(settings.db_path)
    return overview_metrics()


@app.get("/api/examples")
def examples() -> dict:
    return {"examples": EXAMPLES}


@app.post("/api/query")
def query(payload: QueryRequest) -> dict:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")
    try:
        if not settings.db_path.exists():
            generate_database(settings.db_path)
        return run_query(question).as_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询失败：{exc}") from exc


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
