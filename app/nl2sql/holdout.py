from __future__ import annotations

from typing import Any

from app.nl2sql.semantic import DATASET_RANGE as SEMANTIC_DATASET_RANGE

HOLDOUT_SUITE_VERSION = "strict-model-v3-holdout-30"
HOLDOUT_DATASET_RANGE = SEMANTIC_DATASET_RANGE

PERIODS = {
    "jan": ("上个月", "2026-01-01", "2026-01-31"),
    "feb": ("本月", "2026-02-01", "2026-02-28"),
    "last30": ("近30天", "2026-01-30", "2026-02-28"),
    "last90": ("近90天", "2025-12-01", "2026-02-28"),
}


def _case(
    case_id: str,
    question: str,
    intent: str,
    reference_sql: str,
    metric: str = "销售额",
    dimension: str = "",
    order: str = "",
    setup: str = "",
    expected_time_range: str = "",
) -> dict[str, Any]:
    return {
        "id": case_id,
        "question": question,
        "intent": intent,
        "reference_sql": reference_sql,
        "metric": metric,
        "dimension": dimension,
        "order": order,
        "setup": setup,
        "expected_time_range": expected_time_range,
    }


def _period(period_id: str) -> tuple[str, str]:
    _, start, end = PERIODS[period_id]
    return start, end


def _sales_sql(start: str, end: str, filters: str = "", dimension: str = "") -> str:
    select = f"o.{dimension} AS {dimension}, " if dimension else ""
    group = f" GROUP BY o.{dimension} ORDER BY 销售额 DESC" if dimension else ""
    extra = f" AND {filters}" if filters else ""
    return (
        f"SELECT {select}ROUND(SUM(o.paid_amount), 2) AS 销售额 "
        "FROM orders o WHERE o.status = 'paid' "
        f"AND o.order_date BETWEEN '{start}' AND '{end}'{extra}{group}"
    )


def _aov_sql(start: str, end: str, filters: str = "", dimension: str = "") -> str:
    select = f"o.{dimension} AS {dimension}, " if dimension else ""
    group = f" GROUP BY o.{dimension} ORDER BY 客单价 DESC" if dimension else ""
    extra = f" AND {filters}" if filters else ""
    return (
        f"SELECT {select}ROUND(AVG(o.paid_amount), 2) AS 客单价 "
        "FROM orders o WHERE o.status = 'paid' "
        f"AND o.order_date BETWEEN '{start}' AND '{end}'{extra}{group}"
    )


def _units_sql(start: str, end: str, filters: str = "", dimension: str = "") -> str:
    select = "oi.category AS category, " if dimension == "category" else ""
    group = " GROUP BY oi.category ORDER BY 销量 DESC" if dimension == "category" else ""
    extra = f" AND {filters}" if filters else ""
    return (
        f"SELECT {select}SUM(oi.quantity) AS 销量 "
        "FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
        "WHERE o.status = 'paid' "
        f"AND o.order_date BETWEEN '{start}' AND '{end}'{extra}{group}"
    )


def _refund_rate_sql(start: str, end: str, filters: str = "") -> str:
    extra = f" AND {filters}" if filters else ""
    return (
        "SELECT o.category AS category, "
        "ROUND(COUNT(DISTINCT r.refund_id) * 100.0 / COUNT(DISTINCT o.order_id), 2) AS 退款率 "
        "FROM orders o LEFT JOIN refunds r ON r.order_id = o.order_id "
        f"WHERE o.order_date BETWEEN '{start}' AND '{end}'{extra} "
        "GROUP BY o.category ORDER BY 退款率 DESC"
    )


def _refund_amount_sql(start: str, end: str, filters: str = "") -> str:
    extra = f" AND {filters}" if filters else ""
    return (
        "SELECT r.reason AS reason, ROUND(SUM(r.refund_amount), 2) AS 退款金额 "
        "FROM refunds r JOIN orders o ON o.order_id = r.order_id "
        f"WHERE o.order_date BETWEEN '{start}' AND '{end}'{extra} "
        "GROUP BY r.reason ORDER BY 退款金额 DESC"
    )


