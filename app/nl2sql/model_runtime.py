from __future__ import annotations

import threading
import time
from contextlib import contextmanager


class ModelCallError(RuntimeError):
    pass


class ModelCallTimeout(ModelCallError):
    pass


class ModelCallRejected(ModelCallError):
    pass


_MAX_CONCURRENCY = 4
_ADMISSION_TIMEOUT_SECONDS = 5.0
_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENCY)
_STATE_LOCK = threading.Lock()
_STATS = {
    "started": 0,
    "succeeded": 0,
    "failed": 0,
    "rejected": 0,
    "timed_out": 0,
    "queue_wait_ms_total": 0.0,
    "call_ms_total": 0.0,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
}


def _usage_value(usage, *names):
    if usage is None:
        return 0
    total = 0
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += int(value)
    return total


def record_usage(usage) -> None:
    input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
    total_tokens = _usage_value(usage, "total_tokens") or input_tokens + output_tokens
    with _STATE_LOCK:
        _STATS["input_tokens"] += input_tokens
        _STATS["output_tokens"] += output_tokens
        _STATS["total_tokens"] += total_tokens


@contextmanager
def model_call_slot(purpose: str):
    del purpose  # Reserved for future per-purpose metric labels.
    queued = time.perf_counter()
    acquired = _SEMAPHORE.acquire(timeout=_ADMISSION_TIMEOUT_SECONDS)
    wait_ms = (time.perf_counter() - queued) * 1000
    if not acquired:
        with _STATE_LOCK:
            _STATS["rejected"] += 1
        raise ModelCallRejected("模型调用并发已满，请稍后重试")
    started = time.perf_counter()
    with _STATE_LOCK:
        _STATS["started"] += 1
        _STATS["queue_wait_ms_total"] += wait_ms
    try:
        yield
    except TimeoutError as exc:
        with _STATE_LOCK:
            _STATS["timed_out"] += 1
            _STATS["failed"] += 1
        raise ModelCallTimeout(str(exc)) from exc
    except ModelCallRejected:
        with _STATE_LOCK:
            _STATS["rejected"] += 1
            _STATS["failed"] += 1
        raise
    except Exception:
        with _STATE_LOCK:
            _STATS["failed"] += 1
        raise
    else:
        with _STATE_LOCK:
            _STATS["succeeded"] += 1
    finally:
        with _STATE_LOCK:
            _STATS["call_ms_total"] += (time.perf_counter() - started) * 1000
        _SEMAPHORE.release()


def snapshot() -> dict:
    with _STATE_LOCK:
        values = dict(_STATS)
    started = values["started"]
    return {
        "max_concurrency": _MAX_CONCURRENCY,
        "admission_timeout_seconds": _ADMISSION_TIMEOUT_SECONDS,
        **values,
        "success_rate": round(values["succeeded"] * 100 / max(1, started), 2),
        "avg_queue_wait_ms": round(values["queue_wait_ms_total"] / max(1, started), 1),
        "avg_call_ms": round(values["call_ms_total"] / max(1, started), 1),
    }


def reset() -> None:
    with _STATE_LOCK:
        for key, value in _STATS.items():
            _STATS[key] = 0 if isinstance(value, int) else 0.0
