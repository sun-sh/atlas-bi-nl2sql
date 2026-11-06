from __future__ import annotations

import json
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