def _refund_count_sql(start: str, end: str, dimension: str, filters: str = "") -> str:
    column = "r.reason" if dimension == "reason" else "o.category"
    extra = f" AND {filters}" if filters else ""
    return (
        f"SELECT {column} AS {dimension}, COUNT(*) AS 退款单数 "
        "FROM refunds r JOIN orders o ON o.order_id = r.order_id "
        f"WHERE o.order_date BETWEEN '{start}' AND '{end}'{extra} "
        f"GROUP BY {column} ORDER BY 退款单数 DESC"
    )


def _roi_sql(start: str, end: str) -> str:
    return (
        "SELECT m.channel AS channel, ROUND(s.revenue / m.spend, 2) AS ROI "
        "FROM (SELECT channel, SUM(spend) AS spend FROM marketing_spend "
        f"WHERE spend_date BETWEEN '{start}' AND '{end}' GROUP BY channel) m "
        "LEFT JOIN (SELECT channel, SUM(paid_amount) AS revenue FROM orders "
        f"WHERE status = 'paid' AND order_date BETWEEN '{start}' AND '{end}' "
        "GROUP BY channel) s ON s.channel = m.channel ORDER BY ROI DESC"
    )


def _daily_sql(start: str, end: str, filters: str = "") -> str:
    extra = f" AND {filters}" if filters else ""
    return (
        "SELECT o.order_date AS order_date, ROUND(SUM(o.paid_amount), 2) AS 销售额 "
        "FROM orders o WHERE o.status = 'paid' "
        f"AND o.order_date BETWEEN '{start}' AND '{end}'{extra} "
        "GROUP BY o.order_date ORDER BY o.order_date"
    )


