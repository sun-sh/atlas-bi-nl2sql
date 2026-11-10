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


def test_answer_and_plan_scores_are_independent(monkeypatch, active_model):
    case = case_named("sales-east")
    monkeypatch.setattr(evaluation, "CASES", (case,))

    # Correct answer, wrong plan metadata.
    wrong_plan = model_result(case)
    wrong_plan.plan.intent = "refund_analysis"
    monkeypatch.setattr(evaluation, "run_query", lambda *args, **kwargs: wrong_plan)
    report = evaluation.run_evaluation()
    assert report["answer_correct"] == 1
    assert report["plan_correct"] == 0

    # Correct plan metadata, answer rejected by the independent row oracle.
    correct_plan_bad_rows = model_result(case, rows=[{"销售额": 1}])
    monkeypatch.setattr(evaluation, "run_query", lambda *args, **kwargs: correct_plan_bad_rows)
    report = evaluation.run_evaluation()
    assert report["answer_correct"] == 0
    assert report["plan_correct"] == 1
    assert report["cases"][0]["answer_error"]
    assert report["cases"][0]["plan_error"] is None


def test_plan_correct_checks_expected_time_range(monkeypatch, active_model):
    case = case_named("followup-month")
    setup = case_named(case["setup"])
    monkeypatch.setattr(evaluation, "CASES", (setup, case))
    calls = []
    def fake_query(question, **kwargs):
        item = setup if question == setup["question"] else case
        calls.append(question)
        return model_result(item, context=kwargs.get("context"))
    monkeypatch.setattr(evaluation, "run_query", fake_query)
    report = evaluation.run_evaluation()
    # Both selected cases run in the suite; the follow-up is the one whose
    # expected time range must be checked.
    assert report["answer_correct"] == 2
    assert report["plan_correct"] == 2


def test_unavailable_model_has_neither_answer_nor_plan_correct(monkeypatch):
    case = case_named("sales-east")
    monkeypatch.setattr(evaluation, "CASES", (case,))
    monkeypatch.setattr(evaluation, "_model_state", lambda: {
        "configured": False, "active": False, "model": "", "provider": "",
    })
    report = evaluation.run_evaluation()
    assert report["answer_correct"] == 0
    assert report["plan_correct"] == 0


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
    assert result["plan_correct"] is False
    assert "前置问题未通过" in (result["plan_error"] or "")


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
    assert report["failure_reasons"]["setup"] == 1
    assert report["failure_reasons"]["other"] == 1
    assert report["self_healing"] == {"attempted_cases": 1, "passed_cases": 1}
    assert next(item for item in report["by_intent"] if item["intent"] == "sales_overview")["hit_rate"] == 50


def test_scope_guard_suite_passes():
    from app.nl2sql.evaluation import run_scope_evaluation

    report = run_scope_evaluation()
    assert report["suite_version"] == "scope-guard-v1"
    assert report["total"] >= 7
    assert report["passed"] == report["total"]
    assert all(case["sql"] == "" and case["rows"] == 0 for case in report["cases"])


def test_natural_language_time_range_is_checked(monkeypatch):
    from app.nl2sql import workflow
    from app.nl2sql.evaluation import run_natural_language_evaluation

    original = workflow.run_query

    def wrong_time(question, **kwargs):
        result = original(question, **kwargs)
        if question == "这个月每天卖得怎么样？":
            result.plan.time_range = "全部数据（2025-10-01 至 2026-02-28）"
        return result

    monkeypatch.setattr(workflow, "run_query", wrong_time)
    report = run_natural_language_evaluation()
    case = next(item for item in report["cases"] if item["question"] == "这个月每天卖得怎么样？")
    assert case["time_range_ok"] is False
    assert case["end_to_end_pass"] is False


