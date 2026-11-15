from __future__ import annotations

import pytest

from app.nl2sql.planner import make_plan
from app.nl2sql.semantic import dimension_label
from app.nl2sql.workflow import run_query


CONSISTENCY_QUERIES = [
    "近90天各品类客单价",
    "各地区销量排名",
    "本月每天的退款金额",
    "上个月华东地区各品类销售额",
    "各会员等级的销售额",
    "本月各渠道退款率",
    "本月各渠道投放花费",
    "上个月各渠道归因订单数",
    "本月App渠道销售额",
    "本月投放花费是多少",
]


KNOWN_DERIVED_COLUMNS = {
    "退款金额": {"退款单数"},
    "退款率": {"退款单数", "订单数"},
}


def _assert_bidirectional_contract(result):
    expected = set(result.plan.metrics) | {
        dimension_label(item) for item in result.plan.dimensions
    }
    derived = set()
    for metric in result.plan.metrics:
        derived.update(KNOWN_DERIVED_COLUMNS.get(metric, set()))
    derived.update(result.derived_columns)
    assert set(result.plan.metrics) <= set(result.columns)
    assert expected <= set(result.columns)
    assert set(result.columns) <= expected | derived, (
        result.question, result.plan.as_dict(), result.columns, result.sql
    )


@pytest.mark.parametrize("question", CONSISTENCY_QUERIES)
def test_plan_and_sql_columns_are_bidirectionally_consistent(question):
    result = run_query(
        question,
        narrate=False,
        allow_rule_fallback=True,
        force_rule=True,
    )
    assert result.outcome == "answered", result.attempts
    _assert_bidirectional_contract(result)


@pytest.mark.parametrize(
    ("question", "period"),
    [
        ("本月销售额", "2026-02-01"),
        ("上个月销售额", "2026-01-01"),
        ("近30天销售额", "2026-01-30"),
    ],
)
def test_ungrouped_sales_is_a_scalar(question, period):
    result = run_query(
        question,
        narrate=False,
        allow_rule_fallback=True,
        force_rule=True,
    )
    assert result.outcome == "answered", result.attempts
    assert len(result.rows) == 1
    assert result.columns == ["销售额"]
    assert result.plan.dimensions == []
    assert period in result.sql


def test_region_filter_does_not_become_a_region_dimension():
    plan = make_plan("上个月华东地区销售额", [])
    assert plan.dimensions == []
    assert any("o.region = '华东'" in item for item in plan.filters)


def test_region_breakdown_still_requires_an_explicit_grouping():
    plan = make_plan("上个月各地区销售额", [])
    assert plan.dimensions == ["region"]


def test_channel_filter_does_not_become_a_channel_dimension():
    plan = make_plan("本月App渠道销售额", [])
    assert plan.dimensions == []
    assert any("o.channel = 'App'" in item for item in plan.filters)


def test_planner_uses_metric_declared_dimensions_only():
    plan = make_plan("本月各渠道退款率", [])
    assert plan.intent == "refund_analysis"
    assert plan.metrics == ["退款率"]
    assert plan.dimensions == ["channel"]


def test_member_level_sales_plan_joins_customer_dimension():
    result = run_query(
        "各会员等级的销售额",
        narrate=False,
        allow_rule_fallback=True,
        force_rule=True,
    )
    assert result.plan.dimensions == ["member_level"]
    assert "JOIN customers" in result.sql
    assert result.columns == ["会员等级", "销售额"]


def test_attributed_orders_routes_to_marketing_table():
    result = run_query(
        "上个月各渠道归因订单数",
        narrate=False,
        allow_rule_fallback=True,
        force_rule=True,
    )
    assert result.outcome == "answered", result.attempts
    assert result.plan.intent == "marketing_efficiency"
    assert "marketing_spend" in result.plan.tables
    assert "marketing_spend" in result.sql
    assert result.columns == ["渠道", "归因订单数"]
    assert len(result.rows) == 4


def test_marketing_scalar_does_not_invent_a_channel_dimension():
    result = run_query(
        "本月投放花费是多少",
        narrate=False,
        allow_rule_fallback=True,
        force_rule=True,
    )
    assert result.outcome == "answered", result.attempts
    assert result.plan.dimensions == []
    assert result.columns == ["投放花费", "归因订单数"]
    assert len(result.rows) == 1
