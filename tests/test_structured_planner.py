from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.nl2sql import structured_planner


def _draft(**overrides):
    return structured_planner.StructuredPlanDraft(**overrides)


def test_valid_structured_plan_is_built_from_registry_values():
    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="sales_overview",
            metrics=["销售额"],
            dimensions=["channel"],
            filters=[structured_planner.PlanFilter(field="region", values=["华东"])],
            time_window="last_month",
            chart_type="bar",
        ),
        question="上个月华东各渠道销售额",
        reference_date="2026-02-28",
    )
    assert plan.intent == "sales_overview"
    assert plan.metrics == ["销售额"]
    assert plan.dimensions == ["channel"]
    assert any("o.region = '华东'" in item for item in plan.filters)
    assert any("o.order_date" in item for item in plan.filters)


def test_unknown_metric_is_rejected():
    with pytest.raises(ValueError, match="unknown metrics"):
        structured_planner.validate_structured_plan(
            _draft(intent="sales_overview", metrics=["不存在的指标"]),
            question="随便问",
        )


def test_dimension_not_supported_by_metric_is_rejected():
    with pytest.raises(ValueError, match="does not support dimensions"):
        structured_planner.validate_structured_plan(
            _draft(intent="sales_overview", metrics=["销售额"], dimensions=["reason"]),
            question="退款原因销售额",
        )


def test_metric_tables_extend_the_intent_table_set():
    plan = structured_planner.validate_structured_plan(
        _draft(intent="refund_analysis", metrics=["退款率"]),
        question="退款率",
    )
    assert plan.tables == ["orders", "refunds"]


def test_single_value_filter_does_not_become_a_dimension():
    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="sales_overview",
            metrics=["销售额"],
            dimensions=["channel"],
            filters=[structured_planner.PlanFilter(field="channel", values=["App"])],
        ),
        question="本月App渠道销售额",
    )
    assert plan.dimensions == []


def test_multi_value_filter_remains_a_breakdown_when_dimension_declared():
    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="sales_overview",
            metrics=["销售额"],
            dimensions=["channel"],
            filters=[structured_planner.PlanFilter(field="channel", operator="in", values=["App", "Web"])],
        ),
        question="本月App和Web渠道销售额对比",
    )
    assert plan.dimensions == ["channel"]


def test_structured_plan_preserves_context_metrics_and_retrieval_candidates():
    plan = structured_planner.validate_structured_plan(
        _draft(intent="sales_overview", metrics=["销售额"], dimensions=["channel"]),
        question="本月各渠道销售额",
        context={"question": "上个月销售额", "retrieval_candidates": ["orders", "customers"]},
        reference_date="2026-10-31",
    )
    assert plan.context["reference_date"] == "2026-10-31"
    assert plan.context["metrics"] == ["销售额"]
    assert plan.context["retrieval_candidates"] == ["orders", "customers"]
    assert plan.source_question == "上个月销售额"


def test_structured_clarification_has_no_sql_plan():
    plan = structured_planner.validate_structured_plan(
        _draft(outcome="clarification", clarification_question="请指定退款指标", reason_code="missing_metric"),
        question="退款问题严重吗",
    )
    assert plan.outcome == "clarification"
    assert plan.metrics == []
    assert plan.tables == []


def test_all_selected_metrics_must_support_the_dimensions():
    with pytest.raises(ValueError, match="metric does not support dimensions"):
        structured_planner.validate_structured_plan(
            _draft(
                intent="sales_overview",
                metrics=["销售额"],
                dimensions=["reason"],
            ),
            question="各退款原因的销售额和退款金额",
        )


def test_valid_llm_json_is_adopted(monkeypatch):
    config = SimpleNamespace(enabled=True, active=True, model="test-model", provider="custom")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "outcome": "answered",
            "intent": "sales_overview",
            "metrics": ["销售额"],
            "dimensions": ["channel"],
            "filters": [{"field": "region", "operator": "eq", "values": ["华东"]}],
            "time_window": "last_month",
            "chart_type": "bar",
        }, ensure_ascii=False)))],
        usage=None,
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
    monkeypatch.setattr(structured_planner, "get_model_config", lambda: config)
    monkeypatch.setattr(structured_planner, "make_client", lambda *args, **kwargs: client)
    plan, error = structured_planner.plan_with_llm(
        "上个月华东各渠道销售额", [], reference_date="2026-02-28"
    )
    assert error == ""
    assert plan.metrics == ["销售额"]
    assert plan.dimensions == ["channel"]
    assert plan.context["planner_mode"] == "llm"


