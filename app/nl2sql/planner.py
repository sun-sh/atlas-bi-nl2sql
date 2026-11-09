from __future__ import annotations

import os
import re
from datetime import date, timedelta

from app.nl2sql.models import QueryPlan, SchemaHit


DATASET_REFERENCE_DATE = date(2026, 2, 28)
REGION_TERMS = {"华东": "华东", "华南": "华南", "华北": "华北", "西南": "西南"}
CHANNEL_TERMS = {"app": "App", "小程序": "MiniProgram", "miniprogram": "MiniProgram", "网页": "Web", "web": "Web", "抖音": "Douyin", "douyin": "Douyin"}
CATEGORY_TERMS = ("运动鞋服", "食品饮料", "数码家电", "美妆个护")
MEMBER_TERMS = ("普通", "银卡", "金卡")
FOLLOW_UP_TERMS = ("那", "换成", "改成", "再看", "继续", "呢", "这个", "这些", "同样", "那个", "它")
INTENT_TERMS = {
    "refund_analysis": ("退款率", "售后", "退货", "退款", "退的钱"),
    "marketing_efficiency": ("roi", "投产比", "投放", "广告", "获客成本", "花费", "值不值"),
    "category_analysis": ("品类", "类目", "商品", "销量", "卖得最多", "卖得多", "件数"),
    "sales_overview": ("销售额", "销售", "趋势", "每天", "按日", "订单", "渠道", "地区", "最赚钱", "收入", "每单平均", "客单价"),
}


def _reference_date(value: date | str | None = None) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        return date.fromisoformat(value.strip()[:10])
    configured = os.getenv("ATLAS_REFERENCE_DATE", "").strip()
    if configured:
        return date.fromisoformat(configured[:10])
    # Direct planner calls are used by offline evaluation and must stay
    # reproducible. The runtime workflow always supplies MAX(order_date) from
    # the active database, so this fixed demo anchor is not a hidden wall-clock
    # default in the application path.
    return DATASET_REFERENCE_DATE


def _last_month_sql(reference_date: date | str | None = None) -> tuple[str, str]:
    today = _reference_date(reference_date)
    first = date(today.year, today.month, 1)
    last = first - timedelta(days=1)
    previous = last.replace(day=1)
    return previous.isoformat(), last.isoformat()


def _time_filter(
    question: str,
    reference_date: date | str | None = None,
) -> tuple[str, str, str]:
    q = question.lower()
    if not any(
        term in q
        for term in (
            "上个月", "上月", "本月", "这个月",
            "近30天", "最近30天", "过去30天", "最近一个月", "过去一个月",
            "近90天", "最近90天", "过去90天", "近三个月", "最近三个月", "过去三个月",
        )
    ):
        return "", "", ""
    today = _reference_date(reference_date)
    month_start = date(today.year, today.month, 1)
    if "上个月" in q or "上月" in q:
        start, end = _last_month_sql(today)
        return f"o.order_date BETWEEN '{start}' AND '{end}'", f"{start} 至 {end}", "上个月"
    if "本月" in q or "这个月" in q:
        start = month_start.isoformat()
        end = today.isoformat()
        return f"o.order_date >= '{start}' AND o.order_date <= '{end}'", f"{start} 至 {end}", "本月"
    if "近30天" in q or "最近30天" in q:
        start = (today - timedelta(days=29)).isoformat()
        end = today.isoformat()
        return f"o.order_date BETWEEN '{start}' AND '{end}'", f"{start} 至 {end}", "近30天"
    if any(
        term in q
        for term in ("近90天", "最近90天", "过去90天", "近三个月", "最近三个月", "过去三个月")
    ):
        start = (today - timedelta(days=89)).isoformat()
        end = today.isoformat()
        return f"o.order_date BETWEEN '{start}' AND '{end}'", f"{start} 至 {end}", "近90天"
    return "", "", ""


def _pick(question: str, mapping: dict[str, str], column: str, prefix: str) -> str:
    q = question.lower()
    for term, value in mapping.items():
        if term in q:
            return f"{prefix}.{column} = '{value}'"
    return ""


