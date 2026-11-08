from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "Atlas BI｜电商 NL2SQL 智能分析平台"
    root: Path = ROOT
    data_dir: Path = ROOT / "data"
    db_path: Path = ROOT / "data" / "commerce.db"
    use_bge: bool = _as_bool(os.getenv("USE_BGE"), True)
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    llm_enabled: bool = _as_bool(os.getenv("LLM_ENABLED"), False)
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4.1-mini")
    qdrant_url: str | None = os.getenv("QDRANT_URL") or None
    elasticsearch_url: str | None = os.getenv("ELASTICSEARCH_URL") or None
    max_repair_attempts: int = int(os.getenv("MAX_REPAIR_ATTEMPTS", "2"))
    max_rows: int = int(os.getenv("MAX_RESULT_ROWS", "200"))
    query_timeout_seconds: float = float(os.getenv("QUERY_TIMEOUT_SECONDS", "5"))
    model_call_timeout_seconds: float = float(os.getenv("MODEL_CALL_TIMEOUT_SECONDS", "30"))
    model_max_concurrency: int = int(os.getenv("MODEL_MAX_CONCURRENCY", "4"))
    model_admission_timeout_seconds: float = float(os.getenv("MODEL_ADMISSION_TIMEOUT_SECONDS", "5"))
    model_sql_max_tokens: int = int(os.getenv("MODEL_SQL_MAX_TOKENS", "1800"))
    model_sql_retry_max_tokens: int = int(os.getenv("MODEL_SQL_RETRY_MAX_TOKENS", "3600"))
    workspace_id: str = os.getenv("ATLAS_WORKSPACE_ID", "default")
    admin_api_token: str = os.getenv("ATLAS_ADMIN_API_TOKEN", "").strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