def test_invalid_llm_metric_returns_validation_error(monkeypatch):
    config = SimpleNamespace(enabled=True, active=True, model="test-model", provider="custom")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "outcome": "answered", "intent": "sales_overview",
            "metrics": ["未注册指标"], "dimensions": [],
        }, ensure_ascii=False)))],
        usage=None,
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
    monkeypatch.setattr(structured_planner, "get_model_config", lambda: config)
    monkeypatch.setattr(structured_planner, "make_client", lambda *args, **kwargs: client)
    plan, error = structured_planner.plan_with_llm("本月销售额", [])
    assert plan is None
    assert "unknown metrics" in error


def test_disabled_model_does_not_call_llm():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(structured_planner, "get_model_config", lambda: SimpleNamespace(enabled=False, active=False))
    try:
        plan, error = structured_planner.plan_with_llm("本月ROI", [])
        assert plan is None
        assert error == "模型未启用"
    finally:
        monkeypatch.undo()


def test_workflow_keeps_structured_clarification_authoritative(monkeypatch):
    from types import SimpleNamespace

    from app.nl2sql import workflow
    from app.nl2sql.models import QueryPlan

    guard = SimpleNamespace(
        outcome="answered",
        reason_code="",
        answerable=True,
        as_dict=lambda: {
            "outcome": "answered",
            "reason_code": "",
            "message": "",
            "suggestions": [],
        },
    )
    structured = QueryPlan(
        intent="",
        tables=[],
        metrics=[],
        dimensions=[],
        filters=[],
        time_range="",
        chart_type="table",
        notes=["请明确退款率还是退款金额"],
        outcome="clarification",
        reason_code="missing_metric",
    )
    monkeypatch.setattr(workflow, "assess_question", lambda *args, **kwargs: guard)
    monkeypatch.setattr(workflow, "plan_with_llm", lambda *args, **kwargs: (structured, ""))
    monkeypatch.setattr(workflow, "_query_reference_date", lambda *args, **kwargs: "2026-02-28")
    state = workflow.plan_node({
        "question": "退款问题严重吗",
        "hits": [],
        "context": {},
        "force_rule": False,
    })
    assert state["decision"]["outcome"] == "clarification"
    assert state["plan"].outcome == "clarification"
    assert state["plan"].reason_code == "missing_metric"
    assert state["plan"].tables == []


def test_workflow_reapplies_semantic_guard_when_llm_plan_fails(monkeypatch):
    from app.nl2sql import workflow
    from app.nl2sql.intent_guard import QuestionDecision

    calls = []

    def fake_guard(question, context=None, *, semantic_checks=True):
        calls.append(semantic_checks)
        if semantic_checks:
            return QuestionDecision(
                "clarification", "missing_metric", "请指定退款指标", []
            )
        return QuestionDecision()

    monkeypatch.setattr(workflow, "assess_question", fake_guard)
    monkeypatch.setattr(workflow, "_llm_planning_enabled", lambda: True)
    monkeypatch.setattr(workflow, "get_model_config", lambda: SimpleNamespace(enabled=True, active=True))
    monkeypatch.setattr(workflow, "plan_with_llm", lambda *args, **kwargs: (None, "invalid JSON"))
    monkeypatch.setattr(workflow, "_query_reference_date", lambda *args, **kwargs: "2026-02-28")
    state = workflow.plan_node({
        "question": "退款问题严重吗",
        "hits": [],
        "context": {},
        "force_rule": False,
    })
    assert calls == [False, True]
    assert state["decision"]["outcome"] == "clarification"
    assert state["plan"].tables == []


@pytest.mark.parametrize(
    ("field", "value", "expected_table"),
    [
        ("member_level", "普通", "customers"),
    ],
)
def test_filter_table_dependencies_are_closed(field, value, expected_table):
    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="sales_overview",
            metrics=["销售额"],
            filters=[structured_planner.PlanFilter(field=field, values=[value])],
        ),
        question="本月销售额",
    )
    assert expected_table in plan.tables


