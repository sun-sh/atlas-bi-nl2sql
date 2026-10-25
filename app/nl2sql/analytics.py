from __future__ import annotations

import sqlite3
from typing import Any

from app.nl2sql.settings import get_settings


def overview_metrics() -> dict[str, Any]:
    settings = get_settings()
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS orders,
                ROUND(SUM(paid_amount), 2) AS revenue,
                ROUND(AVG(paid_amount), 2) AS aov,
                ROUND(COUNT(DISTINCT customer_id), 0) AS customers
            FROM orders WHERE status = 'paid'
            """
        ).fetchone()
        refund = conn.execute(
            """
            SELECT
                COUNT(*) AS refund_orders,
                ROUND(SUM(r.refund_amount), 2) AS refund_amount
            FROM refunds r WHERE r.status = 'approved'
            """
        ).fetchone()
        spend = conn.execute("SELECT ROUND(SUM(spend), 2) AS spend FROM marketing_spend").fetchone()
        trend = conn.execute(
            """
            SELECT order_date AS date, ROUND(SUM(paid_amount), 2) AS revenue
            FROM orders WHERE status = 'paid' AND order_date >= '2026-02-01'
            GROUP BY order_date ORDER BY order_date
            """
        ).fetchall()
        channel = conn.execute(
            """
            SELECT channel, ROUND(SUM(paid_amount), 2) AS revenue, COUNT(*) AS orders
            FROM orders WHERE status = 'paid' GROUP BY channel ORDER BY revenue DESC
            """
        ).fetchall()
    finally:
        conn.close()
    revenue = float(row["revenue"] or 0)
    spend_value = float(spend["spend"] or 0)
    return {
        "revenue": revenue,
        "orders": int(row["orders"] or 0),
        "aov": float(row["aov"] or 0),
        "customers": int(row["customers"] or 0),
        "refund_orders": int(refund["refund_orders"] or 0),
        "refund_amount": float(refund["refund_amount"] or 0),
        "refund_rate": round((refund["refund_orders"] or 0) * 100 / max(1, row["orders"] or 1), 2),
        "spend": spend_value,
        "blended_roi": round(revenue / spend_value, 2) if spend_value else 0,
        "trend": [dict(r) for r in trend],
        "channel": [dict(r) for r in channel],
    }
