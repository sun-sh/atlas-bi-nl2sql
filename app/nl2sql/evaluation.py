from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.nl2sql.model_config import get_model_config
from app.nl2sql.result_insight import digest_from_result
from app.nl2sql.workflow import run_query


DATASET_RANGE = "2025-10-01 至 2026-02-28"
MODEL_MODE = "model"
# Evaluation runs the strict model path and never invokes rule SQL fallback.
MODEL_SQL_MODES = {"llm", "llm-repair"}
CASES = (
    {
        "question": "上个月华东地区销售额是多少？",
        "intent": "sales_overview",
        "check": lambda rows: len(rows) == 1
        and any(
            key in rows[0]
            for key in ("销售额", "sales_amount", "revenue", "total_sales")
        )
        and abs(
            float(
                rows[0].get("销售额")
                or rows[0].get("sales_amount")
                or rows[0].get("revenue")
                or rows[0].get("total_sales")
                or 0
            )
            - 79947.85
        )
        < 1,
    },
    {
        "question": "近90天各品类销售额排名",
        "intent": "category_analysis",
        "check": lambda rows: len(rows) == 4
        and str(rows[0].get("品类") or rows[0].get("category") or "") == "运动鞋服",
    },
    {
        "question": "退款率最高的品类是什么？",
        "intent": "refund_analysis",
        "check": lambda rows: len(rows) >= 1
        and str(rows[0].get("品类") or rows[0].get("category") or "") == "美妆个护",
    },
    {
        "question": "上个月各渠道ROI",
        "intent": "marketing_efficiency",
        "check": lambda rows: len(rows) == 4
        and all(
            any(str(key).lower() == "roi" for key in row)
            for row in rows
        ),
    },
    {
        "question": "按日看本月销售额趋势",
        "intent": "sales_overview",
        "check": lambda rows: len(rows) == 28
        and all(
            any(
                str(key) in {"日期", "order_date", "date", "时间"}
                for key in row
            )
            for row in rows
        ),
    },
    {
        "question": "各渠道客单价",
        "intent": "sales_overview",
        "check": lambda rows: len(rows) == 4
        and all(
            any("客单价" in str(key) or "avg_order_value" in str(key).lower() for key in row)
            for row in rows
        ),
    },
)


def _model_state() -> dict[str, Any]:
    config = get_model_config()
    return {
        "configured": bool(config.enabled),
        "active": bool(config.enabled and config.active),
        "model": config.model if config.enabled else "",
        "provider": config.provider if config.enabled else "",
    }


def _model_grounded(result: Any) -> bool:
    if not result or not result.rows:
        return False
    digest = digest_from_result(result)
    return bool(digest.get("facts")) and digest.get("row_count", 0) > 0


def run_evaluation() -> dict[str, Any]:
    """Run every golden case through the strict model path.

    The evaluation never invokes the runtime rule fallback. If the model does
    not return SQL, the case is recorded as a model failure instead of being
    masked by a deterministic query.
    """
    model = _model_state()
    cases: list[dict[str, Any]] = []
    executed_count = 0
    hit_count = 0
    total_elapsed = 0.0

    if not model["active"]:
        total = len(CASES)
        return {
            "mode": MODEL_MODE,
            "model": model,
            "available": False,
            "dataset_range": DATASET_RANGE,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "passed": 0,
            "execution_rate": 0.0,
            "hit_rate": 0.0,
            "model_rate": 0.0,
            "avg_elapsed_ms": 0.0,
            "intents": len({case["intent"] for case in CASES}),
            "cases": [
                {
                    "question": case["question"],
                    "intent": case["intent"],
                    "executed": False,
                    "hit": False,
                    "model_used": False,
                    "narrative_mode": "unavailable",
                    "elapsed_ms": 0.0,
                    "attempts": 0,
                    "sql_mode": "unavailable",
                    "warnings": ["模型未配置或未启用"],
                    "error": "模型未配置或未启用，评测未运行",
                }
                for case in CASES
            ],
        }

    for case in CASES:
        started = time.perf_counter()
        error = ""
        try:
            result = run_query(case["question"], allow_rule_fallback=False)
            executed = bool(
                result.attempts
                and result.attempts[-1]["status"] == "success"
            )
            sql_model_used = result.mode in MODEL_SQL_MODES
            narrative_model_used = getattr(result, "narrative_mode", "") == "model"
            model_used = sql_model_used and narrative_model_used
            hit = bool(
                executed
                and model_used
                and case["check"](result.rows)
                and _model_grounded(result)
            )
            warnings = list(result.warnings)
            if result.mode == "model-failed":
                error = "模型未生成 SQL；严格模型评测未使用其他生成器补位"
            elif result.mode == "model-repair-failed":
                error = "模型修复未生成 SQL；严格模型评测未使用其他生成器补位"
            elif not executed:
                error = "模型 SQL 未成功执行"
            elif not sql_model_used:
                error = "该用例未由模型完成 SQL 生成"
            elif not narrative_model_used:
                error = "模型解读回退为确定性摘要，严格模型评测不通过"
            elif not hit:
                error = "模型结果未命中验收值"
        except Exception as exc:
            result = None
            executed = False
            model_used = False
            hit = False
            warnings = []
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
                "model_used": model_used,
                "narrative_mode": getattr(result, "narrative_mode", "unavailable") if result else "error",
                "elapsed_ms": round(elapsed, 1),
                "attempts": len(result.attempts) if result else 0,
                "sql_mode": result.mode if result else "error",
                "warnings": warnings,
                "error": error or None,
            }
        )

    total = len(cases)
    model_count = sum(1 for case in cases if case["model_used"])
    return {
        "mode": MODEL_MODE,
        "model": model,
        "available": True,
        "dataset_range": DATASET_RANGE,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "passed": hit_count,
        "execution_rate": round(executed_count * 100 / total, 1),
        "hit_rate": round(hit_count * 100 / total, 1),
        "model_rate": round(model_count * 100 / total, 1),
        "avg_elapsed_ms": round(total_elapsed / total, 1),
        "intents": len({case["intent"] for case in CASES}),
        "cases": cases,
    }
