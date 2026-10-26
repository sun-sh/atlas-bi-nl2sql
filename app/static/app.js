const state = {
  overview: null,
  lastResult: null,
  examples: [],
  model: null,
  sessions: [],
  session: null,
  sending: false,
  creatingSession: false,
  sessionListRequest: 0,
  sessionOpenRequest: 0,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const fmtMoney = (v) =>
  '¥' + Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
const fmtNum = (v) => Number(v || 0).toLocaleString('zh-CN');

function toast(message) {
  const el = document.createElement('div');
  el.className = 'toast show';
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.classList.remove('show'), 2400);
  setTimeout(() => el.remove(), 2800);
}

async function api(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${res.status})`);
  }
  return res.json();
}

function metricCard(label, value, foot, accent = false) {
  return `<div class="metric-card${accent ? ' metric-accent' : ''}">
    <div class="metric-label"><span>${label}</span><span>↗</span></div>
    <div class="metric-value">${value}</div>
    <div class="metric-foot">${foot}</div>
  </div>`;
}

async function loadOverview() {
  try {
    const data = await api('/api/overview');
    state.overview = data;
    $('#metrics-grid').innerHTML = [
      metricCard(
        '累计实付销售额',
        fmtMoney(data.revenue),
        '2025.10 – 2026.02 · 已支付订单',
        true,
      ),
      metricCard(
        '有效订单数',
        fmtNum(data.orders),
        `覆盖 ${fmtNum(data.customers)} 位客户`,
      ),
      metricCard('平均客单价', fmtMoney(data.aov), '实付金额 / 有效订单'),
      metricCard(
        '退款率',
        data.refund_rate + '%',
        `${fmtNum(data.refund_orders)} 笔已通过退款`,
        true,
      ),
    ].join('');
    renderTrend(data.trend);
    renderChannels(data.channel);
  } catch (error) {
    toast(error.message);
  }
}

function renderTrend(rows) {
  const max = Math.max(...(rows || []).map((r) => Number(r.revenue)), 1);
  $('#trend-chart').innerHTML = (rows || [])
    .map((row) => {
      const h = Math.max(3, (Number(row.revenue) / max) * 135);
      return `<div class="bar-wrap" data-label="${row.date} · ${fmtMoney(row.revenue)}"><div class="bar" style="height:${h}px"></div></div>`;
    })
    .join('');
}

function renderChannels(rows) {
  const max = Math.max(...(rows || []).map((r) => Number(r.revenue)), 1);
  $('#channel-list').innerHTML = (rows || [])
    .map(
      (row) => `
    <div class="channel-row">
      <span>${row.channel}</span>
      <span class="channel-track"><span class="channel-fill" style="width:${(Number(row.revenue) / max) * 100}%"></span></span>
      <span class="channel-value">${fmtMoney(row.revenue)}</span>
    </div>`,
    )
    .join('');
}

async function loadHealth() {
  try {
    const data = await api('/api/health');
    $('#health-text').textContent = data.database
      ? '数据服务正常'
      : '数据库缺失';
    const r = data.retrieval;
    const semanticLabel =
      r.qdrant_enabled || r.semantic_backend !== 'local fallback'
        ? 'BGE 语义召回'
        : '语义降级';
    const lexicalLabel = r.elasticsearch_enabled ? 'ES BM25' : '本地 IDF';
    $('#retrieval-meta').textContent =
      `${semanticLabel} + ${lexicalLabel} · 只读 SQL`;
    state.model = data.model;
    renderModelRuntime(data.model, data.llm_active);
  } catch {
    $('#health-text').textContent = '连接失败';
  }
}

async function loadExamples() {
  const data = await api('/api/examples');
  state.examples = data.examples;
  $('#example-list').innerHTML = data.examples
    .map(
      (q) => `<button class="example-chip" data-question="${q}">${q}</button>`,
    )
    .join('');
  $$('.example-chip').forEach((btn) =>
    btn.addEventListener('click', () => {
      $('#question').value = btn.dataset.question;
      $('#question').focus();
    }),
  );
}

function renderResultBody(
  result,
  cols,
  rows,
  firstDimension,
  numericCol,
  max,
  attempts,
  bars,
  table,
) {
  return `
    <div class="result-answer"><strong>分析结论</strong> <span class="narrative-tag">${result.narrative?.mode === 'model' ? '模型解读 · 流式' : '确定性摘要'}</span><div class="narrative-text">${escapeHtml(result.answer).replace(/\n/g, '<br>')}</div></div>
    ${(result.warnings || []).length ? `<div class="result-meta">${result.warnings.map((w) => `<span class="small-pill">${escapeHtml(w)}</span>`).join('')}</div>` : ''}
    <div class="result-meta">
      <span class="small-pill">${{ llm: 'LLM 生成', 'llm-repair': 'LLM 修复', 'rule-repair': '规则回退修复' }[result.mode] || '规则生成'}</span>
      <span class="small-pill">${escapeHtml(result.plan?.intent || '')}</span>
      <span class="small-pill">${escapeHtml(result.plan?.time_range || '')}</span>
      <span class="small-pill">${escapeHtml(result.elapsed_ms)} ms</span>
      <span class="small-pill">${attempts.length} 次执行</span>
      ${(result.plan?.inherited || []).map((item) => `<span class="small-pill inherited-pill">继承 · ${escapeHtml(item)}</span>`).join('')}
    </div>
    ${numericCol && rows.length ? `<div class="chart-box"><div class="chart-title">${escapeHtml(numericCol)} · 按 ${escapeHtml(firstDimension)} 展示</div><div class="chart-bars">${bars}</div></div>` : ''}
    ${table}
    <details class="accordion"><summary><span>查看生成的 SQL</span><span>⌄</span></summary><div class="sql-block">${escapeHtml(result.sql)}</div></details>
    <details class="accordion"><summary><span>执行与自愈轨迹</span><span>⌄</span></summary><div class="trace">${attempts.map((a) => `<div class="trace-item"><span class="trace-mark ${a.status === 'failed' ? 'bad' : ''}">${a.status === 'failed' ? '!' : '✓'}</span><span>第 ${escapeHtml(a.attempt)} 次 · ${escapeHtml(a.status)} · ${escapeHtml(a.elapsed_ms)} ms${a.error ? ' · ' + escapeHtml(a.error) : ''}</span></div>`).join('')}</div></details>
    <details class="accordion"><summary><span>Schema 召回依据</span><span>⌄</span></summary><div class="trace">${(result.retrieval || []).map((hit) => `<div class="trace-item"><span class="trace-mark">◎</span><span>${escapeHtml(hit.label)}（${escapeHtml(hit.table)}）· 融合分 ${escapeHtml(hit.score)} · 语义 ${escapeHtml(hit.vector_score)} · 关键词 ${escapeHtml(hit.lexical_score)}</span></div>`).join('')}</div></details>`;
}

function renderResult(result) {
  state.lastResult = result;
  const cols = result.columns || [];
  const rows = result.rows || [];
  const firstDimension = cols[0];
  const preferred = [
    '销售额',
    '客单价',
    'ROI',
    '退款率',
    '销量',
    '退款金额',
    '投放花费',
  ];
  const numericCol =
    preferred.find((c) => cols.includes(c)) ||
    cols.find((c) => rows.some((r) => typeof r[c] === 'number'));
  const max = numericCol
    ? Math.max(...rows.map((r) => Math.abs(Number(r[numericCol]) || 0)), 1)
    : 1;
  const bars = rows
    .slice(0, 8)
    .map(
      (row) => `
    <div class="chart-row">
      <span>${escapeHtml(row[firstDimension])}</span>
      <span class="chart-track"><span class="chart-fill" style="width:${((Math.abs(Number(row[numericCol]) || 0) / max) * 100).toFixed(1)}%"></span></span>
      <span class="chart-num">${numericCol && typeof row[numericCol] === 'number' ? row[numericCol].toLocaleString('zh-CN') : ''}</span>
    </div>`,
    )
    .join('');
  const table = rows.length
    ? `
    <div class="table-wrap"><table class="result-table">
      <thead><tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead>
      <tbody>${rows
        .slice(0, 30)
        .map(
          (row) =>
            `<tr>${cols.map((c) => `<td>${escapeHtml(row[c] ?? '')}</td>`).join('')}</tr>`,
        )
        .join('')}</tbody>
    </table></div>`
    : '<p class="muted">没有匹配数据。</p>';
  const attempts = result.attempts || [];
  const thread = $('#conversation-thread');
  if (thread.querySelector('.conversation-empty')) thread.innerHTML = '';
  thread.insertAdjacentHTML(
    'beforeend',
    `
    <article class="turn-card">
      <div class="turn-question"><span class="turn-label">你</span><div>${escapeHtml(result.question)}</div></div>
      <div class="turn-meta"><span>${escapeHtml(result.plan?.time_range || '')}</span><span>${escapeHtml(result.trace_id || '')}</span><span>${escapeHtml(result.elapsed_ms)} ms</span></div>
      ${renderResultBody(result, cols, rows, firstDimension, numericCol, max, attempts, bars, table)}
    </article>`,
  );
  renderEvidence(result);
}
function setTurnNarrative(card, text, trailing = false) {
  const box = card?.querySelector('.narrative-text');
  if (box) {
    box.innerHTML =
      escapeHtml(text).replace(/\n/g, '<br>') +
      (trailing ? '<span class="stream-caret"></span>' : '');
  }
}

function setNarrativeTag(card, narrative) {
  const tag = card?.querySelector('.narrative-tag');
  if (!tag) return;
  const model = narrative?.mode === 'model';
  tag.textContent = model
    ? `模型解读 · ${narrative.model || '流式'}`
    : '确定性摘要';
  tag.classList.toggle('model', model);
}

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderEvidence(result) {
  const hits = result.retrieval || [];
  $('#evidence-content').innerHTML =
    hits
      .map(
        (hit, index) => `
    <article class="panel schema-card">
      <div class="schema-head"><div><div class="panel-kicker">RECALL #${index + 1}</div><div class="schema-name">${hit.label} · ${hit.table}</div></div><span class="badge">融合 ${hit.score}</span></div>
      <p class="schema-desc">${hit.description}</p>
      <div class="score-grid"><div class="score-box"><div class="score-label">语义分数</div><div class="score-value">${hit.vector_score}</div></div><div class="score-box"><div class="score-label">关键词分数</div><div class="score-value">${hit.lexical_score}</div></div><div class="score-box"><div class="score-label">命中词</div><div class="score-value" style="font-size:10px">${hit.matched_terms.join(' / ') || '—'}</div></div></div>
    </article>`,
      )
      .join('') +
    `
    <article class="panel schema-card"><div class="panel-kicker">QUERY TRACE</div><div class="schema-name" style="margin-top:7px">执行轨迹与安全门禁</div>
    <div class="trace" style="margin-top:14px">${(result.attempts || []).map((a) => `<div class="trace-item"><span class="trace-mark ${a.status === 'failed' ? 'bad' : ''}">${a.status === 'failed' ? '!' : '✓'}</span><span>attempt ${a.attempt} · ${a.status} · ${a.elapsed_ms} ms ${a.error ? '· ' + a.error : ''}</span></div>`).join('')}</div>
    <div class="sql-block" style="margin-top:12px">${escapeHtml(result.sql)}</div></article>`;
}

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
    intent: '分析意图',
  };
  return Object.entries(labels)
    .map(([key, label]) => {
      let value = context?.[key];
      if (Array.isArray(value)) value = value.join(' / ');
      if (!value) return '';
      return `<div class="context-row"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`;
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
    rows || '<div class="context-empty">提交第一条问题后生成分析上下文。</div>';
  const title = $('#session-title');
  if (title) title.textContent = session?.title || '新的分析会话';
  const count = $('#session-count');
  if (count) count.textContent = `${sessionTurnCount(session)} 轮`;
}

function renderEmptyConversation() {
  const thread = $('#conversation-thread');
  if (!thread) return;
  thread.innerHTML = `<div class="conversation-empty">
          <div class="conversation-empty-mark">AI</div>
              <h3>从一个经营问题开始</h3>
                  <p>例如先问“上个月华东地区销售额是多少？”，再追问“那华南呢”。系统会继承时间与指标，只替换地区。</p>
                      <div class="starter-grid" id="example-list">${state.examples.map((q) => `<button class="example-chip" data-question="${escapeHtml(q)}">${escapeHtml(q)}</button>`).join('')}</div>
                        </div>`;
  thread.querySelectorAll('.example-chip').forEach((btn) =>
    btn.addEventListener('click', () => {
      $('#question').value = btn.dataset.question || '';
      $('#question').focus();
    }),
  );
}
function scrollThreadToLatest() {
  const thread = $('#conversation-thread');
  requestAnimationFrame(() =>
    thread?.lastElementChild?.scrollIntoView({
      block: 'nearest',
      behavior: 'smooth',
    }),
  );
}
function renderSessionThread(session, options = {}) {
  const thread = $('#conversation-thread');
  if (!thread) return;
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
      const result = turn.result || {};
      result.question = turn.question;
      renderResult(result);
    });
    state.lastResult = session.turns[session.turns.length - 1]?.result || null;
    if (options.scrollToLatest) scrollThreadToLatest();
  } else {
    renderEmptyConversation();
  }
}
async function openSession(sessionId, options = {}) {
  const requestId = ++state.sessionOpenRequest;
  try {
    const session = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (requestId !== state.sessionOpenRequest) return;
    renderSessionThread(session, options);
  } catch (error) {
    if (requestId !== state.sessionOpenRequest) return;
    toast(error.message);
    if (state.session?.id === sessionId) state.session = null;
    await loadSessions();
  }
}
async function loadSessions() {
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
  if (state.creatingSession) return;
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

function parseSseFrame(frame) {
  let event = 'message';
  const data = [];
  frame.split(/\r?\n/).forEach((line) => {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  });
  if (!data.length) return null;
  const raw = data.join('\n');
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event: 'error', data: { message: '流式响应解析失败' } };
  }
}

async function streamQuery(question, sessionId) {
  const response = await fetch('/api/query/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({ question, session_id: sessionId }),
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
  let card = null;
  let narrative = '';
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
        if (event === 'result') {
          $('#thinking-card')?.remove();
          result = data.result;
          result.session_id = data.session_id;
          renderResult(result);
          card = $('#conversation-thread .turn-card:last-of-type');
          narrative = '';
          setTurnNarrative(card, '', true);
          scrollThreadToLatest();
        } else if (event === 'delta') {
          narrative += data.delta || '';
          setTurnNarrative(card, narrative, true);
          scrollThreadToLatest();
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
        } else if (event === 'done') {
          if (data.session) state.session = data.session;
          if (result) {
            result.elapsed_ms = data.elapsed_ms ?? result.elapsed_ms;
            result.trace_id = data.trace_id || result.trace_id;
          }
          const turnMeta = card?.querySelector('.turn-meta');
          if (turnMeta && result) {
            turnMeta.innerHTML = `<span>${escapeHtml(result.plan?.time_range || '')}</span><span>${escapeHtml(result.trace_id || '')}</span><span>${escapeHtml(result.elapsed_ms)} ms</span>`;
          }
        } else if (event === 'error') {
          throw new Error(data.message || '流式查询失败');
        }
      }
      index = buffer.indexOf('\n\n');
    }
  }
  if (!result) throw new Error('流式响应未返回查询结果');
  if (state.session) {
    renderSessionList();
    renderContext(state.session);
  }
  return result;
}

async function submitQuery(question) {
  if (!question || question.trim().length < 2) {
    toast('请输入一个完整问题');
    return;
  }
  if (state.sending) return;
  state.sending = true;
  const button = $('#submit-btn');
  button.disabled = true;
  button.querySelector('span').textContent = '分析中…';
  const thread = $('#conversation-thread');
  thread.querySelector('.conversation-empty')?.remove();
  thread.insertAdjacentHTML(
    'beforeend',
    '<div id="thinking-card" class="thinking-card"><span class="thinking-dot"></span><span>正在检索 Schema、生成 SQL 并执行安全校验</span></div>',
  );
  scrollThreadToLatest();
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
    const result = await streamQuery(question, sessionId);
    $('#thinking-card')?.remove();
    if (state.session) {
      const latest = state.session.turns?.[state.session.turns.length - 1];
      if (latest) {
        latest.answer = result.answer;
        latest.result = result;
      }
    }
    $('#question').value = '';
    toast(`分析完成 · ${result.elapsed_ms} ms`);
  } catch (error) {
    $('#thinking-card')?.remove();
    toast(error.message);
    if (state.session)
      renderSessionThread(state.session, { scrollToLatest: true });
    else renderEmptyConversation();
  } finally {
    state.sending = false;
    button.disabled = false;
    button.querySelector('span').textContent = '发送问题';
  }
}
function switchView(view) {
  if (!document.querySelector(`#view-${view}`)) view = 'workspace';
  $$('.nav-item').forEach((x) =>
    x.classList.toggle('active', x.dataset.view === view),
  );
  $$('.view').forEach((x) =>
    x.classList.toggle('active', x.id === `view-${view}`),
  );
  if (window.location.pathname !== `/${view}`) {
    window.history.pushState({}, '', `/${view}`);
  }
  if (view === 'models') loadModelConfig();
  if (view === 'evaluation') loadEvaluation();
  if (view === 'overview') loadOverview();
  if (view === 'workspace') loadSessions();
}