def _holdout_cases() -> tuple[dict[str, Any], ...]:
    jan_start, jan_end = _period("jan")
    feb_start, feb_end = _period("feb")
    last30_start, last30_end = _period("last30")
    last90_start, last90_end = _period("last90")
    return (
        _case("holdout-sales-jan-east-miniprogram", "上个月华东地区MiniProgram渠道已支付订单销售额是多少？", "sales_overview",
              _sales_sql(jan_start, jan_end, "o.region = '华东' AND o.channel = 'MiniProgram'")),
        _case("holdout-sales-feb-south-web", "本月华南地区Web渠道已支付订单销售额是多少？", "sales_overview",
              _sales_sql(feb_start, feb_end, "o.region = '华南' AND o.channel = 'Web'")),
        _case("holdout-sales-last30-north-douyin", "近30天华北地区抖音渠道已支付订单销售额是多少？", "sales_overview",
              _sales_sql(last30_start, last30_end, "o.region = '华北' AND o.channel = 'Douyin'")),
        _case("holdout-sales-last90-south-app", "近90天华南地区App渠道已支付订单销售额是多少？", "sales_overview",
              _sales_sql(last90_start, last90_end, "o.region = '华南' AND o.channel = 'App'")),
        _case("holdout-sales-group-feb-east-channel", "本月华东地区各渠道已支付订单销售额，按销售额降序", "sales_overview",
              _sales_sql(feb_start, feb_end, "o.region = '华东'", "channel"), dimension="channel", order="desc"),
        _case("holdout-sales-group-last30-south-channel", "近30天华南地区各渠道已支付订单销售额，按销售额降序", "sales_overview",
              _sales_sql(last30_start, last30_end, "o.region = '华南'", "channel"), dimension="channel", order="desc"),
        _case("holdout-sales-group-last90-north-channel", "近90天华北地区各渠道已支付订单销售额，按销售额降序", "sales_overview",
              _sales_sql(last90_start, last90_end, "o.region = '华北'", "channel"), dimension="channel", order="desc"),
        _case("holdout-sales-group-last90-app-region", "近90天App渠道各地区已支付订单销售额，按销售额降序", "sales_overview",
              _sales_sql(last90_start, last90_end, "o.channel = 'App'", "region"), dimension="region", order="desc"),
        _case("holdout-sales-group-feb-douyin-region", "本月抖音渠道各地区已支付订单销售额，按销售额降序", "sales_overview",
              _sales_sql(feb_start, feb_end, "o.channel = 'Douyin'", "region"), dimension="region", order="desc"),
        _case("holdout-aov-last30-south", "近30天华南地区已支付订单客单价是多少？", "sales_overview",
              _aov_sql(last30_start, last30_end, "o.region = '华南'"), "客单价"),
        _case("holdout-aov-jan-east", "上个月华东地区已支付订单客单价是多少？", "sales_overview",
              _aov_sql(jan_start, jan_end, "o.region = '华东'"), "客单价"),
        _case("holdout-aov-feb-east-channel", "本月华东地区各渠道已支付订单客单价，按客单价降序", "sales_overview",
              _aov_sql(feb_start, feb_end, "o.region = '华东'", "channel"), "客单价", "channel", "desc"),
        _case("holdout-aov-last90-north-channel", "近90天华北地区各渠道已支付订单客单价，按客单价降序", "sales_overview",
              _aov_sql(last90_start, last90_end, "o.region = '华北'", "channel"), "客单价", "channel", "desc"),
        _case("holdout-units-feb-east-miniprogram-shoes", "本月华东地区MiniProgram渠道运动鞋服商品行的已支付商品销量是多少？按商品明细件数统计", "category_analysis",
              _units_sql(feb_start, feb_end, "o.region = '华东' AND o.channel = 'MiniProgram' AND oi.category = '运动鞋服'"), "销量"),
        _case("holdout-units-last90-south-web-category", "近90天华南地区Web渠道各品类已支付商品销量，按商品行品类汇总件数，销量降序", "category_analysis",
              _units_sql(last90_start, last90_end, "o.region = '华南' AND o.channel = 'Web'", "category"), "销量", "category", "desc"),
        _case("holdout-units-jan-north-douyin-category", "上个月华北地区抖音渠道各品类已支付商品销量，按商品行品类汇总件数，销量降序", "category_analysis",
              _units_sql(jan_start, jan_end, "o.region = '华北' AND o.channel = 'Douyin'", "category"), "销量", "category", "desc"),
        _case("holdout-refund-rate-jan-south-category", "上个月华南地区各品类退款率，退款申请单数除以全部订单数乘100，退款率降序", "refund_analysis",
              _refund_rate_sql(jan_start, jan_end, "o.region = '华南'"), "退款率", "category", "desc"),
        _case("holdout-refund-rate-last90-north-category", "近90天华北地区各品类退款率，退款申请单数除以全部订单数乘100，退款率降序", "refund_analysis",
              _refund_rate_sql(last90_start, last90_end, "o.region = '华北'"), "退款率", "category", "desc"),
        _case("holdout-refund-amount-last30-douyin-reason", "近30天抖音渠道各退款原因的退款金额，按退款金额降序", "refund_analysis",
              _refund_amount_sql(last30_start, last30_end, "o.channel = 'Douyin'"), "退款金额", "reason", "desc"),
        _case("holdout-refund-amount-feb-east-reason", "本月华东地区各退款原因的退款金额，按退款金额降序", "refund_analysis",
              _refund_amount_sql(feb_start, feb_end, "o.region = '华东'"), "退款金额", "reason", "desc"),
        _case("holdout-refund-count-feb-north-category", "本月华北地区各品类退款单数，退款申请单数降序", "refund_analysis",
              _refund_count_sql(feb_start, feb_end, "category", "o.region = '华北'"), "退款单数", "category", "desc"),
        _case("holdout-refund-count-jan-web-reason", "上个月Web渠道各退款原因的退款单数，退款申请单数降序", "refund_analysis",
              _refund_count_sql(jan_start, jan_end, "reason", "o.channel = 'Web'"), "退款单数", "reason", "desc"),
        _case("holdout-roi-feb-all-channel", "本月各渠道ROI：已支付销售额除以投放花费，两表各自按渠道汇总后计算，按ROI降序", "marketing_efficiency",
              _roi_sql(feb_start, feb_end), "ROI", "channel", "desc"),
        _case("holdout-roi-last30-all-channel", "近30天各渠道ROI：已支付销售额除以投放花费，两表各自按渠道汇总后计算，按ROI降序", "marketing_efficiency",
              _roi_sql(last30_start, last30_end), "ROI", "channel", "desc"),
        _case("holdout-roi-last90-all-channel", "近90天各渠道ROI：已支付销售额除以投放花费，两表各自按渠道汇总后计算，按ROI降序", "marketing_efficiency",
              _roi_sql(last90_start, last90_end), "ROI", "channel", "desc"),
        _case("holdout-daily-last30-east-web", "近30天华东地区Web渠道按日查看已支付订单销售额趋势，日期升序", "sales_overview",
              _daily_sql(last30_start, last30_end, "o.region = '华东' AND o.channel = 'Web'"), "销售额", "order_date", "date"),
        _case("holdout-daily-jan-south-app", "上个月华南地区App渠道按日查看已支付订单销售额趋势，日期升序", "sales_overview",
              _daily_sql(jan_start, jan_end, "o.region = '华南' AND o.channel = 'App'"), "销售额", "order_date", "date"),
        _case("holdout-followup-sales-west-miniprogram", "保留上个月和MiniProgram渠道，改成西南地区呢？", "sales_overview",
              _sales_sql(jan_start, jan_end, "o.region = '西南' AND o.channel = 'MiniProgram'"),
              setup="holdout-sales-jan-east-miniprogram"),
        _case("holdout-followup-roi-jan", "那上个月呢？", "marketing_efficiency",
              _roi_sql(jan_start, jan_end), "ROI", "channel", "desc",
              setup="holdout-roi-feb-all-channel",
              expected_time_range="2026-01-01 至 2026-01-31"),
        _case("holdout-sales-last30-east-web", "近30天华东地区Web渠道已支付订单销售额是多少？", "sales_overview",
              _sales_sql(last30_start, last30_end, "o.region = '华东' AND o.channel = 'Web'")),
    )


