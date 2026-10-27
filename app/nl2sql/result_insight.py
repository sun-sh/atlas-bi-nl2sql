from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Iterator

from app.nl2sql.model_config import get_model_config, make_client, model_error_message
from app.nl2sql.models import QueryPlan


@dataclass(frozen=True)
class NarrativeResult:
    text: str
    mode: str
    model: str = ""
    fallback_reason: str = ""
    elapsed_ms: float = 0.0
    retries: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "mode": self.mode,
            "model": self.model,
            "retries": self.retries,
            "fallback_reason": self.fallback_reason,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        if abs(value) < 0.1:
            return f"{value:,.4f}".rstrip("0").rstrip(".")
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _pct(part: float, total: float) -> float | None:
    return round(part * 100 / total, 2) if total else None


METRIC_ALIASES = {
    "销售额": {"sales", "sales_amount", "revenue", "gmv", "total_sales"},
    "客单价": {"avg_order_value", "average_order_value", "aov"},
    "roi": {"roi", "投产比"},
    "退款率": {"refund_rate", "return_rate"},
    "销量": {"销量", "件数", "quantity", "units_sold", "sales_volume"},
    "退款金额": {"退款金额", "refund_amount"},
    "投放花费": {"投放花费", "花费", "spend", "ad_spend"},
    "归因销售额": {"归因销售额", "attributed_sales", "attributed_revenue"},
    "归因订单数": {"归因订单数", "attributed_orders"},
    "订单数": {"订单数", "order_count", "orders"},
}


def _metric_key(value: str) -> str:
    raw = str(value).strip().lower()
    compact = re.sub(r"[\s_\-]+", "", raw)
    if raw in METRIC_ALIASES:
        return raw
    for canonical, aliases in METRIC_ALIASES.items():
        normalized_aliases = {
            re.sub(r"[\s_\-]+", "", alias.strip().lower()) for alias in aliases
        }
        if compact in normalized_aliases:
            return canonical.lower()
    return compact


def _is_rate_metric(name: str, values: list[float]) -> bool:
    key = _metric_key(name)
    return (
        "率" in str(name)
        or key == "退款率"
        or key.endswith("rate")
        or key == "roi"
    )


