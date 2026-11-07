from __future__ import annotations

import json

from app.nl2sql.harness import register_cancel

from app.nl2sql.planner import make_plan
from app.nl2sql.result_insight import build_result_digest, stream_result_narrative
from tests.test_api import client


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for frame in text.replace('\r\n', '\n').split('\n\n'):
        event = 'message'
        data = []
        for line in frame.splitlines():
            if line.startswith('event:'):
                event = line[6:].strip()
            elif line.startswith('data:'):
                data.append(line[5:].lstrip())
        if data:
            events.append((event, json.loads('\n'.join(data))))
    return events


def _query_stream(client, question, session_id):
    with client.stream(
        'POST',
        '/api/query/stream',
        json={'question': question, 'session_id': session_id},
        headers={'Accept': 'text/event-stream'},
    ) as response:
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/event-stream')
        return parse_sse(''.join(response.iter_text()))


def test_query_stream_returns_result_digest_and_done(monkeypatch, tmp_path):
    monkeypatch.setenv('ATLAS_SESSION_DB_PATH', str(tmp_path / 'sessions.db'))
    created = client.post('/api/sessions', json={}).json()
    events = _query_stream(client, '上个月各渠道ROI', created['id'])
    names = [name for name, _ in events]
    assert names[0] == 'start'
    start = events[0][1]
    assert start['trace_id']
    assert names.count('status') >= 5
    assert names.index('status') < names.index('result')
    assert names.index('result') < names.index('delta')
    assert names[-1] == 'done'

    stage_names = [
        data['stage'] for name, data in events if name == 'status'
    ]
    assert stage_names == ['retrieve', 'plan', 'generate', 'execute', 'finalize']

    result_event = next(data for name, data in events if name == 'result')
    result = result_event['result']
    assert result['attempts'][-1]['status'] == 'success'
    assert result['rows']
    assert result['narrative']['mode'] == 'digest'

    narrative = next(data for name, data in events if name == 'narrative')
    assert narrative['mode'] == 'digest'
    assert '结论' in narrative['text']
    assert 'Web' in narrative['text']

    streamed = ''.join(
        data.get('delta', '') for name, data in events if name == 'delta'
    )
    assert streamed == narrative['text']

    done = events[-1][1]
    assert done['session']['turn_count'] == 1
    assert done['session']['turns'][0]['answer'] == narrative['text']


def test_cancel_endpoint_returns_false_for_unknown_trace():
    response = client.post("/api/query/cancel/unknown-trace")
    assert response.status_code == 200
    assert response.json()["cancelled"] is False


def test_stream_narrative_uses_model_delta_when_available(monkeypatch):
    class FakeDelta:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.delta = FakeDelta(content)

    class FakeChunk:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs['stream'] is True
            return iter([FakeChunk('结论：模型解读。'), FakeChunk('\n关键发现：- 数据正常。')])

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr('app.nl2sql.result_insight.get_model_config', lambda: type('C', (), {
        'enabled': True,
        'active': True,
        'model': 'fake-model',
    })())
    monkeypatch.setattr('app.nl2sql.result_insight.make_client', lambda config: FakeClient())
    plan = make_plan('各渠道销售额', [])
    digest = build_result_digest(
        '各渠道销售额',
        plan,
        [{'渠道': 'App', '销售额': 120}, {'渠道': 'Web', '销售额': 80}],
        ['渠道', '销售额'],
        2,
        False,
    )
    events = list(stream_result_narrative('各渠道销售额', plan, digest))
    deltas = [event['delta'] for event in events if event['type'] == 'delta']
    assert ''.join(deltas) == '结论：模型解读。\n关键发现：- 数据正常。'
    final = next(event['narrative'] for event in events if event['type'] == 'done')
    assert final.mode == 'model'
    assert final.model == 'fake-model'


