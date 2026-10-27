from __future__ import annotations

import re
import json

from app.nl2sql.model_config import get_model_config, make_client, model_error_message
from app.nl2sql.models import QueryPlan, SchemaHit
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
