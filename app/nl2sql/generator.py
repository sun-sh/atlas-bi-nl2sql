from __future__ import annotations

import re
import json

from app.nl2sql.model_config import get_model_config, make_client, model_error_message
from app.nl2sql.models import QueryPlan, SchemaHit
from app.nl2sql.planner import CATEGORY_TERMS
from app.nl2sql.schema_catalog import semantic_schema_prompt


SQL_INSTRUCTIONS = (
    "你是 SQLite 电商分析专家。输出一条完整 SELECT SQL，支持 SELECT 内的子查询；不要解释或代码块。"
    "只能查询提供的公开字段；禁止 customer_id、cost_price、SELECT *，必须聚合。"
    "数据范围2025-10-01至2026-02-28，相对日期基准固定为2026-02-28；不得使用系统当前日期。"
    "严格遵守已解析时间区间和当前筛选，不要因无数据擅自扩大范围。"
    "订单状态必须用真实英文枚举：已支付为paid，已退款为refunded，取消为cancelled。"
    "销售额SUM(orders.paid_amount)、客单价AVG(orders.paid_amount)、销量均默认仅统计paid订单。"
    "订单主表品类用orders.category；商品行品类及件数用order_items.category和quantity，"
    "按商品行品类筛选时不要同时筛选orders.category，也不要累加重复关联的订单金额。"
    "退款统计默认按orders.order_date筛选，关联refunds.order_id=orders.order_id；"
    "退款率为退款申请数/全部订单数*100，不筛paid、不排除processing退款；金额SUM(refund_amount)。"
    "ROI=已支付订单销售额/投放花费（倍数，不乘100）；orders和marketing_spend分别按渠道聚合，"
    "两表都使用相同日期区间，然后关联，禁止把每日花费在订单连接后重复累计。"
    "App不变，小程序=MiniProgram，抖音=Douyin，网页=Web。"
    "orders别名可用o，order_items可用oi，marketing_spend可用m；规划中o.order_date对应投放表spend_date。"
    "未要求分组的总量只返回一行；按要求排序，指标保留两位小数。"
    "输出列使用明确业务名：销售额、销量、客单价、退款率、退款单数、退款金额、ROI；"
    "维度用地区、渠道、品类、日期、退款原因。"
    "追问必须以当前计划的指标、时间与筛选为准，并结合前置问题理解继承项。"
)


