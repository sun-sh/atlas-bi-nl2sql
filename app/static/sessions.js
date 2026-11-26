import { state } from "./state.js";
import { $, $$, toast, api, escapeHtml } from "./utils.js";
import { parseSseFrame } from "./sse.js";
import { EVAL_LABELS } from "./labels.js";
import { renderGuardResult, renderResult, renderPendingTurn, updateStreamStage, setTurnNarrative, setNarrativeTag, followNarrative, renderQueryError, scrollThreadToLatest } from "./result-view.js";

function sessionTurnCount(session) {
  return Number(session?.turn_count || 0);
}

function formatSessionTime(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
}

function renderSessionList() {
  const list = $('#session-list');
  if (!list) return;
  if (!state.sessions.length) {
    list.innerHTML =
      '<div class="session-empty">还没有会话，发送第一条问题会自动创建。</div>';
    return;
  }
  list.innerHTML = state.sessions
    .map(
      (session) => `
    <button class="session-item ${session.id === state.session?.id ? 'active' : ''}" data-session-id="${escapeHtml(session.id)}">
      <span class="session-item-title">${escapeHtml(session.title || '新的分析会话')}</span>
      <span class="session-item-meta">${sessionTurnCount(session)} 轮 · ${escapeHtml(formatSessionTime(session.updated_at))}</span>
    </button>`,
    )
    .join('');
  list.querySelectorAll('.session-item').forEach((button) => {
    button.addEventListener('click', () =>
      openSession(button.dataset.sessionId),
    );
  });
}

function contextRows(context) {
  const labels = {
    time_range: '时间范围',
    region_label: '地区',
    channel_label: '渠道',
    category_label: '品类',
    metrics: '指标',
    intent: '分析类型',
  };
  return Object.entries(labels)
    .map(([key, label]) => {
      let value = context?.[key];
      if (key === 'intent') value = EVAL_LABELS[value] || value;
      if (Array.isArray(value)) value = value.join(' / ');
      if (!value) return '';
      return `<div class="context-row"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`;
    })
    .filter(Boolean)
    .join('');
}

function contextChips(context) {
  const labels = {
    time_range: '时间',
    region_label: '地区',
    channel_label: '渠道',
    category_label: '品类',
    metrics: '指标',
  };
  return Object.entries(labels)
    .map(([key, label]) => {
      let value = context?.[key];
      if (Array.isArray(value)) value = value.join(' / ');
      if (!value) return '';
      return `<span class="context-chip"><span>${label}</span>${escapeHtml(value)}</span>`;
    })
    .filter(Boolean)
    .join('');
}

function renderContext(session) {
  const list = $('#context-list');
  if (!list) return;
  const context = session?.context || {};
  const rows = contextRows(context);
  list.innerHTML =
    rows || '<div class="context-empty">提交第一条问题后生成分析条件。</div>';
  const summary = $('#thread-context-summary');
  const chips = $('#thread-context-chips');
  const chipMarkup = contextChips(context);
  if (summary) summary.hidden = !chipMarkup;
  if (chips) chips.innerHTML = chipMarkup;
  const title = $('#session-title');
  if (title) title.textContent = session?.title || '新的分析会话';
  const count = $('#session-count');
  if (count) count.textContent = `${sessionTurnCount(session)} 轮`;
}

function renderEmptyConversation() {
  const thread = $('#conversation-thread');
  if (!thread) return;
  thread.innerHTML = `<div class="conversation-empty">
          <p class="conversation-empty-lead">直接问一个经营问题，例如“上个月各渠道 ROI”，也可以继续追问“那华东呢”。</p>
          <div class="starter-grid" id="example-list">${state.examples.map((q) => `<button class="example-chip" data-question="${escapeHtml(q)}">${escapeHtml(q)}</button>`).join('')}</div>
        </div>`;
  thread.querySelectorAll('.example-chip').forEach((btn) =>
    btn.addEventListener('click', () => {
      $('#question').value = btn.dataset.question || '';
      $('#question').focus();
    }),
  );
}
function updateLatestReplyButton() {
  const button = $('#latest-reply-btn');
  if (!button) return;
  button.hidden = state.threadPinned;
}



