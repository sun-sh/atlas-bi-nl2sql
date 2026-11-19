from __future__ import annotations

import re
import json

import sqlglot
from sqlglot import exp

from app.nl2sql.model_config import get_model_config, make_client, model_error_message
from app.nl2sql.model_runtime import model_call_slot, record_usage
from app.nl2sql.models import QueryPlan
from app.nl2sql.planner import CATEGORY_TERMS
from app.nl2sql.semantic import (
    DATASET_RANGE,
    DATASET_START,
    DATASET_END,
    REFERENCE_DATE,
    dimension_label,
    dimension_sql,
    enum_prompt,
    metric_filters,
    metric_prompt,
    metric_sql,
    metric_table_grain,
    output_metric_names,
)
from app.nl2sql.schema_catalog import semantic_schema_prompt
from app.nl2sql.settings import get_settings


SQL_INSTRUCTIONS = (
    "你是 SQLite 电商分析专家。输出一条完整 SELECT SQL，支持 SELECT 内的子查询；不要解释或代码块。"
    "只能查询提供的公开字段；禁止 customer_id、cost_price、SELECT *，必须聚合。"
    "数据范围以当前激活数据源和当前计划中的时间过滤为准；相对日期基准见下方当前计划中的 reference_date，不得使用系统当前日期。"
    "严格遵守已解析时间区间和当前筛选，不要因无数据擅自扩大范围。"
    f"订单状态必须用真实英文枚举：{enum_prompt('orders.status')}。"
    "业务指标必须使用语义层给出的 expr 和 filters，不得自行更换口径。"
    f"指标定义：\n{metric_prompt()}\n"
    "订单主表品类用orders.category；商品行品类及件数用order_items.category和quantity，"
    "按商品行品类筛选时不要同时筛选orders.category，也不要累加重复关联的订单金额。"
    "退款统计默认按orders.order_date筛选，关联refunds.order_id=orders.order_id。"
    "ROI 的 orders 和 marketing_spend 分别按渠道聚合后关联，禁止把每日花费在订单连接后重复累计。"
    "问题出现“各渠道/按渠道/渠道ROI/哪个渠道”时，必须返回每个有投放花费的渠道，"
    "除非用户明确只要第一名，否则不要加 LIMIT 1 把排名压成单行。"
    f"渠道映射：{enum_prompt('orders.channel')}；按语义层别名解析。"
    "orders别名可用o，order_items可用oi，marketing_spend可用m；规划中o.order_date对应投放表spend_date。"
    "未要求分组的总量只返回一行；按要求排序，指标保留两位小数。"
    f"输出列使用明确业务名：{'、'.join(output_metric_names())}；"
    "维度用地区、渠道、品类、日期、退款原因、会员等级、客户城市。"
    "追问必须以当前计划的指标、时间与筛选为准，并结合前置问题理解继承项。"
)

DIALECT_INSTRUCTIONS = {
    "postgres": (
        "当前分析库是 PostgreSQL，请输出 PostgreSQL 方言 SQL；"
        "标识符如与关键字冲突请使用双引号，不要使用 SQLite 专有函数或 PRAGMA。"
    ),
    "mysql": (
        "当前分析库是 MySQL，请输出 MySQL 方言 SQL；"
        "标识符如与关键字冲突请使用反引号，不要使用 SQLite 专有函数或 PRAGMA。"
    ),
}


def _relative_date_instruction(plan: QueryPlan) -> str:
    reference_date = str(plan.context.get("reference_date") or REFERENCE_DATE.isoformat())
    return f"本次查询的参考日期是 {reference_date}；用户说本月/上个月时，以该日期为锚点。"


def _sql_prompt(question: str, plan: QueryPlan) -> str:
    # Only the tables selected by the planner enter the prompt. The retriever
    # can add at most one supplement to that plan; the remaining ranked hits
    # stay visible as response evidence but must not silently become schema.
    selected_tables = list(dict.fromkeys(plan.tables))
    retrieval_note = (
        f"检索补充表：{', '.join(plan.context.get('retrieval_supplements') or []) or '无'}\n"
    )
    return (
        f"{_relative_date_instruction(plan)}\n"
        f"当前问题：{question}\n前置问题：{plan.source_question or '无'}\n"
        f"当前完整计划：{json.dumps(plan.as_dict(), ensure_ascii=False)}\n"
        f"{retrieval_note}"
        f"进入 SQL 上下文的字段、枚举与表粒度：\n{semantic_schema_prompt(selected_tables)}\n只返回 SQL。"
    )