def _sql_prompt(question: str, plan: QueryPlan) -> str:
    return (
        f"当前问题：{question}\n前置问题：{plan.source_question or '无'}\n"
        f"当前完整计划：{json.dumps(plan.as_dict(), ensure_ascii=False)}\n"
        f"数据库字段、枚举与表粒度：\n{semantic_schema_prompt(plan.tables)}\n只返回 SQL。"
    )


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
        # Aggregate each grain before joining. Joining daily spend directly to
        # orders repeats the spend for every matching order and understates ROI.
        filters = _marketing_filters(plan)
        spend_where = _where(filters)
        order_filters: list[str] = []
        for item in filters:
            if "m.spend_date" in item:
                order_filters.append(
                    item.replace("m.spend_date", "o.order_date").replace(
                        "m.channel", "o.channel"
                    )
                )
            elif "m.channel" in item:
                order_filters.append(item.replace("m.channel", "o.channel"))
        # ROI revenue is paid revenue. Keep this invariant in the rule path even
        # when the planner did not add a status filter.
        if not any("o.status" in item for item in order_filters):
            order_filters.append("o.status = 'paid'")
        order_where = _where(order_filters)
        return (
            "SELECT m.channel AS 渠道, ROUND(m.spend, 2) AS 投放花费, "
            "ROUND(m.attributed_orders, 0) AS 归因订单数, "
            "ROUND(o.revenue, 2) AS 归因销售额, "
            "ROUND(o.revenue / NULLIF(m.spend, 0), 2) AS ROI "
            "FROM (SELECT channel, SUM(spend) AS spend, SUM(attributed_orders) AS attributed_orders "
            "FROM marketing_spend m " + spend_where + " GROUP BY channel) m "
            "LEFT JOIN (SELECT channel, SUM(paid_amount) AS revenue FROM orders o "
            + order_where + " GROUP BY channel) o ON o.channel = m.channel "
            "ORDER BY ROI DESC"
        )
    if plan.intent == "refund_analysis":
        if "原因" in q:
            reason_metric = "退款金额" if "退款金额" in q else "退款单数"
            return (
                "SELECT r.reason AS 退款原因, COUNT(*) AS 退款单数, "
                "ROUND(SUM(r.refund_amount), 2) AS 退款金额 FROM refunds r "
                "JOIN orders o ON o.order_id = r.order_id " + _where(filters)
                + " GROUP BY r.reason ORDER BY " + reason_metric + " DESC"
            )
        if "退款金额" in q:
            # Refund amount is additive; using the refund-rate query here would
            # silently answer a different business question.
            item_category = "商品行" in q or "商品明细" in q
            category_expr = "oi.category" if item_category else "o.category"
            join = (
                "JOIN order_items oi ON oi.order_id = o.order_id JOIN orders o ON o.order_id = oi.order_id"
                if item_category
                else "JOIN orders o ON o.order_id = r.order_id"
            )
            return (
                f"SELECT {category_expr} AS 品类, COUNT(*) AS 退款单数, "
                "ROUND(SUM(r.refund_amount), 2) AS 退款金额 FROM refunds r "
                + join + _where(filters)
                + " GROUP BY " + category_expr + " ORDER BY 退款金额 DESC"
            )
        top_only = (
            any(term in q for term in ("最高", "最大", "第一", "top 1", "top1"))
            or any(term in q for term in ("哪一个", "哪个品类", "哪个类目"))
        )
        return (
            "SELECT o.category AS 品类, COUNT(DISTINCT o.order_id) AS 订单数, "
            "COUNT(DISTINCT r.refund_id) AS 退款单数, "
            "ROUND(COUNT(DISTINCT r.refund_id) * 100.0 / COUNT(DISTINCT o.order_id), 2) AS 退款率 "
            "FROM orders o LEFT JOIN refunds r ON r.order_id = o.order_id " + _where(filters)
            + " GROUP BY o.category ORDER BY 退款率 DESC"
            + (" LIMIT 1" if top_only else "")
        )
    if plan.intent == "category_analysis":
        item_grain = any(term in q for term in ("商品行", "商品明细"))
        aggregate_only = not any(
            term in q
            for term in ("各品类", "按品类", "分品类", "品类排名", "类目排名", "哪些品类", "哪些类目")
        ) and any(term in q for term in CATEGORY_TERMS)
        if "销量" in q or "件数" in q or "卖得最多" in q or "卖得多" in q:
            if aggregate_only:
                return (
                    "SELECT SUM(oi.quantity) AS 销量 FROM order_items oi "
                    "JOIN orders o ON o.order_id = oi.order_id "
                    + _where(filters + ["o.status = 'paid'"] if not any("o.status" in item for item in filters) else filters)
                )
            return (
                "SELECT oi.category AS 品类, SUM(oi.quantity) AS 销量 "
                "FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
                + _where(filters + ["o.status = 'paid'"] if not any("o.status" in item for item in filters) else filters)
                + " GROUP BY oi.category ORDER BY 销量 DESC"
            )
        if item_grain:
            # Amounts live on orders. For item-grain grouping, aggregate order
            # amounts first, otherwise a two-line order repeats its paid amount.
            return (
                "SELECT oi.category AS 品类, ROUND(SUM(order_amount), 2) AS 销售额 "
                "FROM order_items oi JOIN (SELECT order_id, "
                "ROUND(SUM(paid_amount), 2) AS order_amount FROM orders o "
                + _where(filters + ["o.status = 'paid'"] if not any("o.status" in item for item in filters) else filters)
                + " GROUP BY order_id) o ON o.order_id = oi.order_id "
                "GROUP BY oi.category ORDER BY 销售额 DESC"
            )
        if aggregate_only:
            return (
                "SELECT ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o "
                + _where(filters + ["o.status = 'paid'"] if not any("o.status" in item for item in filters) else filters)
            )
        # The semantic layer defines category sales on the order grain unless
        # the question explicitly asks for item-line grain.
        return (
            "SELECT o.category AS 品类, COUNT(*) AS 订单数, "
            "ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o "
            + _where(filters + ["o.status = 'paid'"] if not any("o.status" in item for item in filters) else filters)
            + " GROUP BY o.category ORDER BY 销售额 DESC"
        )
    where = _where(filters)
    paid_where = where + " AND o.status = 'paid'" if where else " WHERE o.status = 'paid'"
    if plan.chart_type == "line":
        return (
            "SELECT o.order_date AS 日期, ROUND(SUM(o.paid_amount), 2) AS 销售额, "
            "COUNT(*) AS 订单数 FROM orders o " + paid_where
            + " GROUP BY o.order_date ORDER BY o.order_date"
        )
    asks_region_dimension = any(
        term in q for term in ("各地区", "按地区", "分地区", "地区客单价", "区域客单价")
    )
    if "客单价" in q or "每单平均" in q or "平均花" in q:
        group, label = (
            ("o.region", "地区") if asks_region_dimension else ("o.channel", "渠道")
        )
        return (
            f"SELECT {group} AS {label}, COUNT(*) AS 订单数, ROUND(SUM(o.paid_amount), 2) AS 销售额, "
            "ROUND(AVG(o.paid_amount), 2) AS 客单价 FROM orders o " + paid_where
            + f" GROUP BY {group} ORDER BY 客单价 DESC"
        )
    region_filter = next(
        (item for item in filters if "o.region" in item),
        "",
    )
    channel_filter = next(
        (item for item in filters if "o.channel" in item),
        "",
    )
    asks_channel_group = any(term in q for term in ("各渠道", "按渠道", "分渠道", "渠道排名", "渠道对比"))
    asks_region_group = any(term in q for term in ("各地区", "按地区", "分地区", "地区排名", "地区对比"))
    single_region_total = region_filter and not asks_channel_group
    single_channel_total = channel_filter and not asks_region_group
    if single_region_total or single_channel_total:
        # A single-region/single-channel question without an explicit grouping
        # dimension is a scalar total, not a breakdown by the other dimension.
        return (
            "SELECT ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o " + paid_where
        )
    if asks_region_group:
        group, label = "o.region", "地区"
    elif asks_channel_group:
        group, label = "o.channel", "渠道"
    else:
        group, label = "o.region", "地区"
    return (
        f"SELECT {group} AS {label}, COUNT(*) AS 订单数, ROUND(SUM(o.paid_amount), 2) AS 销售额 "
        "FROM orders o " + paid_where + f" GROUP BY {group} ORDER BY 销售额 DESC"
    )