def test_natural_language_metric_must_actually_execute(monkeypatch):
    from app.nl2sql import workflow
    from app.nl2sql.evaluation import run_natural_language_evaluation
    from app.nl2sql.models import QueryResult

    original = workflow.run_query

    def wrong_metric(question, **kwargs):
        result = original(question, **kwargs)
        if question == "每单平均花多少钱？":
            return QueryResult(
                question=result.question,
                sql=result.sql,
                rows=[{"渠道": "App", "销售额": 100.0}],
                columns=["渠道", "销售额"],
                chart_type=result.chart_type,
                answer=result.answer,
                plan=result.plan,
                retrieval=result.retrieval,
                attempts=result.attempts,
                elapsed_ms=result.elapsed_ms,
                mode=result.mode,
            )
        return result

    monkeypatch.setattr(workflow, "run_query", wrong_metric)
    report = run_natural_language_evaluation()
    case = next(item for item in report["cases"] if item["question"] == "每单平均花多少钱？")
    assert case["metric_ok"] is True
    assert case["metric_column_ok"] is False
    assert case["end_to_end_pass"] is False
    assert report["passed"] == report["total"] - 1


def test_natural_language_suite_and_retrieval_ablation():
    from app.nl2sql.evaluation import (
        run_natural_language_evaluation,
        run_natural_language_scope_evaluation,
        run_retrieval_ablation,
    )
    from app.nl2sql.holdout import NATURAL_HOLDOUT_CASES

    scope = run_natural_language_scope_evaluation()
    assert scope["total"] == len(NATURAL_HOLDOUT_CASES)
    assert scope["passed"] == scope["total"]

    natural = run_natural_language_evaluation()
    assert natural["total"] == len(NATURAL_HOLDOUT_CASES)
    assert natural["answerable"] == 8
    assert natural["clarification"] == 7
    assert natural["passed"] == natural["total"]
    assert all(case["end_to_end_pass"] for case in natural["cases"])
    for case in natural["cases"]:
        if case["expected_outcome"] == "answered":
            assert case["sql_mode"] in {"rule", "llm", "llm-repair"}
            assert case["row_count"] > 0
            assert case["metric_column_ok"] is True
            assert case["time_range_ok"] is True
        else:
            assert case["row_count"] == 0
            assert case["sql_mode"] == "clarification"
            assert case["actual_intent"] == ""
            assert case["actual_metric"] == ""

    ablation = run_retrieval_ablation()
    assert {"lexical_only", "semantic_only", "hybrid_0.56_0.44"} <= set(ablation["variants"])
    for variant in ablation["variants"].values():
        assert {"top1_accuracy", "mrr", "full_recall_at_4", "cases"} <= set(variant)
    selected = ablation["variants"]["hybrid_0.56_0.44"]
    assert selected["full_recall_at_4"] >= 80
    assert selected["top1_accuracy"] >= 80
    assert selected["mrr"] >= 0.8


def test_rule_equivalence_suite_matches_all_100_gold_results():
    from app.nl2sql.evaluation import run_rule_equivalence_evaluation

    report = run_rule_equivalence_evaluation()
    assert report["suite_version"] == "rule-equivalence-v1"
    assert report["total"] == 100
    assert report["passed"] == 100
    assert all(case["hit"] for case in report["cases"])


def test_colloquial_suite_uses_strict_model_path_and_independent_rows(monkeypatch):
    from app.nl2sql.holdout import COLLOQUIAL_CASES
    from app.nl2sql.retrieval import HybridSchemaRetriever

    calls = []

    def fake_query(question, **kwargs):
        calls.append((question, kwargs))
        assert kwargs.get("allow_rule_fallback") is False
        case = next(item for item in COLLOQUIAL_CASES if item["question"] == question)
        if case.get("expected_outcome") == "clarification":
            plan = make_plan("", [], context=None)
            plan.intent = ""
            plan.tables = []
            plan.metrics = []
            plan.time_range = ""
            return QueryResult(
                question=question, sql="", rows=[], columns=[], chart_type="table",
                answer="请补充指标。", plan=plan, retrieval=[], attempts=[],
                elapsed_ms=1, mode="unsupported", narrative_mode="unavailable",
                outcome="clarification",
            )
        rows = execute_sql(case["reference_sql"])["rows"]
        plan = make_plan(question, [], reference_date="2026-02-28")
        plan.intent = case["expected_intent"]
        plan.metrics = [case["expected_metric"]]
        plan.tables = sorted(case["expected_tables"])
        return QueryResult(
            question=question, sql=case["reference_sql"], rows=rows, columns=list(rows[0]),
            chart_type="table", answer="依据查询结果生成的模型解读。", plan=plan,
            retrieval=[], attempts=[{"status": "success"}], elapsed_ms=1,
            mode="llm", narrative_mode="model", narrative_model="test-model",
        )

    monkeypatch.setattr(evaluation, "run_query", fake_query)
    report = evaluation.run_colloquial_evaluation(HybridSchemaRetriever())
    assert report["mode"] == "model"
    assert report["total"] == len(COLLOQUIAL_CASES)
    if report["passed"] != report["total"]:
        raise AssertionError([case for case in report["cases"] if not case["hit"]])
    assert report["model_rate"] == report["answerable"] * 100.0 / report["total"]
    assert all(call[1]["allow_rule_fallback"] is False for call in calls)
    assert "family_summary" in report


