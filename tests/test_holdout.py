from __future__ import annotations

from app.nl2sql.evaluation import CASES
from app.nl2sql.holdout import HOLDOUT_CASES, HOLDOUT_SUITE_VERSION
from app.nl2sql.sql_engine import execute_sql


def test_holdout_is_30_unique_cases():
    assert len(HOLDOUT_CASES) == 30
    assert len({case["id"] for case in HOLDOUT_CASES}) == 30
    assert len({case["question"] for case in HOLDOUT_CASES}) == 30


def test_holdout_does_not_overlap_regression_suite():
    regression_ids = {case["id"] for case in CASES}
    regression_questions = {case["question"] for case in CASES}
    assert not regression_ids & {case["id"] for case in HOLDOUT_CASES}
    assert not regression_questions & {case["question"] for case in HOLDOUT_CASES}


def test_holdout_reference_sql_is_executable_and_non_empty():
    for case in HOLDOUT_CASES:
        assert execute_sql(case["reference_sql"])["rows"]


def test_holdout_uses_a_distinct_suite_version():
    assert HOLDOUT_SUITE_VERSION == "strict-model-v3-holdout-30"


def test_holdout_covers_every_supported_metric_and_intent():
    assert {case["intent"] for case in HOLDOUT_CASES} == {
        "sales_overview", "category_analysis", "refund_analysis", "marketing_efficiency",
    }
    assert {case["metric"] for case in HOLDOUT_CASES} == {
        "销售额", "销量", "退款率", "退款单数", "退款金额", "ROI", "客单价",
    }
    assert sum(bool(case["setup"]) for case in HOLDOUT_CASES) == 2
    for case in HOLDOUT_CASES:
        if case["setup"]:
            assert case["setup"] in {item["id"] for item in HOLDOUT_CASES}

def test_holdout_dataset_is_registered(monkeypatch, tmp_path):
    from app.nl2sql import evaluation_tasks
    monkeypatch.setenv("ATLAS_EVALUATION_DB_PATH", str(tmp_path / "evaluation_tasks.db"))
    datasets = evaluation_tasks.list_datasets()
    holdout = next(item for item in datasets if item["id"] == "dataset_v3_holdout_30")
    assert holdout["case_count"] == 30
    assert holdout["status"] == "holdout"
    assert holdout["name"] == HOLDOUT_SUITE_VERSION