def _where(filters: list[str]) -> str:
    return " WHERE " + " AND ".join(filters) if filters else ""


def _with_metric_filters(filters: list[str], *metric_names: str) -> list[str]:
    """Append a metric's declared predicate unless the planner supplied it."""
    result = list(filters)
    for metric_name in metric_names:
        for predicate in metric_filters(metric_name):
            if predicate not in result:
                result.append(predicate)
    return result


def _marketing_filters(plan: QueryPlan) -> list[str]:
    """Move known order predicates onto marketing_spend by AST column mapping.

    String replacement is unsafe once a predicate contains a function call,
    subquery, or a literal that happens to contain an alias-like substring.
    """
    filters: list[str] = []
    for item in plan.filters:
        try:
            predicate = sqlglot.parse_one(item, read="sqlite")
        except Exception as exc:
            raise ValueError(f"无法解析营销筛选条件：{item}") from exc
        for column in predicate.find_all(exp.Column):
            if column.table.lower() != "o":
                continue
            name = column.name.lower()
            if name == "order_date":
                column.set("this", exp.to_identifier("spend_date"))
            elif name == "channel":
                column.set("this", exp.to_identifier("channel"))
            else:
                raise ValueError(
                    "营销投放表只支持订单日期和渠道筛选，暂不支持其他订单字段"
                )
            column.set("table", exp.to_identifier("m"))
        filters.append(str(predicate))
    return filters


def channel_filter_present(plan: QueryPlan) -> bool:
    return any("m.channel" in item or "o.channel" in item for item in plan.filters)


def derived_columns_for_plan(plan: QueryPlan) -> dict[str, str]:
    """Declare response-only companion columns without changing plan metrics."""
    metrics = set(plan.metrics or [])
    if plan.intent == "marketing_efficiency":
        derived = {"归因订单数": "marketing companion column"} if metrics & MARKETING_SPEND_COMPANION_METRICS else {}
        if channel_filter_present(plan):
            derived["渠道"] = "single-channel filter label"
        return derived
    if "退款金额" in metrics:
        return {"退款单数": "refund amount companion column"}
    if "退款率" in metrics:
        return {
            "退款单数": "refund rate numerator",
            "订单数": "refund rate denominator",
        }
    return {}


def _plan_metric_names(plan: QueryPlan) -> list[str]:
    """Use the planner's metric contract, with a narrow compatibility default."""
    return list(dict.fromkeys(plan.metrics or ["销售额"]))


def _plan_dimension_sql(plan: QueryPlan) -> tuple[list[str], list[str]]:
    return (
        [dimension_sql(item) for item in plan.dimensions],
        [dimension_label(item) for item in plan.dimensions],
    )


def _plan_requires_customer_join(plan: QueryPlan) -> bool:
    return any(item in {"member_level", "customer_city"} for item in plan.dimensions)


def _grouped_metric_sql(
    plan: QueryPlan,
    *,
    filters: list[str],
    table_sql: str,
    default_metric: str = "销售额",
    extra_metrics: list[str] | None = None,
    extra_metrics_first: bool = True,
) -> str:
    """Render the generic rule path from the same plan the API reports.

    The rule generator must not re-infer metrics or dimensions from the
    question. Specialized intents (ROI and item-grain quantity) retain their
    own joins, but ordinary sales/refund/category queries share this renderer.
    """
    metrics = _plan_metric_names(plan)
    metric = metrics[0] if metrics else default_metric
    dimensions, labels = _plan_dimension_sql(plan)
    selected = []
    aliases = []
    for dimension, label in zip(dimensions, labels):
        selected.append(f"{dimension} AS {label}")
        aliases.append(dimension)
    ordered_metrics = [
        *((extra_metrics or []) if extra_metrics_first else []),
        *metrics,
        *([] if extra_metrics_first else (extra_metrics or [])),
    ]
    for metric_name in ordered_metrics:
        selected.append(f"{metric_sql(metric_name)} AS {metric_name}")
    selected_sql = ", ".join(selected)
    all_filters = _with_metric_filters(filters, *metrics)
    where_sql = _where(all_filters)
    if dimensions:
        order = f" ORDER BY {aliases[0]}" if aliases[0].endswith("order_date") else f" ORDER BY {metric} DESC"
        from_sql = f"SELECT {selected_sql} FROM {table_sql}"
        if _plan_requires_customer_join(plan):
            from_sql += " JOIN customers c ON c.customer_id = o.customer_id"
        return from_sql + where_sql + " GROUP BY " + ", ".join(aliases) + order
    from_sql = f"SELECT {selected_sql} FROM {table_sql}"
    if _plan_requires_customer_join(plan):
        from_sql += " JOIN customers c ON c.customer_id = o.customer_id"
    return from_sql + where_sql