HOLDOUT_CASES = _holdout_cases()

# Independent natural-language cases. These check whether the system
# understands wording deliberately avoided while tuning the fixed v3 suites.
# Each answered case is validated end to end against scope, plan, retrieval and
# executed rows. Clarification cases must produce no SQL or rows.
NATURAL_HOLDOUT_CASES = (
    {"id": "natural-channel-best", "question": "哪个渠道最赚钱？", "expected_tables": {"orders"}, "expected_intent": "sales_overview", "expected_metric": "销售额", "expected_outcome": "answered"},
    {"id": "natural-ad-return", "question": "广告花的钱值不值？", "expected_tables": {"marketing_spend", "orders"}, "expected_intent": "marketing_efficiency", "expected_metric": "ROI", "expected_outcome": "answered"},
    {"id": "natural-refund-cause", "question": "退款主要是哪些原因？", "expected_tables": {"refunds", "orders"}, "expected_intent": "refund_analysis", "expected_metric": "退款金额", "expected_outcome": "answered"},
    {"id": "natural-category-volume", "question": "什么类目卖得最多？", "expected_tables": {"order_items", "orders"}, "expected_intent": "category_analysis", "expected_metric": "销量", "expected_outcome": "answered"},
    {"id": "natural-membership", "question": "会员复购情况怎么样？", "expected_tables": {"customers", "orders"}, "expected_outcome": "clarification"},
    {"id": "natural-aov", "question": "每单平均花多少钱？", "expected_tables": {"orders"}, "expected_intent": "sales_overview", "expected_metric": "客单价", "expected_outcome": "answered"},
    {"id": "natural-region-gap", "question": "华东和西南的生意差多少？", "expected_tables": {"orders"}, "expected_outcome": "clarification"},
    {"id": "natural-daily", "question": "这个月每天卖得怎么样？", "expected_tables": {"orders"}, "expected_intent": "sales_overview", "expected_metric": "销售额", "expected_outcome": "answered", "expected_time_range": "2026-02-01 至 2026-02-28"},
    {"id": "natural-refund-money", "question": "退的钱都花在哪些原因上？", "expected_tables": {"refunds", "orders"}, "expected_intent": "refund_analysis", "expected_metric": "退款金额", "expected_outcome": "answered"},
    {"id": "natural-ads", "question": "广告曝光和点击效果如何？", "expected_tables": {"marketing_spend"}, "expected_outcome": "clarification"},
    {"id": "natural-conversion", "question": "点击之后到底有多少人下单？", "expected_tables": {"marketing_spend", "orders"}, "expected_outcome": "clarification"},
    {"id": "natural-city", "question": "客户都来自哪些城市？", "expected_tables": {"customers"}, "expected_outcome": "clarification"},
    {"id": "natural-refund-ratio", "question": "售后问题占比大吗？", "expected_tables": {"refunds", "orders"}, "expected_intent": "refund_analysis", "expected_metric": "退款率", "expected_outcome": "answered"},
    {"id": "natural-low-price", "question": "哪些商品卖得多但单价低？", "expected_tables": {"order_items", "orders"}, "expected_outcome": "clarification"},
    {"id": "natural-new-customer", "question": "新客户和老客户谁贡献更多？", "expected_tables": {"customers", "orders"}, "expected_outcome": "clarification"},
)


