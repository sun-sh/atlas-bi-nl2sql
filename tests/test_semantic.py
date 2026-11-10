from __future__ import annotations

from pathlib import Path

import yaml

from app.nl2sql import semantic
from app.nl2sql.schema_catalog import semantic_schema_prompt
from app.nl2sql.sql_engine import validate_sql


def test_semantic_snapshot_comes_from_yaml_and_has_runtime_contract():
    snapshot = semantic.semantic_snapshot()
    assert snapshot["path"] == "app/semantic.yaml"
    assert snapshot["version"] == semantic.VERSION
    assert snapshot["tables"] == [table.name for table in semantic.SCHEMA]
    assert {metric["name"] for metric in snapshot["metrics"]} >= {
        "销售额", "客单价", "退款率", "ROI", "销量", "订单数"
    }
    raw = yaml.safe_load(Path(semantic.SEMANTIC_PATH).read_text(encoding="utf-8"))
    assert snapshot["version"] == raw["version"]
    assert snapshot["dataset"] == raw["dataset"]


def test_metric_expression_and_filters_render_from_same_entry(monkeypatch):
    monkeypatch.setitem(
        semantic.METRIC_BY_NAME,
        "销售额",
        {
            **semantic.METRIC_BY_NAME["销售额"],
            "expr": "ROUND(SUM({o}.paid_amount), 1)",
            "filters": ["{o}.status = 'paid'"],
        },
    )
    assert semantic.metric_sql("销售额") == "ROUND(SUM(o.paid_amount), 1)"
    assert semantic.metric_filters("销售额") == ("o.status = 'paid'",)


def test_prompt_renders_schema_and_metric_contract_from_semantic_layer():
    prompt = semantic_schema_prompt(["orders"])
    assert "paid_amount" in prompt
    assert "customer_id" not in prompt
    metric_prompt = semantic.metric_prompt()
    assert "ROUND(SUM({o}.paid_amount), 2)" in metric_prompt
    assert "{o}.status = 'paid'" in metric_prompt


def test_semantic_metric_sql_is_valid_under_shared_guard():
    sql = "SELECT " + semantic.metric_sql("销售额") + " AS 销售额 FROM orders o"
    assert "SUM" in validate_sql(sql).upper()



def test_sqlite_schema_is_rendered_from_semantic_yaml(tmp_path):
    import sqlite3
    from app.nl2sql.seed import generate_database

    db = tmp_path / "schema.db"
    generate_database(db)
    conn = sqlite3.connect(db)
    try:
        actual = {
            table: [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            for table in (table.name for table in semantic.SCHEMA)
        }
    finally:
        conn.close()
    expected = {
        table.name: [column.name for column in table.columns]
        for table in semantic.SCHEMA
    }
    assert actual == expected


def test_overview_metrics_uses_semantic_expressions():
    from app.nl2sql.analytics import overview_metrics

    overview = overview_metrics()
    assert overview["revenue"] > 0
    assert overview["aov"] > 0
    assert overview["orders"] > 0
    assert overview["dataset"] == {
        "start": semantic.DATASET_START,
        "end": semantic.DATASET_END,
    }
    assert "dashboard_metrics" in semantic.SEMANTIC
    assert "概览退款率" in semantic.DASHBOARD_METRIC_BY_NAME
    assert semantic.metric_sql(
        "概览退款率", values={"refund_count": "2", "order_count": "10"}
    ) == "ROUND(2 * 100.0 / NULLIF(10, 0), 2)"