def _item_grain_sales_sql(plan: QueryPlan, filters: list[str]) -> str:
    """Aggregate order revenue before joining item rows to avoid duplication."""
    metrics = _plan_metric_names(plan)
    metric = metrics[0]
    dimensions = [
        "oi.category" if item == "category" else dimension_sql(item)
        for item in plan.dimensions
    ]
    labels = [dimension_label(item) for item in plan.dimensions]
    selected = [f"{dimension} AS {label}" for dimension, label in zip(dimensions, labels)]
    selected.append(f"{metric_sql('归因销售额', aliases={'o': 'o'})} AS {metric}")
    inner_metric = "销售额" if metric == "销售额" else metric
    inner = (
        f"(SELECT order_id, {metric_sql(inner_metric)} AS revenue FROM orders o "
        + _where(_with_metric_filters(filters, inner_metric))
        + " GROUP BY order_id) o"
    )
    from_sql = "SELECT " + ", ".join(selected) + " FROM order_items oi JOIN " + inner
    from_sql += " ON oi.order_id = o.order_id"
    if dimensions:
        return (
            from_sql + " GROUP BY " + ", ".join(dimensions)
            + f" ORDER BY {metric} DESC"
        )
    return from_sql



def _marketing_channel_value(plan: QueryPlan) -> str:
    for item in plan.filters:
        marker = "channel = '"
        if marker in item and item.rstrip().endswith("'"):
            value = item.split(marker, 1)[1].rsplit("'", 1)[0]
            if value:
                return value
    return ""


def _marketing_metric_column_sql(metric_name: str) -> str:
    if metric_name == "ROI":
        return "ROUND(o.revenue / NULLIF(m.spend, 0), 2)"
    if metric_name == "归因销售额":
        return "ROUND(o.revenue, 2)"
    if metric_name == "投放花费":
        return "ROUND(m.spend, 2)"
    if metric_name == "归因订单数":
        return "m.attributed_orders"
    return metric_sql(metric_name)


MARKETING_SPEND_COMPANION_METRICS = {"ROI", "投放花费"}