# Colloquial suite: everyday wording without formulas, grain instructions,
# status enums or sort directions. Each answered item has an independent
# reference SQL and a single reasonable business interpretation. Items whose
# metric or grain cannot be inferred uniquely are labelled as clarifications.
COLLOQUIAL_CASES = (
    {
        "id": "colloquial-channel-sales",
        "question": "近90天销售额最高的渠道是哪个？",
        "expected_tables": {"orders"},
        "expected_intent": "sales_overview",
        "expected_metric": "销售额",
        "expected_outcome": "answered",
        "dimension": "channel",
        "order": "desc",
        "reference_sql": _sales_sql(
            _period("last90")[0], _period("last90")[1], dimension="channel"
        ) + " LIMIT 1",
    },
    {
        "id": "colloquial-region-sales",
        "question": "近90天销售额最高的地区是哪个？",
        "expected_tables": {"orders"},
        "expected_intent": "sales_overview",
        "expected_metric": "销售额",
        "expected_outcome": "answered",
        "dimension": "region",
        "order": "desc",
        "reference_sql": _sales_sql(
            _period("last90")[0], _period("last90")[1], dimension="region"
        ) + " LIMIT 1",
    },
    {
        "id": "colloquial-category-sales",
        "question": "近90天销售额最高的品类是哪个？",
        "expected_tables": {"orders"},
        "expected_intent": "category_analysis",
        "expected_metric": "销售额",
        "expected_outcome": "answered",
        "dimension": "category",
        "order": "desc",
        "reference_sql": _sales_sql(
            _period("last90")[0], _period("last90")[1], dimension="category"
        ) + " LIMIT 1",
    },
    {
        "id": "colloquial-refund-reason",
        "question": "近90天因为退款损失的钱主要是什么原因？",
        "expected_tables": {"orders", "refunds"},
        "expected_intent": "refund_analysis",
        "expected_metric": "退款金额",
        "expected_outcome": "answered",
        "dimension": "reason",
        "order": "desc",
        "reference_sql": _refund_amount_sql(
            _period("last90")[0], _period("last90")[1]
        ),
    },
    {
        "id": "colloquial-roi-channel",
        "question": "近90天投放回报最高的渠道是哪个？",
        "expected_tables": {"orders", "marketing_spend"},
        "expected_intent": "marketing_efficiency",
        "expected_metric": "ROI",
        "expected_outcome": "answered",
        "dimension": "channel",
        "order": "desc",
        "reference_sql": _roi_sql(
            _period("last90")[0], _period("last90")[1]
        ) + " LIMIT 1",
    },
    {
        "id": "colloquial-aov",
        "question": "近90天用户每单大概花多少钱？",
        "expected_tables": {"orders"},
        "expected_intent": "sales_overview",
        "expected_metric": "客单价",
        "expected_outcome": "answered",
        "reference_sql": _aov_sql(
            _period("last90")[0], _period("last90")[1]
        ),
    },
    {
        "id": "colloquial-daily",
        "question": "上个月每天卖得怎么样？",
        "expected_tables": {"orders"},
        "expected_intent": "sales_overview",
        "expected_metric": "销售额",
        "expected_outcome": "answered",
        "dimension": "order_date",
        "order": "date",
        "reference_sql": _daily_sql(
            _period("jan")[0], _period("jan")[1]
        ),
    },
    {
        "id": "colloquial-refund-count",
        "question": "上个月各品类退款有多少单？",
        "expected_tables": {"orders", "refunds"},
        "expected_intent": "refund_analysis",
        "expected_metric": "退款单数",
        "expected_outcome": "answered",
        "dimension": "category",
        "order": "desc",
        "reference_sql": _refund_count_sql(
            _period("jan")[0], _period("jan")[1], "category"
        ),
    },
    {
        "id": "colloquial-units-category",
        "question": "上个月各品类卖出去多少件？",
        "expected_tables": {"orders", "order_items"},
        "expected_intent": "category_analysis",
        "expected_metric": "销量",
        "expected_outcome": "answered",
        "dimension": "category",
        "order": "desc",
        "reference_sql": _units_sql(
            _period("jan")[0], _period("jan")[1], dimension="category"
        ),
    },
    {
        "id": "colloquial-vague-business",
        "question": "最近生意怎么样？",
        "expected_tables": set(),
        "expected_outcome": "clarification",
    },
)