def _detect_intent(q: str) -> str | None:
    # Explicit business signals take precedence over generic retail verbs
    # such as “卖得最多”, which can otherwise be misread as category quantity.
    if any(term in q for term in INTENT_TERMS["refund_analysis"]):
        return "refund_analysis"
    if any(term in q for term in INTENT_TERMS["marketing_efficiency"]):
        return "marketing_efficiency"
    if any(term in q for term in ("件数", "多少件", "商品行", "商品明细")):
        return "category_analysis"
    if any(term in q for term in ("品类", "类目")) and "渠道" not in q and "地区" not in q:
        return "category_analysis"
    if any(term in q for term in INTENT_TERMS["sales_overview"]):
        return "sales_overview"
    if any(term in q for term in INTENT_TERMS["category_analysis"]):
        return "category_analysis"
    return None


def _context_filter(context: dict, key: str) -> str:
    value = context.get(key)
    return str(value) if value else ""


def _follow_up(question: str, context: dict | None) -> bool:
    if not context:
        return False
    q = question.lower()
    return any(term in q for term in FOLLOW_UP_TERMS) or not _detect_intent(q)


def make_plan(
    question: str,
    hits: list[SchemaHit],
    context: dict | None = None,
    *,
    reference_date: date | str | None = None,
) -> QueryPlan:
    q = question.lower()
    tables = [h.table for h in hits]
    filters: list[str] = []
    inherited: list[str] = []
    context = context or {}
    is_follow_up = _follow_up(question, context)

    detected_intent = _detect_intent(q)
    retrieved_tables = [h.table for h in hits]
    if detected_intent == "refund_analysis":
        required_tables = ["orders", "refunds"]
    elif detected_intent == "marketing_efficiency":
        required_tables = ["orders", "marketing_spend"]
    elif detected_intent == "category_analysis":
        required_tables = ["orders", "order_items"]
    elif detected_intent == "sales_overview":
        required_tables = ["orders"]
    else:
        required_tables = []

    if required_tables:
        intent = detected_intent
        # Retrieval expands context but never removes tables required by the
        # business definition. This keeps the contract explicit and testable.
        tables = list(dict.fromkeys([*required_tables, *retrieved_tables]))
        chart = "line" if ("趋势" in q or "每天" in q or "按日" in q) else "bar"
    elif detected_intent == "sales_overview":
        intent = "sales_overview"
        tables = ["orders"]
        chart = "line" if ("趋势" in q or "每天" in q or "按日" in q) else "bar"
    elif is_follow_up:
        intent = str(context.get("intent") or "sales_overview")
        tables = list(context.get("tables") or ["orders"])
        chart = str(context.get("chart_type") or "table")
        inherited.append("分析意图")
    else:
        tables = ["orders"]
        intent = "sales_overview"
        chart = "line" if ("趋势" in q or "每天" in q or "按日" in q) else "bar"

    time_filter, time_range, time_label = _time_filter(question, reference_date)
    if time_filter:
        filters.append(time_filter)
    elif is_follow_up:
        previous_time = _context_filter(context, "time_filter")
        if previous_time:
            filters.append(previous_time)
            time_range = str(context.get("time_range") or "继承上一轮时间范围")
            inherited.append(f"时间范围：{time_range}")

    asks_channel_dimension = any(term in q for term in ("各渠道", "按渠道", "渠道对比", "分渠道"))
    asks_region_dimension = any(term in q for term in ("各地区", "按地区", "地区对比", "分地区"))
    region = _pick(question, REGION_TERMS, "region", "o")
    channel = _pick(question, CHANNEL_TERMS, "channel", "o")
    category = next((c for c in CATEGORY_TERMS if c in question), "")

    if region:
        filters.append(region)
    elif is_follow_up and not asks_channel_dimension and _context_filter(context, "region_filter"):
        filters.append(_context_filter(context, "region_filter"))
        inherited.append("地区：" + str(context.get("region_label") or "继承上一轮"))

    if channel:
        filters.append(channel)
    elif is_follow_up and not asks_region_dimension and not asks_channel_dimension and _context_filter(context, "channel_filter"):
        filters.append(_context_filter(context, "channel_filter"))
        inherited.append("渠道：" + str(context.get("channel_label") or "继承上一轮"))

    item_category = any(term in q for term in ("商品行", "商品明细")) and any(term in q for term in ("销量", "件数"))
    category_filter = f"{'oi' if item_category else 'o'}.category = '{category}'" if category else ""
    if category:
        filters.append(category_filter)
    elif is_follow_up and _context_filter(context, "category_filter") and not asks_channel_dimension:
        filters.append(_context_filter(context, "category_filter"))
        inherited.append("品类：" + str(context.get("category_label") or "继承上一轮"))

    metrics: list[str] = []
    refund_metric = False
    if "退款率" in q or ("售后" in q and any(term in q for term in ("占比", "比例", "多大"))):
        metrics.append("退款率")
        refund_metric = True
    elif "退款金额" in q or "退的钱" in q:
        metrics.append("退款金额")
        refund_metric = True
    elif "退款" in q and any(term in q for term in ("原因", "分布")):
        metrics.append("退款金额")
        refund_metric = True
    elif "退款" in q and any(term in q for term in ("单数", "申请数", "多少单")):
        metrics.append("退款单数")
        refund_metric = True
    if (
        "roi" in q or "投产比" in q
        or ("广告" in q and "值不值" in q)
        or (("投放" in q or "回报" in q) and any(term in q for term in ("划算", "回报", "最高", "最好")))
    ):
        metrics.append("ROI")
    elif "客单价" in q or "每单平均" in q or "平均花" in q or "花多少钱" in q:
        metrics.append("客单价")
    elif not refund_metric and ("销量" in q or "件数" in q or "多少件" in q or "卖得最多" in q or "卖得多" in q):
        metrics.append("销量")
    previous_metrics = list(context.get("metrics") or [])
    if is_follow_up:
        previous_intent = str(context.get("intent") or "")
        if not detected_intent and previous_intent in {"refund_analysis", "marketing_efficiency", "category_analysis"}:
            intent = previous_intent
        if not metrics and previous_metrics:
            metrics = previous_metrics
            inherited.append("指标：" + " / ".join(map(str, previous_metrics)))
    if not metrics:
        metrics.append("销售额")

    if not time_range:
        time_range = str(context.get("time_range") or "全部数据（2025-10-01 至 2026-02-28）") if is_follow_up else "全部数据（2025-10-01 至 2026-02-28）"

    region_label = next((v for k, v in REGION_TERMS.items() if k in q), str(context.get("region_label") or "") if is_follow_up and not asks_channel_dimension else "")
    channel_label = next((v for k, v in CHANNEL_TERMS.items() if k in q), str(context.get("channel_label") or "") if is_follow_up and not asks_region_dimension and not asks_channel_dimension else "")
    category_label = next((c for c in CATEGORY_TERMS if c in q), str(context.get("category_label") or ""))

    dimensions: list[str] = []
    if (
        asks_channel_dimension
        or (("渠道" in q or "投放" in q) and detected_intent in {"sales_overview", "marketing_efficiency"})
        or ("哪个渠道" in q)
        or ("各渠道" in q)
    ):
        dimensions.append("channel")
    if (
        asks_region_dimension
        or ("地区" in q and detected_intent == "sales_overview")
        or ("哪个地区" in q)
        or ("各地区" in q)
    ):
        dimensions.append("region")
    if detected_intent == "category_analysis" or "品类" in q:
        dimensions.append("category")
    if "原因" in q or ("退款" in q and any(term in q for term in ("主要是什么", "损失的钱", "哪些原因"))):
        dimensions.append("reason")
    if chart == "line":
        dimensions.append("order_date")
    dimensions = list(dict.fromkeys(dimensions))
    return QueryPlan(
        intent=intent,
        tables=tables,
        metrics=metrics,
        dimensions=dimensions,
        filters=filters,
        time_range=time_range,
        chart_type=chart,
        notes=["规则规划器可离线运行；配置 LLM 后由模型生成 SQL，并沿用同一安全校验。"] + (["已继承上一轮分析上下文"] if inherited else []),
        inherited=inherited,
        source_question=str(context.get("question") or "") if is_follow_up else "",
        context={
            "reference_date": _reference_date(reference_date).isoformat(),
            "time_filter": time_filter or _context_filter(context, "time_filter"),
            "time_range": time_range,
            "region_filter": region or _context_filter(context, "region_filter"),
            "region_label": region_label,
            "channel_filter": channel or _context_filter(context, "channel_filter"),
            "channel_label": channel_label,
            "category_filter": category_filter or _context_filter(context, "category_filter"),
            "category_label": category_label,
            "metrics": metrics,
        },
    )