function renderSessionThread(session, options = {}) {
  const thread = $('#conversation-thread');
  if (!thread) return;
  state.activeQueryCard = null;
  state.threadPinned = true;
  state.session = session;
  if (session?.id) {
    const summary = {
      ...(state.sessions.find((item) => item.id === session.id) || {}),
      id: session.id,
      title: session.title,
      turn_count: session.turn_count,
      updated_at: session.updated_at,
      created_at: session.created_at,
    };
    state.sessions = [
      summary,
      ...state.sessions.filter((item) => item.id !== session.id),
    ];
  }
  renderSessionList();
  renderContext(session);
  if (session?.turns?.length) {
    thread.innerHTML = '';
    session.turns.forEach((turn) => {
      const result = { ...(turn.result || {}) };
      result.question = turn.question;
      renderResult(result, { append: true });
    });
    state.lastResult = session.turns[session.turns.length - 1]?.result || null;
    if (options.scrollToLatest) {
      const latest = thread.querySelector('.turn-card:last-child');
      requestAnimationFrame(() => {
        if (latest) thread.scrollTop += latest.getBoundingClientRect().top - thread.getBoundingClientRect().top - 12;
      });
    }
  } else {
    renderEmptyConversation();
  }
}
async function openSession(sessionId, options = {}) {
  if (state.sending || state.creatingSession) return;
  const requestId = ++state.sessionOpenRequest;
  try {
    const session = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (requestId !== state.sessionOpenRequest) return;
    renderSessionThread(session, { scrollToLatest: true, ...options });
    closeWorkspacePanels();
  } catch (error) {
    if (requestId !== state.sessionOpenRequest) return;
    toast(error.message);
    if (state.session?.id === sessionId) state.session = null;
    await loadSessions();
  }
}
async function loadSessions() {
  if (state.sending || state.creatingSession) return;
  const requestId = ++state.sessionListRequest;
  try {
    const data = await api('/api/sessions');
    if (requestId !== state.sessionListRequest) return;
    state.sessions = data.sessions || [];
    const activeId = state.session?.id || state.sessions[0]?.id;
    if (activeId) {
      await openSession(activeId);
    } else {
      state.session = null;
      renderSessionList();
      renderContext(null);
      renderEmptyConversation();
    }
  } catch (error) {
    if (requestId === state.sessionListRequest) toast(error.message);
  }
}
async function createSession() {
  if (state.creatingSession || state.sending) return;
  state.creatingSession = true;
  state.sessionListRequest += 1;
  state.sessionOpenRequest += 1;
  try {
    const session = await api('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    state.session = session;
    state.sessions = [
      session,
      ...state.sessions.filter((item) => item.id !== session.id),
    ];
    state.lastResult = null;
    renderSessionThread(session);
    closeWorkspacePanels();
    renderEvidence({ retrieval: [], attempts: [], sql: '' });
    $('#question').value = '';
    $('#question').focus();
    toast('已新建会话，上下文已清空');
  } catch (error) {
    toast(error.message);
  } finally {
    state.creatingSession = false;
  }
}

async function streamQuery(question, sessionId) {
  const card = state.activeQueryCard;
  const requestId = crypto.randomUUID().replace(/-/g, '').slice(0, 24);
  state.activeTraceId = null;
    const controller = new AbortController();
      state.activeAbortController = controller;
        const response = await fetch('/api/query/stream', {
            method: 'POST',
                headers: {
                      'Content-Type': 'application/json',
                            Accept: 'text/event-stream',
                                },
                                    body: JSON.stringify({ question, session_id: sessionId, request_id: requestId }),
                                        signal: controller.signal,
                                          });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  if (!response.body) throw new Error('浏览器不支持流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result = null;
  let narrative = '';
  let completed = false;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index = buffer.indexOf('\n\n');
    while (index >= 0) {
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      const parsed = parseSseFrame(frame);
      if (parsed) {
        const { event, data } = parsed;
        if (event === 'start') {
          if (data.trace_id) state.activeTraceId = data.trace_id;
          updateStreamStage(card, data);
        } else if (event === 'status') {
          updateStreamStage(card, data);
        } else if (event === 'result') {
          result = data.result;
          result.session_id = data.session_id;
          narrative = '';
          if (result.outcome && result.outcome !== 'answered') {
            renderGuardResult(result, { card });
            card?.classList.remove('is-streaming');
            continue;
          }
          renderResult(result, { card });
          setTurnNarrative(card, '', true);
          const tag = card?.querySelector('.narrative-tag');
          if (tag) tag.textContent = '正在组织分析结论…';
          followNarrative(card);
        } else if (event === 'delta') {
          narrative += data.delta || '';
          setTurnNarrative(card, narrative, true);
          followNarrative(card);
        } else if (event === 'reset') {
          narrative = data.text || '';
          setTurnNarrative(card, narrative, true);
        } else if (event === 'thinking') {
          if (!narrative) {
            const box = card?.querySelector('.narrative-text');
            if (box) {
              box.innerHTML =
                '<span class="narrative-thinking"><span class="thinking-dot"></span>模型正在组织分析结论…</span>';
            }
          }
        } else if (event === 'narrative') {
          result.answer = data.text || narrative || result.answer;
          result.narrative = { mode: data.mode, model: data.model };
          narrative = result.answer;
          setTurnNarrative(card, narrative, false);
          setNarrativeTag(card, result.narrative);
          followNarrative(card);
        } else if (event === 'done') {
          completed = true;
          if (data.session) state.session = data.session;
          if (result) {
            result.elapsed_ms = data.elapsed_ms ?? result.elapsed_ms;
            result.trace_id = data.trace_id || result.trace_id;
          }
          const turnMeta = card?.querySelector('.turn-meta');
          if (turnMeta && result) {
            turnMeta.innerHTML = `<span>${escapeHtml(result.plan?.time_range || '')}</span><span>${escapeHtml(result.elapsed_ms)} 毫秒</span>`;
          }
          card?.classList.remove('is-streaming');
        } else if (event === 'cancelled') {
          throw new DOMException(data.message || '查询已取消', 'AbortError');
                  } else if (event === 'error') {
                            throw new Error(data.message || '流式查询失败');
        }
      }
      index = buffer.indexOf('\n\n');
    }
  }
  if (!result) throw new Error('流式响应未返回查询结果');
  if (!completed) throw new Error('连接提前结束，请重新尝试');
  if (state.session) {
    renderSessionList();
    renderContext(state.session);
  }
  return result;
}

async function cancelActiveQuery() {
  const traceId = state.activeTraceId;
  state.activeAbortController?.abort();
  if (!traceId) return;
  try {
    await api(`/api/query/cancel/${encodeURIComponent(traceId)}`, {
      method: 'POST',
    });
  } catch {
    // Local abort already stopped the browser stream.
  }
}


async function submitQuery(question) {
  if (!question || question.trim().length < 2) {
    toast('请输入一个完整问题');
    return;
  }
  if (state.sending) return;
  state.sending = true;
  state.sessionListRequest += 1;
  state.sessionOpenRequest += 1;
  $$('#new-session-btn, #reset-context-btn, .session-item, #refresh-btn').forEach(el => { el.disabled = true; });
  const button = $('#submit-btn');
  button.disabled = false;
  button.dataset.mode = 'cancel';
  button.querySelector('span').textContent = '停止分析';
  const submittedQuestion = question.trim();
  const liveCard = renderPendingTurn(submittedQuestion);
  if (liveCard) liveCard.dataset.question = submittedQuestion;
  try {
    let sessionId = state.session?.id || null;
    if (!sessionId) {
      state.sessionListRequest += 1;
      state.sessionOpenRequest += 1;
      const created = await api('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      sessionId = created.id;
      state.session = created;
      state.sessions = [
        created,
        ...state.sessions.filter((item) => item.id !== created.id),
      ];
    }
    const result = await streamQuery(submittedQuestion, sessionId);
    if (state.session) {
      const latest = state.session.turns?.[state.session.turns.length - 1];
      if (latest) {
        latest.answer = result.answer;
        latest.result = result;
      }
    }
    if ($('#question').value.trim() === submittedQuestion) $('#question').value = '';
    toast(`分析完成 · ${result.elapsed_ms} 毫秒`);
  } catch (error) {
    state.activeQueryCard = null;
    const cancelled = error?.name === 'AbortError';
    toast(cancelled ? '已停止本次分析' : error.message);
    renderQueryError(liveCard, cancelled ? '已停止本次分析，可重新提问或继续追问。' : error.message);
    state.threadPinned = true;
    scrollThreadToLatest({ force: true });
  } finally {
    state.sending = false;
    state.activeAbortController = null;
    state.activeTraceId = null;
    $$('#new-session-btn, #reset-context-btn, .session-item, #refresh-btn').forEach(el => { el.disabled = false; });
    button.disabled = false;
    button.dataset.mode = '';
    button.querySelector('span').textContent = '发送问题';
  }
}

export {
  sessionTurnCount,
  formatSessionTime,
  renderSessionList,
  contextRows,
  contextChips,
  renderContext,
  renderEmptyConversation,
  updateLatestReplyButton,
  scrollThreadToLatest,
  renderSessionThread,
  openSession,
  loadSessions,
  createSession,
  streamQuery,
  cancelActiveQuery,
  submitQuery,
};