def test_runtime_self_healing_grounds_small_ratios_and_rejects_unrelated_numbers():
    from app.nl2sql.result_insight import validate_narrative

    digest = {
        "question": "各渠道ROI",
        "time_range": "2026-01-01 至 2026-01-31",
        "facts": ["ROI"],
        "metrics": [{"column": "ROI", "sum": 2.5, "avg": 0.85, "min": 0.52, "max": 2.18}],
        "sample_rows": [{"渠道": "App", "ROI": 0.85}, {"渠道": "Web", "ROI": 0.52}],
        "ranking": [],
        "trend": {},
        "row_count": 2,
    }
    assert validate_narrative("App 的 ROI 是 0.85，Web 是 0.52。", digest)["passed"]
    assert not validate_narrative("App 的 ROI 是 1.52。", digest)["passed"]


def test_case_family_summary_reduces_matrix_to_business_shapes():
    report = evaluation.case_family_summary(
        [{**case, "hit": case["id"] != "matrix-roi-last90-web"} for case in evaluation.CASES]
    )
    assert 10 < report["family_count"] < len(evaluation.CASES)
    assert report["fully_passed_families"] == report["family_count"] - 1
    assert report["family_hit_rate"] < 100
    roi_family = next(
        item for item in report["families"]
        if item["family"] == "marketing_efficiency|ROI|scalar-channel"
    )
    assert roi_family["total"] == 8
    assert roi_family["passed"] == 7



def test_colloquial_answer_and_plan_are_scored_independently(monkeypatch):
    from app.nl2sql.holdout import COLLOQUIAL_CASES
    from app.nl2sql.retrieval import HybridSchemaRetriever

    case = next(
        item for item in COLLOQUIAL_CASES
        if item["id"] == "colloquial-channel-sales"
    )
    rows = execute_sql(case["reference_sql"])["rows"]
    plan = make_plan(case["question"], [], reference_date="2026-02-28")
    plan.intent = case["expected_intent"]
    plan.metrics = [case["expected_metric"]]
    plan.tables = sorted(case["expected_tables"])

    # Correct plan, wrong rows / failed answer.
    monkeypatch.setattr(
        evaluation,
        "run_query",
        lambda *args, **kwargs: QueryResult(
            question=case["question"], sql=case["reference_sql"],
            rows=[{"渠道": "not-a-channel", "销售额": 1}],
            columns=["渠道", "销售额"], chart_type="table",
            answer="依据查询结果生成的模型解读。", plan=plan, retrieval=[],
            attempts=[{"status": "success"}], elapsed_ms=1, mode="llm",
            narrative_mode="model", narrative_model="test-model",
        ),
    )
    report = evaluation.run_colloquial_evaluation(
        HybridSchemaRetriever(), selected_cases=(case,),
        suite_version="test",
    )
    assert report["answer_correct"] == 0
    assert report["plan_correct"] == 1

    # Correct rows / answer, wrong plan metadata.
    plan.intent = "refund_analysis"
    monkeypatch.setattr(
        evaluation,
        "run_query",
        lambda *args, **kwargs: QueryResult(
            question=case["question"], sql=case["reference_sql"], rows=rows,
            columns=list(rows[0]), chart_type="table",
            answer="依据查询结果生成的模型解读。", plan=plan, retrieval=[],
            attempts=[{"status": "success"}], elapsed_ms=1, mode="llm",
            narrative_mode="model", narrative_model="test-model",
        ),
    )
    report = evaluation.run_colloquial_evaluation(
        HybridSchemaRetriever(), selected_cases=(case,),
        suite_version="test",
    )
    assert report["answer_correct"] == 1
    assert report["plan_correct"] == 0
