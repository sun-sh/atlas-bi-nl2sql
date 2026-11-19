from __future__ import annotations

from app.nl2sql.semantic import FOLLOW_UP_TERMS, INTENT_TERMS, metric_dimensions, metric_from_text


SALES_METRICS = frozenset({"销售额", "客单价", "订单数"})
CATEGORY_METRICS = frozenset({*SALES_METRICS, "销量"})
MARKETING_METRICS = frozenset({"ROI", "投放花费", "归因订单数", "归因销售额"})
REFUND_METRICS = frozenset({"退款率", "退款金额", "退款单数"})

# These are not "registered metrics"; they are combinations the deterministic
# rule renderer can actually execute. The structured planner must reject a
# semantically registered shape before it reaches SQL generation.
INTENT_METRICS: dict[str, frozenset[str]] = {
    "sales_overview": SALES_METRICS,
    "category_analysis": CATEGORY_METRICS,
    "marketing_efficiency": MARKETING_METRICS,
    "refund_analysis": REFUND_METRICS,
}

FILTER_TABLES = {
    "channel": "orders",
    "region": "orders",
    "category": "orders",
    "member_level": "customers",
    "refund_reason": "refunds",
}

DIMENSION_TABLES = {
    "channel": "orders",
    "region": "orders",
    "category": "orders",
    "order_date": "orders",
    "reason": "refunds",
    "member_level": "customers",
    "customer_city": "customers",
}

FILTER_TO_DIMENSION = {
    "channel": "channel",
    "region": "region",
    "category": "category",
    "member_level": "member_level",
    "refund_reason": "reason",
}

CONTEXT_FILTER_KEYS = {
    "channel": "channel_label",
    "region": "region_label",
    "category": "category_label",
    "member_level": "member_level_label",
    "refund_reason": "refund_reason_label",
}

FILTER_COLUMNS = {
    "channel": "o.channel",
    "region": "o.region",
    "category": "o.category",
    "member_level": "c.member_level",
    "refund_reason": "r.reason",
}


def validate_executable_shape(
    *,
    intent: str,
    metrics: list[str],
    dimensions: list[str],
    filter_fields: set[str],
) -> None:
    """Reject registered shapes the current rule renderer cannot execute."""
    if intent not in INTENT_TERMS:
        raise ValueError(f"unknown intent: {intent}")
    allowed_metrics = INTENT_METRICS.get(intent, frozenset())
    unsupported_metrics = sorted(set(metrics) - allowed_metrics)
    if unsupported_metrics:
        raise ValueError(
            f"intent {intent} cannot render metrics: {unsupported_metrics}"
        )
    # The specialized quantity renderer currently emits exactly one metric.
    # Reject multi-metric category plans instead of silently dropping columns.
    if intent == "category_analysis" and "销量" in metrics and len(metrics) > 1:
        raise ValueError("category quantity analysis supports exactly one metric")
    # Refund rendering is intentionally single-metric; refund amount/rate add
    # their own declared companion columns.
    if intent == "refund_analysis" and len(metrics) != 1:
        raise ValueError("refund analysis supports exactly one primary metric")
    unknown_filters = sorted(set(filter_fields) - set(FILTER_TABLES))
    if unknown_filters:
        raise ValueError(f"unsupported filter fields: {unknown_filters}")
    for field in filter_fields:
        dimension = FILTER_TO_DIMENSION[field]
        # A filter is only part of the shared outer query. It must therefore be
        # supported by every selected metric, not just one of them.
        unsupported = [
            metric for metric in metrics if dimension not in metric_dimensions(metric)
        ]
        if unsupported:
            raise ValueError(
                f"filter {field} is not supported by metrics: {unsupported}"
            )


def dependency_tables(
    *,
    metrics: list[str],
    dimensions: list[str],
    filter_fields: set[str],
    item_metric: bool = False,
) -> set[str]:
    """Return physical tables required by the selected plan shape."""
    tables: set[str] = set()
    for dimension in dimensions:
        tables.add(DIMENSION_TABLES.get(dimension, "orders"))
    for field in filter_fields:
        if field == "category" and item_metric:
            tables.add("order_items")
        else:
            tables.add(FILTER_TABLES.get(field, "orders"))
    return tables


def is_follow_up_question(question: str, context: dict | None) -> bool:
    """Decide whether a turn may inherit omitted slots from the prior turn.

    A new question with an explicit metric/dimension is a fresh query: stale
    filters must not leak into it. Bare follow-ups and explicit follow-up
    words inherit the prior plan.
    """
    if not context:
        return False
    q = str(question or "").strip().lower()
    if not q:
        return False
    if any(term in q for term in FOLLOW_UP_TERMS):
        return True
    # A bare time/dimension adjustment is a follow-up. If the question names a
    # metric or explicitly starts a new scope (全国/全部), it is fresh.
    if any(term in q for term in ("全国", "全部", "全渠道", "整体")):
        return False
    return metric_from_text(q) is None