def generate_rule_sql(question: str, plan: QueryPlan) -> str:
    q = question.lower()
    filters = list(plan.filters)
    if plan.intent == "marketing_efficiency":
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
        order_filters = _with_metric_filters(order_filters, "归因销售额")
        order_where = _where(order_filters)
        metrics = _plan_metric_names(plan)
        dimensions = [item for item in plan.dimensions if item in {"channel", "order_date"}]
        companion = bool(set(metrics) & MARKETING_SPEND_COMPANION_METRICS)

        if not dimensions:
            spend_expr = f"(SELECT SUM(m.spend) FROM marketing_spend m {spend_where})"
            revenue_expr = f"(SELECT SUM(o.paid_amount) FROM orders o {order_where})"
            attributed_expr = f"(SELECT SUM(m.attributed_orders) FROM marketing_spend m {spend_where})"
            scalar = {
                "ROI": f"ROUND({revenue_expr} / NULLIF({spend_expr}, 0), 2)",
                "投放花费": f"ROUND({spend_expr}, 2)",
                "归因订单数": attributed_expr,
                "归因销售额": f"ROUND({revenue_expr}, 2)",
            }
            selected = []
            channel = _marketing_channel_value(plan)
            if channel:
                selected.append(f"'{channel}' AS 渠道")
            for metric_name in metrics:
                selected.append(f"{scalar.get(metric_name, metric_sql(metric_name))} AS {metric_name}")
            if companion:
                selected.append(f"{attributed_expr} AS 归因订单数")
            return "SELECT " + ", ".join(selected)

        spend_keys = []
        revenue_keys = []
        join_conditions = []
        selected = []
        if "channel" in dimensions:
            spend_keys.append("channel")
            revenue_keys.append("channel")
            join_conditions.append("o.channel = m.channel")
            selected.append("m.channel AS 渠道")
        if "order_date" in dimensions:
            spend_keys.append("spend_date")
            revenue_keys.append("order_date")
            join_conditions.append("o.order_date = m.spend_date")
            selected.append("m.spend_date AS 日期")
        for metric_name in metrics:
            selected.append(f"{_marketing_metric_column_sql(metric_name)} AS {metric_name}")
        if companion:
            selected.append("m.attributed_orders AS 归因订单数")
        spend_select = ", ".join([*spend_keys, "SUM(spend) AS spend", "SUM(attributed_orders) AS attributed_orders"])
        revenue_select = ", ".join([*revenue_keys, "SUM(paid_amount) AS revenue"])
        spend_group = " GROUP BY " + ", ".join(spend_keys)
        revenue_group = " GROUP BY " + ", ".join(revenue_keys)
        join_sql = " ON " + " AND ".join(join_conditions)
        # The inner aggregate already produced one row per requested grain.
        # Do not add an outer GROUP BY; that is redundant and dialect-sensitive.
        if "order_date" in dimensions:
            order_parts = ["日期 ASC"]
            if "channel" in dimensions:
                order_parts.append("渠道 ASC")
        else:
            order_parts = [f"{metrics[0]} DESC"]
        suffix = " ORDER BY " + ", ".join(order_parts)
        if any(item in {"ROI", "归因销售额"} for item in metrics):
            return (
                "SELECT " + ", ".join(selected) +
                " FROM (SELECT " + spend_select + " FROM marketing_spend m "
                + spend_where + spend_group + ") m "
                "LEFT JOIN (SELECT " + revenue_select + " FROM orders o "
                + order_where + revenue_group + ") o "
                + join_sql + suffix
            )
        return (
            "SELECT " + ", ".join(selected) +
            " FROM (SELECT " + spend_select + " FROM marketing_spend m "
            + spend_where + spend_group + ") m "
            + suffix
        )
    if plan.intent == "refund_analysis":
        metrics = _plan_metric_names(plan)
        metric = metrics[0]
        dimensions, labels = _plan_dimension_sql(plan)
        if metric == "退款率":
            table_sql = "orders o LEFT JOIN refunds r ON r.order_id = o.order_id"
        else:
            table_sql = "refunds r JOIN orders o ON o.order_id = r.order_id"
        top_only = (
            any(term in q for term in ("最高", "最大", "第一", "top 1", "top1"))
            or any(term in q for term in ("哪一个", "哪个品类", "哪个类目"))
        )
        extra_metrics = {"退款金额": ["退款单数"], "退款率": ["退款单数", "订单数"]}.get(metric)
        if metric == "退款率":
            selected = []
            group_aliases = []
            for dimension, label in zip(dimensions, labels):
                selected.append(f"{dimension} AS {label}")
                group_aliases.append(dimension)
            selected.extend([
                f"{metric_sql(metric)} AS {metric}",
                f"{metric_sql('退款单数')} AS 退款单数",
                f"{metric_sql('订单数')} AS 订单数",
            ])
            from_sql = "SELECT " + ", ".join(selected) + " FROM " + table_sql
            if group_aliases:
                sql = from_sql + _where(filters) + " GROUP BY " + ", ".join(group_aliases) + " ORDER BY 退款率 DESC"
            else:
                sql = from_sql + _where(filters)
            return sql + (" LIMIT 1" if top_only and group_aliases else "")
        sql = _grouped_metric_sql(plan, filters=filters, table_sql=table_sql, extra_metrics=extra_metrics, extra_metrics_first=(metric == "退款金额"))
        if top_only and dimensions:
            sql += " LIMIT 1"
        return sql

    if plan.intent == "category_analysis":
        metrics = _plan_metric_names(plan)
        metric = metrics[0]
        if metric == "销量":
            dimensions, labels = _plan_dimension_sql(plan)
            selected = []
            for dimension, label in zip(dimensions, labels):
                item_dimension = "oi.category" if dimension.endswith("category") else dimension
                selected.append(f"{item_dimension} AS {label}")
            selected.append(f"{metric_sql(metric)} AS {metric}")
            sales_filters = _with_metric_filters(filters, metric)
            from_sql = "SELECT " + ", ".join(selected) + " FROM order_items oi JOIN orders o ON o.order_id = oi.order_id"
            if _plan_requires_customer_join(plan):
                from_sql += " JOIN customers c ON c.customer_id = o.customer_id"
            if dimensions:
                group_columns = ["oi.category" if item.endswith("category") else item for item in dimensions]
                return from_sql + _where(sales_filters) + " GROUP BY " + ", ".join(group_columns) + f" ORDER BY {metric} DESC"
            return from_sql + _where(sales_filters)
        item_grain_requested = any(term in q for term in ("商品行", "商品明细"))
        if item_grain_requested and "category" in plan.dimensions:
            return _item_grain_sales_sql(plan, filters)
        return _grouped_metric_sql(plan, filters=filters, table_sql="orders o")

    return _grouped_metric_sql(plan, filters=filters, table_sql="orders o")



