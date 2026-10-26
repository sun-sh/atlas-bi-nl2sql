from __future__ import annotations

from app.nl2sql.planner import make_plan
from app.nl2sql.retrieval import HybridSchemaRetriever


def test_follow_up_inherits_time_and_metric_but_replaces_region():
    retriever = HybridSchemaRetriever()
    initial_hits = retriever.search("上个月华东地区销售额")
    initial = make_plan("上个月华东地区销售额", initial_hits)
    context = {
        **initial.context,
        "intent": initial.intent,
        "tables": initial.tables,
        "metrics": initial.metrics,
        "time_range": initial.time_range,
        "chart_type": initial.chart_type,
        "question": "上个月华东地区销售额",
    }
    follow_up = make_plan("那华南呢", retriever.search("华南地区销售额"), context)
    assert follow_up.intent == "sales_overview"
    assert follow_up.metrics == ["销售额"]
    assert follow_up.time_range == "2026-01-01 至 2026-01-31"
    assert "o.region = '华南'" in follow_up.filters
    assert not any("华东" in item for item in follow_up.filters)
    assert any("时间范围" in item for item in follow_up.inherited)
    assert follow_up.source_question == "上个月华东地区销售额"


def test_new_explicit_dimension_does_not_reuse_old_region():
    context = {
        "intent": "sales_overview",
        "tables": ["orders"],
        "metrics": ["销售额"],
        "time_range": "2026-01-01 至 2026-01-31",
        "chart_type": "bar",
        "time_filter": "o.order_date BETWEEN '2026-01-01' AND '2026-01-31'",
        "region_filter": "o.region = '华东'",
        "region_label": "华东",
        "question": "上个月华东销售额",
    }
    plan = make_plan("上个月各渠道销售额", [], context)
    assert plan.context.get("region_label") == ""
    assert not any("region" in item for item in plan.filters)
    assert any("channel" in item or "o.channel" in item for item in plan.filters) is False
