from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.nl2sql.schema_catalog import SCHEMA
from app.nl2sql.seed import generate_database
from app.nl2sql.settings import get_settings


DATASET_START = "2025-10-01"
DATASET_END = "2026-02-28"

TABLE_PROFILES: dict[str, dict[str, Any]] = {
    "orders": {"key": ["order_id"], "date_column": "order_date"},
    "order_items": {"key": ["order_id", "line_no"], "date_column": None, "date_parent": ("orders", "order_id")},
    "refunds": {"key": ["refund_id"], "date_column": "request_date"},
    "marketing_spend": {"key": ["spend_date", "channel"], "date_column": "spend_date"},
    "customers": {"key": ["customer_id"], "date_column": "register_date"},
}

RELATIONSHIPS = (
    {"source": "order_items", "source_column": "order_id", "target": "orders", "target_column": "order_id", "description": "商品行必须归属有效订单"},
    {"source": "refunds", "source_column": "order_id", "target": "orders", "target_column": "order_id", "description": "退款申请必须关联有效订单"},
    {"source": "orders", "source_column": "customer_id", "target": "customers", "target_column": "customer_id", "description": "订单必须关联有效客户"},
)

METRICS = (
    {"name": "销售额", "grain": "订单", "definition": "SUM(orders.paid_amount)，仅统计 paid 订单"},
    {"name": "客单价", "grain": "订单", "definition": "AVG(orders.paid_amount)，仅统计 paid 订单"},
    {"name": "退款率", "grain": "订单 / 品类", "definition": "退款申请数 / 全部订单数 × 100%"},
    {"name": "退款金额", "grain": "退款申请", "definition": "SUM(refunds.refund_amount)"},
    {"name": "ROI", "grain": "渠道", "definition": "已支付销售额 / 投放花费，两表分别聚合后计算"},
    {"name": "商品销量", "grain": "商品行", "definition": "SUM(order_items.quantity)，仅统计 paid 订单"},
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect() -> sqlite3.Connection:
    settings = get_settings()
    if not settings.db_path.exists():
        generate_database(settings.db_path)
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _scalar(conn: sqlite3.Connection, sql: str) -> Any:
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


def _duplicate_key_groups(conn: sqlite3.Connection, table: str, keys: list[str]) -> int:
    columns = ", ".join(keys)
    row = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT COUNT(*) AS n FROM {table} "
        f"GROUP BY {columns} HAVING COUNT(*) > 1)"
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _null_count(conn: sqlite3.Connection, table: str, column: str) -> int:
    return int(_scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL") or 0)


def _orphan_count(conn: sqlite3.Connection, relation: dict[str, str]) -> int:
    source, source_column = relation["source"], relation["source_column"]
    target, target_column = relation["target"], relation["target_column"]
    return int(_scalar(
        conn,
        f"SELECT COUNT(*) FROM {source} s LEFT JOIN {target} t "
        f"ON t.{target_column} = s.{source_column} "
        f"WHERE s.{source_column} IS NOT NULL AND t.{target_column} IS NULL",
    ) or 0)


def _date_range(conn: sqlite3.Connection, table: str, profile: dict[str, Any]) -> dict[str, str | None]:
    column = profile.get("date_column")
    if column:
        row = conn.execute(f"SELECT MIN({column}) AS min_date, MAX({column}) AS max_date FROM {table}").fetchone()
        return {"start": row["min_date"], "end": row["max_date"]} if row else {"start": None, "end": None}
    parent = profile.get("date_parent")
    if parent:
        parent_table, parent_key = parent
        row = conn.execute(
            f"SELECT MIN(p.order_date) AS min_date, MAX(p.order_date) AS max_date "
            f"FROM {table} s JOIN {parent_table} p ON p.{parent_key} = s.{parent_key}"
        ).fetchone()
        return {"start": row["min_date"], "end": row["max_date"]} if row else {"start": None, "end": None}
    return {"start": None, "end": None}


def _check(check_id: str, label: str, value: int | str | None, *, passed: bool) -> dict[str, Any]:
    return {"id": check_id, "label": label, "value": value, "severity": "error", "passed": bool(passed)}


def data_assets_overview() -> dict[str, Any]:
    """Return the project-level data inventory and quality checks."""
    conn = _connect()
    checks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    try:
        for table in SCHEMA:
            profile = TABLE_PROFILES[table.name]
            row_count = int(_scalar(conn, f"SELECT COUNT(*) FROM {table.name}") or 0)
            date_range = _date_range(conn, table.name, profile)
            duplicate_groups = _duplicate_key_groups(conn, table.name, profile["key"])
            nulls = {column.name: _null_count(conn, table.name, column.name) for column in table.columns}
            null_total = sum(nulls.values())
            checks.extend([
                _check(f"{table.name}.nonempty", f"{table.label}有数据", row_count, passed=row_count > 0),
                _check(f"{table.name}.key_unique", f"{table.label}主键唯一", duplicate_groups, passed=duplicate_groups == 0),
                _check(f"{table.name}.required_values", f"{table.label}必填字段完整", null_total, passed=null_total == 0),
            ])
            if table.name == "orders":
                count = int(_scalar(conn, "SELECT COUNT(*) FROM orders WHERE paid_amount < 0") or 0)
                checks.append(_check("orders.amount_nonnegative", "订单实付金额非负", count, passed=count == 0))
            if table.name == "refunds":
                count = int(_scalar(conn, "SELECT COUNT(*) FROM refunds WHERE refund_amount < 0") or 0)
                checks.append(_check("refunds.amount_nonnegative", "退款金额非负", count, passed=count == 0))
            if table.name == "marketing_spend":
                count = int(_scalar(conn, "SELECT COUNT(*) FROM marketing_spend WHERE spend < 0") or 0)
                checks.append(_check("marketing_spend.spend_nonnegative", "投放花费非负", count, passed=count == 0))
            tables.append({
                "name": table.name,
                "label": table.label,
                "description": table.description,
                "grain": table.grain,
                "aliases": list(table.aliases),
                "key": profile["key"],
                "date_column": profile.get("date_column"),
                "date_range": date_range,
                "row_count": row_count,
                "column_count": len(table.columns),
                "nulls": nulls,
                "columns": [
                    {"name": column.name, "type": column.type, "description": column.description, "sensitivity": column.sensitivity}
                    for column in table.columns
                ],
            })

        relations = []
        for relation in RELATIONSHIPS:
            orphan_count = _orphan_count(conn, relation)
            checks.append(_check(
                f"relation.{relation['source']}.{relation['source_column']}",
                relation["description"], orphan_count, passed=orphan_count == 0,
            ))
            relations.append({**relation, "orphan_count": orphan_count})

        failed = [check for check in checks if not check["passed"]]
        status = "healthy" if not failed else "degraded"
        coverage = next((table["date_range"] for table in tables if table["name"] == "orders"), {})
        return {
            "generated_at": _now_iso(),
            "dataset": {"name": "commerce-demo", "expected_start": DATASET_START, "expected_end": DATASET_END, "coverage": coverage},
            "summary": {
                "status": status,
                "table_count": len(tables),
                "total_rows": sum(table["row_count"] for table in tables),
                "column_count": sum(table["column_count"] for table in tables),
                "check_count": len(checks),
                "passed_checks": len(checks) - len(failed),
                "failed_checks": len(failed),
            },
            "tables": tables,
            "relations": relations,
            "quality": {"status": status, "checks": checks, "failed": failed},
            "semantic_layer": {"source": "app/nl2sql/schema_catalog.py", "metrics": list(METRICS)},
            "boundary": {
                "level": "project-level-data-asset-layer",
                "description": "单数据源、固定业务域的可审计数据资产层；不包含多源接入、租户隔离或企业级调度。",
            },
        }
    finally:
        conn.close()
