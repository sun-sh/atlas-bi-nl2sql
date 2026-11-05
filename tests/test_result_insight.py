from __future__ import annotations

from app.nl2sql.models import QueryPlan
from app.nl2sql.result_insight import build_result_digest


def _mock_narrator(monkeypatch, responses):
    from types import SimpleNamespace
    from app.nl2sql import result_insight as insight
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        response = responses[len(calls) - 1]
        def chunks():
            for content, reason in response:
                if isinstance(content, Exception):
                    raise content
                yield SimpleNamespace(choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=content), finish_reason=reason)])
        return chunks()

    config = SimpleNamespace(enabled=True, active=True, model="test-model", provider="custom")
    monkeypatch.setattr(insight, "get_model_config", lambda: config)
    monkeypatch.setattr(insight, "make_client", lambda c: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    return insight, calls


def test_truncated_stream_retries_and_replaces_partial_answer(monkeypatch):
    insight, calls = _mock_narrator(monkeypatch, [
        [("残缺内容", "length")], [("完整结论", "stop")],
    ])
    events = list(insight.stream_result_narrative("销售额", QueryPlan("sales_overview", ["orders"]),
                                                 {"row_count": 1, "facts": ["销售额100"]}))
    assert [call["max_tokens"] for call in calls] == [1800, 3600]
    assert {"type": "reset", "text": ""} in events
    final = events[-1]["narrative"]
    assert final.mode == "model" and final.text == "完整结论" and final.retries == 1


def test_interrupted_stream_cannot_be_counted_as_completed_model_answer(monkeypatch):
    insight, calls = _mock_narrator(monkeypatch, [
        [("残缺", None), (RuntimeError("stream failure"), None)],
        [("再次残缺", "length")],
    ])
    events = list(insight.stream_result_narrative("销售额", QueryPlan("sales_overview", ["orders"]),
                                                 {"row_count": 1, "facts": ["销售额100"]}))
    final = events[-1]["narrative"]
    assert len(calls) == 2
    assert final.mode == "digest" and final.retries == 1 and final.fallback_reason
    assert events[-2] == {"type": "reset", "text": final.text}


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


def _grounded_digest():
    plan = QueryPlan(
        intent="sales_overview",
        tables=["orders"],
        metrics=["销售额"],
    )
    return build_result_digest(
        "各渠道销售额",
        plan,
        [{"渠道": "App", "销售额": 120.0}, {"渠道": "Web", "销售额": 80.0}],
        ["渠道", "销售额"],
        2,
        False,
    )


def test_narrative_accepts_only_digest_numbers_and_correct_top_channel():
    from app.nl2sql.result_insight import validate_narrative

    digest = _grounded_digest()
    good = validate_narrative(
        "结论：App 渠道销售额最高。\n关键发现：- App 为 120；Web 为 80。",
        digest,
    )
    assert good["passed"] is True
    assert good["errors"] == []


def test_narrative_rejects_unfounded_number():
    from app.nl2sql.result_insight import validate_narrative

    check = validate_narrative("结论：App 销售额为 999。", _grounded_digest())
    assert check["passed"] is False
    assert any("摘要外数值" in error for error in check["errors"])


def test_narrative_rejects_wrong_top_channel_and_trend_direction():
    from app.nl2sql.result_insight import validate_narrative

    plan = QueryPlan(intent="sales_overview", tables=["orders"], metrics=["销售额"])
    digest = build_result_digest(
        "按日看本月销售额趋势",
        plan,
        [
            {"日期": "2026-02-01", "销售额": 100.0},
            {"日期": "2026-02-02", "销售额": 80.0},
        ],
        ["日期", "销售额"],
        2,
        False,
    )
    digest["ranking"] = [
        {"label": "2026-02-01", "value": "100", "share_pct": 55.56},
        {"label": "2026-02-02", "value": "80", "share_pct": 44.44},
    ]
    check = validate_narrative(
        "结论：2026-02-02 最高，销售额下降 20%。",
        digest,
    )
    assert check["passed"] is False
    assert any("非榜首" in error for error in check["errors"])

    digest["trend"] = {**digest["trend"], "change_pct": 20.0}
    check = validate_narrative(
        "结论：2026-02-01 最高，销售额下降 20%。",
        digest,
    )
    assert check["passed"] is False
    assert any("趋势方向" in error for error in check["errors"])
