from __future__ import annotations

import json
import os
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.nl2sql.model_config import get_model_config, make_client, model_error_message
from app.nl2sql.model_runtime import model_call_slot, record_usage
from app.nl2sql.models import QueryPlan, SchemaHit
from app.nl2sql.planner import time_filter_for_label
from app.nl2sql.semantic import (
    CATEGORIES, CHANNELS, DIMENSIONS, INTENT_TERMS, MEMBER_LEVELS, REFUND_REASONS, REGIONS,
    REQUIRED_TABLES, TIME_WINDOWS, metric_dimensions, metric_tables, METRICS,
)

class PlanFilter(BaseModel):
    field: str
    operator: Literal["eq", "in"] = "eq"
    values: list[str] = Field(min_length=1, max_length=20)

class StructuredPlanDraft(BaseModel):
    outcome: Literal["answered", "clarification", "unsupported"] = "answered"
    intent: str = ""
    metrics: list[str] = Field(default_factory=list, max_length=8)
    dimensions: list[str] = Field(default_factory=list, max_length=8)
    filters: list[PlanFilter] = Field(default_factory=list, max_length=12)
    time_window: str = ""
    chart_type: Literal["table", "bar", "line"] = "table"
    clarification_question: str = ""
    reason_code: str = ""

METRIC_NAMES = [str(item["name"]) for item in METRICS]
DIMENSION_NAMES = sorted(DIMENSIONS)
INTENT_NAMES = sorted(INTENT_TERMS)
TIME_WINDOW_NAMES = sorted(TIME_WINDOWS)

def _planner_prompt(question: str, context: dict[str, Any] | None, reference_date: str | None) -> str:
    allowed = {"intents": INTENT_NAMES, "metrics": METRIC_NAMES, "dimensions": DIMENSION_NAMES, "time_windows": TIME_WINDOW_NAMES, "channels": list(CHANNELS), "regions": list(REGIONS), "categories": list(CATEGORIES), "member_levels": list(MEMBER_LEVELS), "refund_reasons": list(REFUND_REASONS)}
    return ("你是电商分析问题的结构化语义规划器。只输出 JSON，不要解释。\n"
        "必须从 allowed 注册表选择 intent、metrics、dimensions 和枚举值；不得发明字段。\n"
        "如果问题缺少明确指标、范围或存在多个合理解释，outcome=clarification，并给出 clarification_question。\n"
        "如果问题是写操作、敏感字段或域外问题，outcome=unsupported。\n"
        "filters 只使用 field=channel/region/category/member_level/refund_reason，operator=eq/in。\n"
        "只有当问题要求“各/按/分/对比”某维度时才设置 dimensions；单值渠道、地区或品类条件必须放入 filters，不能重复声明为维度。\n"
        "time_window 只能使用 allowed.time_windows 中的英文 key。\n"
        f"参考日期：{reference_date or '未知'}。\n"
        f"allowed={json.dumps(allowed, ensure_ascii=False)}\n"
        f"previous_plan={json.dumps(context or {}, ensure_ascii=False)}\n"
        f"question={question}\n"
        "输出 JSON 字段：outcome,intent,metrics,dimensions,filters,time_window,chart_type,clarification_question,reason_code")

