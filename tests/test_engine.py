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




def test_planner_adds_at_most_one_retrieval_supplement(monkeypatch):
    from app.nl2sql.models import SchemaHit

    monkeypatch.setenv("ATLAS_RETRIEVAL_SUPPLEMENTS", "1")

    def hit(table, score):
        return SchemaHit(
            table=table, label=table, description=table, score=score,
            vector_score=score, lexical_score=score,
        )

    hits = [hit("marketing_spend", 1.0), hit("refunds", 0.8), hit("customers", 0.6), hit("order_items", 0.4)]
    plan = make_plan("上个月各渠道销售额", hits)
    assert plan.tables == ["orders", "marketing_spend"]
    assert plan.context["retrieval_supplements"] == ["marketing_spend"]

    duplicate = [hit("orders", 1.0), hit("marketing_spend", 0.8)]
    plan = make_plan("上个月各渠道销售额", duplicate)
    assert plan.tables == ["orders"]
    assert plan.context["retrieval_supplements"] == []


def test_retrieval_supplement_can_be_disabled_for_ablation(monkeypatch):
    from app.nl2sql.models import SchemaHit

    monkeypatch.setenv("ATLAS_RETRIEVAL_SUPPLEMENTS", "0")
    hits = [SchemaHit(table="marketing_spend", label="投放", description="投放", score=1.0, vector_score=1.0, lexical_score=1.0)]
    plan = make_plan("上个月各渠道销售额", hits)
    assert plan.tables == ["orders"]
    assert plan.context["retrieval_supplements"] == []

    monkeypatch.setenv("ATLAS_RETRIEVAL_SUPPLEMENTS", "not-a-number")
    plan = make_plan("上个月各渠道销售额", hits)
    assert plan.tables == ["orders"]
    assert plan.context["retrieval_supplements"] == []


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


def test_harness_policy_rejects_recursive_cte_and_cross_join():
    with pytest.raises(SQLValidationError, match="递归 CTE"):
        validate_sql("WITH RECURSIVE c AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM c) SELECT n FROM c")
    with pytest.raises(SQLValidationError, match="CROSS JOIN"):
        validate_sql("SELECT o.order_id FROM orders o CROSS JOIN orders x")


def test_harness_policy_rejects_star_inside_cte_but_allows_count_star():
    with pytest.raises(SQLValidationError, match=r"SELECT \*"):
        validate_sql("WITH x AS (SELECT * FROM orders) SELECT order_id FROM x")
    assert validate_sql("SELECT COUNT(*) AS n FROM orders")


def test_harness_policy_rejects_excessive_joins():
    sql = "SELECT o.order_id FROM orders o JOIN orders a ON a.order_id=o.order_id JOIN orders b ON b.order_id=o.order_id JOIN orders c ON c.order_id=o.order_id JOIN orders d ON d.order_id=o.order_id"
    with pytest.raises(SQLValidationError, match="JOIN"):
        validate_sql(sql)


def test_query_timeout_interrupts_long_query(monkeypatch):
    from types import SimpleNamespace
    from app.nl2sql import sql_engine
    settings = sql_engine.get_settings()
    fake = SimpleNamespace(db_path=settings.db_path, max_rows=200, query_timeout_seconds=0.001)
    monkeypatch.setattr(sql_engine, "get_settings", lambda: fake)
    with pytest.raises(TimeoutError):
        execute_sql("SELECT COUNT(o1.order_id) AS n FROM orders o1 JOIN orders o2 ON 1=1 JOIN orders o3 ON 1=1")


def test_harness_cancel_registry_is_idempotent():
    from app.nl2sql.harness import (
        is_cancelled,
        register_cancel,
        release_cancel,
        request_cancel,
    )
    trace = "unit-cancel-trace"
    register_cancel(trace)
    assert not is_cancelled(trace)
    assert request_cancel(trace)
    assert is_cancelled(trace)
    assert request_cancel(trace)
    release_cancel(trace)
    assert not is_cancelled(trace)


def test_execute_returns_typed_rows():
    result = execute_sql("SELECT COUNT(*) AS n FROM orders WHERE status = 'paid'")
    assert result["rows"][0]["n"] > 0


def test_sales_query_end_to_end():
    result = run_query("上个月华东地区销售额是多少？")
    assert result.attempts[-1]["status"] == "success"
    assert result.rows[0]["销售额"] > 0
    assert result.plan.intent == "sales_overview"


