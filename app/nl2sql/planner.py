from __future__ import annotations

import re
from datetime import date

from app.nl2sql.models import QueryPlan, SchemaHit


REGION_TERMS = {"华东": "华东", "华南": "华南", "华北": "华北", "西南": "西南"}
CHANNEL_TERMS = {"app": "App", "小程序": "MiniProgram", "miniprogram": "MiniProgram", "网页": "Web", "web": "Web", "抖音": "Douyin", "douyin": "Douyin"}
CATEGORY_TERMS = ("运动鞋服", "食品饮料", "数码家电", "美妆个护")
MEMBER_TERMS = ("普通", "银卡", "金卡")


def _last_month_sql() -> tuple[str, str]:
    today = date(2026, 2, 28)
    first = date(today.year, today.month, 1)
    last = first.replace(day=1) - __import__("datetime").timedelta(days=1)
    previous = last.replace(day=1)
    return previous.isoformat(), last.isoformat()


def _time_filter(question: str) -> tuple[str, str]:
    q = question.lower()
    if "上个月" in q or "上月" in q:
        start, end = _last_month_sql()
        return f"o.order_date BETWEEN '{start}' AND '{end}'", f"{start} 至 {end}"
    if "本月" in q:
        return "o.order_date >= '2026-02-01' AND o.order_date <= '2026-02-28'", "2026-02-01 至 2026-02-28"
    if "近30天" in q or "最近30天" in q:
        return "o.order_date BETWEEN '2026-01-30' AND '2026-02-28'", "2026-01-30 至 2026-02-28"
    if "近90天" in q or "最近90天" in q:
        return "o.order_date BETWEEN '2025-12-01' AND '2026-02-28'", "2025-12-01 至 2026-02-28"
    return "", "全部数据（2025-10-01 至 2026-02-28）"


def _pick(question: str, mapping: dict[str, str], column: str, prefix: str) -> str:
    q = question.lower()
    for term, value in mapping.items():
        if term in q:
            return f"{prefix}.{column} = '{value}'"
    return ""


def make_plan(question: str, hits: list[SchemaHit]) -> QueryPlan:
    q = question.lower()
    tables = [h.table for h in hits]
    filters = []
    if "退款" in q or "售后" in q or "退货" in q:
        tables = ["orders", "refunds"]
        intent = "refund_analysis"
        chart = "bar"
    elif "roi" in q or "投放" in q or "广告" in q or "获客成本" in q or "花费" in q:
        tables = ["marketing_spend"]
        intent = "marketing_efficiency"
        chart = "bar"
    elif "品类" in q or "类目" in q or "商品" in q or "销量" in q:
        tables = ["orders", "order_items"]
        intent = "category_analysis"
        chart = "bar"
    else:
        tables = ["orders"]
        intent = "sales_overview"
        chart = "line" if ("趋势" in q or "每天" in q or "按日" in q) else "bar"
    time_filter, time_range = _time_filter(question)
    if time_filter:
        filters.append(time_filter)
    region = _pick(question, REGION_TERMS, "region", "o")
    channel = _pick(question, CHANNEL_TERMS, "channel", "o")
    if region:
        filters.append(region)
    if channel:
        filters.append(channel)
    category = next((c for c in CATEGORY_TERMS if c in question), "")
    if category:
        filters.append(f"o.category = '{category}'")
    metrics = []
    if "退款率" in q:
        metrics.append("退款率")
    if "roi" in q or "投产比" in q:
        metrics.append("ROI")
    if "客单价" in q:
        metrics.append("客单价")
    if "销量" in q or "件数" in q:
        metrics.append("销量")
    if not metrics:
        metrics.append("销售额")
    return QueryPlan(
        intent=intent,
        tables=tables,
        metrics=metrics,
        dimensions=[],
        filters=filters,
        time_range=time_range,
        chart_type=chart,
        notes=["规则规划器可离线运行；配置 LLM 后由模型生成 SQL，并沿用同一安全校验。"],
    )