def test_stream_failed_query_reports_failure_not_business_conclusion(monkeypatch, tmp_path):
    import app.api.main as api_main
    from app.nl2sql.models import QueryResult
    from app.nl2sql.planner import make_plan

    monkeypatch.setenv('ATLAS_SESSION_DB_PATH', str(tmp_path / 'sessions.db'))
    plan = make_plan('各渠道销售额', [])

    def failed_events(*args, **kwargs):
        result = QueryResult(
            question='各渠道销售额',
            sql='',
            rows=[],
            columns=[],
            chart_type='bar',
            answer='查询未成功执行，不能给出业务结论。',
            plan=plan,
            retrieval=[],
            attempts=[{'attempt': 1, 'status': 'failed', 'sql': '', 'error': 'SQL 为空'}],
            elapsed_ms=1,
            mode='model-failed',
            outcome='failed',
            narrative_mode='error',
            trace_id=kwargs['trace_id'],
        )
        yield {'type': 'result', 'result': result}

    monkeypatch.setattr(api_main, 'query_stream_events', failed_events)
    created = client.post('/api/sessions', json={}).json()
    events = _query_stream(client, '各渠道销售额', created['id'])
    result = next(data for name, data in events if name == 'result')['result']
    assert result['outcome'] == 'failed'
    assert result['rows'] == []
    assert result['narrative']['mode'] == 'error'
    assert '查询未成功执行' in result['answer']
    assert '查询已成功执行' not in result['answer']
    assert 'delta' not in [name for name, _ in events]


def test_stream_cancel_during_sql_execution_returns_cancelled_event(monkeypatch, tmp_path):
    import app.api.main as api_main

    monkeypatch.setenv('ATLAS_SESSION_DB_PATH', str(tmp_path / 'sessions.db'))

    def cancelled_events(*args, **kwargs):
        yield {'type': 'cancelled', 'trace_id': kwargs['trace_id']}

    monkeypatch.setattr(api_main, 'query_stream_events', cancelled_events)
    created = client.post('/api/sessions', json={}).json()
    events = _query_stream(client, '上个月各渠道ROI', created['id'])
    assert events[-1][0] == 'cancelled'
    assert events[-1][1]['trace_id'] == events[0][1]['trace_id']


def test_stream_cancel_during_model_narrative_returns_cancelled_event(monkeypatch, tmp_path):
    import app.api.main as api_main
    from app.nl2sql.models import QueryResult
    from app.nl2sql.planner import make_plan

    monkeypatch.setenv('ATLAS_SESSION_DB_PATH', str(tmp_path / 'sessions.db'))
    plan = make_plan('各渠道销售额', [])

    def one_result(*args, **kwargs):
        result = QueryResult(
            question='各渠道销售额', sql='SELECT 1 AS 销售额', rows=[{'渠道': 'App', '销售额': 100}],
            columns=['渠道', '销售额'], chart_type='bar', answer='', plan=plan, retrieval=[],
            attempts=[{'status': 'success'}], elapsed_ms=1, mode='llm', trace_id='trace-narrative',
        )
        yield {'type': 'result', 'result': result}

    def cancelled_narrative(*args, **kwargs):
        assert kwargs['trace_id']
        yield {'type': 'cancelled'}

    monkeypatch.setattr(api_main, 'query_stream_events', one_result)
    monkeypatch.setattr(api_main, 'stream_result_narrative', cancelled_narrative)
    created = client.post('/api/sessions', json={}).json()
    events = _query_stream(client, '各渠道销售额', created['id'])
    assert events[-1][0] == 'cancelled'
    assert events[-1][1]['trace_id'] == events[0][1]['trace_id']



def test_query_stream_events_converts_execution_cancel_to_event(monkeypatch):
    from app.nl2sql.sql_engine import QueryCancelled
    from app.nl2sql.workflow import query_stream_events

    monkeypatch.setattr('app.nl2sql.workflow.is_cancelled', lambda trace_id: True)
    monkeypatch.setattr('app.nl2sql.workflow.build_graph', lambda: type('Graph', (), {
        'stream': lambda self, *args, **kwargs: iter([('updates', {'execute': {'attempts': []}})]),
    })())
    monkeypatch.setattr('app.nl2sql.workflow._GRAPH', None)
    events = list(query_stream_events('上个月各渠道ROI', trace_id='cancel-workflow'))
    assert events == [{'type': 'cancelled', 'trace_id': 'cancel-workflow'}]
