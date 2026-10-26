from __future__ import annotations

import re
from datetime import date

from app.nl2sql.models import QueryPlan, SchemaHit


REGION_TERMS = {"华东": "华东", "华南": "华南", "华北": "华北", "西南": "西南"}
CHANNEL_TERMS = {"app": "App", "小程序": "MiniProgram", "miniprogram": "MiniProgram", "网页": "Web", "web": "Web", "抖音": "Douyin", "douyin": "Douyin"}
CATEGORY_TERMS = ("运动鞋服", "食品饮料", "数码家电", "美妆个护")
MEMBER_TERMS = ("普通", "银卡", "金卡")
FOLLOW_UP_TERMS = ("那", "换成", "改成", "再看", "继续", "呢", "这个", "这些", "同样", "那个", "它")
INTENT_TERMS = {
    "refund_analysis": ("退款率", "售后", "退货", "退款"),
    "marketing_efficiency": ("roi", "投放", "广告", "获客成本", "花费"),
    "category_analysis": ("品类", "类目", "商品", "销量", "件数"),
    "sales_overview": ("销售额", "销售", "趋势", "每天", "按日", "订单", "渠道", "地区"),
}


def _last_month_sql() -> tuple[str, str]:
    today = date(2026, 2, 28)
    first = date(today.year, today.month, 1)
    last = first.replace(day=1) - __import__("datetime").timedelta(days=1)
    previous = last.replace(day=1)
    return previous.isoformat(), last.isoformat()


def _time_filter(question: str) -> tuple[str, str, str]:
    q = question.lower()
    if "上个月" in q or "上月" in q:
        start, end = _last_month_sql()
        return f"o.order_date BETWEEN '{start}' AND '{end}'", f"{start} 至 {end}", "上个月"
    if "本月" in q:
        return "o.order_date >= '2026-02-01' AND o.order_date <= '2026-02-28'", "2026-02-01 至 2026-02-28", "本月"
    if "近30天" in q or "最近30天" in q:
        return "o.order_date BETWEEN '2026-01-30' AND '2026-02-28'", "2026-01-30 至 2026-02-28", "近30天"
    if "近90天" in q or "最近90天" in q:
        return "o.order_date BETWEEN '2025-12-01' AND '2026-02-28'", "2025-12-01 至 2026-02-28", "近90天"
    return "", "", ""


def _pick(question: str, mapping: dict[str, str], column: str, prefix: str) -> str:
    q = question.lower()
    for term, value in mapping.items():
        if term in q:
            return f"{prefix}.{column} = '{value}'"
    return ""


def _detect_intent(q: str) -> str | None:
    for intent, terms in INTENT_TERMS.items():
        if any(term in q for term in terms):
            return intent
    return None


def _context_filter(context: dict, key: str) -> str:
    value = context.get(key)
    return str(value) if value else ""


def _follow_up(question: str, context: dict | None) -> bool:
    if not context:
        return False
    q = question.lower()
    return any(term in q for term in FOLLOW_UP_TERMS) or not _detect_intent(q)


def make_plan(question: str, hits: list[SchemaHit], context: dict | None = None) -> QueryPlan:
    q = question.lower()
    tables = [h.table for h in hits]
    filters: list[str] = []
    inherited: list[str] = []
    context = context or {}
    is_follow_up = _follow_up(question, context)

    detected_intent = _detect_intent(q)
    if detected_intent == "refund_analysis":
        tables = ["orders", "refunds"]
        intent = "refund_analysis"
        chart = "bar"
    elif detected_intent == "marketing_efficiency":
        tables = ["marketing_spend"]
        intent = "marketing_efficiency"
        chart = "bar"
    elif detected_intent == "category_analysis":
        tables = ["orders", "order_items"]
        intent = "category_analysis"
        chart = "bar"
    elif detected_intent == "sales_overview":
        tables = ["orders"]
        intent = "sales_overview"
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

    time_filter, time_range, time_label = _time_filter(question)
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

    if category:
        filters.append(f"o.category = '{category}'")
    elif is_follow_up and _context_filter(context, "category_filter") and not asks_channel_dimension:
        filters.append(_context_filter(context, "category_filter"))
        inherited.append("品类：" + str(context.get("category_label") or "继承上一轮"))

    metrics: list[str] = []
    if "退款率" in q:
        metrics.append("退款率")
    if "roi" in q or "投产比" in q:
        metrics.append("ROI")
    if "客单价" in q:
        metrics.append("客单价")
    if "销量" in q or "件数" in q:
        metrics.append("销量")
    if not metrics and is_follow_up:
        previous_metrics = list(context.get("metrics") or [])
        if previous_metrics:
            metrics = previous_metrics
            inherited.append("指标：" + " / ".join(map(str, previous_metrics)))
    if not metrics:
        metrics.append("销售额")

    if not time_range:
        time_range = str(context.get("time_range") or "全部数据（2025-10-01 至 2026-02-28）") if is_follow_up else "全部数据（2025-10-01 至 2026-02-28）"

    region_label = next((v for k, v in REGION_TERMS.items() if k in q), str(context.get("region_label") or "") if is_follow_up and not asks_channel_dimension else "")
    channel_label = next((v for k, v in CHANNEL_TERMS.items() if k in q), str(context.get("channel_label") or "") if is_follow_up and not asks_region_dimension and not asks_channel_dimension else "")
    category_label = next((c for c in CATEGORY_TERMS if c in q), str(context.get("category_label") or ""))

    return QueryPlan(
        intent=intent,
        tables=tables,
        metrics=metrics,
        dimensions=[],
        filters=filters,
        time_range=time_range,
        chart_type=chart,
        notes=["规则规划器可离线运行；配置 LLM 后由模型生成 SQL，并沿用同一安全校验。"] + (["已继承上一轮分析上下文"] if inherited else []),
        inherited=inherited,
        source_question=str(context.get("question") or "") if is_follow_up else "",
        context={
            "time_filter": time_filter or _context_filter(context, "time_filter"),
            "time_range": time_range,
            "region_filter": region or _context_filter(context, "region_filter"),
            "region_label": region_label,
            "channel_filter": channel or _context_filter(context, "channel_filter"),
            "channel_label": channel_label,
            "category_filter": f"o.category = '{category}'" if category else _context_filter(context, "category_filter"),
            "category_label": category_label,
            "metrics": metrics,
        },
    )
