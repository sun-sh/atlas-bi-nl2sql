from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from app.nl2sql.workflow import run_query


DATASET_RANGE = "2025-10-01 至 2026-02-28"
MODE = "rule"
CASES = (
    {"question": "上个月华东地区销售额是多少？", "intent": "sales_overview", "check": lambda rows: len(rows) == 1 and abs(float(rows[0].get("销售额", 0)) - 79947.85) < 1},
    {"question": "近90天各品类销售额排名", "intent": "category_analysis", "check": lambda rows: len(rows) == 4 and rows[0].get("品类") == "运动鞋服"},
    {"question": "退款率最高的品类是什么？", "intent": "refund_analysis", "check": lambda rows: len(rows) == 4 and rows[0].get("品类") == "美妆个护"},
    {"question": "上个月各渠道ROI", "intent": "marketing_efficiency", "check": lambda rows: len(rows) == 4 and all("ROI" in row for row in rows)},
    {"question": "按日看本月销售额趋势", "intent": "sales_overview", "check": lambda rows: len(rows) == 28 and all("日期" in row for row in rows)},
    {"question": "各渠道客单价", "intent": "sales_overview", "check": lambda rows: len(rows) == 4 and all("客单价" in row for row in rows)},
)


def run_evaluation() -> dict[str, Any]:
    previous_model_flag = os.environ.get("ATLAS_DISABLE_MODEL")
    os.environ["ATLAS_DISABLE_MODEL"] = "1"
    try:
        return _run_evaluation()
    finally:
        if previous_model_flag is None:
            os.environ.pop("ATLAS_DISABLE_MODEL", None)
        else:
            os.environ["ATLAS_DISABLE_MODEL"] = previous_model_flag


def _run_evaluation() -> dict[str, Any]:
    cases = []
    executed_count = 0
    hit_count = 0
    total_elapsed = 0.0
    for case in CASES:
        started = time.perf_counter()
        try:
            result = run_query(case["question"])
            hit = bool(case["check"](result.rows))
            executed = bool(result.attempts and result.attempts[-1]["status"] == "success")
        except Exception as exc:
            result = None
            hit = False
            executed = False
            error = str(exc)
        elapsed = (time.perf_counter() - started) * 1000
        total_elapsed += elapsed
        executed_count += int(executed)
        hit_count += int(hit)
        cases.append(
            {
                "question": case["question"],
                "intent": case["intent"],
                "executed": executed,
                "hit": hit,
                "elapsed_ms": round(elapsed, 1),
                "attempts": len(result.attempts) if result else 0,
                "error": None if result and executed else (error if "error" in locals() else "结论未命中"),
            }
        )
    total = len(cases)
    return {
        "mode": MODE,
        "dataset_range": DATASET_RANGE,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "passed": hit_count,
        "execution_rate": round(executed_count * 100 / total, 1),
        "hit_rate": round(hit_count * 100 / total, 1),
        "avg_elapsed_ms": round(total_elapsed / total, 1),
        "intents": len({c["intent"] for c in CASES}),
        "cases": cases,
    }
