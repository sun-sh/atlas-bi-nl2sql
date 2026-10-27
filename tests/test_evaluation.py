from __future__ import annotations

from copy import deepcopy

import pytest

from app.nl2sql import evaluation
from app.nl2sql.models import QueryResult
from app.nl2sql.planner import make_plan
from app.nl2sql.sql_engine import execute_sql


def case_named(case_id):
    return next(case for case in evaluation.CASES if case["id"] == case_id)


def model_result(case, context=None, **overrides):
    rows = execute_sql(case["reference_sql"])["rows"]
    fields = dict(
        question=case["question"], sql=case["reference_sql"], rows=rows,
        columns=list(rows[0]), chart_type="table", answer="依据查询结果生成的模型解读。",
        plan=make_plan(case["question"], [], context), retrieval=[],
        attempts=[{"status": "success"}], elapsed_ms=1, mode="llm",
        narrative_mode="model", narrative_model="test-model",
    )
    fields.update(overrides)
    return QueryResult(**fields)


@pytest.fixture
def active_model(monkeypatch):
    monkeypatch.setattr(evaluation, "_model_state", lambda: {
        "configured": True, "active": True, "model": "test-model", "provider": "test",
    })


def test_suite_coverage_and_independent_business_references():
    cases = evaluation.CASES
    assert len(cases) == len({case["id"] for case in cases}) == 100
    assert len({case["question"] for case in cases}) == 100
    assert len({case["intent"] for case in cases}) == 4
    assert sum(bool(case["setup"]) for case in cases) == 11
    assert {case["metric"] for case in cases} >= {"销售额", "销量", "退款率", "退款单数", "退款金额", "ROI", "客单价"}
    for case in cases:
        assert execute_sql(case["reference_sql"])["rows"]
        if case["setup"]:
            assert not case_named(case["setup"])["setup"]

    # Fixed seed anchors catch accidental changes in the benchmark's grain.
    assert execute_sql(case_named("sales-east")["reference_sql"])["rows"] == [{"销售额": 79947.85}]
    assert execute_sql(case_named("food-units")["reference_sql"])["rows"] == [{"销量": 263}]
    roi = execute_sql(case_named("roi-channels")["reference_sql"])["rows"]
    revenue = execute_sql(
        "SELECT SUM(paid_amount) AS revenue FROM orders WHERE status = 'paid' "
        "AND channel = 'App' AND order_date BETWEEN '2026-01-01' AND '2026-01-31'"
    )["rows"][0]["revenue"]
    spend = execute_sql(
        "SELECT SUM(spend) AS spend FROM marketing_spend WHERE channel = 'App' "
        "AND spend_date BETWEEN '2026-01-01' AND '2026-01-31'"
    )["rows"][0]["spend"]
    assert next(row["ROI"] for row in roi if row["channel"] == "App") == round(revenue / spend, 2)


def test_wrong_amount_missing_paid_filter_and_nonfinite_values_are_rejected():
    case = case_named("sales-east")
    rows = execute_sql(case["reference_sql"].replace("o.status = 'paid'", "1=1"))["rows"]
    assert "销售额不符" in evaluation._check_rows(case, rows)
    for value in (None, True, float("nan"), float("inf"), 79949, "not a number"):
        assert evaluation._check_rows(case, [{"销售额": value}])
    assert evaluation._check_rows(case, [{"Total_Sales": "79947.85", "订单数": 214}]) == ""
    assert evaluation._check_rows(case, [{"销售额": 79947.85, "revenue": 79947.85}]) == ""
    assert evaluation._check_rows(case, [{"总销售额": 79947.85}]) == ""


@pytest.mark.parametrize("case_id", ["category-sales", "roi-channels", "daily-sales", "channel-aov", "refund-month"])
def test_all_groups_and_values_are_checked(case_id):
    case = case_named(case_id)
    rows = execute_sql(case["reference_sql"])["rows"]
    changed = deepcopy(rows)
    changed[-1][case["metric"]] += 1
    assert evaluation._check_rows(case, changed)
    duplicate = [deepcopy(rows[0]), *deepcopy(rows[:-1])]
    assert "重复" in evaluation._check_rows(case, duplicate)
    assert "行数不符" in evaluation._check_rows(case, rows[:-1])
    assert evaluation._check_rows(case, list(reversed(rows)))


def test_aliases_and_channel_labels_do_not_change_the_score():
    case = case_named("roi-channels")
    rows = execute_sql(case["reference_sql"])["rows"]
    labels = {"Douyin": "抖音", "MiniProgram": "小程序", "Web": "网页", "App": "APP"}
    renamed = [{"渠道": labels[row["channel"]], "roi": row["ROI"]} for row in rows]
    assert evaluation._check_rows(case, renamed) == ""


def test_single_channel_roi_accepts_scalar_metric_without_repeating_filter():
    case = case_named("roi-douyin")
    expected = execute_sql(case["reference_sql"])["rows"][0]["ROI"]
    assert evaluation._check_rows(case, [{"ROI": expected}]) == ""
    assert evaluation._check_rows(case, [{"ROI": expected + 1}])
    assert evaluation._check_rows(case, [{"channel": "App", "ROI": expected}])
    assert evaluation._check_rows(case, [{"channel": "Douyin", "渠道": "App", "ROI": expected}])
    assert evaluation._check_rows(case_named("roi-channels"), [{"ROI": expected}])


