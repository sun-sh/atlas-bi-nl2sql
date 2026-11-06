from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx


QUESTIONS = (
    "上个月华东地区销售额是多少？",
    "各渠道 ROI 是多少？",
    "退款率最高的品类是什么？",
    "各品类商品销量是多少？",
    "按日看本月销售额趋势",
)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return round(ordered[index], 1)


async def _one(client: httpx.AsyncClient, base_url: str, index: int) -> dict[str, Any]:
    started = time.perf_counter()
    error = ""
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/api/query",
            json={"question": QUESTIONS[index % len(QUESTIONS)]},
        )
        if response.status_code != 200:
            error = f"HTTP {response.status_code}: {response.text[:180]}"
    except Exception as exc:
        error = str(exc)[:300]
    elapsed = (time.perf_counter() - started) * 1000
    return {"ok": not error, "elapsed_ms": round(elapsed, 1), "error": error}


async def run(base_url: str, *, requests: int, concurrency: int) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(index: int) -> dict[str, Any]:
        async with semaphore:
            return await _one(client, base_url, index)

    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as client:
        results = await asyncio.gather(*(bounded(index) for index in range(requests)))
    wall = time.perf_counter() - started
    latencies = [item["elapsed_ms"] for item in results]
    successes = sum(item["ok"] for item in results)
    errors = [item["error"] for item in results if item["error"]]
    return {
        "suite_version": "http-loadtest-v1",
        "requests": requests,
        "concurrency": concurrency,
        "success": successes,
        "failed": requests - successes,
        "success_rate": round(successes * 100 / requests, 1),
        "wall_seconds": round(wall, 2),
        "throughput_rps": round(requests / wall, 2),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 1) if latencies else 0.0,
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "max": round(max(latencies), 1) if latencies else 0.0,
        },
        "errors": errors[:10],
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model_enabled": False,
            "workload": "local deterministic rule SQL over seeded SQLite",
        },
        "note": "Local rule-SQL/HTTP workload with the model disabled; this is not LLM or production capacity evidence.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a bounded HTTP NL2SQL load test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.requests <= 500:
        parser.error("--requests must be between 1 and 500")
    if not 1 <= args.concurrency <= 50:
        parser.error("--concurrency must be between 1 and 50")
    report = asyncio.run(run(args.base_url, requests=args.requests, concurrency=args.concurrency))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