function bindNavigation() {
  $$('.nav-item').forEach((item) =>
    item.addEventListener('click', () => switchView(item.dataset.view)),
  );
  const initial =
    window.location.pathname.replace(/^\/|\/$/g, '') || 'workspace';
  switchView(initial);
  window.addEventListener('popstate', () => {
    switchView(window.location.pathname.replace(/^\/|\/$/g, '') || 'workspace');
  });
}

const EVAL_LABELS = {
  sales_overview: '销售总览',
  category_analysis: '品类分析',
  refund_analysis: '退款分析',
  marketing_efficiency: '投放效率',
};

function formatEvaluatedAt(value) {
  if (!value) return '未提供';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false });
}

function renderEvaluation(data) {
  $('#eval-meta').innerHTML = `
    <span>评测模式 <strong>${escapeHtml(data.mode || 'rule')}</strong></span>
    <span>数据集 <strong>${escapeHtml(data.dataset_range || '固定数据集')}</strong></span>
    <span>结果时间 <strong>${escapeHtml(formatEvaluatedAt(data.evaluated_at))}</strong></span>`;
  $('#eval-summary').innerHTML = [
    metricCard(
      '执行成功率',
      data.execution_rate + '%',
      `${data.passed}/${data.total} 条通过`,
      true,
    ),
    metricCard('结果命中率', data.hit_rate + '%', '关键结果值符合预期'),
    metricCard(
      '平均响应时间',
      data.avg_elapsed_ms + ' ms',
      '本地运行 · 含检索与 SQL',
    ),
    metricCard(
      '覆盖意图',
      data.intents + ' 类',
      '销售 / 品类 / 退款 / ROI / 趋势',
    ),
  ].join('');
  $('#eval-count').textContent = `${data.passed}/${data.total} 通过`;
  const rows = (data.cases || [])
    .map(
      (item) => `
    <tr>
      <td>${escapeHtml(item.question)}</td>
      <td>${escapeHtml(EVAL_LABELS[item.intent] || item.intent)}</td>
      <td><span class="badge ${item.executed ? '' : 'fail'}">${item.executed ? '已执行' : '失败'}</span></td>
      <td><span class="badge ${item.hit ? '' : 'fail'}">${item.hit ? '命中' : '未命中'}</span></td>
      <td>${escapeHtml(item.attempts)} 次 / ${escapeHtml(item.elapsed_ms)} ms</td>
      <td class="eval-result-cell">${item.error ? `<span class="eval-error">${escapeHtml(item.error)}</span>` : '<span class="eval-pass">符合验收标准</span>'}</td>
    </tr>`,
    )
    .join('');
  $('#eval-table').innerHTML = rows
    ? `<table class="eval-table"><thead><tr><th>黄金问题</th><th>意图</th><th>执行</th><th>结果</th><th>尝试 / 耗时</th><th>验收说明</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<p class="muted">当前评测集没有返回用例。</p>';
}

async function loadEvaluation() {
  const button = $('#run-eval-btn');
  button.disabled = true;
  button.textContent = '运行中…';
  try {
    const data = await api('/api/evaluation');
    renderEvaluation(data);
    toast(`评测完成 · ${data.passed}/${data.total} 通过`);
  } catch (error) {
    toast(error.message);
    $('#eval-table').innerHTML =
      `<p class="muted">评测加载失败：${escapeHtml(error.message)}</p>`;
  } finally {
    button.disabled = false;
    button.textContent = '重新运行评测';
  }
}

const MODEL_BASE_URLS = {
  openai: 'https://api.openai.com/v1',
  deepseek: 'https://api.deepseek.com/v1',
  qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  siliconflow: 'https://api.siliconflow.cn/v1',
  moonshot: 'https://api.moonshot.cn/v1',
};

function renderModelRuntime(model, active) {
  if (!model) return;
  const status = $('#model-status');
  if (status) {
    status.textContent = active ? '已启用' : '未启用';
    status.className = `badge ${active ? '' : 'fail'}`;
  }
  const runtime = $('#model-runtime');
  if (runtime)
    runtime.innerHTML = [
      ['状态', active ? '大模型生成已启用' : '规则生成模式'],
      ['模型', model.model || '未选择'],
      ['服务商', model.provider || '未配置'],
      ['接口地址', model.base_url || '未配置'],
      ['API Key', model.api_key_configured ? '已保存（服务端）' : '未配置'],
    ]
      .map(
        ([k, v]) => `<div><span>${k}</span><span>${escapeHtml(v)}</span></div>`,
      )
      .join('');
}

function modelFormPayload() {
  return {
    provider: $('#model-provider').value,
    base_url: $('#model-base-url').value.trim(),
    model: $('#model-name-manual').value.trim() || $('#model-name').value,
    api_key: $('#model-api-key').value.trim() || null,
  };
}

function setModelMessage(message, kind = '') {
  const el = $('#model-message');
  if (!el) return;
  el.textContent = message;
  el.className = `model-message ${kind}`;
}

async function loadModelConfig() {
  try {
    const data = await api('/api/model/config');
    state.model = data;
    $('#model-provider').value = data.provider || 'custom';
    $('#model-base-url').value = data.base_url || 'https://api.openai.com/v1';
    $('#model-name-manual').value = data.model || '';
    $('#model-enabled').checked = !!data.enabled;
    renderModelRuntime(data, data.active);
    if (data.api_key_configured)
      $('#model-api-key').placeholder = '已保存，留空保持不变';
    setModelMessage(
      data.active
        ? '模型配置已生效，查询将由当前模型生成。'
        : '当前未启用模型，查询使用规则生成器。',
    );
  } catch (error) {
    toast(error.message);
  }
}

async function fetchModels() {
  const button = $('#model-fetch-btn');
  button.disabled = true;
  setModelMessage('正在请求模型列表…');
  try {
    const data = await api('/api/model/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(modelFormPayload()),
    });
    const select = $('#model-name');
    select.innerHTML = '<option value="">请选择模型</option>';
    data.models.forEach((name) => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });
    setModelMessage(
      `读取到 ${data.models.length} 个模型，请选择后保存。`,
      'ok',
    );
  } catch (error) {
    setModelMessage(error.message, 'bad');
  } finally {
    button.disabled = false;
  }
}

async function testModel() {
  const button = $('#model-test-btn');
  button.disabled = true;
  setModelMessage('正在测试模型连接…');
  try {
    const data = await api('/api/model/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(modelFormPayload()),
    });
    setModelMessage(`连接成功，模型回复：${data.reply}`, 'ok');
  } catch (error) {
    setModelMessage(error.message, 'bad');
  } finally {
    button.disabled = false;
  }
}

async function saveModel(event) {
  event.preventDefault();
  const payload = modelFormPayload();
  payload.enabled = $('#model-enabled').checked;
  try {
    const data = await api('/api/model/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    setModelMessage(
      data.active
        ? '配置已保存，后续查询将使用该模型。'
        : '配置已保存，当前仍使用规则生成。',
      'ok',
    );
    $('#model-api-key').value = '';
    await loadHealth();
  } catch (error) {
    setModelMessage(error.message, 'bad');
  }
}

function init() {
  bindNavigation();
  loadHealth();
  loadOverview();
  loadExamples();
  $('#new-session-btn')?.addEventListener('click', createSession);
  $('#reset-context-btn')?.addEventListener('click', createSession);
  $('#overview-refresh-btn')?.addEventListener('click', () => {
    loadOverview();
    loadHealth();
  });
  $('#query-form').addEventListener('submit', (event) => {
    event.preventDefault();
    submitQuery($('#question').value);
  });
  $('#question').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      $('#query-form').requestSubmit();
    }
  });
  $('#refresh-btn').addEventListener('click', () => {
    loadOverview();
    loadHealth();
  });
  $('#run-eval-btn').addEventListener('click', loadEvaluation);
  $('#model-provider')?.addEventListener('change', () => {
    const baseUrl = MODEL_BASE_URLS[$('#model-provider').value];
    if (baseUrl) $('#model-base-url').value = baseUrl;
  });
  $('#model-fetch-btn')?.addEventListener('click', fetchModels);
  $('#model-test-btn')?.addEventListener('click', testModel);
  $('#model-form')?.addEventListener('submit', saveModel);
}

document.addEventListener('DOMContentLoaded', init);
