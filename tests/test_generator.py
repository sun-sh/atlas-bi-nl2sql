from __future__ import annotations

from app.nl2sql import generator
from app.nl2sql.planner import make_plan


def test_qwen_sql_request_disables_thinking(monkeypatch):
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {
                "choices": [type("Choice", (), {"message": type("Message", (), {"content": "SELECT COUNT(*) AS n FROM orders"})()})()],
                "usage": type("Usage", (), {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7})(),
            })()

    config = type("Config", (), {
        "enabled": True,
        "provider": "custom",
        "model": "qwen/qwen3.8-flash",
    })()
    client = type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    monkeypatch.setattr(generator, "get_model_config", lambda: config)
    monkeypatch.setattr(generator, "make_client", lambda *_args, **_kwargs: client)

    assert generator._llm_sql("system", "user")
    assert captured["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_roi_prompt_includes_both_grains_enums_and_no_restricted_fields(monkeypatch):
    captured = {}
    def capture(instructions, prompt, **kwargs):
        captured.update(instructions=instructions, prompt=prompt)
        return "SELECT COUNT(*) AS n FROM orders", ""
    monkeypatch.setattr(generator, "_llm_sql_result", capture)
    question = "上个月各渠道ROI"
    plan = make_plan(question, [])
    generator.generate_llm_sql(question, plan)
    assert set(plan.tables) == {"orders", "marketing_spend"}
    assert "paid / refunded / cancelled" in captured["prompt"]
    assert "spend_date" in captured["prompt"]
    assert "2026-01-01" in captured["prompt"]
    assert "MiniProgram" in captured["prompt"]
    assert "customer_id" not in captured["prompt"]
    assert "cost_price" not in captured["prompt"]


def test_followup_prompt_contains_current_plan_and_source_question(monkeypatch):
    captured = {}
    monkeypatch.setattr(generator, "_llm_sql_result", lambda instructions, prompt, **kwargs: (captured.update(prompt=prompt) or "SELECT 1", ""))
    original = make_plan("上个月华东已支付销售额", [])
    context = {**original.as_dict(), **original.context, "question": "上个月华东已支付销售额"}
    plan = make_plan("那华南呢", [], context)
    generator.generate_llm_sql("那华南呢", plan)
    assert "前置问题：上个月华东已支付销售额" in captured["prompt"]
    assert "o.region = '华南'" in captured["prompt"]
    assert "2026-01-31" in captured["prompt"]


def test_truncated_sql_is_rejected_before_execution(monkeypatch):
    from types import SimpleNamespace
    def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="SELECT SUM(paid_amount"), finish_reason="length",
        )])
    monkeypatch.setattr(generator, "get_model_config", lambda: SimpleNamespace(enabled=True, provider="custom", model="test"))
    monkeypatch.setattr(generator, "make_client", lambda *_args, **_kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    assert generator._llm_sql("system", "user") is None
    assert "finish_reason=length" in generator.LAST_LLM_ERROR


def test_external_dialect_is_explicit_in_generation_and_repair(monkeypatch):
    captured = []

    def capture(instructions, prompt, **kwargs):
        captured.append(instructions)
        return "SELECT SUM(o.paid_amount) AS 销售额 FROM orders o", ""

    monkeypatch.setattr(generator, "_llm_sql_result", capture)
    plan = make_plan("上个月销售额", [])
    generator.generate_llm_sql("上个月销售额", plan, dialect="postgres")
    generator.generate_llm_repair_sql(
        "上个月销售额", plan, "SELECT 1", "syntax error", dialect="mysql"
    )
    assert "PostgreSQL" in captured[0]
    assert "SQLite 专有" in captured[0]
    assert "MySQL" in captured[1]



def test_generation_prompt_only_includes_selected_plan_tables(monkeypatch):
    captured = {}

    def capture(instructions, prompt, **kwargs):
        captured["prompt"] = prompt
        return "SELECT SUM(o.paid_amount) AS 销售额 FROM orders o", ""

    monkeypatch.setattr(generator, "_llm_sql_result", capture)
    from app.nl2sql.models import SchemaHit, ColumnInfo
    hits = [
        SchemaHit(table="marketing_spend", label="投放", description="投放表", score=1.0, vector_score=1.0, lexical_score=1.0, columns=(ColumnInfo("spend", "REAL", "花费"),)),
        SchemaHit(table="order_items", label="明细", description="订单明细", score=0.9, vector_score=0.9, lexical_score=0.9),
    ]
    plan = make_plan("销售额", [])
    generator.generate_llm_sql("销售额", plan)
    assert "marketing_spend" not in captured["prompt"]
    assert "order_items" not in captured["prompt"]
    assert "paid_amount" in captured["prompt"]


def test_roi_prompt_requires_all_channels_unless_single_top_requested():
    from app.nl2sql.planner import make_plan
    from app.nl2sql.generator import _sql_prompt, SQL_INSTRUCTIONS

    prompt = _sql_prompt("上个月各渠道ROI", make_plan("上个月各渠道ROI", []))
    assert "必须返回每个有投放花费的渠道" in SQL_INSTRUCTIONS
    assert "不要加 LIMIT 1" in SQL_INSTRUCTIONS
    assert "上个月各渠道ROI" in prompt



def test_rule_sql_uses_semantic_metric_filters():
    from app.nl2sql.generator import generate_rule_sql
    from app.nl2sql.planner import make_plan
    from app.nl2sql.sql_engine import execute_sql

    question = "近90天各品类已支付商品销量排名，按商品行的品类汇总件数，销量降序"
    sql = generate_rule_sql(question, make_plan(question, []))
    assert "o.status = 'paid'" in sql
    assert execute_sql(sql)["rows"]

    question = "上个月各渠道ROI：已支付销售额除以投放花费，两表各自按渠道汇总后计算，按ROI降序"
    sql = generate_rule_sql(question, make_plan(question, []))
    rows = execute_sql(sql)["rows"]
    assert len(rows) == 4
    assert "o.status = 'paid'" in sql
    assert "GROUP BY m.channel" in sql


def test_rule_sql_renders_item_grain_sales_from_semantic_expression():
    from app.nl2sql.generator import generate_rule_sql
    from app.nl2sql.planner import make_plan
    from app.nl2sql.sql_engine import execute_sql

    question = "近90天各品类已支付商品销售额排名，按商品行的品类汇总"
    sql = generate_rule_sql(question, make_plan(question, []))
    assert "ROUND(SUM(o.revenue), 2)" in sql
    assert execute_sql(sql)["rows"]
