from __future__ import annotations

import statistics
import threading
import time
from collections import defaultdict
from typing import Any


_LOCK = threading.Lock()
_LATENCIES: dict[str, list[float]] = defaultdict(list)
_COUNTS: dict[str, int] = defaultdict(int)
_ERRORS: dict[str, int] = defaultdict(int)
_STARTED_AT = time.time()


def observe(route: str, elapsed_ms: float, *, error: bool = False) -> None:
    with _LOCK:
        _LATENCIES[route].append(float(elapsed_ms))
        _LATENCIES[route] = _LATENCIES[route][-1000:]
        _COUNTS[route] += 1
        if error:
            _ERRORS[route] += 1


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return round(ordered[index], 1)


def snapshot() -> dict[str, Any]:
    with _LOCK:
        routes = {}
        for route, values in sorted(_LATENCIES.items()):
            routes[route] = {
                "count": _COUNTS[route],
                "errors": _ERRORS[route],
                "error_rate": round(_ERRORS[route] * 100 / max(1, _COUNTS[route]), 2),
                "p50_ms": _percentile(values, 50),
                "p95_ms": _percentile(values, 95),
                "max_ms": round(max(values), 1) if values else 0.0,
            }
    return {
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "routes": routes,
        "sample_limit": 1000,
    }


def reset() -> None:
    with _LOCK:
        _LATENCIES.clear()
        _COUNTS.clear()
        _ERRORS.clear()
