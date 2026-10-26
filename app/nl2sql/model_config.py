from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.nl2sql.settings import get_settings


DEFAULT_BASE_URL = "https://api.openai.com/v1"
SUPPORTED_PROVIDERS = ("openai", "deepseek", "qwen", "siliconflow", "moonshot", "custom")


class ModelConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key: str = ""
    source: str = "environment"

    @property
    def active(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def public(self) -> dict:
        return {
            "enabled": self.enabled,
            "active": self.enabled and self.active,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_configured": bool(self.api_key),
            "source": self.source,
        }


def _runtime_path() -> Path:
    override = os.getenv("ATLAS_MODEL_CONFIG_PATH")
    if override:
        return Path(override)
    return get_settings().data_dir / "model_config.json"


def _read_runtime() -> dict:
    path = _runtime_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _environment_config() -> ModelConfig:
    return ModelConfig(
        enabled=os.getenv("LLM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
        provider=os.getenv("LLM_PROVIDER", "openai"),
        base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        model=os.getenv("LLM_MODEL", "gpt-4.1-mini").strip(),
        api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        source="environment",
    )


def get_model_config() -> ModelConfig:
    if os.getenv("ATLAS_DISABLE_MODEL") == "1":
        return ModelConfig(False, "disabled", "", "", "", "disabled")
    environment = _environment_config()
    runtime = _read_runtime()
    if not runtime:
        return environment
    return ModelConfig(
        enabled=bool(runtime.get("enabled", False)),
        provider=str(runtime.get("provider") or environment.provider or "openai"),
        base_url=str(runtime.get("base_url") or environment.base_url or DEFAULT_BASE_URL).rstrip("/"),
        model=str(runtime.get("model") or environment.model),
        api_key=str(runtime.get("api_key") or environment.api_key),
        source="runtime",
    )


def _normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ModelConfigurationError("Base URL must start with http:// or https://")
    return value


def save_model_config(
    *,
    enabled: bool,
    provider: str,
    base_url: str,
    model: str,
    api_key: str | None = None,
    clear_api_key: bool = False,
) -> ModelConfig:
    current = get_model_config()
    provider = (provider or "custom").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ModelConfigurationError("Unsupported provider")
    normalized_base = _normalize_base_url(base_url or DEFAULT_BASE_URL)
    normalized_model = model.strip()
    if clear_api_key:
        key = ""
    elif api_key is not None and api_key.strip():
        key = api_key.strip()
    else:
        key = current.api_key
    if enabled and not key:
        raise ModelConfigurationError("API key is required when enabling the model")
    if enabled and not normalized_model:
        raise ModelConfigurationError("Model is required when enabling the model")
    payload = {
        "enabled": bool(enabled),
        "provider": provider,
        "base_url": normalized_base,
        "model": normalized_model,
        "api_key": key,
    }
    path = _runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return ModelConfig(
        enabled=bool(enabled),
        provider=provider,
        base_url=normalized_base,
        model=normalized_model,
        api_key=key,
        source="runtime",
    )


def candidate_config(
    *,
    provider: str,
    base_url: str,
    model: str = "",
    api_key: str | None = None,
) -> ModelConfig:
    saved = get_model_config()
    key = api_key.strip() if api_key and api_key.strip() else saved.api_key
    return ModelConfig(
        enabled=True,
        provider=(provider or saved.provider or "custom").strip().lower(),
        base_url=_normalize_base_url(base_url or saved.base_url or DEFAULT_BASE_URL),
        model=(model or saved.model).strip(),
        api_key=key,
        source="request" if api_key else saved.source,
    )


def make_client(config: ModelConfig):
    if not config.api_key:
        raise ModelConfigurationError("API key is required")
    from openai import OpenAI

    return OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=30)


def available_models(config: ModelConfig) -> list[str]:
    client = make_client(config)
    response = client.models.list()
    return sorted({str(item.id) for item in response.data if getattr(item, "id", None)})


def test_model_chat(config: ModelConfig) -> str:
    if not config.model:
        raise ModelConfigurationError("Model is required")
    client = make_client(config)
    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": "Connectivity check."},
            {"role": "user", "content": "Reply OK only."},
        ],
        max_tokens=8,
    )
    content = response.choices[0].message.content if response.choices else ""
    return str(content or "OK").strip()


def model_error_message(exc: Exception) -> str:
    name = type(exc).__name__
    message = str(exc).strip() or name
    if "authentication" in name.lower() or "401" in message:
        return "API key is invalid or unauthorized"
    if "connection" in name.lower() or "connect" in message.lower():
        return "Cannot connect to the model service; check the base URL and network"
    if "notfound" in name.lower() or "404" in message:
        return "Endpoint or model not found; check the base URL and model name"
    return message[:500]