LAST_LLM_ERROR: str | None = None
# Compatibility alias for callers that only want the last process-wide error.
# Request-scoped code must use the returned error from _llm_sql_result.


def _llm_sql_result(
    instructions: str,
    prompt: str,
    *,
    max_tokens: int | None = None,
) -> tuple[str | None, str]:
    """Return SQL and the error for this call, so concurrent requests cannot leak state."""
    settings = get_model_config()
    if not settings.enabled:
        return None, "模型未启用"
    configured = get_settings()
    token_budget = max_tokens or configured.model_sql_max_tokens
    try:
        client = make_client(settings, timeout=configured.model_call_timeout_seconds)
        with model_call_slot("sql"):
            response = client.chat.completions.create(
                model=settings.model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=token_budget,
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
            record_usage(getattr(response, "usage", None))
        text = (response.choices[0].message.content or "").strip()
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        if finish_reason == "length":
            return None, "模型输出因token预算耗尽被截断（finish_reason=length），未执行残缺SQL"
        text = re.sub(r"^```(?:sql)?\s*|\s*```$", "", text, flags=re.I).strip()
        if not text:
            return None, "模型返回了空内容"
        return text, ""
    except Exception as exc:
        return None, model_error_message(exc)


def _llm_sql(instructions: str, prompt: str, *, max_tokens: int | None = None) -> str | None:
    """Backward-compatible wrapper; new request-scoped code uses _llm_sql_result."""
    global LAST_LLM_ERROR
    result, error = _llm_sql_result(instructions, prompt, max_tokens=max_tokens)
    LAST_LLM_ERROR = error or None
    return result


def generate_llm_sql_result(
    question: str,
    plan: QueryPlan,
    *,
    dialect: str = "sqlite",
) -> tuple[str | None, str]:
    return _llm_sql_result(
        SQL_INSTRUCTIONS + DIALECT_INSTRUCTIONS.get(dialect, ""),
        _sql_prompt(question, plan),
    )


def generate_llm_sql(
    question: str,
    plan: QueryPlan,
    *,
    dialect: str = "sqlite",
) -> str | None:
    sql, error = generate_llm_sql_result(question, plan, dialect=dialect)
    global LAST_LLM_ERROR
    LAST_LLM_ERROR = error or None
    return sql


def generate_llm_repair_sql_result(
    question: str,
    plan: QueryPlan,
    previous_sql: str,
    error: str,
    *,
    dialect: str = "sqlite",
) -> tuple[str | None, str]:
    return _llm_sql_result(
        SQL_INSTRUCTIONS
        + DIALECT_INSTRUCTIONS.get(dialect, "")
        + "根据反馈修复原SQL，保持用户要求的业务口径，不得改变筛选来制造非空结果。",
        _sql_prompt(question, plan) + "\n"
        f"失败 SQL：\n{previous_sql}\n错误：\n{error}\n只返回修复后的 SQL。",
        # A repair may need to restate a multi-branch ROI query; using the
        # smaller first-pass budget here caused avoidable truncation.
        max_tokens=max(
            get_settings().model_sql_max_tokens,
            get_settings().model_sql_retry_max_tokens,
        ),
    )


def generate_llm_repair_sql(
    question: str,
    plan: QueryPlan,
    previous_sql: str,
    error: str,
    *,
    dialect: str = "sqlite",
) -> str | None:
    sql, call_error = generate_llm_repair_sql_result(
        question, plan, previous_sql, error, dialect=dialect
    )
    global LAST_LLM_ERROR
    LAST_LLM_ERROR = call_error or None
    return sql