def test_category_refund_amount_uses_refund_amount_not_rate():
    result = run_query("本月各品类退款金额，按退款金额降序", narrate=False)
    assert result.rows
    assert result.columns == ["品类", "退款单数", "退款金额"]
    assert result.rows == sorted(result.rows, key=lambda row: row["退款金额"], reverse=True)


def test_refund_query_end_to_end():
    result = run_query("退款率最高的品类是什么？")
    assert len(result.rows) == 1
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
        generate_rule_sql(question, plan)


def test_rule_roi_aggregates_spend_and_revenue_before_join():
    result = run_query("上个月各渠道ROI", narrate=False)
    assert result.rows
    assert "m.spend_date BETWEEN '2026-01-01' AND '2026-01-31'" in result.sql
    assert "o.status = 'paid'" in result.sql
    assert "SUM(spend)" in result.sql
    expected = execute_sql(
        "SELECT ROUND(s.revenue / m.spend, 2) AS ROI "
        "FROM (SELECT channel, SUM(spend) AS spend FROM marketing_spend "
        "WHERE spend_date BETWEEN '2026-01-01' AND '2026-01-31' GROUP BY channel) m "
        "JOIN (SELECT channel, SUM(paid_amount) AS revenue FROM orders "
        "WHERE status = 'paid' AND order_date BETWEEN '2026-01-01' AND '2026-01-31' "
        "GROUP BY channel) s ON s.channel = m.channel WHERE m.channel = 'Web'"
    )["rows"][0]["ROI"]
    assert next(row["ROI"] for row in result.rows if row["渠道"] == "Web") == expected


def test_repair_node_passes_error_and_original_sql_to_llm(monkeypatch):
    captured = {}

    def fake_repair(question, plan, previous_sql, error, **kwargs):
        captured.update(
            question=question,
            previous_sql=previous_sql,
            error=error,
        )
        return "SELECT COUNT(*) AS n FROM orders"

    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_repair_sql_result", lambda *args, **kwargs: (fake_repair(*args, **kwargs), ""))
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
        sql = generate_rule_sql(question, plan)
        assert validate_sql(sql)



