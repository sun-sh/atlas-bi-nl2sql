from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.nl2sql import evaluation, evaluation_tasks


@pytest.fixture(autouse=True)
def isolated_eval(monkeypatch, tmp_path):
    monkeypatch.setenv('ATLAS_EVALUATION_DB_PATH', str(tmp_path / 'tasks.db'))
    monkeypatch.setattr(evaluation_tasks, 'get_model_config', lambda: SimpleNamespace(enabled=True, active=True, api_key='secret', base_url='https://private.invalid'))
    yield


def fake_report(**kwargs):
    cases = kwargs.get('cases') or ()
    items = [{'id': c['id'], 'question': c['question'], 'intent': c['intent'], 'hit': True} for c in cases]
    total = len(items)
    return {
        'suite_version': 'strict-model-v3-100', 'available': True,
        'model': {'active': True, 'model': 'test-model', 'provider': 'test'},
        'total': total, 'passed': total, 'hit_rate': 100.0, 'execution_rate': 100.0,
        'model_rate': 100.0, 'avg_elapsed_ms': 1.0, 'cases': items,
    }


def test_dataset_version_and_experiment_lifecycle(monkeypatch):
    ds = evaluation_tasks.create_dataset(name='ROI 专项', version='v1', case_ids=['roi-channels', 'roi-douyin'])
    assert ds['case_count'] == 2
    assert len(ds['checksum']) == 64
    exp = evaluation_tasks.create_experiment(name='ROI 实验', dataset_id=ds['id'], hypothesis='提高 ROI 计算准确率')
    assert exp['dataset_id'] == ds['id']
    assert any(item['id'] == ds['id'] for item in evaluation_tasks.list_datasets())


def test_queue_accepts_multiple_tasks_and_claim_is_fifo(monkeypatch):
    monkeypatch.setattr(evaluation_tasks, 'run_evaluation', fake_report)
    first = evaluation_tasks.create_evaluation_task(label='first', workers=1, priority=1)
    second = evaluation_tasks.create_evaluation_task(label='second', workers=1, priority=2)
    claimed = evaluation_tasks.claim_next_evaluation_task()
    assert claimed['id'] == first['id']
    evaluation_tasks.execute_evaluation_task(first['id'])
    claimed2 = evaluation_tasks.claim_next_evaluation_task()
    assert claimed2['id'] == second['id']


def test_execute_uses_selected_dataset_and_cancel_retry(monkeypatch):
    calls = []
    def runner(**kwargs):
        calls.append([item['id'] for item in kwargs['cases']])
        return fake_report(**kwargs)
    monkeypatch.setattr(evaluation_tasks, 'run_evaluation', runner)
    ds = evaluation_tasks.create_dataset(name='销售小集', version='v1', case_ids=['sales-east'])
    task = evaluation_tasks.create_evaluation_task(label='small', workers=1, dataset_id=ds['id'])
    completed = evaluation_tasks.execute_evaluation_task(task['id'])
    assert completed['status'] == 'completed'
    assert completed['total'] == 1
    assert calls == [['sales-east']]
    retry = evaluation_tasks.retry_evaluation_task(task['id'])
    assert retry['retry_of'] == task['id']
    cancelled = evaluation_tasks.cancel_evaluation_task(retry['id'])
    assert cancelled['status'] == 'cancelled'


def test_feedback_is_written_and_deduplicated():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(evaluation_tasks, 'run_evaluation', fake_report)
    task = evaluation_tasks.create_evaluation_task(label='feedback', workers=1)
    item = evaluation_tasks.add_feedback(task_id=task['id'], case_id='sales-east', category='wrong_sql', note='口径待复核')
    assert item['status'] == 'open'
    item2 = evaluation_tasks.add_feedback(task_id=task['id'], case_id='sales-east', category='retrieval', note='更新分类')
    assert item2['id'] == item['id']
    assert item2['category'] == 'retrieval'
    assert len(evaluation_tasks.list_feedback()) == 1
    monkeypatch.undo()


def test_cancel_check_stops_evaluation():
    def runner(**kwargs):
        assert kwargs['cancel_check']() is True
        raise RuntimeError('evaluation cancelled')
    evaluation_tasks.run_evaluation = runner
    task = evaluation_tasks.create_evaluation_task(label='cancel', workers=1)
    evaluation_tasks.cancel_evaluation_task(task['id'])
    assert evaluation_tasks.get_evaluation_task(task['id'])['status'] == 'cancelled'
