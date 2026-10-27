from __future__ import annotations


def test_evaluation_model_snapshot_is_restored_after_scope():
    from app.nl2sql.model_config import ModelConfig, get_model_config, model_config_snapshot
    original = get_model_config()
    snapshot = ModelConfig(True, "custom", "https://example.invalid/v1", "frozen-test", "fake")
    with model_config_snapshot(snapshot):
        assert get_model_config() is snapshot
    assert get_model_config() == original

from pathlib import Path

import pytest

from app.nl2sql.model_config import (
    ModelConfigurationError,
    get_model_config,
    save_model_config,
)


@pytest.fixture(autouse=True)
def isolate_model_config(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ATLAS_MODEL_CONFIG_PATH", str(tmp_path / "model_config.json"))
    monkeypatch.setenv("ATLAS_DISABLE_MODEL", "0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    yield


def test_model_config_persists_without_exposing_key():
    saved = save_model_config(
        enabled=True,
        provider="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4.1-mini",
        api_key="secret-value",
    )
    assert saved.active
    public = get_model_config().public()
    assert public["active"] is True
    assert public["model"] == "gpt-4.1-mini"
    assert public["api_key_configured"] is True
    assert "secret-value" not in str(public)


def test_model_config_rejects_enable_without_key():
    with pytest.raises(ModelConfigurationError):
        save_model_config(
            enabled=True,
            provider="openai",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            api_key=None,
        )


def test_model_config_can_be_disabled():
    save_model_config(
        enabled=True,
        provider="custom",
        base_url="https://example.com/v1",
        model="demo",
        api_key="secret",
    )
    saved = save_model_config(
        enabled=False,
        provider="custom",
        base_url="https://example.com/v1",
        model="demo",
    )
    assert saved.enabled is False
    assert saved.api_key == "secret"
