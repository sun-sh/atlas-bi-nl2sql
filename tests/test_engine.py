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



def test_model_failure_is_visible_when_falling_back_to_rules(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql", lambda *args: None)
    monkeypatch.setattr("app.nl2sql.generator.LAST_LLM_ERROR", "模拟网络失败")
    result = run_query("各渠道销售额")
    assert result.mode == "rule"
    assert any("模型调用失败" in warning for warning in result.warnings)


def test_strict_model_query_never_calls_rule_fallback(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql", lambda *args: None)
    monkeypatch.setattr("app.nl2sql.generator.LAST_LLM_ERROR", "模拟空返回")

    def forbidden_rule(*args):
        raise AssertionError("strict model evaluation must not call rule SQL")

    monkeypatch.setattr("app.nl2sql.workflow.generate_rule_sql", forbidden_rule)
    result = run_query("各渠道销售额", allow_rule_fallback=False)
    assert result.mode == "model-failed"
    assert result.sql == ""
    assert result.attempts[-1]["status"] == "failed"
    assert any("不回退规则" in warning for warning in result.warnings)


def test_empty_model_sql_gets_one_strict_model_retry(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql", lambda *args: None)
    monkeypatch.setattr(
        "app.nl2sql.workflow.generate_llm_repair_sql",
        lambda *args: "SELECT COUNT(*) AS n FROM orders",
    )
    result = run_query("各渠道销售额", allow_rule_fallback=False)
    assert result.mode == "llm-repair"
    assert result.attempts[-1]["status"] == "success"
    assert any("模型重试" in warning for warning in result.warnings)
    assert result.generation_retries == 1


def test_model_retry_budget_includes_empty_generation(monkeypatch):
    calls = []
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql", lambda *args: None)
    def broken(*args):
        calls.append(args)
        return "SELECT missing_column FROM orders"
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_repair_sql", broken)
    result = run_query("上个月销售额", allow_rule_fallback=False)
    assert len(calls) == 2
    assert len(result.attempts) == 2
    assert result.generation_retries == 1
    assert all(attempt["status"] == "failed" for attempt in result.attempts)


def test_all_null_aggregation_reenters_model_repair_without_broadening_filters(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql", lambda *args: "SELECT SUM(paid_amount) AS 销售额 FROM orders WHERE status = '支付'")
    captured = []
    def repair(question, plan, hits, sql, error):
        captured.append((plan, error))
        return "SELECT SUM(paid_amount) AS 销售额 FROM orders WHERE status = 'paid' AND order_date BETWEEN '2026-01-01' AND '2026-01-31' AND region = '华东'"
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_repair_sql", repair)
    result = run_query("上个月华东已支付销售额", allow_rule_fallback=False)
    assert result.mode == "llm-repair"
    assert [attempt["status"] for attempt in result.attempts] == ["failed", "success"]
    assert "全为空" in captured[0][1]
    assert result.rows[0]["销售额"] == pytest.approx(79947.85)


def test_zero_value_is_valid_and_does_not_trigger_repair(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql", lambda *args: "SELECT COUNT(*) AS 订单数 FROM orders WHERE status = 'absent'")
    def forbidden(*args):
        raise AssertionError("valid zero counts must not trigger repair")
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_repair_sql", forbidden)
    result = run_query("订单数是多少", allow_rule_fallback=False)
    assert result.attempts[-1]["status"] == "success"
    assert result.rows == [{"订单数": 0}]


def test_evaluation_does_not_run_when_model_is_disabled(monkeypatch):
    monkeypatch.setenv("ATLAS_DISABLE_MODEL", "1")
    from app.nl2sql.evaluation import run_evaluation

    report = run_evaluation()
    assert report["mode"] == "model"
    assert report["available"] is False
    assert report["passed"] == 0
    assert all(case["model_used"] is False for case in report["cases"])


def test_evaluation_strict_model_failure_is_not_masked(monkeypatch):
    from app.nl2sql import evaluation

    class FakeResult:
        mode = "model-failed"
        narrative_mode = "digest"
        rows = []
        attempts = [{"status": "failed"}]
        warnings = ["模型未生成 SQL，严格模型评测不回退规则：模拟空返回"]

    monkeypatch.setattr(evaluation, "_model_state", lambda: {
        "configured": True,
        "active": True,
        "model": "fake-model",
        "provider": "fake",
    })
    monkeypatch.setattr(
        evaluation,
        "run_query",
        lambda question, **kwargs: FakeResult(),
    )
    report = evaluation.run_evaluation()
    assert report["available"] is True
    assert report["passed"] == 0
    assert report["model_rate"] == 0.0
    assert all(case["model_used"] is False for case in report["cases"])
    assert all(case["hit"] is False for case in report["cases"])
    assert all("严格模型评测" in case["error"] for case in report["cases"])


def test_evaluation_rejects_digest_narrative_as_model_pass(monkeypatch):
    from app.nl2sql import evaluation

    class FakeResult:
        mode = "llm"
        narrative_mode = "digest"
        rows = [{"销售额": 79947.85}]
        attempts = [{"status": "success"}]
        warnings = ["模型结果解读失败，已回退确定性摘要：模拟超时"]

    monkeypatch.setattr(evaluation, "_model_state", lambda: {
        "configured": True,
        "active": True,
        "model": "fake-model",
        "provider": "fake",
    })
    monkeypatch.setattr(
        evaluation,
        "run_query",
        lambda question, **kwargs: FakeResult(),
    )
    report = evaluation.run_evaluation()
    assert report["passed"] == 0
    assert report["model_rate"] == 0.0
    assert all(case["narrative_mode"] == "digest" for case in report["cases"])
    assert all("确定性摘要" in case["error"] for case in report["cases"])
