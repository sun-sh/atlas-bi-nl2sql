from __future__ import annotations

from pathlib import Path
from typing import Any

from app.nl2sql.semantic import DATASET_END, DATASET_START, REFERENCE_DATE, metric_filters, metric_sql
from app.nl2sql.settings import get_settings
from app.nl2sql.sql_engine import QueryTarget, adapter_for


def _where(*metric_names: str) -> str:
    predicates: list[str] = []
    for metric_name in metric_names:
        for predicate in metric_filters(metric_name):
            if predicate not in predicates:
                predicates.append(predicate)
    return " WHERE " + " AND ".join(predicates) if predicates else ""


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

    sales_where = _where("销售额")
    row = rows_for(
        f"""
        SELECT COUNT(*) AS orders,
               {metric_sql('销售额')} AS revenue,
               {metric_sql('客单价')} AS aov,
               {metric_sql('客户数')} AS customers
        FROM orders o{sales_where}
        """
    )[0]
    refund = rows_for(
        f"SELECT {metric_sql('已通过退款单数')} AS refund_orders, "
        f"{metric_sql('已通过退款金额')} AS refund_amount "
        f"FROM refunds r{_where('已通过退款单数', '已通过退款金额')}"
    )[0]
    spend = rows_for(
        f"SELECT {metric_sql('投放花费')} AS spend FROM marketing_spend m"
    )[0]
    trend_start = REFERENCE_DATE.replace(day=1)
    trend = rows_for(
        f"SELECT o.order_date AS date, {metric_sql('销售额')} AS revenue "
        f"FROM orders o{sales_where} AND o.order_date >= '{trend_start.isoformat()}' "
        "GROUP BY o.order_date ORDER BY o.order_date"
    )
    channel = rows_for(
        f"SELECT o.channel AS channel, {metric_sql('销售额')} AS revenue, "
        f"{metric_sql('订单数')} AS orders "
        f"FROM orders o{sales_where} GROUP BY o.channel ORDER BY revenue DESC"
    )
    revenue = float(row["revenue"] or 0)
    spend_value = float(spend["spend"] or 0)
    channel_rows = [dict(item) for item in channel]

    def computed_metric(name: str, **values: float) -> float:
        expr = metric_sql(
            name, values={key: repr(value) for key, value in values.items()}
        )
        return float(rows_for(f"SELECT {expr} AS value")[0]["value"] or 0)

    return {
        "revenue": revenue,
        "orders": int(row["orders"] or 0),
        "aov": float(row["aov"] or 0),
        "customers": int(row["customers"] or 0),
        "refund_orders": int(refund["refund_orders"] or 0),
        "refund_amount": float(refund["refund_amount"] or 0),
        "refund_rate": computed_metric(
            "概览退款率",
            refund_count=float(refund["refund_orders"] or 0),
            order_count=float(row["orders"] or 0),
        ),
        "spend": spend_value,
        "blended_roi": computed_metric(
            "综合ROI", revenue=revenue, spend=spend_value
        ),
        "trend": [dict(item) for item in trend],
        "channel": channel_rows,
        "channel_metric": "销售额",
        "dataset": {"start": DATASET_START, "end": DATASET_END},
        "adapter": target.as_dict(),
    }
