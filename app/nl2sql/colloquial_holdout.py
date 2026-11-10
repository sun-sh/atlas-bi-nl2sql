"""Frozen colloquial holdout authored before retrieval/planner changes.

Do not edit these cases while tuning keyword rules. The suite is intentionally
small and fixed-domain; it is a regression snapshot, not an open-domain score.
"""

from __future__ import annotations

from typing import Any

COLLOQUIAL_HOLDOUT_SUITE_VERSION = "colloquial-frozen-v3"
COLLOQUIAL_HOLDOUT_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "colloquial-v3-channel-revenue",
        "question": "过去三个月，按渠道拆开后销售额最高的是谁？",
        "expected_tables": {"orders"},
        "expected_intent": "sales_overview",
        "expected_metric": "销售额",
        "expected_outcome": "answered",
        "dimension": "channel",
        "order": "desc",
        "reference_sql": "SELECT o.channel AS channel, ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o WHERE o.status = 'paid' AND o.order_date BETWEEN '2025-12-01' AND '2026-02-28' GROUP BY o.channel ORDER BY 销售额 DESC LIMIT 1",
    },
    {
        "id": "colloquial-v3-region-revenue",
        "question": "过去三个月，按地区拆开后销售额最高的是谁？",
        "expected_tables": {"orders"},
        "expected_intent": "sales_overview",
        "expected_metric": "销售额",
        "expected_outcome": "answered",
        "dimension": "region",
        "order": "desc",
        "reference_sql": "SELECT o.region AS region, ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o WHERE o.status = 'paid' AND o.order_date BETWEEN '2025-12-01' AND '2026-02-28' GROUP BY o.region ORDER BY 销售额 DESC LIMIT 1",
    },
    {
        "id": "colloquial-v3-category-revenue",
        "question": "过去三个月，按商品大类看销售额排名如何？",
        "expected_tables": {"orders"},
        "expected_intent": "category_analysis",
        "expected_metric": "销售额",
        "expected_outcome": "answered",
        "dimension": "category",
        "order": "desc",
        "reference_sql": "SELECT o.category AS category, ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o WHERE o.status = 'paid' AND o.order_date BETWEEN '2025-12-01' AND '2026-02-28' GROUP BY o.category ORDER BY 销售额 DESC",
    },
    {
        "id": "colloquial-v3-refund-reason",
        "question": "过去三个月，按退款原因看退款金额排名如何？",
        "expected_tables": {"orders", "refunds"},
        "expected_intent": "refund_analysis",
        "expected_metric": "退款金额",
        "expected_outcome": "answered",
        "dimension": "reason",
        "order": "desc",
        "reference_sql": "SELECT r.reason AS reason, ROUND(SUM(r.refund_amount), 2) AS 退款金额 FROM refunds r JOIN orders o ON o.order_id = r.order_id WHERE o.order_date BETWEEN '2025-12-01' AND '2026-02-28' GROUP BY r.reason ORDER BY 退款金额 DESC",
    },
    {
        "id": "colloquial-v3-roi-channel",
        "question": "过去三个月，按投放渠道看ROI排名如何？",
        "expected_tables": {"orders", "marketing_spend"},
        "expected_intent": "marketing_efficiency",
        "expected_metric": "ROI",
        "expected_outcome": "answered",
        "dimension": "channel",
        "order": "desc",
        "reference_sql": "SELECT m.channel AS channel, ROUND(s.revenue / m.spend, 2) AS ROI FROM (SELECT channel, SUM(spend) AS spend FROM marketing_spend WHERE spend_date BETWEEN '2025-12-01' AND '2026-02-28' GROUP BY channel) m LEFT JOIN (SELECT channel, SUM(paid_amount) AS revenue FROM orders WHERE status = 'paid' AND order_date BETWEEN '2025-12-01' AND '2026-02-28' GROUP BY channel) s ON s.channel = m.channel ORDER BY ROI DESC",
    },
    {
        "id": "colloquial-v3-aov",
        "question": "过去三个月，一个订单平均实际付了多少钱？",
        "expected_tables": {"orders"},
        "expected_intent": "sales_overview",
        "expected_metric": "客单价",
        "expected_outcome": "answered",
        "dimension": "",
        "order": "",
        "reference_sql": "SELECT ROUND(AVG(o.paid_amount), 2) AS 客单价 FROM orders o WHERE o.status = 'paid' AND o.order_date BETWEEN '2025-12-01' AND '2026-02-28'",
    },
    {
        "id": "colloquial-v3-daily-sales",
        "question": "上个月按天看，每天的销售额走势如何？",
        "expected_tables": {"orders"},
        "expected_intent": "sales_overview",
        "expected_metric": "销售额",
        "expected_outcome": "answered",
        "dimension": "order_date",
        "order": "date",
        "reference_sql": "SELECT o.order_date AS order_date, ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o WHERE o.status = 'paid' AND o.order_date BETWEEN '2026-01-01' AND '2026-01-31' GROUP BY o.order_date ORDER BY o.order_date",
    },
    {
        "id": "colloquial-v3-refund-requests",
        "question": "上个月按退款原因统计，退款申请分别有多少笔？",
        "expected_tables": {"orders", "refunds"},
        "expected_intent": "refund_analysis",
        "expected_metric": "退款单数",
        "expected_outcome": "answered",
        "dimension": "reason",
        "order": "desc",
        "reference_sql": "SELECT r.reason AS reason, COUNT(*) AS 退款单数 FROM refunds r JOIN orders o ON o.order_id = r.order_id WHERE o.order_date BETWEEN '2026-01-01' AND '2026-01-31' GROUP BY r.reason ORDER BY 退款单数 DESC",
    },
    {
        "id": "colloquial-v3-item-units",
        "question": "上个月按品类统计，商品售出件数排名如何？",
        "expected_tables": {"orders", "order_items"},
        "expected_intent": "category_analysis",
        "expected_metric": "销量",
        "expected_outcome": "answered",
        "dimension": "category",
        "order": "desc",
        "reference_sql": "SELECT oi.category AS category, SUM(oi.quantity) AS 销量 FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE o.status = 'paid' AND o.order_date BETWEEN '2026-01-01' AND '2026-01-31' GROUP BY oi.category ORDER BY 销量 DESC",
    },
    {
        "id": "colloquial-v3-refund-rate",
        "question": "上个月按品类看，退款申请占全部订单的比例是多少？",
        "expected_tables": {"orders", "refunds"},
        "expected_intent": "refund_analysis",
        "expected_metric": "退款率",
        "expected_outcome": "answered",
        "dimension": "category",
        "order": "desc",
        "reference_sql": "SELECT o.category AS category, ROUND(COUNT(DISTINCT r.refund_id) * 100.0 / COUNT(DISTINCT o.order_id), 2) AS 退款率 FROM orders o LEFT JOIN refunds r ON r.order_id = o.order_id WHERE o.order_date BETWEEN '2026-01-01' AND '2026-01-31' GROUP BY o.category ORDER BY 退款率 DESC",
    },
    {
        "id": "colloquial-v3-ambiguous-product",
        "question": "哪个商品最受欢迎？",
        "expected_tables": set(),
        "expected_outcome": "clarification",
    },
    {
        "id": "colloquial-v3-ambiguous-refund",
        "question": "退款问题严重吗？",
        "expected_tables": set(),
        "expected_outcome": "clarification",
    },
    {
        "id": "colloquial-v3-ambiguous-business",
        "question": "最近经营哪里最拖后腿？",
        "expected_tables": set(),
        "expected_outcome": "clarification",
    },
)

COLLOQUIAL_HOLDOUT_MANIFEST = tuple(
    {
        "id": case["id"],
        "question": case["question"],
        "expected_outcome": case["expected_outcome"],
        "reference_sql": case.get("reference_sql", ""),
    }
    for case in COLLOQUIAL_HOLDOUT_CASES
)