def test_each_followup_uses_a_fresh_model_run_and_real_plan_context(monkeypatch, active_model):
    calls = []

    def fake_query(question, *, context=None, allow_rule_fallback):
        assert allow_rule_fallback is False
        case = next(case for case in evaluation.CASES if case["question"] == question)
        calls.append((case["id"], context))
        if case["setup"]:
            assert context["question"] == case_named(case["setup"])["question"]
            assert context["time_filter"]
        else:
            assert context is None
        return model_result(case, context)

    monkeypatch.setattr(evaluation, "run_query", fake_query)
    report = evaluation.run_evaluation()
    assert report["passed"] == report["total"] == 100
    assert report["execution_rate"] == report["model_rate"] == 100
    assert len(calls) == 111
    followups = [case for case in report["cases"] if case["setup_question"]]
    assert all(case["attempts"] == 2 for case in followups)

    contexts = dict(calls)
    region = make_plan("那华南呢？", [], contexts["followup-region"])
    assert "o.region = '华南'" in region.filters
    assert "o.region = '华东'" not in region.filters
    month = make_plan("那本月呢？", [], contexts["followup-month"])
    assert "o.order_date >= '2026-02-01' AND o.order_date <= '2026-02-28'" in month.filters
    assert "o.region = '华南'" in month.filters
    channel = make_plan("换成小程序渠道呢？", [], contexts["followup-channel"])
    assert "o.channel = 'MiniProgram'" in channel.filters
    assert "o.channel = 'Web'" not in channel.filters
    assert "o.region = '华北'" in channel.filters


@pytest.mark.parametrize("overrides, reason", [
    ({"mode": "rule"}, "未由模型"),
    ({"mode": "rule-repair"}, "未由模型"),
    ({"mode": "model-failed", "rows": []}, "严格模型评测"),
    ({"mode": "model-repair-failed", "rows": []}, "严格模型评测"),
    ({"attempts": [{"status": "failed"}]}, "未成功执行"),
    ({"narrative_mode": "digest"}, "确定性摘要"),
    ({"answer": "   "}, "解读为空"),
])
def test_correct_values_cannot_mask_model_failures(monkeypatch, active_model, overrides, reason):
    case = case_named("sales-east")
    monkeypatch.setattr(evaluation, "CASES", (case,))
    monkeypatch.setattr(evaluation, "run_query", lambda *args, **kwargs: model_result(case, **overrides))
    report = evaluation.run_evaluation()
    assert report["passed"] == 0
    assert reason in report["cases"][0]["error"]


def test_model_repair_can_pass_when_both_model_stages_succeed(monkeypatch, active_model):
    case = case_named("sales-east")
    monkeypatch.setattr(evaluation, "CASES", (case,))
    monkeypatch.setattr(evaluation, "run_query", lambda *args, **kwargs: model_result(case, mode="llm-repair"))
    assert evaluation.run_evaluation()["passed"] == 1


def test_failed_setup_stops_followup_and_cannot_count_as_completed(monkeypatch, active_model):
    setup, followup = case_named("sales-east"), case_named("followup-region")
    monkeypatch.setattr(evaluation, "CASES", (setup, followup))
    calls = []

    def fake_query(question, **kwargs):
        calls.append(question)
        assert question == setup["question"]
        return model_result(setup, rows=[{"销售额": 1}])

    monkeypatch.setattr(evaluation, "run_query", fake_query)
    report = evaluation.run_evaluation()
    assert len(calls) == 2
    assert report["passed"] == 0
    result = report["cases"][1]
    assert result["error"].startswith("前置问题未通过")
    assert result["executed"] is False
    assert result["model_used"] is False


def test_exception_does_not_abort_the_remaining_cases(monkeypatch, active_model):
    first, second = case_named("sales-east"), case_named("sales-south")
    monkeypatch.setattr(evaluation, "CASES", (first, second))

    def fake_query(question, **kwargs):
        if question == first["question"]:
            raise RuntimeError("model timeout")
        return model_result(second)

    monkeypatch.setattr(evaluation, "run_query", fake_query)
    report = evaluation.run_evaluation()
    assert report["passed"] == 1
    assert report["cases"][0]["error"] == "model timeout"
    assert report["cases"][1]["hit"] is True


def test_two_isolated_workers_can_run_without_model_or_network():
    from concurrent.futures import ProcessPoolExecutor
    from app.nl2sql.model_config import ModelConfig
    config = ModelConfig(False, "disabled", "", "", "")
    cases = [case_named("sales-east"), case_named("sales-south")]
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(evaluation._evaluate_with_config, case, config) for case in cases]
        results = [future.result(timeout=45) for future in futures]
    assert [result["id"] for result in results] == [case["id"] for case in cases]
    assert all(result["hit"] is False for result in results)
    assert all(result["sql_mode"] == "model-failed" for result in results)
    assert all(result["generation_retries"] == 1 for result in results)


def test_failure_diagnostics_count_setup_and_narrative_separately():
    cases = [
        {"intent": "sales_overview", "hit": True, "repair_used": True},
        {"intent": "sales_overview", "hit": False, "executed": False, "error": "前置问题未通过"},
        {"intent": "category_analysis", "hit": False, "executed": True, "narrative_mode": "digest"},
    ]
    report = evaluation._diagnostics(cases)
    assert report["failures"] == {"setup": 1, "narrative": 1}
    assert report["self_healing"] == {"attempted_cases": 1, "passed_cases": 1}
    assert next(item for item in report["by_intent"] if item["intent"] == "sales_overview")["hit_rate"] == 50