def build_result_digest(
    question: str,
    plan: QueryPlan,
    rows: list[dict[str, Any]],
    columns: list[str],
    row_count: int,
    truncated: bool,
) -> dict[str, Any]:
    numeric_columns = [
        col for col in columns if any(_is_number(row.get(col)) for row in rows)
    ]
    dimension = next(
        (
            col
            for col in columns
            if col not in numeric_columns
            and any(row.get(col) is not None for row in rows)
        ),
        "",
    )
    metrics: list[dict[str, Any]] = []
    for col in numeric_columns:
        values = [float(row[col]) for row in rows if _is_number(row.get(col))]
        if values:
            col_name = str(col)
            is_rate = _is_rate_metric(col_name, values)
            aggregated_sum = None if is_rate else round(sum(values), 4)
            metrics.append(
                {
                    "name": col_name,
                    "sum": aggregated_sum,
                    "avg": round(sum(values) / len(values), 4),
                    "min": round(min(values), 4),
                    "max": round(max(values), 4),
                    "aggregation": "avg" if is_rate else "sum",
                }
            )

    preferred = next(
        (
            metric
            for plan_metric in plan.metrics
            for metric in metrics
            if _metric_key(metric["name"]) == _metric_key(plan_metric)
        ),
        metrics[0] if metrics else None,
    )
    if preferred:
        preferred["label"] = next(
            (
                str(plan_metric)
                for plan_metric in plan.metrics
                if _metric_key(preferred["name"]) == _metric_key(plan_metric)
            ),
            preferred["name"],
        )
    ranking: list[dict[str, Any]] = []
    if preferred and dimension:
        total = float(preferred["sum"] or preferred["avg"] or 0)
        ordered = sorted(
            (
                row
                for row in rows
                if _is_number(row.get(preferred["name"]))
                and row.get(dimension) is not None
            ),
            key=lambda row: float(row[preferred["name"]]),
            reverse=True,
        )
        for row in ordered[:5]:
            value = float(row[preferred["name"]])
            ranking.append(
                {
                    "label": str(row[dimension]),
                    "value": _fmt(value),
                    "share_pct": (
                        _pct(value, total)
                        if preferred.get("aggregation") == "sum"
                        and not _is_rate_metric(
                            str(preferred.get("label") or preferred["name"]),
                            [],
                        )
                        else None
                    ),
                }
            )

    trend = None
    date_column = next(
        (
            col
            for col in columns
            if any(token in col.lower() for token in ("日期", "date", "时间"))
        ),
        "",
    )
    if preferred and date_column and len(rows) > 1:
        ordered_rows = [
            row
            for row in rows
            if row.get(date_column) is not None
            and _is_number(row.get(preferred["name"]))
        ]
        if len(ordered_rows) > 1:
            first_value = float(ordered_rows[0][preferred["name"]])
            last_value = float(ordered_rows[-1][preferred["name"]])
            trend = {
                "date_column": date_column,
                "metric": preferred["name"],
                "start": str(ordered_rows[0][date_column]),
                "end": str(ordered_rows[-1][date_column]),
                "start_value": _fmt(first_value),
                "end_value": _fmt(last_value),
                "start_raw": round(first_value, 6),
                "end_raw": round(last_value, 6),
                "change_pct": (
                    round((last_value - first_value) * 100 / first_value, 2)
                    if first_value
                    else None
                ),
            }

    facts = [f"返回 {row_count} 行数据"]
    if truncated:
        facts.append("结果已按行数上限截断")
    if preferred:
        metric_label = str(preferred.get("label") or preferred["name"])
        if row_count == 1:
            value = preferred["max"]
            facts.append(f"{metric_label}为 {_fmt(value)}")
        elif preferred.get("aggregation") == "avg":
            facts.append(f"{metric_label}均值 {_fmt(preferred['avg'])}")
        elif preferred.get("sum") is not None:
            facts.append(f"{metric_label}合计 {_fmt(preferred['sum'])}")
    if ranking:
        top = ranking[0]
        share = (
            f"，占比 {top['share_pct']:.2f}%"
            if top["share_pct"] is not None
            else ""
        )
        facts.append(
            f"{dimension}中 {top['label']} 最高，"
            f"{preferred.get('label') or preferred['name']}为 {top['value']}{share}"
        )
    if trend and trend.get("change_pct") is not None:
        direction = "上升" if trend["change_pct"] >= 0 else "下降"
        facts.append(
            f"{preferred.get('label') or trend['metric']}从 {trend['start']} 到 {trend['end']} "
            f"{direction} {abs(trend['change_pct']):.2f}%"
        )

    return {
        "question": question,
        "intent": plan.intent,
        "time_range": plan.time_range or "未限定",
        "filters": list(plan.filters),
        "columns": [str(col) for col in columns],
        "row_count": int(row_count),
        "truncated": bool(truncated),
        "dimension": dimension,
        "metrics": metrics,
        "metric_unit": "rate" if preferred and preferred.get("aggregation") == "avg" else "value",
        "preferred_metric": (
            str(preferred.get("label") or preferred["name"]) if preferred else ""
        ),
        "ranking": ranking,
        "ranking_comparable": bool(
            preferred
            and preferred.get("aggregation") == "sum"
            and not _is_rate_metric(
                str(preferred.get("label") or preferred["name"]),
                [],
            )
        ),
        "trend": trend,
        "facts": facts,
        "sample_rows": [
            {col: row.get(col) for col in columns}
            for row in rows[:12]
        ],
    }


def digest_from_result(result: Any, truncated: bool = False) -> dict[str, Any]:
    return build_result_digest(
        result.question,
        result.plan,
        result.rows,
        result.columns,
        len(result.rows),
        truncated,
    )


def rule_narrative(digest: dict[str, Any]) -> str:
    row_count = int(digest.get("row_count") or 0)
    if row_count == 0:
        return (
            "结论：当前筛选条件下没有匹配数据。\n"
            "关键发现：\n"
            "- 查询已成功执行，但没有返回可用记录。\n"
            f"口径：{digest.get('time_range') or '未限定时间范围'}。\n"
            "建议追问：\n"
            "- 是否需要扩大时间范围或移除部分筛选条件？"
        )

    preferred = digest.get("preferred_metric") or "查询结果"
    lines = [
        f"结论：本次查询返回 {row_count} 行，核心指标为{preferred}。",
        "关键发现：",
    ]
    for fact in list(digest.get("facts") or [])[1:5]:
        lines.append(f"- {fact}")
    if len(lines) == 2:
        lines.append("- 查询已成功执行，详细数值请查看下方结果表。")
    lines.append(
        f"口径：{digest.get('time_range') or '未限定时间范围'}；"
        "仅依据本次 SQL 返回结果。"
    )
    lines.append("建议追问：")
    ranking = list(digest.get("ranking") or [])
    if ranking and digest.get("dimension"):
        lines.append(
            f"- 为什么 {ranking[0].get('label')} 表现最高？"
            "继续按时间或细分维度拆解。"
        )
    lines.append("- 需要我把结果按贡献占比或环比变化继续拆解吗？")
    return "\n".join(lines)


