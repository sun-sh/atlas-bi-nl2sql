from __future__ import annotations

from app.nl2sql.models import QueryPlan
from app.nl2sql.result_insight import build_result_digest


def test_ratio_metrics_use_average_and_never_show_share():
    plan = QueryPlan(
        intent="marketing_efficiency",
        tables=["marketing_spend"],
        metrics=["ROI"],
    )
    digest = build_result_digest(
        "上个月各渠道ROI",
        plan,
        [
            {"channel": "App", "roi": 0.017547},
            {"channel": "Douyin", "roi": 0.016507},
            {"channel": "Web", "roi": 0.014943},
        ],
        ["channel", "roi"],
        3,
        False,
    )
    assert digest["preferred_metric"] == "ROI"
    assert digest["metrics"][0]["aggregation"] == "avg"
    assert digest["metrics"][0]["sum"] is None
    assert digest["ranking_comparable"] is False
    assert all(item["share_pct"] is None for item in digest["ranking"])
    assert not any("占比" in fact for fact in digest["facts"])


def test_english_refund_rate_column_maps_to_chinese_metric():
    plan = QueryPlan(
        intent="refund_analysis",
        tables=["orders", "refunds"],
        metrics=["退款率"],
    )
    digest = build_result_digest(
        "退款率最高的品类是什么？",
        plan,
        [{"category": "美妆个护", "refund_rate": 0.05846}],
        ["category", "refund_rate"],
        1,
        False,
    )
    assert digest["preferred_metric"] == "退款率"
    assert digest["metrics"][0]["aggregation"] == "avg"
    assert digest["metrics"][0]["sum"] is None
    assert digest["ranking_comparable"] is False
    assert not any("占比" in fact for fact in digest["facts"])
