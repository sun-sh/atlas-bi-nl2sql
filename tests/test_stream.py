from __future__ import annotations

import json

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