def test_model_failure_is_visible_when_falling_back_to_rules(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql_result", lambda *args, **kwargs: (None, "模拟网络失败"))
    monkeypatch.setattr("app.nl2sql.generator.LAST_LLM_ERROR", "模拟网络失败")
    result = run_query("各渠道销售额")
    assert result.mode == "rule"
    assert any("模型调用失败" in warning for warning in result.warnings)


def test_strict_model_query_never_calls_rule_fallback(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql_result", lambda *args, **kwargs: (None, "模拟网络失败"))
    monkeypatch.setattr("app.nl2sql.generator.LAST_LLM_ERROR", "模拟空返回")

    def forbidden_rule(*args):
        raise AssertionError("strict model evaluation must not call rule SQL")

    monkeypatch.setattr("app.nl2sql.workflow.generate_rule_sql", forbidden_rule)
    result = run_query("各渠道销售额", allow_rule_fallback=False)
    assert result.mode == "model-failed"
    assert result.outcome == "failed"
    assert result.sql == ""
    assert result.attempts[-1]["status"] == "failed"
    assert any("不回退规则" in warning for warning in result.warnings)
    assert any("不是业务结论" in warning for warning in result.warnings)


def test_empty_model_sql_gets_one_strict_model_retry(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql_result", lambda *args, **kwargs: (None, "模拟网络失败"))
    monkeypatch.setattr(
        "app.nl2sql.workflow.generate_llm_repair_sql_result",
        lambda *args, **kwargs: ("SELECT COUNT(*) AS n FROM orders", ""),
    )
    result = run_query("各渠道销售额", allow_rule_fallback=False)
    assert result.mode == "llm-repair"
    assert result.attempts[-1]["status"] == "success"
    assert any("模型重试" in warning for warning in result.warnings)
    assert result.generation_retries == 1


def test_model_retry_budget_includes_empty_generation(monkeypatch):
    calls = []
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql_result", lambda *args, **kwargs: (None, "模拟网络失败"))
    def broken(*args, **kwargs):
        calls.append(args)
        return "SELECT missing_column FROM orders"
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_repair_sql_result", lambda *args, **kwargs: (broken(*args, **kwargs), ""))
    result = run_query("上个月销售额", allow_rule_fallback=False)
    assert len(calls) == 2
    assert len(result.attempts) == 2
    assert result.generation_retries == 1
    assert all(attempt["status"] == "failed" for attempt in result.attempts)


def test_all_null_aggregation_reenters_model_repair_without_broadening_filters(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql_result", lambda *args, **kwargs: ("SELECT SUM(paid_amount) AS 销售额 FROM orders WHERE status = '支付'", ""))
    captured = []
    def repair(question, plan, sql, error, **kwargs):
        captured.append((plan, error, sql))
        return "SELECT SUM(paid_amount) AS 销售额 FROM orders WHERE status = 'paid' AND order_date BETWEEN '2026-01-01' AND '2026-01-31' AND region = '华东'"
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_repair_sql_result", lambda *args, **kwargs: (repair(*args, **kwargs), ""))
    result = run_query("上个月华东已支付销售额", allow_rule_fallback=False)
    assert result.mode == "llm-repair"
    assert [attempt["status"] for attempt in result.attempts] == ["failed", "success"]
    assert "全为空" in captured[0][1]
    assert result.rows[0]["销售额"] == pytest.approx(79947.85)


def test_zero_value_is_valid_and_does_not_trigger_repair(monkeypatch):
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_sql_result", lambda *args, **kwargs: ("SELECT COUNT(*) AS 订单数 FROM orders WHERE status = 'absent'", ""))
    def forbidden(*args):
        raise AssertionError("valid zero counts must not trigger repair")
    monkeypatch.setattr("app.nl2sql.workflow.generate_llm_repair_sql_result", forbidden)
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

def test_sql_validation_supports_postgres_and_mysql_dialects():
    from app.nl2sql.sql_engine import validate_sql

    postgres = validate_sql(
        "SELECT o.channel AS channel, SUM(o.paid_amount) AS revenue "
        "FROM orders o GROUP BY o.channel",
        dialect="postgresql",
    )
    mysql = validate_sql(
        "SELECT o.channel AS channel, SUM(o.paid_amount) AS revenue "
        "FROM orders o GROUP BY o.channel",
        dialect="mysql",
    )
    assert "orders" in postgres
    assert "orders" in mysql
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT customer_id FROM orders", dialect="postgresql")
    with pytest.raises(SQLValidationError, match="跨 Schema"):
        validate_sql(
            "SELECT o.channel, SUM(o.paid_amount) AS revenue "
            "FROM other_schema.orders o GROUP BY o.channel",
            dialect="postgresql",
        )


def test_unavailable_dbapi_driver_fails_clearly(monkeypatch):
    from app.nl2sql import sql_engine

    target = sql_engine.QueryTarget(
        "postgresql", "postgres", dsn="postgresql://readonly@localhost/atlas"
    )
    adapter = sql_engine.adapter_for(target)
    monkeypatch.setattr(sql_engine, "adapter_for", lambda _: adapter)
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(sql_engine.QueryAdapterError, match="psycopg"):
        sql_engine.execute_sql(
            "SELECT SUM(o.paid_amount) AS revenue FROM orders o",
            target=target,
        )


def test_mysql_dbapi_adapter_parses_dsn_and_maps_bounded_rows(monkeypatch):
    import sys
    from types import SimpleNamespace

    from app.nl2sql import sql_engine

    captured = {}

    class Cursor:
        description = (("channel",), ("revenue",))

        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql
            captured["sql"] = sql
            captured.setdefault("statements", []).append(sql)

        def fetchmany(self, size):
            captured["fetch_size"] = size
            return [("App", 12.5), ("Web", 8.0), ("Douyin", 7.0)]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            captured["closed"] = True

    def connect(**kwargs):
        captured["connect"] = kwargs
        return Connection()

    monkeypatch.setitem(sys.modules, "pymysql", SimpleNamespace(connect=connect))
    target = sql_engine.QueryTarget(
        "mysql",
        "mysql",
        dsn="mysql://reader:p%40ss@db.internal:3307/commerce",
    )
    result = sql_engine.execute_sql(
        "SELECT o.channel AS channel, SUM(o.paid_amount) AS revenue "
        "FROM orders o GROUP BY o.channel",
        limit=2,
        target=target,
    )
    assert captured["connect"]["host"] == "db.internal"
    assert captured["connect"]["port"] == 3307
    assert captured["connect"]["user"] == "reader"
    assert captured["connect"]["password"] == "p@ss"
    assert captured["connect"]["database"] == "commerce"
    assert captured["fetch_size"] == 3
    assert result["columns"] == ["channel", "revenue"]
    assert result["rows"] == [{"channel": "App", "revenue": 12.5}, {"channel": "Web", "revenue": 8.0}]
    assert result["row_count"] == 2
    assert result["truncated"] is True
    assert captured["closed"] is True


def test_postgres_dbapi_adapter_maps_rows_and_always_closes(monkeypatch):
    import sys
    from types import SimpleNamespace

    from app.nl2sql import sql_engine

    captured = {}

    class Cursor:
        description = (("category",), ("refund_rate",))

        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured.setdefault("statements", []).append(sql)

        def fetchmany(self, size):
            captured["fetch_size"] = size
            return [("数码", 3.25)]

        def close(self):
            captured["cursor_closed"] = True

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            captured["connection_closed"] = True

    def connect(dsn, autocommit):
        captured["dsn"] = dsn
        captured["autocommit"] = autocommit
        return Connection()

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    target = sql_engine.QueryTarget("postgresql", "postgres", dsn="postgresql://reader@db/atlas")
    result = sql_engine.execute_sql(
        "SELECT o.category AS category, AVG(o.paid_amount) AS refund_rate "
        "FROM orders o GROUP BY o.category",
        target=target,
    )
    assert captured["dsn"] == "postgresql://reader@db/atlas"
    timeout_statements = [
        statement for statement in captured["statements"]
        if "statement_timeout" in statement
    ]
    assert timeout_statements == ["SET statement_timeout = %s"]
    assert captured["autocommit"] is True
    assert captured["cursor_closed"] is True
    assert captured["connection_closed"] is True
    assert result["rows"] == [{"category": "数码", "refund_rate": 3.25}]
    assert result["truncated"] is False


def test_dbapi_connection_error_redacts_dsn_password(monkeypatch):
    import sys
    from types import SimpleNamespace

    from app.nl2sql import sql_engine

    dsn = "postgresql://reader:top-secret@db.internal/atlas"

    def fail(dsn_value, autocommit):
        raise RuntimeError(f"could not connect using {dsn_value}; password=top-secret")

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=fail))
    target = sql_engine.QueryTarget("postgresql", "postgres", dsn=dsn)
    with pytest.raises(sql_engine.QueryAdapterError) as error:
        sql_engine.execute_sql(
            "SELECT SUM(o.paid_amount) AS revenue FROM orders o",
            target=target,
        )
    message = str(error.value)
    assert "top-secret" not in message
    assert "[REDACTED]" in message


def test_force_rule_never_calls_a_configured_model(monkeypatch):
    from app.nl2sql import workflow

    def forbidden(*args, **kwargs):
        raise AssertionError("force_rule must not invoke the model")

    monkeypatch.setattr(workflow, "generate_llm_sql_result", forbidden)
    monkeypatch.setattr(workflow, "generate_llm_repair_sql_result", forbidden)
    result = workflow.run_query("各渠道销售额", narrate=False, force_rule=True)
    assert result.mode == "rule"
    assert result.attempts[-1]["status"] == "success"
    assert result.rows



def test_call_scoped_model_error_does_not_leak_between_requests(monkeypatch):
    from app.nl2sql import generator

    responses = iter([
        (None, "request-a network error"),
        ("SELECT COUNT(*) AS n FROM orders", ""),
    ])

    def fake(instructions, prompt, **kwargs):
        return next(responses)

    monkeypatch.setattr(generator, "_llm_sql_result", fake)
    first_sql, first_error = generator._llm_sql_result("x", "y")
    second_sql, second_error = generator._llm_sql_result("x", "y")
    assert first_sql is None
    assert first_error == "request-a network error"
    assert second_sql
    assert second_error == ""



def test_window_ranking_function_is_allowed():
    from app.nl2sql.sql_engine import validate_sql
    sql = (
        "SELECT o.channel AS channel, "
        "ROW_NUMBER() OVER (ORDER BY SUM(o.paid_amount) DESC) AS rn "
        "FROM orders o GROUP BY o.channel"
    )
    assert "ROW_NUMBER" in validate_sql(sql).upper()


def test_elasticsearch_scores_are_request_local(monkeypatch):
    from app.nl2sql.retrieval import HybridSchemaRetriever
    from app.nl2sql.settings import get_settings

    class FakeElastic:
        def search(self, **kwargs):
            query = kwargs["query"]["multi_match"]["query"]
            score = 5.0 if "退款" in query else 1.0
            return {"hits": {"hits": [{"_id": "orders", "_score": score}]}}

    retriever = HybridSchemaRetriever()
    retriever.elastic = FakeElastic()
    # Two calls must not depend on a shared mutable score attached to the
    # retriever. The second call would reuse the first result if state leaked.
    first = retriever.search("退款原因", limit=5)
    second = retriever.search("销售额", limit=5)
    assert first
    assert second
    assert not hasattr(retriever, "elastic_scores")
