from __future__ import annotations

from typing import Literal

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


EXPLICIT_RESET_TERMS = (
    "全国", "全部", "全渠道", "整体", "清空筛选", "清除筛选",
    "换成全部", "所有渠道", "不限渠道", "不限地区",
)

TIME_REFERENCE_TERMS = (
    "本月", "这个月", "上个月", "下个月",
    "近30天", "近90天", "最近30天", "最近90天",
    "过去30天", "过去90天", "今年", "去年", "本季度", "上个季度",
)

# These are grammatical follow-up markers only. Bare substring checks such as
# "这个" in q incorrectly classify ordinary phrases like "这个月".
STANDALONE_FOLLOW_UP_MARKERS = (
    "那呢", "呢", "换成", "改成", "再看", "继续", "同样吧", "同一个指标",
)


def normalize_question_scope(question: str) -> str:
    """Return a comparison form with explicit time words removed.

    Time labels are not follow-up markers. Removing them before detecting
    markers prevents "这个月" and "那上个月呢" from being interpreted only by
    incidental character overlap.
    """
    q = str(question or "").strip().lower()
    for term in sorted(TIME_REFERENCE_TERMS, key=len, reverse=True):
        q = q.replace(term, " ")
    return " ".join(q.split())


def context_scope(question: str, context: dict | None) -> Literal[
    "new_query", "reset_scope", "follow_up"
]:
    """Classify whether a turn can inherit previous slots.

    Explicit scope resets and metric-bearing new questions always win over
    follow-up words. This prevents "这个月", "换成全部渠道" and "那全部渠道呢"
    from silently inheriting a prior region/channel filter.
    """
    if not context:
        return "new_query"
    raw = str(question or "").strip().lower()
    if not raw:
        return "follow_up"
    if any(term in raw for term in EXPLICIT_RESET_TERMS):
        return "reset_scope"
    if metric_from_text(raw) is not None:
        return "new_query"
    normalized = normalize_question_scope(raw)
    # A time-only adjustment such as "那上个月呢" still inherits non-time slots.
    if normalized and any(term in normalized for term in STANDALONE_FOLLOW_UP_MARKERS):
        return "follow_up"
    # Bare time adjustments have no remaining business text.
    return "follow_up" if not normalized else "new_query"


def is_follow_up_question(question: str, context: dict | None) -> bool:
    """Backward-compatible predicate: only genuine follow-ups inherit slots."""
    return context_scope(question, context) == "follow_up"
