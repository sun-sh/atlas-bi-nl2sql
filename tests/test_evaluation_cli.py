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