@pytest.mark.parametrize(
    ("intent", "metrics", "dimensions", "filters"),
    [
        ("marketing_efficiency", ["销售额"], [], []),
        ("category_analysis", ["销量", "销售额"], ["category"], []),
        ("refund_analysis", ["退款金额", "退款单数"], ["reason"], []),
    ],
)
def test_structured_planner_rejects_shapes_the_rule_generator_cannot_execute(
    intent, metrics, dimensions, filters
):
    with pytest.raises(ValueError, match="cannot render|exactly one"):
        structured_planner.validate_structured_plan(
            _draft(intent=intent, metrics=metrics, dimensions=dimensions, filters=filters),
            question="测试不可执行计划",
        )


def test_follow_up_inherits_time_and_filter_slots_without_model_repetition():
    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="sales_overview",
            metrics=["销售额"],
            filters=[structured_planner.PlanFilter(field="channel", values=["Web"])],
        ),
        question="那 Web 呢？",
        context={
            "question": "上个月华东各渠道销售额",
            "time_filter": "o.order_date BETWEEN '2026-01-01' AND '2026-01-31'",
            "time_range": "2026-01-01 至 2026-01-31",
            "region_filter": "o.region = '华东'",
            "region_label": "华东",
            "metrics": ["销售额"],
            "slots": {"filters": [{"field": "region", "operator": "eq", "values": ["华东"]}]},
        },
        reference_date="2026-02-28",
    )
    assert any("o.order_date BETWEEN" in item for item in plan.filters)
    assert any("o.region = '华东'" in item for item in plan.filters)
    assert any("o.channel = 'Web'" in item for item in plan.filters)
    assert plan.context["time_range"] == "2026-01-01 至 2026-01-31"
    assert plan.context["region_label"] == "华东"


def test_member_filter_requires_customers_table_and_keeps_sql_executable():
    from app.nl2sql.generator import generate_rule_sql
    from app.nl2sql.sql_engine import execute_sql, validate_sql

    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="sales_overview",
            metrics=["销售额"],
            filters=[structured_planner.PlanFilter(field="member_level", values=["普通"])],
            time_window="current_month",
        ),
        question="本月普通会员的销售额",
        reference_date="2026-02-28",
    )
    assert "customers" in plan.tables
    sql = generate_rule_sql("本月普通会员的销售额", plan)
    assert "JOIN customers c" in sql
    assert "c.member_level = '普通'" in sql
    assert validate_sql(sql)
    assert execute_sql(sql)["rows"]


def test_new_question_clears_prior_filters_unless_explicitly_repeated():
    plan = structured_planner.validate_structured_plan(
        _draft(intent="sales_overview", metrics=["销售额"], dimensions=["channel"], time_window="current_month"),
        question="本月全国各渠道销售额",
        context={
            "question": "上个月华东各渠道销售额",
            "region_label": "华东",
            "region_filter": "o.region = '华东'",
            "slots": {"filters": [{"field": "region", "operator": "eq", "values": ["华东"]}]},
        },
        reference_date="2026-02-28",
    )
    assert not any("o.region" in item for item in plan.filters)
    assert plan.context["region_label"] == ""
    assert plan.context["region_filter"] == ""


def test_follow_up_reads_multi_value_channel_slots():
    plan = structured_planner.validate_structured_plan(
        _draft(intent="sales_overview", metrics=["销售额"], dimensions=["channel"], time_window="last_month"),
        question="那上个月呢？",
        context={
            "question": "本月 App 和 Web 销售额",
            "channel_label": ["App", "Web"],
            "channel_filter": "o.channel IN ('App', 'Web')",
            "slots": {"filters": [{"field": "channel", "operator": "in", "values": ["App", "Web"]}]},
        },
        reference_date="2026-02-28",
    )
    assert any("o.channel IN ('App', 'Web')" in item for item in plan.filters)
    assert plan.context["channel_label"] == ["App", "Web"]


def test_sales_intent_rejects_refund_rate():
    with pytest.raises(ValueError, match="cannot render metrics"):
        structured_planner.validate_structured_plan(
            _draft(intent="sales_overview", metrics=["退款率"]),
            question="本月退款率",
        )


def test_follow_up_inherits_refund_reason_slot_and_table():
    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="refund_analysis",
            metrics=["退款金额"],
            dimensions=["reason"],
            time_window="last_month",
        ),
        question="那上个月呢？",
        context={
            "question": "本月质量问题退款金额",
            "slots": {"filters": [
                {"field": "refund_reason", "operator": "eq", "values": ["质量问题"]}
            ]},
        },
        reference_date="2026-02-28",
    )
    assert "refunds" in plan.tables
    assert any("r.reason = '质量问题'" in item for item in plan.filters)


