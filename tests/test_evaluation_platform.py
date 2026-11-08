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


def test_dataset_content_is_snapshotted_and_tampering_is_rejected(monkeypatch):
    monkeypatch.setattr(evaluation_tasks, 'run_evaluation', fake_report)
    ds = evaluation_tasks.create_dataset(
        name='不可变小集', version='v1', case_ids=['sales-east']
    )
    task = evaluation_tasks.create_evaluation_task(
        label='不可变', workers=1, dataset_id=ds['id']
    )
    with evaluation_tasks._connect() as conn:
        conn.execute(
            "UPDATE evaluation_datasets SET case_snapshot_json=? WHERE id=?",
            ('[{"id":"sales-east","question":"被人改过","reference_sql":"SELECT 1"}]', ds['id']),
        )
    completed = evaluation_tasks.execute_evaluation_task(task['id'])
    assert completed['status'] == 'failed'
    assert '数据集内容已变更' in completed['error']


def test_empty_dataset_does_not_fall_back_to_full_regression(monkeypatch):
    from app.nl2sql import evaluation

    monkeypatch.setattr(evaluation, 'get_model_config', lambda: SimpleNamespace(
        enabled=True, active=True, model='test', provider='test'
    ))
    monkeypatch.setattr(
        evaluation,
        '_evaluate_case',
        lambda case, cases=None: {
            'id': case['id'], 'question': case['question'], 'intent': case['intent'],
            'hit': True, 'executed': True, 'model_used': True,
        },
    )
    with pytest.raises(ValueError, match='cannot be empty'):
        evaluation.run_evaluation(cases=())


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


def test_failed_cases_flow_into_reviewable_dataset(monkeypatch):
    def runner(**kwargs):
        cases = kwargs.get('cases') or ()
        items = []
        for index, case in enumerate(cases):
            items.append({
                'id': case['id'],
                'question': case['question'],
                'intent': case['intent'],
                'expected_metric': case['metric'],
                'hit': index != 0,
                'error': 'model token budget exhausted' if index == 0 else None,
            })
        total = len(items)
        return {
            'suite_version': 'strict-model-v3-100',
            'available': True,
            'model': {'active': True, 'model': 'test-model', 'provider': 'test'},
            'total': total,
            'passed': total - 1,
            'hit_rate': round((total - 1) * 100 / total, 1),
            'execution_rate': 100.0,
            'model_rate': 100.0,
            'avg_elapsed_ms': 1.0,
            'failure_reasons': {'token_budget': 1},
            'cases': items,
        }

    monkeypatch.setattr(evaluation_tasks, 'run_evaluation', runner)
    ds = evaluation_tasks.create_dataset(
        name='回流源', version='v1', case_ids=['sales-east', 'sales-south']
    )
    task = evaluation_tasks.create_evaluation_task(label='回流', workers=1, dataset_id=ds['id'])
    evaluation_tasks.execute_evaluation_task(task['id'])
    candidates = evaluation_tasks.ingest_failed_cases(task['id'])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate['question'] == '上个月华东地区已支付订单销售额是多少？'
    assert candidate['status'] == 'pending'
    listed = evaluation_tasks.list_regression_candidates('pending')
    assert listed[0]['expected_metric'] == '销售额'
    approved = evaluation_tasks.approve_regression_candidates(
        [candidate['id']], name='回流回归集', version='v1'
    )
    assert approved['case_count'] == 1
    state = evaluation_tasks.list_regression_candidates('approved')
    assert state[0]['dataset_id'] == approved['id']
    with pytest.raises(ValueError, match='仅待审核候选可批准'):
        evaluation_tasks.approve_regression_candidates(
            [candidate['id']], name='重复审批', version='v2'
        )


def test_compare_marks_cross_dataset_differences_as_not_directly_comparable(monkeypatch):
    monkeypatch.setattr(evaluation_tasks, 'get_model_config', lambda: SimpleNamespace(
        enabled=True, active=True, model='same-model', provider='same-provider',
        api_key='secret', base_url='https://example.com',
    ))
    def runner(**kwargs):
        report = fake_report(**kwargs)
        report['runtime'] = {'source_sha256': 'source-a'}
        report['case_manifest_sha256'] = 'manifest-a'
        return report
    monkeypatch.setattr(evaluation_tasks, 'run_evaluation', runner)
    first_ds = evaluation_tasks.create_dataset(name='A', version='v1', case_ids=['sales-east'])
    second_ds = evaluation_tasks.create_dataset(name='B', version='v1', case_ids=['sales-east'])
    first = evaluation_tasks.create_evaluation_task(label='A', workers=1, dataset_id=first_ds['id'])
    second = evaluation_tasks.create_evaluation_task(label='B', workers=1, dataset_id=second_ds['id'])
    evaluation_tasks.execute_evaluation_task(first['id'])
    evaluation_tasks.execute_evaluation_task(second['id'])
    comparison = evaluation_tasks.compare_evaluation_tasks(first['id'], second['id'])
    assert comparison['comparable'] is False
    assert comparison['comparability']['same_dataset'] is False
    assert '不能作为单一变量因果结论' in comparison['comparison_note']
