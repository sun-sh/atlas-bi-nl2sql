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
                yield SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=content), finish_reason=reason
                    )],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                )
        return chunks()

    config = SimpleNamespace(enabled=True, active=True, model="test-model", provider="custom")
    monkeypatch.setattr(insight, "get_model_config", lambda: config)
    monkeypatch.setattr(insight, "make_client", lambda *_args, **_kwargs: SimpleNamespace(
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


def test_context_numbers_and_next_sentence_do_not_create_false_positives():
    from app.nl2sql.result_insight import validate_narrative

    digest = _grounded_digest()
    digest["question"] = "近30天各渠道销售额，乘100"
    digest["time_range"] = "2026-02-01 至 2026-02-28"
    check = validate_narrative(
        "结论：App 渠道销售额最高，为 120。关键发现：Web 为 80。",
        digest,
    )
    assert check["passed"] is True

    check = validate_narrative(
        "结论：App 渠道销售额最高。近30天数据乘100。Web 为 80。",
        digest,
    )
    assert check["passed"] is True


def test_actual_non_top_claim_still_fails():
    from app.nl2sql.result_insight import validate_narrative

    check = validate_narrative("结论：Web 最高。", _grounded_digest())
    assert check["passed"] is False


def test_rounded_average_and_ratio_values_are_grounded():
    from app.nl2sql.result_insight import validate_narrative

    digest = _grounded_digest()
    digest["metrics"] = [
        {"name": "ROI", "sum": None, "avg": 1.6625, "min": 0.49, "max": 2.27}
    ]
    digest["preferred_metric"] = "ROI"
    digest["ranking"] = [
        {"label": "App", "value": "2.27", "share_pct": None},
        {"label": "Web", "value": "2.21", "share_pct": None},
        {"label": "Douyin", "value": "0.49", "share_pct": None},
    ]
    digest["sample_rows"] = [
        {"渠道": "App", "ROI": 2.27},
        {"渠道": "Web", "ROI": 2.21},
        {"渠道": "Douyin", "ROI": 0.49},
    ]
    check = validate_narrative(
        "结论：App ROI 最高，为 2.27。关键发现：Douyin 最低为 0.49，均值 1.66。",
        digest,
    )
    assert check["passed"] is True


def test_non_target_metric_claim_is_not_a_metric_ranking_error():
    from app.nl2sql.result_insight import validate_narrative

    digest = {
        "row_count": 4,
        "facts": ["返回 4 行"],
        "metrics": [
            {"name": "客单价", "sum": 1612.61, "avg": 403.15, "min": 368.16, "max": 483.0}
        ],
        "sample_rows": [
            {"渠道": "Web", "客单价": 483.0},
            {"渠道": "App", "客单价": 368.16},
        ],
        "ranking": [
            {"label": "Web", "value": "483", "share_pct": 29.95},
            {"label": "App", "value": "368.16", "share_pct": 22.83},
        ],
        "preferred_metric": "客单价",
        "dimension": "渠道",
        "trend": None,
    }
    check = validate_narrative(
        "结论：Web 客单价最高。关键发现：App 订单数最高。",
        digest,
    )
    assert check["passed"] is True

    check = validate_narrative("结论：App 客单价最高。", digest)
    assert check["passed"] is False


def test_rejected_candidate_is_preserved_in_warning(monkeypatch):
    from types import SimpleNamespace
    from app.nl2sql import result_insight as insight

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        def chunks():
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="结论：App 最高。"),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
        return chunks()

    monkeypatch.setattr(insight, "get_model_config", lambda: SimpleNamespace(
        enabled=True, active=True, model="test-model", provider="custom",
    ))
    monkeypatch.setattr(insight, "make_client", lambda *_args, **_kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    ))
    digest = {
        "row_count": 2,
        "facts": ["返回 2 行"],
        "metrics": [{"name": "销售额", "sum": 200, "avg": 100, "min": 80, "max": 120}],
        "sample_rows": [{"渠道": "App", "销售额": 120}, {"渠道": "Web", "销售额": 80}],
        "ranking": [
            {"label": "App", "value": "120", "share_pct": 60},
            {"label": "Web", "value": "80", "share_pct": 40},
        ],
        "preferred_metric": "销售额",
        "dimension": "渠道",
        "trend": None,
    }
    # The candidate is numerically grounded but claims Web (non-top) as top.
    def create_wrong_top(**kwargs):
        calls.append(kwargs)
        def chunks():
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="结论：Web 最高。"),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
        return chunks()

    monkeypatch.setattr(insight, "make_client", lambda *_args, **_kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_wrong_top))
    ))
    events = list(insight.stream_result_narrative("各渠道销售额", SimpleNamespace(), digest))
    final = events[-1]["narrative"]
    assert final.mode == "digest"
    assert "候选解读：结论：Web 最高。" in final.fallback_reason


def test_natural_language_date_labels_do_not_count_as_hallucinated_numbers():
    from app.nl2sql.result_insight import validate_narrative

    digest = _grounded_digest()
    digest["sample_rows"] = [
        {"日期": "2026-02-15", "销售额": 100.0},
        {"日期": "2026-02-16", "销售额": 80.0},
    ]
    digest["trend"] = {
        "start_raw": 100.0,
        "end_raw": 80.0,
        "change_pct": -20.0,
    }
    check = validate_narrative(
        "结论：2月15日销售额为 100，随后到 2月16日降至 80，下降 20%。",
        digest,
    )
    assert check["passed"] is True



def test_small_hallucinated_fraction_is_rejected():
    from app.nl2sql.result_insight import validate_narrative

    digest = _grounded_digest()
    digest["metrics"] = [
        {"name": "ROI", "sum": None, "avg": 0.42, "min": 0.20, "max": 0.80}
    ]
    digest["preferred_metric"] = "ROI"
    check = validate_narrative("分析：平均 ROI 为 0.85。", digest)
    assert check["passed"] is False


def test_tied_top_metrics_may_both_be_called_highest():
    from app.nl2sql.result_insight import validate_narrative

    digest = _grounded_digest()
    digest["preferred_metric"] = "退款单数"
    digest["dimension"] = "退款原因"
    digest["ranking"] = [
        {"label": "物流太慢", "value": "9.00", "share_pct": 25.0},
        {"label": "尺码不合适", "value": "9.00", "share_pct": 25.0},
        {"label": "质量问题", "value": "8.00", "share_pct": 22.2},
    ]
    check = validate_narrative(
        "结论：物流太慢与尺码不合适并列最高。关键发现：两者各 9 单。",
        digest,
    )
    assert check["passed"] is True