def _prompt(digest: dict[str, Any]) -> str:
    compact = json.dumps(digest, ensure_ascii=False, separators=(",", ":"))
    return (
        "你是企业电商经营分析助手。请只依据下面的确定性查询摘要写中文解读，"
        "不得补充摘要之外的数据、原因或行业知识；数据不足时要明确说明。"
        "指标名以 preferred_metric 为准，并严格沿用摘要中的单位和精度。"
        "只有 ranking_comparable=true 时才能描述占比；比率和 ROI 不得相加或计算份额。"
        "输出 80-160 字，固定四段：结论、关键发现（2 条短句）、口径、建议追问（1 条）。"
        "不要输出 SQL、JSON、Markdown 表格或代码块。\n"
        f"查询摘要：{compact}"
    )


def _stream_chunks(text: str, size: int = 24) -> Iterator[str]:
    for index in range(0, len(text), size):
        yield text[index : index + size]


def stream_result_narrative(
    question: str,
    plan: QueryPlan,
    digest: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    started = time.perf_counter()
    fallback = rule_narrative(digest)
    config = get_model_config()
    use_model = (
        config.enabled
        and config.active
        and int(digest.get("row_count") or 0) > 0
    )
    if not use_model:
        yield {
            "type": "delta",
            "delta": fallback,
        }
        yield {
            "type": "done",
            "narrative": NarrativeResult(
                text=fallback,
                mode="digest",
                elapsed_ms=(time.perf_counter() - started) * 1000,
            ),
        }
        return

    error = ""
    emitted = ""
    retries = 0
    for attempt, budget in enumerate((1800, 3600)):
        if attempt:
            retries += 1
            if emitted:
                yield {"type": "reset", "text": ""}
        parts: list[str] = []
        emitted = ""
        error = ""
        try:
            client = make_client(config)
            request: dict[str, Any] = {
                "model": config.model,
                "messages": [
                    {"role": "system", "content": "只依据给定摘要回答，禁止编造数据和因果。"},
                    {"role": "user", "content": _prompt(digest)},
                ],
                "temperature": 0.2,
                "max_tokens": budget,
                "stream": True,
            }
            provider = str(getattr(config, "provider", "")).lower()
            if provider == "qwen" or "qwen" in str(config.model).lower():
                request["extra_body"] = {
                    "chat_template_kwargs": {"enable_thinking": False},
                }
            response = client.chat.completions.create(**request)
            for chunk in response:
                if not getattr(chunk, "choices", None):
                    continue
                choice = chunk.choices[0]
                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason == "length":
                    error = "模型解读因token预算耗尽而截断"
                elif finish_reason not in (None, "stop"):
                    error = f"模型解读未正常完成：{finish_reason}"
                delta = getattr(choice.delta, "content", None)
                if delta:
                    parts.append(delta)
                    emitted += delta
                    yield {"type": "delta", "delta": delta}
                elif getattr(choice.delta, "reasoning", None):
                    yield {"type": "thinking"}
        except Exception as exc:
            error = model_error_message(exc)

        text = re.sub(r"```(?:text)?", "", "".join(parts)).replace("```", "").strip()
        if not error and text:
            yield {
                "type": "done",
                "narrative": NarrativeResult(
                    text=text, mode="model", model=config.model, retries=retries,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                ),
            }
            return
        error = error or "模型返回空内容"

    if emitted:
        yield {"type": "reset", "text": fallback}
    else:
        yield {"type": "delta", "delta": fallback}
    yield {
        "type": "done",
        "narrative": NarrativeResult(
            text=fallback, mode="digest", fallback_reason=error, retries=retries,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        ),
    }


def narrate_result(
    question: str,
    plan: QueryPlan,
    digest: dict[str, Any],
) -> NarrativeResult:
    final: NarrativeResult | None = None
    for event in stream_result_narrative(question, plan, digest):
        if event.get("type") == "done":
            final = event["narrative"]
    return final or NarrativeResult(text=rule_narrative(digest), mode="digest")


def narrative_warnings(narrative: NarrativeResult) -> list[str]:
    if narrative.mode == "digest" and narrative.fallback_reason:
        return [f"模型结果解读失败，已回退确定性摘要：{narrative.fallback_reason}"]
    return []
