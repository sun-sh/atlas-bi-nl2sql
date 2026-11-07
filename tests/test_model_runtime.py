from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.nl2sql import model_runtime


@pytest.fixture(autouse=True)
def clean_runtime():
    model_runtime.reset()
    yield
    model_runtime.reset()


def test_usage_records_openai_and_responses_field_names():
    model_runtime.record_usage(SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14))
    model_runtime.record_usage(SimpleNamespace(input_tokens=3, output_tokens=2))
    values = model_runtime.snapshot()
    assert values["input_tokens"] == 13
    assert values["output_tokens"] == 6
    assert values["total_tokens"] == 19


def test_successful_slot_records_latency_and_success_rate():
    with model_runtime.model_call_slot("test"):
        pass
    values = model_runtime.snapshot()
    assert values["started"] == 1
    assert values["succeeded"] == 1
    assert values["failed"] == 0
    assert values["success_rate"] == 100.0


def test_failed_slot_records_failure():
    with pytest.raises(RuntimeError):
        with model_runtime.model_call_slot("test"):
            raise RuntimeError("provider failed")
    values = model_runtime.snapshot()
    assert values["started"] == 1
    assert values["succeeded"] == 0
    assert values["failed"] == 1


def test_concurrency_slot_releases_semaphore_after_failure():
    with pytest.raises(RuntimeError):
        with model_runtime.model_call_slot("test"):
            raise RuntimeError("failed")
    with model_runtime.model_call_slot("test"):
        pass
    assert model_runtime.snapshot()["succeeded"] == 1
