from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evaluation import run


@pytest.mark.parametrize("rate, available, exit_code", [(89, True, 2), (90, True, 0), (100, False, 2)])
def test_quality_gate_rejects_low_score_and_unavailable_model(monkeypatch, tmp_path, capsys, rate, available, exit_code):
    monkeypatch.setattr(run, "get_model_config", lambda: SimpleNamespace(api_key="fake-secret", base_url="https://private.invalid"))
    monkeypatch.setattr(run, "run_evaluation", lambda **kwargs: {
        "available": available, "total": len(run.CASES), "hit_rate": rate,
        "error": "fake-secret https://private.invalid",
    })
    output = tmp_path / "report.json"
    assert run.main(["--output", str(output), "--min-hit-rate", "90", "--workers", "3"]) == exit_code
    text = output.read_text(encoding="utf-8")
    assert "fake-secret" not in text
    assert "https://private.invalid" not in text
    report = json.loads(text)
    assert report["quality_gate"]["passed"] is (exit_code == 0)
    assert len(report["case_manifest_sha256"]) == 64


def test_holdout_suite_runs_the_30_case_set(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "get_model_config", lambda: SimpleNamespace(api_key="x", base_url="https://example.com"))
    captured = {}
    def fake_eval(**kwargs):
        captured["count"] = len(kwargs["cases"])
        captured["suite_version"] = kwargs["suite_version"]
        captured["dataset_range"] = kwargs["dataset_range"]
        return {
            "available": True,
            "total": captured["count"],
            "hit_rate": 100.0,
            "passed": captured["count"],
            "suite_version": kwargs["suite_version"],
            "cases": [],
        }
    monkeypatch.setattr(run, "run_evaluation", fake_eval)
    output = tmp_path / "holdout.json"
    assert run.main(["--suite", "holdout", "--output", str(output)]) == 0
    assert captured["count"] == 30
    assert captured["suite_version"] == "strict-model-v3-holdout-30"
    assert captured["dataset_range"] == "2025-10-01 至 2026-02-28"
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["total"] == 30
    assert report["suite_version"] == "strict-model-v3-holdout-30"


def test_invalid_worker_count_is_rejected():
    from app.nl2sql.evaluation import run_evaluation
    with pytest.raises(ValueError):
        run_evaluation(workers=100)


def test_evaluation_task_lifecycle_persists_progress_and_redacts_secrets(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app.nl2sql import evaluation_tasks

    monkeypatch.setenv('ATLAS_EVALUATION_DB_PATH', str(tmp_path / 'tasks.db'))
    monkeypatch.setattr(
        evaluation_tasks,
        'get_model_config',
        lambda: SimpleNamespace(enabled=True, active=True, api_key='very-secret', base_url='https://private.invalid'),
    )
    monkeypatch.setattr(
        evaluation_tasks,
        'run_evaluation',
        lambda **kwargs: {
            'suite_version': 'strict-model-v3-100',
            'available': True,
            'model': {'active': True, 'model': 'test-model', 'provider': 'test'},
            'total': 2,
            'passed': 2,
            'hit_rate': 100.0,
            'execution_rate': 100.0,
            'model_rate': 100.0,
            'avg_elapsed_ms': 12.0,
            'cases': [
                {'id': 'a', 'question': 'q1', 'hit': True},
                {'id': 'b', 'question': 'q2', 'hit': True},
            ],
        },
    )
    task = evaluation_tasks.create_evaluation_task(label='smoke', workers=1)
    assert task['status'] == 'queued'
    completed = evaluation_tasks.execute_evaluation_task(task['id'])
    assert completed['status'] == 'completed'
    assert completed['progress']['percent'] == 100
    assert completed['passed'] == 2
    serialized = str(completed)
    assert 'very-secret' not in serialized
    assert 'private.invalid' not in serialized


def test_evaluation_queue_accepts_multiple_but_claims_one(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app.nl2sql import evaluation_tasks

    monkeypatch.setenv('ATLAS_EVALUATION_DB_PATH', str(tmp_path / 'tasks.db'))
    monkeypatch.setattr(
        evaluation_tasks,
        'get_model_config',
        lambda: SimpleNamespace(enabled=True, active=True, api_key='secret', base_url='https://example.com'),
    )
    first = evaluation_tasks.create_evaluation_task(label='first', workers=1, priority=1)
    evaluation_tasks.create_evaluation_task(label='second', workers=1, priority=2)
    claimed = evaluation_tasks.claim_next_evaluation_task()
    assert claimed['id'] == first['id']
    assert evaluation_tasks.claim_next_evaluation_task() is None


def test_retrieval_gate_uses_selected_variant_not_best_variant(monkeypatch, tmp_path):
    def fake_ablation():
        return {
            "suite_version": "retrieval-ablation-v1",
            "total": 2,
            "selected": "hybrid_0.56_0.44",
            "variants": {
                "lexical_only": {
                    "full_recall_at_4": 100.0,
                    "full_recall_cases": 2,
                    "top1_accuracy": 100.0,
                    "mrr": 1.0,
                },
                "hybrid_0.56_0.44": {
                    "full_recall_at_4": 50.0,
                    "full_recall_cases": 1,
                    "top1_accuracy": 50.0,
                    "mrr": 0.5,
                },
            },
            "limitations": "test",
        }

    monkeypatch.setattr(run, "run_retrieval_ablation", fake_ablation)
    output = tmp_path / "retrieval.json"
    assert run.main(["--suite", "retrieval", "--min-hit-rate", "80", "--output", str(output)]) == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] == 1
    assert report["hit_rate"] == 50.0
    assert report["quality_gate"]["passed"] is False


def test_natural_gate_requires_end_to_end_not_only_scope(monkeypatch, tmp_path):
    def fake_natural():
        return {
            "suite_version": "natural-language-holdout-v1",
            "total": 2,
            "passed": 1,
            "hit_rate": 50.0,
            "answerable": 2,
            "clarification": 0,
            "table_recall_at_4": 100.0,
            "cases": [
                {"id": "a", "hit": True, "end_to_end_pass": True},
                {"id": "b", "hit": False, "end_to_end_pass": False},
            ],
            "limitations": "test",
        }

    monkeypatch.setattr(run, "run_natural_language_evaluation", fake_natural)
    output = tmp_path / "natural.json"
    assert run.main(["--suite", "natural", "--min-hit-rate", "80", "--output", str(output)]) == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["quality_gate"]["passed"] is False


def test_rule_equivalence_gate_requires_all_cases(monkeypatch, tmp_path):
    def fake_rules(**kwargs):
        return {
            "suite_version": "rule-equivalence-v1",
            "mode": "deterministic-rule",
            "available": True,
            "total": 2,
            "passed": 1,
            "hit_rate": 50.0,
            "cases": [
                {"id": "a", "hit": True},
                {"id": "b", "hit": False},
            ],
            "limitations": "test",
        }

    monkeypatch.setattr(run, "run_rule_equivalence_evaluation", fake_rules)
    output = tmp_path / "rules.json"
    assert run.main(["--suite", "rules", "--min-hit-rate", "100", "--output", str(output)]) == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["evaluation_contract"] == "rule-equivalence-v1"
    assert report["quality_gate"]["passed"] is False


def test_manifest_hash_is_stable_across_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "get_model_config", lambda: SimpleNamespace(api_key="", base_url=""))
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert run.main(["--suite", "scope", "--output", str(first)]) == 0
    assert run.main(["--suite", "scope", "--output", str(second)]) == 0
    assert json.loads(first.read_text(encoding="utf-8"))["case_manifest_sha256"] == json.loads(second.read_text(encoding="utf-8"))["case_manifest_sha256"]


