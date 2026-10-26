from __future__ import annotations

import re

from app.nl2sql.model_config import get_model_config
from app.nl2sql.models import QueryPlan, SchemaHit
from app.nl2sql.schema_catalog import compact_schema_prompt, schema_prompt


def _where(filters: list[str]) -> str:
    return " WHERE " + " AND ".join(filters) if filters else ""


def _marketing_filters(plan: QueryPlan) -> list[str]:
    filters: list[str] = []
    for item in plan.filters:
        if ".region" in item or ".category" in item:
            raise ValueError("营销投放表不包含地区或品类字段，暂不支持按这些维度拆分 ROI")
        filters.append(item.replace("o.order_date", "m.spend_date").replace("o.channel", "m.channel"))
    return filters


def generate_rule_sql(question: str, plan: QueryPlan, hits: list[SchemaHit]) -> str:
    q = question.lower()
    filters = list(plan.filters)
    if plan.intent == "marketing_efficiency":
        where = _where(_marketing_filters(plan))
        return (
            "SELECT m.channel AS 渠道, ROUND(SUM(m.spend), 2) AS 投放花费, "
            "ROUND(SUM(m.attributed_orders), 0) AS 归因订单数, "
            "ROUND(SUM(o.paid_amount), 2) AS 归因销售额, "
            "ROUND(SUM(o.paid_amount) / NULLIF(SUM(m.spend), 0), 2) AS ROI "
            "FROM marketing_spend m LEFT JOIN orders o ON o.channel = m.channel "
            "AND o.order_date = m.spend_date AND o.status = 'paid' "
            + where + " GROUP BY m.channel ORDER BY ROI DESC"
        )
    if plan.intent == "refund_analysis":
        if "原因" in q:
            return (
                "SELECT r.reason AS 退款原因, COUNT(*) AS 退款单数, "
                "ROUND(SUM(r.refund_amount), 2) AS 退款金额 FROM refunds r "
                "JOIN orders o ON o.order_id = r.order_id " + _where(filters)
                + " GROUP BY r.reason ORDER BY 退款单数 DESC"
            )
        return (
            "SELECT o.category AS 品类, COUNT(DISTINCT o.order_id) AS 订单数, "
            "COUNT(DISTINCT r.refund_id) AS 退款单数, "
            "ROUND(COUNT(DISTINCT r.refund_id) * 100.0 / COUNT(DISTINCT o.order_id), 2) AS 退款率 "
            "FROM orders o LEFT JOIN refunds r ON r.order_id = o.order_id " + _where(filters)
            + " GROUP BY o.category ORDER BY 退款率 DESC"
        )
    if plan.intent == "category_analysis":
        if "销量" in q or "件数" in q:
            return (
                "SELECT oi.category AS 品类, SUM(oi.quantity) AS 销量 "
                "FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
                + _where(filters) + " GROUP BY oi.category ORDER BY 销量 DESC"
            )
        return (
            "SELECT oi.category AS 品类, COUNT(DISTINCT o.order_id) AS 订单数, "
            "ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o "
            "JOIN order_items oi ON oi.order_id = o.order_id " + _where(filters)
            + " GROUP BY oi.category ORDER BY 销售额 DESC"
        )
    where = _where(filters)
    paid_where = where + " AND o.status = 'paid'" if where else " WHERE o.status = 'paid'"
    if plan.chart_type == "line":
        return (
            "SELECT o.order_date AS 日期, ROUND(SUM(o.paid_amount), 2) AS 销售额, "
            "COUNT(*) AS 订单数 FROM orders o " + paid_where
            + " GROUP BY o.order_date ORDER BY o.order_date"
        )
    if "客单价" in q:
        return (
            "SELECT o.channel AS 渠道, COUNT(*) AS 订单数, ROUND(SUM(o.paid_amount), 2) AS 销售额, "
            "ROUND(AVG(o.paid_amount), 2) AS 客单价 FROM orders o " + paid_where
            + " GROUP BY o.channel ORDER BY 客单价 DESC"
        )
    if "地区" in q or any(x in q for x in ("华东", "华南", "华北", "西南")):
        group, label = "o.region", "地区"
    else:
        group, label = "o.channel", "渠道"
    return (
        f"SELECT {group} AS {label}, COUNT(*) AS 订单数, ROUND(SUM(o.paid_amount), 2) AS 销售额 "
        "FROM orders o " + paid_where + f" GROUP BY {group} ORDER BY 销售额 DESC"
    )


def _llm_sql(instructions: str, prompt: str) -> str | None:
    settings = get_model_config()
    if not settings.enabled:
        return None
    try:
        from app.nl2sql.model_config import make_client

        client = make_client(settings)
        response = client.chat.completions.create(
            model=settings.model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=800,
        )
        text = (response.choices[0].message.content or "").strip()
        text = re.sub(r"^```(?:sql)?\s*|\s*```$", "", text, flags=re.I).strip()
        return text or None
    except Exception:
        return None


def generate_llm_sql(question: str, plan: QueryPlan, hits: list[SchemaHit]) -> str | None:
    del hits
    return _llm_sql(
        "你是 SQLite 电商经营分析专家。只输出一条 SELECT SQL，不要解释。"
        "只能使用给定表和字段；禁止 customer_id 和 cost_price；必须聚合。",
        f"问题：{question}\n表结构：\n{compact_schema_prompt(plan.tables)}\n筛选：{plan.filters}\n只返回 SQL。",
    )


def generate_llm_repair_sql(question: str, plan: QueryPlan, hits: list[SchemaHit], previous_sql: str, error: str) -> str | None:
    del hits
    return _llm_sql(
        "修复失败的 SQLite SQL。根据错误修复，只输出一条 SELECT SQL，不要解释。"
        "禁止编造表或列；禁止 customer_id 和 cost_price。",
        f"问题：{question}\n计划：{plan.as_dict()}\n表结构：\n{compact_schema_prompt(plan.tables)}\n"
        f"失败 SQL：\n{previous_sql}\n错误：\n{error}\n只返回修复后的 SQL。",
    )
