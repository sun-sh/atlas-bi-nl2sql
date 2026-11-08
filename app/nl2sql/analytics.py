from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.nl2sql.settings import get_settings
from app.nl2sql.sql_engine import QueryTarget, adapter_for


def overview_metrics(db_path: str | Path | QueryTarget | None = None) -> dict[str, Any]:
    settings = get_settings()
    target = (
        db_path
        if isinstance(db_path, QueryTarget)
        else QueryTarget("sqlite", "sqlite", Path(db_path) if db_path else settings.db_path)
    )
    adapter = adapter_for(target)
    def rows_for(sql: str) -> list[dict[str, Any]]:
        return adapter.execute(
            sql,
            limit=settings.max_rows,
            timeout_seconds=settings.query_timeout_seconds,
        )["rows"]

    row = rows_for(
        """
        SELECT COUNT(*) AS orders, ROUND(SUM(paid_amount), 2) AS revenue,
               ROUND(AVG(paid_amount), 2) AS aov,
               COUNT(DISTINCT customer_id) AS customers
        FROM orders WHERE status = 'paid'
        """
    )[0]
    refund = rows_for(
        "SELECT COUNT(*) AS refund_orders, ROUND(SUM(refund_amount), 2) AS refund_amount "
        "FROM refunds WHERE status = 'approved'"
    )[0]
    spend = rows_for("SELECT ROUND(SUM(spend), 2) AS spend FROM marketing_spend")[0]
    trend = rows_for(
        "SELECT order_date AS date, ROUND(SUM(paid_amount), 2) AS revenue "
        "FROM orders WHERE status = 'paid' AND order_date >= '2026-02-01' "
        "GROUP BY order_date ORDER BY order_date"
    )
    channel = rows_for(
        "SELECT channel, ROUND(SUM(paid_amount), 2) AS revenue, COUNT(*) AS orders "
        "FROM orders WHERE status = 'paid' GROUP BY channel ORDER BY revenue DESC"
    )
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
        "trend": [dict(item) for item in trend],
        "channel": [dict(item) for item in channel],
        "adapter": target.as_dict(),
    }