def test_frozen_colloquial_suite_uses_frozen_cases(monkeypatch, tmp_path):
    from app.nl2sql.colloquial_holdout import (
        COLLOQUIAL_HOLDOUT_CASES,
        COLLOQUIAL_HOLDOUT_SUITE_VERSION,
    )

    monkeypatch.setattr(run, "get_model_config", lambda: SimpleNamespace(api_key="x", base_url="https://example.com"))
    captured = {}

    def fake_colloquial(**kwargs):
        captured.update(kwargs)
        return {
            "available": True,
            "total": len(kwargs["selected_cases"]),
            "passed": 7,
            "hit_rate": 53.8,
            "suite_version": kwargs["suite_version"],
            "cases": [],
        }

    monkeypatch.setattr(run, "run_colloquial_evaluation", fake_colloquial)
    output = tmp_path / "frozen.json"
    assert run.main(["--suite", "colloquial-frozen", "--output", str(output)]) == 0
    assert captured["selected_cases"] == COLLOQUIAL_HOLDOUT_CASES
    assert captured["suite_version"] == COLLOQUIAL_HOLDOUT_SUITE_VERSION
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["total"] == 13
    assert report["evaluation_contract"] == COLLOQUIAL_HOLDOUT_SUITE_VERSION



def test_report_exposes_answer_and_plan_denominators(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "get_model_config", lambda: SimpleNamespace(api_key="", base_url=""))
    def fake_eval(**kwargs):
        return {
            "available": True,
            "total": 2,
            "passed": 1,
            "hit_rate": 50.0,
            "answer_correct": 1,
            "answer_correct_rate": 50.0,
            "plan_correct": 2,
            "plan_correct_rate": 100.0,
            "cases": [],
        }
    monkeypatch.setattr(run, "run_evaluation", fake_eval)
    output = tmp_path / "dual.json"
    assert run.main(["--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["answer_correct"] == 1
    assert report["answer_correct_rate"] == 50.0
    assert report["plan_correct"] == 2
    assert report["plan_correct_rate"] == 100.0


def test_scope_report_gets_answer_first_aliases(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "get_model_config", lambda: SimpleNamespace(api_key="", base_url=""))
    output = tmp_path / "scope.json"
    assert run.main(["--suite", "scope", "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["answer_correct"] == report["passed"]
    assert report["answer_correct_rate"] == report["hit_rate"]
    assert report["plan_correct"] == report["passed"]



def test_source_hash_covers_semantic_yaml(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "get_model_config", lambda: SimpleNamespace(api_key="", base_url=""))
    output = tmp_path / "hash.json"
    assert run.main(["--suite", "scope", "--output", str(output)]) == 0
    before = json.loads(output.read_text(encoding="utf-8"))["runtime"]["source_sha256"]
    semantic_path = Path(run.__file__).resolve().parents[1] / "semantic.yaml"
    original = semantic_path.read_bytes()
    try:
        semantic_path.write_bytes(original + b"\n# provenance-test\n")
        assert run.main(["--suite", "scope", "--output", str(output)]) == 0
        after = json.loads(output.read_text(encoding="utf-8"))["runtime"]["source_sha256"]
    finally:
        semantic_path.write_bytes(original)
    assert before != after