LAST_LLM_ERROR: str | None = None


def _llm_sql(instructions: str, prompt: str) -> str | None:
    global LAST_LLM_ERROR
    LAST_LLM_ERROR = None
    settings = get_model_config()
    if not settings.enabled:
        return None
    try:
        client = make_client(settings)
        response = client.chat.completions.create(
            model=settings.model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=1800,
            **(
                {
                    "extra_body": {
                        "chat_template_kwargs": {"enable_thinking": False},
                    }
                }
                if settings.provider.lower() == "qwen" or "qwen" in settings.model.lower()
                else {}
            ),
        )
        text = (response.choices[0].message.content or "").strip()
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        if finish_reason == "length":
            LAST_LLM_ERROR = "模型输出因token预算耗尽被截断（finish_reason=length），未执行残缺SQL"
            return None
        text = re.sub(r"^```(?:sql)?\s*|\s*```$", "", text, flags=re.I).strip()
        if not text:
            LAST_LLM_ERROR = "模型返回了空内容"
            return None
        return text
    except Exception as exc:
        LAST_LLM_ERROR = model_error_message(exc)
        return None


def generate_llm_sql(question: str, plan: QueryPlan, hits: list[SchemaHit]) -> str | None:
    del hits
    return _llm_sql(
        SQL_INSTRUCTIONS,
        _sql_prompt(question, plan),
    )


def generate_llm_repair_sql(question: str, plan: QueryPlan, hits: list[SchemaHit], previous_sql: str, error: str) -> str | None:
    del hits
    return _llm_sql(
        SQL_INSTRUCTIONS + "根据反馈修复原SQL，保持用户要求的业务口径，不得改变筛选来制造非空结果。",
        _sql_prompt(question, plan) + "\n"
        f"失败 SQL：\n{previous_sql}\n错误：\n{error}\n只返回修复后的 SQL。",
    )