@pytest.mark.parametrize(
    "question",
    [
        "这个月全国各渠道销售额",
        "换成全部渠道",
        "那全部渠道呢？",
    ],
)
def test_new_scope_phrases_do_not_inherit_old_filters(question):
    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="sales_overview",
            metrics=["销售额"],
            dimensions=["channel"],
            time_window="current_month",
        ),
        question=question,
        context={
            "question": "上个月华东 App 和 Web 各渠道销售额",
            "time_filter": "o.order_date BETWEEN '2026-01-01' AND '2026-01-31'",
            "time_range": "2026-01-01 至 2026-01-31",
            "region_label": "华东",
            "region_filter": "o.region = '华东'",
            "channel_label": ["App", "Web"],
            "channel_filter": "o.channel IN ('App', 'Web')",
            "slots": {
                "filters": [
                    {"field": "region", "operator": "eq", "values": ["华东"]},
                    {"field": "channel", "operator": "in", "values": ["App", "Web"]},
                ]
            },
        },
        reference_date="2026-02-28",
    )
    assert not any("o.region" in item for item in plan.filters)
    assert not any("o.channel" in item for item in plan.filters)
    assert plan.context["region_label"] == ""
    assert plan.context["channel_label"] == ""


@pytest.mark.parametrize(
    "question",
    ["这个月全国各渠道销售额", "换成全部渠道", "那全部渠道呢？"],
)
def test_rule_planner_uses_same_new_scope_semantics(question):
    from app.nl2sql.planner import make_plan

    plan = make_plan(
        question,
        [],
        context={
            "question": "上个月华东 App 和 Web 各渠道销售额",
            "time_filter": "o.order_date BETWEEN '2026-01-01' AND '2026-01-31'",
            "time_range": "2026-01-01 至 2026-01-31",
            "region_filter": "o.region = '华东'",
            "region_label": "华东",
            "channel_filter": "o.channel IN ('App', 'Web')",
            "channel_label": ["App", "Web"],
            "metrics": ["销售额"],
        },
        reference_date="2026-02-28",
    )
    assert not any("o.region" in item for item in plan.filters)
    assert not any("o.channel" in item for item in plan.filters)
    assert plan.context["region_filter"] == ""
    assert plan.context["channel_filter"] == ""


@pytest.mark.parametrize(
    "question",
    ["换成客单价", "再看订单数", "那客单价呢？"],
)
def test_metric_bearing_followups_inherit_prior_time_and_region(question):
    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="sales_overview",
            metrics=["客单价" if "客单价" in question else "订单数"],
            dimensions=[],
        ),
        question=question,
        context={
            "question": "上个月华东销售额",
            "time_filter": "o.order_date BETWEEN '2026-01-01' AND '2026-01-31'",
            "time_range": "2026-01-01 至 2026-01-31",
            "region_label": "华东",
            "region_filter": "o.region = '华东'",
            "slots": {
                "filters": [
                    {"field": "region", "operator": "eq", "values": ["华东"]},
                ]
            },
        },
        reference_date="2026-02-28",
    )
    assert any("o.order_date BETWEEN" in item for item in plan.filters)
    assert any("o.region = '华东'" in item for item in plan.filters)
    assert plan.context["time_range"] == "2026-01-01 至 2026-01-31"


@pytest.mark.parametrize(
    "question",
    ["本月客单价", "这个月订单数"],
)
def test_ordinary_metric_questions_do_not_inherit_old_filters(question):
    plan = structured_planner.validate_structured_plan(
        _draft(
            intent="sales_overview",
            metrics=["客单价" if "客单价" in question else "订单数"],
            dimensions=[],
            time_window="current_month",
        ),
        question=question,
        context={
            "question": "上个月华东销售额",
            "time_filter": "o.order_date BETWEEN '2026-01-01' AND '2026-01-31'",
            "time_range": "2026-01-01 至 2026-01-31",
            "region_label": "华东",
            "region_filter": "o.region = '华东'",
            "slots": {"filters": [{"field": "region", "operator": "eq", "values": ["华东"]}]},
        },
        reference_date="2026-02-28",
    )
    assert not any("o.region" in item for item in plan.filters)
    assert plan.context["region_label"] == ""
