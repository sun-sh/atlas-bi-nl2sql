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