def _extract_json(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start: raise ValueError("planner did not return a JSON object")
    parsed = json.loads(value[start:end + 1])
    if not isinstance(parsed, dict): raise ValueError("planner JSON must be an object")
    return parsed

def _filter_sql(item: PlanFilter, *, item_metric: bool) -> str:
    field = item.field.strip().lower()
    values = [str(value) for value in item.values]
    mapping = {"channel": (set(CHANNELS), "o.channel"), "region": (set(REGIONS), "o.region"), "category": (set(CATEGORIES), "oi.category" if item_metric else "o.category"), "member_level": (set(MEMBER_LEVELS), "c.member_level"), "refund_reason": (set(REFUND_REASONS), "r.reason")}
    if field not in mapping: raise ValueError(f"unsupported filter field: {field}")
    allowed, column = mapping[field]
    invalid = [value for value in values if value not in allowed]
    if invalid: raise ValueError(f"invalid values for {field}: {invalid}")
    if item.operator == "in": return f"{column} IN ({', '.join(repr(value) for value in values)})"
    if len(values) != 1: raise ValueError(f"eq filter requires exactly one value: {field}")
    return f"{column} = {values[0]!r}"

def validate_structured_plan(draft: StructuredPlanDraft, *, question: str, context: dict[str, Any] | None = None, reference_date: str | None = None) -> QueryPlan:
    context = context or {}
    if draft.outcome != "answered": return QueryPlan(intent="", tables=[], metrics=[], dimensions=[], filters=[], time_range="", chart_type="table", notes=[draft.clarification_question or "需要澄清业务问题。"], outcome=draft.outcome, reason_code=draft.reason_code or "missing_metric")
    if draft.intent not in INTENT_TERMS: raise ValueError(f"unknown intent: {draft.intent}")
    metrics = list(dict.fromkeys(draft.metrics))
    dimensions = list(dict.fromkeys(draft.dimensions))
    if not metrics: raise ValueError("answered plan must include at least one metric")
    if any(item not in METRIC_NAMES for item in metrics): raise ValueError(f"unknown metrics: {metrics}")
    if any(item not in DIMENSIONS for item in dimensions): raise ValueError(f"unknown dimensions: {dimensions}")
    # A single-value channel/region/category condition belongs in filters.
    # Treating it as a dimension produces a redundant GROUP BY and a plan that
    # claims a breakdown the user did not request.
    filter_fields = {item.field.strip().lower() for item in draft.filters}
    scalar_filtered_dimensions = {
        item for item in dimensions
        if item in filter_fields and any(
            filter.field.strip().lower() == item and filter.operator == "eq"
            for filter in draft.filters
        )
    }
    if scalar_filtered_dimensions:
        dimensions = [item for item in dimensions if item not in scalar_filtered_dimensions]
    # Every selected metric must support the requested dimensions. Checking
    # only metrics[0] let a compatible first metric mask an incompatible later
    # metric and produced a plan that could not be rendered consistently.
    supported_by_metric = {metric: set(metric_dimensions(metric)) for metric in metrics}
    unsupported = {
        metric: sorted(set(dimensions) - supported)
        for metric, supported in supported_by_metric.items()
        if set(dimensions) - supported
    }
    if unsupported:
        raise ValueError(f"metric does not support dimensions: {unsupported}")
    # A comma-separated expression of a single registered metric can supply its
    # dimension grain. A cross-table query is only allowed when every metric
    # can be rendered from the selected table set.
    item_metric = any(metric == "销量" for metric in metrics)
    filters = [_filter_sql(item, item_metric=item_metric) for item in draft.filters]
    time_range = ""
    if draft.time_window:
        if draft.time_window not in TIME_WINDOWS: raise ValueError(f"unknown time window: {draft.time_window}")
        label = str(TIME_WINDOWS[draft.time_window].get("label") or "")
        time_filter, time_range, _ = time_filter_for_label(label, reference_date)
        if time_filter: filters.insert(0, time_filter)
    intent_tables = list(REQUIRED_TABLES.get(draft.intent, ()))
    # Metric definitions are authoritative for their physical join inputs.
    # The intent only provides a baseline table set; requiring every metric
    # table to already be in that baseline made marketing metrics impossible
    # to validate under sales_overview.
    metric_required = {
        table for metric_name in metrics for table in metric_tables(metric_name)
    }
    tables = list(dict.fromkeys([*intent_tables, *sorted(metric_required)]))
    if not time_range:
        time_range = str(context.get("time_range") or "全部数据")
    return QueryPlan(
        intent=draft.intent,
        tables=tables,
        metrics=metrics,
        dimensions=dimensions,
        filters=filters,
        time_range=time_range,
        chart_type=draft.chart_type,
        notes=["LLM structured planner validated against semantic.yaml."],
        source_question=str(context.get("question") or ""),
        context={
            "reference_date": reference_date or "",
            "planner_mode": "llm",
            "time_range": time_range,
            "metrics": metrics,
            "retrieval_candidates": list(context.get("retrieval_candidates") or []),
            "retrieval_supplements": list(context.get("retrieval_supplements") or []),
        },
    )

def plan_with_llm(question: str, hits: list[SchemaHit], context: dict[str, Any] | None = None, *, reference_date: str | None = None) -> tuple[QueryPlan | None, str]:
    config = get_model_config()
    if not config.enabled or not config.active: return None, "模型未启用"
    try:
        client = make_client(config, timeout=30)
        with model_call_slot("planner"):
            response = client.chat.completions.create(model=config.model, messages=[{"role": "system", "content": "只输出合法 JSON。"}, {"role": "user", "content": _planner_prompt(question, context, reference_date)}], temperature=0, max_tokens=600, response_format={"type": "json_object"})
            record_usage(getattr(response, "usage", None))
        text = response.choices[0].message.content if response.choices else ""
        draft = StructuredPlanDraft.model_validate(_extract_json(text))
        return validate_structured_plan(draft, question=question, context=context, reference_date=reference_date), ""
    except (ValidationError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc: return None, f"结构化规划校验失败：{exc}"
    except Exception as exc: return None, model_error_message(exc)
