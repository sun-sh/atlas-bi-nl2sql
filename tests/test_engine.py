from __future__ import annotations

import pytest

from app.nl2sql.generator import generate_rule_sql
from app.nl2sql.planner import make_plan
from app.nl2sql.retrieval import HybridSchemaRetriever
from app.nl2sql.sql_engine import SQLValidationError, execute_sql, validate_sql
from app.nl2sql.workflow import run_query


@pytest.fixture(scope="module")
def retriever() -> HybridSchemaRetriever:
    return HybridSchemaRetriever()


def test_retrieval_prefers_orders_for_sales_question(retriever: HybridSchemaRetriever):
    hits = retriever.search("上个月华东地区销售额是多少")
    assert hits[0].table == "orders"
    assert hits[0].score > hits[-1].score


def test_select_only_validator_rejects_writes():
    with pytest.raises(SQLValidationError):
        validate_sql("DELETE FROM orders")
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT name FROM orders; DROP TABLE orders")


def test_validator_rejects_sensitive_columns_and_wildcards():
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT customer_id FROM orders")
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT * FROM orders")
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT o.* FROM orders o")


def test_validator_rejects_unknown_functions():
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT load_extension('x') FROM orders")


def test_execute_returns_typed_rows():
    result = execute_sql("SELECT COUNT(*) AS n FROM orders WHERE status = 'paid'")
    assert result["rows"][0]["n"] > 0


def test_sales_query_end_to_end():
    result = run_query("上个月华东地区销售额是多少？")
    assert result.attempts[-1]["status"] == "success"
    assert result.rows[0]["销售额"] > 0
    assert result.plan.intent == "sales_overview"


def test_refund_query_end_to_end():
    result = run_query("退款率最高的品类是什么？")
    assert result.rows[0]["品类"] == "美妆个护"
    assert 0 < result.rows[0]["退款率"] < 20


@pytest.mark.parametrize(
    ("question", "channel", "date_fragment"),
    [
        ("上个月App渠道ROI", "App", "2026-01-01"),
        ("本月抖音投放ROI", "Douyin", "2026-02-01"),
        ("上个月各渠道ROI", None, "2026-01-01"),
    ],
)
def test_marketing_queries_filter_on_spend_date(question, channel, date_fragment):
    result = run_query(question)
    assert result.attempts[-1]["status"] == "success"
    assert "m.spend_date" in result.sql
    assert date_fragment in result.sql
    assert "m.order_date" not in result.sql
    assert all("ROI" in row and "归因订单数" in row for row in result.rows)
    if channel:
        assert len(result.rows) == 1
        assert result.rows[0]["渠道"] == channel
    else:
        assert len(result.rows) == 4


def test_marketing_region_filter_is_rejected_clearly(retriever: HybridSchemaRetriever):
    question = "上个月华东地区各渠道ROI"
    plan = make_plan(question, retriever.search(question))
    with pytest.raises(ValueError, match="不包含地区或品类字段"):
        generate_rule_sql(question, plan, retriever.search(question))


def test_repair_node_passes_error_and_original_sql_to_llm(monkeypatch):
    captured = {}

    def fake_repair(question, plan, hits, previous_sql, error):
        captured.update(
            question=question,
            previous_sql=previous_sql,
            error=error,
        )
        return "SELECT COUNT(*) AS n FROM orders"

    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_repair_sql", fake_repair)
    state = {
        "question": "销售额是多少",
        "plan": make_plan("各渠道销售额", []),
        "hits": [],
        "sql": "SELECT missing_column FROM orders",
        "attempts": [
            {
                "attempt": 1,
                "status": "failed",
                "sql": "SELECT missing_column FROM orders",
                "error": "列不存在：missing_column",
                "elapsed_ms": 1.0,
            }
        ],
        "warnings": [],
    }
    from app.nl2sql.workflow import repair_node

    result = repair_node(state)
    assert captured["previous_sql"] == "SELECT missing_column FROM orders"
    assert "missing_column" in captured["error"]
    assert result["sql"] == "SELECT COUNT(*) AS n FROM orders"
    assert result["mode"] == "llm-repair"


def test_every_rule_query_compiles_and_passes_validator():
    questions = [
        "上个月华东地区销售额是多少？",
        "近90天各品类销售额排名",
        "退款率最高的品类是什么？",
        "退款原因分布",
        "上个月App渠道ROI",
        "本月抖音投放ROI",
        "按日看本月销售额趋势",
        "各渠道客单价",
        "各品类销量排名",
    ]
    retriever = HybridSchemaRetriever()
    for question in questions:
        plan = make_plan(question, retriever.search(question))
        sql = generate_rule_sql(question, plan, retriever.search(question))
        assert validate_sql(sql)
