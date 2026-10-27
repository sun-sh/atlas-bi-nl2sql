from __future__ import annotations

from app.nl2sql import generator
from app.nl2sql.planner import make_plan


def test_qwen_sql_request_disables_thinking(monkeypatch):
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {
                "choices": [type("Choice", (), {"message": type("Message", (), {"content": "SELECT COUNT(*) AS n FROM orders"})()})()]
            })()

    config = type("Config", (), {
        "enabled": True,
        "provider": "custom",
        "model": "qwen/qwen3.8-flash",
    })()
    client = type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    monkeypatch.setattr(generator, "get_model_config", lambda: config)
    monkeypatch.setattr(generator, "make_client", lambda _: client)

    assert generator._llm_sql("system", "user")
    assert captured["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_roi_prompt_includes_both_grains_enums_and_no_restricted_fields(monkeypatch):
    captured = {}
    def capture(instructions, prompt):
        captured.update(instructions=instructions, prompt=prompt)
        return "SELECT COUNT(*) AS n FROM orders"
    monkeypatch.setattr(generator, "_llm_sql", capture)
    question = "上个月各渠道ROI"
    plan = make_plan(question, [])
    generator.generate_llm_sql(question, plan, [])
    assert set(plan.tables) == {"orders", "marketing_spend"}
    assert "paid / refunded / cancelled" in captured["prompt"]
    assert "spend_date" in captured["prompt"]
    assert "2026-01-01" in captured["prompt"]
    assert "MiniProgram" in captured["prompt"]
    assert "customer_id" not in captured["prompt"]
    assert "cost_price" not in captured["prompt"]


def test_followup_prompt_contains_current_plan_and_source_question(monkeypatch):
    captured = {}
    monkeypatch.setattr(generator, "_llm_sql", lambda instructions, prompt: captured.update(prompt=prompt))
    original = make_plan("上个月华东已支付销售额", [])
    context = {**original.as_dict(), **original.context, "question": "上个月华东已支付销售额"}
    plan = make_plan("那华南呢", [], context)
    generator.generate_llm_sql("那华南呢", plan, [])
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
    monkeypatch.setattr(generator, "make_client", lambda _: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    assert generator._llm_sql("system", "user") is None
    assert "finish_reason=length" in generator.LAST_LLM_ERROR
