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
  activeQueryCard: null,
  activeTraceId: null,
  activeAbortController: null,
  threadPinned: true,
  evalTasksData: null,
  evalSelectedTask: null,
  evalBaseline: null,
  evalCandidate: null,
  evalPollTimer: null,
  dataPlatform: null,
  platformTab: 'catalog',
  evaluationTab: 'runs',
  evalTasks: [],
  datasets: [],
  experiments: [],
  feedback: [],
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
    $('#health-text').textContent = data.database ? '数据服务正常' : '数据库缺失';
    $('#retrieval-meta').textContent = data.retrieval.elasticsearch_enabled
      ? '数据检索正常 · 只读查询'
      : '本地检索 · 只读查询';
    state.model = data.model;
    renderModelHeading(data.model, data.llm_active);
    renderModelRuntime(data.model, data.llm_active);
  } catch {
    $('#health-text').textContent = '连接失败';
    const pill = $('#model-pill');
    if (pill) {
      pill.textContent = '状态连接失败';
      pill.classList.add('offline');
    }
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

const SQL_SOURCE_LABELS = {
  llm: '模型生成',
  'llm-repair': '模型修复',
  rule: '规则回退',
  'rule-repair': '规则修复',
  'model-failed': '模型未产出',
  'model-repair-failed': '模型修复失败',
  unavailable: '未运行',
  error: '异常',
};

const ATTEMPT_STATUS_LABELS = {
  success: '成功',
  failed: '失败',
};

function sqlSourceLabel(value) {
  return SQL_SOURCE_LABELS[value] || '来源未知';
}

function attemptLine(attempt, framed = false) {
  const failed = attempt.status === 'failed';
  const status = ATTEMPT_STATUS_LABELS[attempt.status] || attempt.status || '未知';
  const prefix = framed ? '<span class="trace-mark ' + (failed ? 'bad' : '') + '">' + (failed ? '!' : '✓') + '</span>' : '';
  const error = attempt.error ? ' · ' + escapeHtml(attempt.error) : '';
  return '<div class="trace-item">' + prefix + '<span>第 ' + escapeHtml(attempt.attempt) + ' 次 · ' + escapeHtml(status) + ' · ' + escapeHtml(attempt.elapsed_ms) + ' 毫秒' + error + '</span></div>';
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
    <div class="result-answer"><strong>分析结论</strong> <span class="narrative-tag">${result.narrative?.mode === 'model' ? `模型解读 · ${escapeHtml(result.narrative.model || '已完成')}` : '确定性摘要'}</span><div class="narrative-text">${escapeHtml(result.answer).replace(/\n/g, '<br>')}</div></div>
    ${(result.warnings || []).length ? `<div class="result-meta">${result.warnings.map((w) => `<span class="small-pill">${escapeHtml(w)}</span>`).join('')}</div>` : ''}
    <div class="result-meta">
      <span class="small-pill">${sqlSourceLabel(result.mode)}</span>
      <span class="small-pill">${attempts.length} 次执行 · ${escapeHtml(result.elapsed_ms)} 毫秒</span>
      ${(result.plan?.inherited || []).map((item) => `<span class="small-pill inherited-pill">继承 · ${escapeHtml(item)}</span>`).join('')}
    </div>
    ${numericCol && firstDimension && rows.length > 1 ? `<div class="chart-box"><div class="chart-title">${escapeHtml(numericCol)} · 按 ${escapeHtml(firstDimension)} 展示${rows.length > 8 ? ' · 前 8 项，完整结果见数据依据' : ''}</div><div class="chart-bars">${bars}</div></div>` : ''}
    <details class="accordion result-evidence"><summary><span>数据依据与 SQL</span><span>⌄</span></summary>
      <div class="evidence-detail">
        <div class="evidence-detail-head"><span>结果明细</span><span>${rows.length} 行${result.truncated ? ' · 已达到查询行数上限' : ''}</span></div>
        ${table}
        <div class="evidence-detail-head"><span>生成的只读 SQL</span><span>${sqlSourceLabel(result.sql_mode || result.mode)}</span></div>
        <div class="sql-block">${escapeHtml(result.sql)}</div>
        <div class="evidence-detail-head"><span>执行轨迹与数据表召回</span><span>${attempts.length} 次执行</span></div>
        <div class="trace">${attempts.map((a) => attemptLine(a, true)).join('')}${(result.retrieval || []).map((hit) => `<div class="trace-item"><span class="trace-mark">◎</span><span>召回 ${escapeHtml(hit.label)}（${escapeHtml(hit.table)}）· 融合 ${escapeHtml(hit.score)} · 语义 ${escapeHtml(hit.vector_score)} · 关键词 ${escapeHtml(hit.lexical_score)}</span></div>`).join('')}</div>
      </div>
    </details>`;
}

function renderPendingTurn(question) {
  const thread = $('#conversation-thread');
  if (!thread) return null;
  thread.querySelector('.conversation-empty')?.remove();
  const stages = [
    ['retrieve', '召回数据表'],
    ['plan', '理解意图'],
    ['generate', '生成 SQL'],
    ['execute', '只读执行'],
    ['finalize', '组织结论'],
  ];
  thread.insertAdjacentHTML(
    'beforeend',
    `<article class="turn-card is-streaming" data-live-turn="true">
      <div class="turn-question"><span class="turn-label">你</span><div>${escapeHtml(question)}</div></div>
      <div class="turn-answer">
        <div class="turn-label assistant-label">助手</div>
        <div class="assistant-content">
          <div class="stream-head">
            <span class="stream-pulse"></span>
            <strong class="stream-title">正在分析</strong>
            <span class="stream-detail">准备检索数据表</span>
          </div>
          <div class="stream-stage-list" aria-live="polite">
            ${stages
              .map(
                ([stage, label], index) =>
                  `<span class="stream-stage" data-stage="${stage}"><i>${index + 1}</i><span>${label}</span></span>`,
              )
              .join('')}
          </div>
        </div>
      </div>
    </article>`,
  );
  state.activeQueryCard = thread.querySelector(
    '.turn-card[data-live-turn="true"]:last-of-type',
  );
  state.threadPinned = true;
  scrollThreadToLatest({ force: true });
  return state.activeQueryCard;
}

function updateStreamStage(card, data = {}) {
  if (!card || !card.isConnected) return;
  const detail = card.querySelector('.stream-detail');
  if (detail && data.message) detail.textContent = data.message;
  const stages = [...card.querySelectorAll('.stream-stage')];
  const order = ['retrieve', 'plan', 'generate', 'execute', 'finalize'];
  if (data.stage === 'repair') {
    detail?.classList.add('is-warning');
    const execute = card.querySelector('.stream-stage[data-stage="execute"]');
    execute?.classList.add('is-retrying');
    return;
  }
  const index = order.indexOf(data.stage);
  if (index < 0) return;
  stages.forEach((item, itemIndex) => {
    item.classList.toggle('is-current', itemIndex === index);
    item.classList.toggle('is-done', itemIndex < index);
    if (itemIndex === index) item.classList.remove('is-retrying');
  });
  const title = card.querySelector('.stream-title');
  if (title) title.textContent = index === order.length - 1 ? '正在整理回答' : '正在分析';
  scrollThreadToLatest();
}

function renderResult(result, options = {}) {
  state.lastResult = result;
  const cols = result.columns || [];
  const rows = result.rows || [];
  const firstDimension = cols.find(c => rows.some(r => typeof r[c] !== 'number' && r[c] != null));
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
      <tbody>${rows.map(
          (row) =>
            `<tr>${cols.map((c) => `<td>${escapeHtml(row[c] ?? '')}</td>`).join('')}</tr>`,
        )
        .join('')}</tbody>
    </table></div>`
    : '<p class="muted">没有匹配数据。</p>';
  const attempts = result.attempts || [];
  const body = renderResultBody(
    result,
    cols,
    rows,
    firstDimension,
    numericCol,
    max,
    attempts,
    bars,
    table,
  );
  const thread = $('#conversation-thread');
  if (!thread) return;
  if (thread.querySelector('.conversation-empty')) thread.innerHTML = '';
  const liveCard =
    options.card ||
    (!options.append && state.activeQueryCard && state.activeQueryCard.isConnected
      ? state.activeQueryCard
      : null);
  if (liveCard) {
    liveCard.classList.remove('is-streaming');
    liveCard.removeAttribute('data-live-turn');
    liveCard.innerHTML = `
      <div class="turn-question"><span class="turn-label">你</span><div>${escapeHtml(result.question)}</div></div>
      <div class="turn-meta"><span>${escapeHtml(result.plan?.time_range || '')}</span><span>${escapeHtml(result.elapsed_ms)} 毫秒</span></div>
      <div class="turn-answer"><div class="turn-label assistant-label">助手</div><div class="assistant-content">${body}</div></div>`;
    state.activeQueryCard = null;
  } else {
    thread.insertAdjacentHTML(
      'beforeend',
      `
      <article class="turn-card">
        <div class="turn-question"><span class="turn-label">你</span><div>${escapeHtml(result.question)}</div></div>
        <div class="turn-meta"><span>${escapeHtml(result.plan?.time_range || '')}</span><span>${escapeHtml(result.elapsed_ms)} 毫秒</span></div>
        <div class="turn-answer"><div class="turn-label assistant-label">助手</div><div class="assistant-content">${body}</div></div>
      </article>`,
    );
  }
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

function setPageHeading() {
  $('#page-title').textContent = 'Atlas BI';
  $('#page-subtitle').textContent = '电商经营分析';
}

function toggleWorkspacePanel(panel) {
  const workspace = $('#view-workspace');
  if (!workspace) return;
  const target = workspace.querySelector(`#${panel}-drawer`);
  if (!target) return;
  const open = target.classList.contains('is-open');
  closeWorkspacePanels();
  if (!open) {
    target.classList.add('is-open');
    workspace.classList.add('has-workspace-drawer');
    const trigger = panel === 'session' ? $('#history-btn') : $('#context-btn');
    trigger?.setAttribute('aria-expanded', 'true');
    setTimeout(() => target.querySelector('button, .session-item, summary')?.focus(), 0);
  }
}

function closeWorkspacePanels() {
  $('#view-workspace')?.classList.remove('has-workspace-drawer');
  $$('.workspace-drawer.is-open').forEach((panel) => panel.classList.remove('is-open'));
  $('#history-btn')?.setAttribute('aria-expanded', 'false');
  $('#context-btn')?.setAttribute('aria-expanded', 'false');
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
  return String(text ?? '')
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
      <div class="schema-head"><div><div class="schema-name">${hit.label} · ${hit.table}</div></div><span class="badge">融合 ${hit.score}</span></div>
      <p class="schema-desc">${hit.description}</p>
      <div class="score-grid"><div class="score-box"><div class="score-label">语义分数</div><div class="score-value">${hit.vector_score}</div></div><div class="score-box"><div class="score-label">关键词分数</div><div class="score-value">${hit.lexical_score}</div></div><div class="score-box"><div class="score-label">命中词</div><div class="score-value" style="font-size:12px">${hit.matched_terms.join(' / ') || '—'}</div></div></div>
    </article>`,
      )
      .join('') +
    `
    <article class="panel schema-card"><div class="schema-name">执行轨迹与安全门禁</div>
    <div class="trace" style="margin-top:14px">${(result.attempts || []).map((a) => attemptLine(a, true)).join('')}</div>
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

function scrollThreadToLatest({ force = false } = {}) {
  const thread = $('#conversation-thread');
  if (!thread) return;
  if (!force && !state.threadPinned) {
    updateLatestReplyButton();
    return;
  }
  requestAnimationFrame(() => {
    thread.scrollTop = thread.scrollHeight;
    state.threadPinned = true;
    updateLatestReplyButton();
  });
}

function renderQueryError(card, message) {
  if (!card || !card.isConnected) return;
  card.classList.remove('is-streaming');
  card.classList.add('has-error');
  card.removeAttribute('data-live-turn');
  if (card.querySelector('.result-answer')) {
    card.querySelector('.stream-caret')?.remove();
    card.insertAdjacentHTML('beforeend', `<div class="stream-error"><strong>回答生成中断，已保留查询结果</strong><span>${escapeHtml(message)}</span></div>`);
    const tag = card.querySelector('.narrative-tag');
    if (tag) tag.textContent = '回答未完成';
    return;
  }
  card.innerHTML = `
    <div class="turn-question"><span class="turn-label">你</span><div>${escapeHtml(card.dataset.question || '')}</div></div>
    <div class="turn-answer stream-error-answer">
      <div class="turn-label assistant-label">助手</div>
      <div class="stream-error"><strong>这次查询没有完成</strong><span>${escapeHtml(message)}</span><button type="button" class="retry-query-btn">重新尝试</button></div>
    </div>`;
  card.querySelector('.retry-query-btn')?.addEventListener('click', () => {
    const question = card.querySelector('.turn-question div:last-child')?.textContent || '';
    $('#question').value = question;
    $('#question').focus();
  });
}

function followNarrative(card) {
  if (!state.threadPinned) return;
  const thread = $('#conversation-thread');
  const text = card?.querySelector('.narrative-text');
  if (!thread || !text) return;
  requestAnimationFrame(() => {
    if (!state.threadPinned) return;
    const bottom = text.getBoundingClientRect().bottom;
    const edge = thread.getBoundingClientRect().bottom - 16;
    if (bottom > edge) thread.scrollTop += bottom - edge;
  });
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
  const card = state.activeQueryCard;
  const requestId = crypto.randomUUID().replace(/-/g, '').slice(0, 24);
  state.activeTraceId = requestId;
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
        if (event === 'start' || event === 'status') {
          updateStreamStage(card, data);
        } else if (event === 'result') {
          result = data.result;
          result.session_id = data.session_id;
          renderResult(result, { card });
          narrative = '';
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
function switchView(view) {
  if (!document.querySelector(`#view-${view}`)) view = 'workspace';
  document.body.classList.toggle('workspace-active', view === 'workspace');
  $$('.nav-item').forEach((x) =>
    x.classList.toggle('active', x.dataset.view === view),
  );
  $$('.view').forEach((x) =>
    x.classList.toggle('active', x.id === `view-${view}`),
  );
  if (window.location.pathname !== `/${view}`) {
    window.history.pushState({}, '', `/${view}`);
  }
  setPageHeading();
  $$('.mobile-nav-item').forEach((x) =>
    x.classList.toggle('active', x.dataset.view === view),
  );
  closeWorkspacePanels();
  if (view === 'models') loadModelConfig();
  if (view === 'data-assets') loadDataAssets();
  if (view === 'evaluation') loadEvaluationTasks();
  if (view === 'overview') loadOverview();
  if (view === 'workspace') loadSessions();
}

function bindNavigation() {
  $$('.nav-item, .mobile-nav-item').forEach((item) =>
    item.addEventListener('click', () => switchView(item.dataset.view)),
  );
  const initial =
    window.location.pathname.replace(/^\/|\/$/g, '') || 'workspace';
  switchView(initial);
  window.addEventListener('popstate', () => {
    switchView(window.location.pathname.replace(/^\/|\/$/g, '') || 'workspace');
  });
}

async function loadDataAssets() {
  const container = $('#asset-table-list');
  if (!container) return;
  try {
    const data = await api('/api/data-platform');
    state.dataPlatform = data;
    renderDataPlatform(data);
    renderDataAssets(await api('/api/data-assets'));
  } catch (error) {
    toast(error.message);
    container.innerHTML = `<p class="muted">数据中台读取失败：${escapeHtml(error.message)}</p>`;
  }
}

function platformTab(name) {
  state.platformTab = name;
  $$('#data-platform-tabs .platform-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.platformTab === name));
  $$('#view-data-assets .platform-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `platform-panel-${name}`));
}

function statusBadge(status) {
  const map = { ready: ['已就绪', ''], holdout: ['留出集', 'warn'], registered: ['待同步', 'warn'], error: ['异常', 'fail'], running: ['运行中', 'pending'], queued: ['排队中', 'pending'], completed: ['已完成', ''], failed: ['失败', 'fail'], cancelled: ['已取消', 'cancel'] };
  const [label, cls] = map[status] || [status || '未知', ''];
  return `<span class="badge ${cls}">${escapeHtml(label)}</span>`;
}

function renderDataPlatform(data) {
  const summary = data.summary || {};
  $('#asset-summary').innerHTML = [
    metricCard('数据源', `${summary.source_count || 0} 个`, `${summary.ready_sources || 0} 个已同步 · 只读数据服务`, summary.source_count > 0),
    metricCard('质量规则', `${summary.rule_count || 0} 条`, `${summary.last_quality ? `${summary.last_quality.passed} 通过 / ${summary.last_quality.failed} 失败` : '尚未运行巡检'}`),
    metricCard('指标目录', `${summary.metric_count || 0} 个`, `${summary.published_metrics || 0} 个已发布`),
    metricCard('字段血缘', `${summary.lineage_edges || 0} 条`, '源系统字段 → 平台资产字段'),
  ].join('');
  $('#source-count').textContent = `${(data.sources || []).length} 个`;
  $('#source-table').innerHTML = (data.sources || []).map((source) => `<tr><td><strong>${escapeHtml(source.name)}</strong><div class="muted">${source.kind === 'csv' ? 'CSV' : source.kind === 'sqlite' ? 'SQLite' : escapeHtml(source.kind)} · ${escapeHtml(source.location)}</div></td><td>${statusBadge(source.status)}<div class="muted">${escapeHtml(source.last_sync_at || '尚未同步')}</div></td><td>${fmtNum(source.row_count)} 行</td><td>${source.schedule_minutes ? `${source.schedule_minutes} 分钟` : '手动'}</td><td><button class="secondary-btn compact-btn" data-sync-source="${escapeHtml(source.id)}">同步</button></td></tr>`).join('');
  $$('[data-sync-source]').forEach((btn) => btn.addEventListener('click', () => syncPlatformSource(btn.dataset.syncSource)));
  const ruleTypeLabels = { not_null: '非空', unique: '唯一', non_negative: '非负', not_orphan: '外键有效', row_min: '最少行数' };
  $('#quality-rule-table').innerHTML = `<table class="eval-table"><thead><tr><th>规则</th><th>类型</th><th>对象</th><th>状态</th></tr></thead><tbody>${(data.rules || []).map((rule) => `<tr><td>${escapeHtml(rule.name)}</td><td>${escapeHtml(ruleTypeLabels[rule.rule_type] || rule.rule_type)}</td><td>${escapeHtml(rule.table_name)}.${escapeHtml(rule.column_name || '')}</td><td>${rule.enabled ? '<span class="badge">启用</span>' : '<span class="badge cancel">停用</span>'}</td></tr>`).join('')}</tbody></table>`;
  $('#quality-run-table').innerHTML = (data.quality_runs || []).length ? `<table class="eval-table"><thead><tr><th>时间</th><th>数据源</th><th>结果</th></tr></thead><tbody>${data.quality_runs.map((run) => `<tr><td>${escapeHtml(formatEvaluatedAt(run.started_at))}</td><td>${escapeHtml(run.source_id)}</td><td><span class="badge ${run.failed ? 'fail' : ''}">${run.passed} 通过 / ${run.failed} 失败</span></td></tr>`).join('')}</tbody></table>` : '<p class="muted">尚无巡检记录。</p>';
  $('#metric-count').textContent = `${(data.metrics || []).length} 个`;
  $('#metric-table').innerHTML = `<table class="eval-table"><thead><tr><th>指标</th><th>粒度</th><th>口径</th><th>负责人</th><th>状态</th></tr></thead><tbody>${(data.metrics || []).map((metric) => `<tr><td><strong>${escapeHtml(metric.name)}</strong></td><td>${escapeHtml(metric.grain)}</td><td>${escapeHtml(metric.definition)}</td><td>${escapeHtml(metric.owner || '—')}</td><td>${statusBadge(metric.status === 'published' ? 'ready' : metric.status === 'deprecated' ? 'cancelled' : 'queued')}</td></tr>`).join('')}</tbody></table>`;
  $('#lineage-count').textContent = `${(data.lineage || []).length} 条`;
  $('#lineage-table').innerHTML = (data.lineage || []).length ? `<table class="eval-table"><thead><tr><th>上游字段</th><th></th><th>平台资产字段</th><th>关系</th></tr></thead><tbody>${data.lineage.slice(0, 100).map((edge) => `<tr><td><code>${escapeHtml(edge.upstream_node)}</code></td><td class="muted">→</td><td><code>${escapeHtml(edge.downstream_node)}</code></td><td>${escapeHtml(edge.relation)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">同步数据源后生成字段级血缘。</p>';
  $('#audit-list').innerHTML = (data.audit || []).map((item) => `<div class="audit-item"><strong>${escapeHtml(item.action)}</strong><small>${escapeHtml(item.actor)} · ${escapeHtml(formatEvaluatedAt(item.created_at))}</small><code>${escapeHtml(item.resource_type)} / ${escapeHtml(item.resource_id)}</code><small>${item.role}</small></div>`).join('');}

async function syncPlatformSource(sourceId) {
  try {
    await api(`/api/data-platform/sources/${encodeURIComponent(sourceId)}/sync`, { method: 'POST' });
    toast('数据源已同步');
    await loadDataAssets();
  } catch (error) { toast(`同步失败：${error.message}`); }
}

async function runPlatformQuality() {
  try {
    const data = await api('/api/data-platform/sources/ds_commerce/quality', { method: 'POST' });
    toast(`质量巡检完成 · ${data.passed} 通过 / ${data.failed} 失败`);
    await loadDataAssets();
  } catch (error) { toast(error.message); }
}

async function runScheduler() {
  try {
    const data = await api('/api/data-platform/scheduler/run', { method: 'POST' });
    toast(`到期同步完成 · ${data.count} 个数据源`);
    await loadDataAssets();
  } catch (error) { toast(error.message); }
}

function renderDataAssets(data) {
  const summary = data.summary || {};
  const quality = data.quality || {};
  const health = summary.status === 'healthy';
  $('#asset-summary').innerHTML = [
    metricCard('资产健康度', health ? '通过' : '需检查', `${summary.passed_checks || 0}/${summary.check_count || 0} 项质量检查通过`, health),
    metricCard('业务表', `${summary.table_count || 0} 张`, `${summary.column_count || 0} 个字段 · ${summary.total_rows || 0} 行`, false),
    metricCard('关联关系', `${(data.relations || []).length} 条`, '外键语义与孤儿记录检查', false),
    metricCard('指标口径', `${(data.semantic_layer?.metrics || []).length} 个`, '销售额 / 退款 / ROI / 客单价', false),
  ].join('');
  $('#asset-table-count').textContent = `${summary.table_count || 0} 张表`;
  $('#asset-quality-status').textContent = health ? '全部通过' : `${summary.failed_checks || 0} 项失败`;
  $('#asset-quality-status').className = `badge ${health ? '' : 'fail'}`;
  $('#asset-table-list').innerHTML = (data.tables || []).map((table) => `
    <details class="asset-table-card" ${table.name === 'orders' ? 'open' : ''}>
      <summary>
        <span><strong>${escapeHtml(table.label)}</strong><small>${escapeHtml(table.name)} · ${escapeHtml(table.grain)}</small></span>
        <span class="asset-table-meta">${fmtNum(table.row_count)} 行 · ${table.column_count} 字段</span>
      </summary>
      <p>${escapeHtml(table.description)}</p>
      <div class="asset-range">${table.date_range?.start ? `覆盖 ${escapeHtml(table.date_range.start)} 至 ${escapeHtml(table.date_range.end)}` : '无独立日期字段'}</div>
      <div class="table-wrap"><table class="eval-table asset-column-table"><thead><tr><th>字段</th><th>类型</th><th>含义</th><th>敏感级别</th></tr></thead><tbody>${(table.columns || []).map((column) => `<tr><td><code>${escapeHtml(column.name)}</code></td><td>${escapeHtml(column.type)}</td><td>${escapeHtml(column.description)}</td><td>${column.sensitivity === 'restricted' ? '<span class="badge fail">受限</span>' : '<span class="badge">公开</span>'}</td></tr>`).join('')}</tbody></table></div>
    </details>`).join('');
  $('#asset-quality-list').innerHTML = (quality.checks || []).map((check) => `
    <div class="asset-check ${check.passed ? 'pass' : 'fail'}"><span class="asset-check-icon">${check.passed ? '✓' : '!'}</span><span>${escapeHtml(check.label)}</span><strong>${check.passed ? '通过' : escapeHtml(check.value)}</strong></div>`).join('');
  $('#asset-metric-list').innerHTML = (data.semantic_layer?.metrics || []).map((metric) => `
    <div class="asset-metric"><strong>${escapeHtml(metric.name)}</strong><span>${escapeHtml(metric.grain)}</span><small>${escapeHtml(metric.definition)}</small></div>`).join('');
  $('#asset-relation-count').textContent = `${(data.relations || []).length} 条`;
  $('#asset-relation-table').innerHTML = `<table class="eval-table"><thead><tr><th>源表</th><th>关系</th><th>目标表</th><th>孤儿记录</th></tr></thead><tbody>${(data.relations || []).map((r) => `<tr><td><code>${escapeHtml(r.source)}.${escapeHtml(r.source_column)}</code></td><td>${escapeHtml(r.description)}</td><td><code>${escapeHtml(r.target)}.${escapeHtml(r.target_column)}</code></td><td><span class="badge ${r.orphan_count ? 'fail' : ''}">${r.orphan_count}</span></td></tr>`).join('')}</tbody></table>`;}

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

function evalModeLabel(item) {
  return SQL_SOURCE_LABELS[item.sql_mode] || item.sql_mode || '未知';
}

const FEEDBACK_CATEGORY_LABELS = {
  wrong_result: '结果错误',
  wrong_sql: 'SQL 口径',
  bad_narrative: '解读问题',
  retrieval: '召回问题',
  other: '其他',
};

const EVAL_STATUS_LABELS = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
};

function evalTaskStatusBadge(status) {
  return `<span class="badge ${status === 'failed' ? 'fail' : status === 'running' || status === 'queued' ? 'pending' : ''}">${escapeHtml(EVAL_STATUS_LABELS[status] || status)}</span>`;
}

function renderEvaluationTaskList(data) {
  const tasks = data.tasks || [];
  state.evalTasks = tasks;
  $('#eval-history-count').textContent = `${tasks.length} 个任务`;
  $('#eval-task-list').innerHTML = tasks.length
    ? tasks.map((task) => {
      const selected = state.evalSelectedTask === task.id;
      const progress = task.progress || {};
      return `<button type="button" class="eval-task-item ${selected ? 'selected' : ''}" data-eval-task="${escapeHtml(task.id)}">
        <span class="eval-task-main"><strong>${escapeHtml(task.label)}</strong><small>${escapeHtml(task.model_name || '未记录模型')} · ${escapeHtml(formatEvaluatedAt(task.created_at))}</small></span>
        <span class="eval-task-side">${evalTaskStatusBadge(task.status)}<small>${task.status === 'running' ? `${progress.completed || 0}/${progress.total || 0}` : task.total ? `${task.passed}/${task.total} 通过` : '—'}</small></span>
        ${task.status === 'running' ? `<span class="eval-progress"><i style="width:${progress.percent || 0}%"></i></span>` : ''}
        ${task.status === 'queued' ? `<span class="eval-task-actions"><button type="button" class="secondary-btn compact-btn" data-eval-cancel="${escapeHtml(task.id)}">取消</button></span>` : ''}
        ${['failed','cancelled','completed'].includes(task.status) ? `<span class="eval-task-actions"><button type="button" class="secondary-btn compact-btn" data-eval-retry="${escapeHtml(task.id)}">重试</button></span>` : ''}
      </button>`;
    }).join('')
    : '<p class="muted">暂无评测任务。</p>';
  $('#eval-task-list').querySelectorAll('[data-eval-task]').forEach((button) => {
    button.addEventListener('click', (event) => {
      if (event.target.closest('[data-eval-cancel], [data-eval-retry]')) return;
      selectEvaluationTask(button.dataset.evalTask);
    });
  });
  $('#eval-task-list').querySelectorAll('[data-eval-cancel]').forEach((button) => button.addEventListener('click', (event) => { event.stopPropagation(); cancelEvaluation(button.dataset.evalCancel); }));
  $('#eval-task-list').querySelectorAll('[data-eval-retry]').forEach((button) => button.addEventListener('click', (event) => { event.stopPropagation(); retryEvaluation(button.dataset.evalRetry); }));
  const completed = tasks.filter((task) => task.status === 'completed' && task.report);
  const baseline = $('#eval-baseline');
  const candidate = $('#eval-candidate');
  if (baseline && candidate) {
    const options = completed.map((task) => `<option value="${escapeHtml(task.id)}">${escapeHtml(task.label)}</option>`).join('');
    baseline.innerHTML = options;
    candidate.innerHTML = options;
    if (completed.length >= 2) {
      baseline.value = state.evalBaseline || completed[1].id;
      candidate.value = state.evalCandidate || completed[0].id;
      state.evalBaseline = baseline.value;
      state.evalCandidate = candidate.value;
    }
  }
}

function renderEvaluationTaskSummary(task) {
  if (!task) {
    $('#eval-meta').innerHTML = '<span>执行方式 <strong>任务队列 · 单执行槽</strong></span><span>数据集 <strong>2025-10-01 至 2026-02-28</strong></span><span>状态 <strong>暂无任务</strong></span>';
    $('#eval-count').textContent = '等待加载';
    return;
  }
  const progress = task.progress || {};
  $('#eval-meta').innerHTML = `
    <span>任务 <strong>${escapeHtml(task.id)}</strong></span>
    <span>模型 <strong>${escapeHtml(task.model_name || '未记录')}</strong></span>
    <span>状态 <strong>${escapeHtml(EVAL_STATUS_LABELS[task.status] || task.status)}${task.status === 'running' ? ` · ${progress.completed || 0}/${progress.total || 0}` : ''}</strong></span>
    <span>创建时间 <strong>${escapeHtml(formatEvaluatedAt(task.created_at))}</strong></span>`;
  if (task.status === 'failed') {
    $('#eval-summary').innerHTML = `<div class="panel evaluation-empty eval-failed"><strong>任务执行失败</strong><span>${escapeHtml(task.error || '未知错误')}</span></div>`;
  } else if (task.status === 'queued' || task.status === 'running') {
    $('#eval-summary').innerHTML = `<div class="panel evaluation-empty eval-running"><strong>${task.status === 'queued' ? '等待后台执行' : '严格模型评测运行中'}</strong><span>已完成 ${progress.completed || 0} / ${progress.total || 0} 条用例；页面每 3 秒刷新任务状态。</span><div class="eval-progress wide"><i style="width:${progress.percent || 0}%"></i></div></div>`;
  } else {
    const report = task.report || {};
    const model = report.model || {};
    $('#eval-summary').innerHTML = [
      metricCard('模型通过率', `${task.hit_rate ?? 0}%`, `${task.passed}/${task.total} 条模型结果通过`, true),
      metricCard('SQL 执行率', `${task.execution_rate ?? 0}%`, '模型 SQL 实际执行比例'),
      metricCard('平均响应时间', `${task.avg_elapsed_ms ?? 0} 毫秒`, '检索、SQL、修复与解读'),
      metricCard('覆盖意图', `${report.intents ?? 0} 类`, `${escapeHtml(model.model || task.model_name || '模型')} · ${escapeHtml(report.suite_version || task.suite_version || '')}`),
    ].join('');
  }
}

async function cancelEvaluation(taskId) {
  try { await api(`/api/evaluation/tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' }); toast('已请求取消'); await loadEvaluationTasks(); }
  catch (error) { toast(error.message); }
}

async function retryEvaluation(taskId) {
  try { const task = await api(`/api/evaluation/tasks/${encodeURIComponent(taskId)}/retry`, { method: 'POST' }); toast('已加入重试队列'); state.evalSelectedTask = task.id; await loadEvaluationTasks(); }
  catch (error) { toast(error.message); }
}

async function submitDataset(event) {
  event.preventDefault();
  const ids = $('#dataset-cases').value.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
  try {
    await api('/api/evaluation/datasets', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name: $('#dataset-name').value, version: $('#dataset-version').value, description: $('#dataset-description').value, case_ids: ids }) });
    $('#dataset-message').textContent = '数据集版本已发布。';
    await loadEvaluationTasks();
  } catch (error) { $('#dataset-message').textContent = error.message; }
}

async function submitExperiment(event) {
  event.preventDefault();
  try {
    await api('/api/evaluation/experiments', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name: $('#experiment-name').value, dataset_id: $('#experiment-dataset').value, hypothesis: $('#experiment-hypothesis').value }) });
    $('#experiment-message').textContent = '实验已创建。';
    await loadEvaluationTasks();
  } catch (error) { $('#experiment-message').textContent = error.message; }
}

async function submitFeedback(event) {
  event.preventDefault();
  try {
    await api('/api/evaluation/feedback', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ task_id: $('#feedback-task').value, case_id: $('#feedback-case').value, category: $('#feedback-category').value, note: $('#feedback-note').value }) });
    $('#feedback-message').textContent = '已加入失败用例队列。';
    await loadEvaluationTasks();
  } catch (error) { $('#feedback-message').textContent = error.message; }
}

function renderEvaluationReport(task) {
  const report = task?.report;
  if (!report || !report.cases) {
    $('#eval-count').textContent = task?.status === 'running' ? '运行中' : '暂无结果';
    $('#eval-table').innerHTML = `<p class="muted">${task ? escapeHtml(EVAL_STATUS_LABELS[task.status] || task.status) + '，完成后在这里查看逐条结果。' : '选择一份评测任务后查看。'}</p>`;
    return;
  }
  $('#eval-count').textContent = report.available ? `${report.passed}/${report.total} 通过` : '模型未就绪';
  const rows = report.cases.map((item) => {
    const modelBadge = item.model_used ? '<span class="badge">模型完成</span>' : '<span class="badge fail">模型未完成</span>';
    return `<tr>
      <td>${item.setup_question ? `<div class="muted">前置：${escapeHtml(item.setup_question)}</div>` : ''}${escapeHtml(item.question)}</td>
      <td>${escapeHtml(EVAL_LABELS[item.intent] || item.intent)}</td>
      <td>${modelBadge}<div class="muted">${escapeHtml(evalModeLabel(item))}</div></td>
      <td><span class="badge ${item.hit ? '' : 'fail'}">${item.hit ? '通过' : '未通过'}</span></td>
      <td>${escapeHtml(item.attempts)} 次 / ${escapeHtml(item.elapsed_ms)} 毫秒</td>
      <td class="eval-result-cell">${item.error ? `<span class="eval-error">${escapeHtml(item.error)}</span>` : '<span class="eval-pass">模型结果符合验收值</span>'}</td>
    </tr>`;
  }).join('');
  $('#eval-table').innerHTML = `<table class="eval-table"><thead><tr><th>黄金问题</th><th>意图</th><th>SQL 来源</th><th>模型验收</th><th>执行 / 耗时</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function selectEvaluationTask(taskId, options = {}) {
  state.evalSelectedTask = taskId;
  const task = await api(`/api/evaluation/tasks/${encodeURIComponent(taskId)}`);
  renderEvaluationTaskSummary(task);
  renderEvaluationReport(task);
  if (!options.silent) renderEvaluationTaskList(state.evalTasksData || { tasks: [] });
  return task;
}

async function submitSource(event) {
  event.preventDefault();
  const message = $('#source-message');
  try {
    await api('/api/data-platform/sources', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ name: $('#source-name').value, kind: $('#source-kind').value, location: $('#source-location').value, sync_mode: $('#source-kind').value === 'csv' ? 'ingest' : 'snapshot', sensitivity: $('#source-sensitivity').value, schedule_minutes: Number($('#source-schedule').value || 0) }),
    });
    message.textContent = '数据源已注册，可点击“同步”建立首批快照。';
    $('#source-form').reset();
    await loadDataAssets();
  } catch (error) { message.textContent = error.message; }
}

async function submitQualityRule(event) {
  event.preventDefault();
  const message = $('#rule-message');
  try {
    await api('/api/data-platform/quality-rules', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ source_id: 'ds_commerce', name: $('#rule-name').value, rule_type: $('#rule-type').value, table_name: $('#rule-table').value, column_name: $('#rule-column').value, expression: $('#rule-expression').value }),
    });
    message.textContent = '质量规则已注册。';
    $('#quality-rule-form').reset();
    await loadDataAssets();
  } catch (error) { message.textContent = error.message; }
}

async function submitMetric(event) {
  event.preventDefault();
  try {
    await api('/api/data-platform/metrics', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ name: $('#metric-name').value, grain: $('#metric-grain').value, definition: $('#metric-definition').value, owner: $('#metric-owner').value, status: $('#metric-status').value }),
    });
    $('#metric-form').reset();
    toast('指标口径已保存');
    await loadDataAssets();
  } catch (error) { toast(error.message); }
}

async function loadEvaluationTasks(options = {}) {
  try {
    const [data, datasets, experiments, feedback] = await Promise.all([
      api('/api/evaluation/tasks'), api('/api/evaluation/datasets'),
      api('/api/evaluation/experiments'), api('/api/evaluation/feedback'),
    ]);
    state.datasets = datasets.datasets || [];
    state.experiments = experiments.experiments || [];
    state.feedback = feedback.feedback || [];
    renderEvaluationPlatform(data);
    state.evalTasksData = data;
    renderEvaluationTaskList(data);
    const active = data.active;
    const preferred = active?.id || state.evalSelectedTask || data.tasks?.[0]?.id;
    if (preferred) {
      await selectEvaluationTask(preferred, { silent: true });
    } else {
      renderEvaluationTaskSummary(null);
      renderEvaluationReport(null);
    }
    const button = $('#run-eval-btn');
    if (button) {
      button.disabled = Boolean(active);
      button.textContent = active ? '评测运行中…' : '提交评测任务';
    }
    if (active || state.evalPollTimer) {
      if (state.evalPollTimer) clearTimeout(state.evalPollTimer);
      if (active) state.evalPollTimer = setTimeout(() => loadEvaluationTasks({ polled: true }), 3000);
    }
    if (!options.polled && !data.tasks?.length) toast('暂无评测历史任务');
  } catch (error) {
    toast(error.message);
    $('#eval-table').innerHTML = `<p class="muted">评测任务读取失败：${escapeHtml(error.message)}</p>`;
  }
}

async function runEvaluation() {
  const button = $('#run-eval-btn');
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = '正在入队…';
  try {
    const task = await api('/api/evaluation/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workers: 3, dataset_id: $('#eval-dataset')?.value || 'dataset_v3_100' }),
    });
    state.evalSelectedTask = task.id;
    toast('评测任务已创建，后台运行中');
    await loadEvaluationTasks();
  } catch (error) {
    toast(error.message);
    button.disabled = false;
    button.textContent = '提交评测任务';
  }
}

function evaluationTab(name) {
  state.evaluationTab = name;
  $$('#eval-platform-tabs .platform-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.evalTab === name));
  $$('#view-evaluation .platform-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `eval-panel-${name}`));
}

function renderEvaluationPlatform(data) {
  const datasets = state.datasets || [];
  const experiments = state.experiments || [];
  const feedback = state.feedback || [];
  $('#dataset-count').textContent = `${datasets.length} 个版本`;
  $('#dataset-table').innerHTML = datasets.length ? `<table class="eval-table"><thead><tr><th>数据集</th><th>版本</th><th>用例数</th><th>状态</th></tr></thead><tbody>${datasets.map((item) => `<tr><td>${escapeHtml(item.name)}<div class="muted">${escapeHtml(item.description || '')}</div></td><td><code>${escapeHtml(item.version)}</code></td><td>${item.case_count}</td><td>${statusBadge(item.status === 'published' ? 'ready' : item.status === 'holdout' ? 'holdout' : 'cancelled')}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">暂无数据集版本。</p>';
  $('#experiment-dataset').innerHTML = datasets.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.version)}</option>`).join('');
  $('#experiment-count').textContent = `${experiments.length} 个实验`;
  $('#experiment-list').innerHTML = experiments.length ? experiments.map((exp) => `<div class="experiment-card"><strong>${escapeHtml(exp.name)}</strong><p>${escapeHtml(exp.hypothesis || '暂无实验假设')}</p><small>${escapeHtml(exp.dataset_id)} · ${escapeHtml(formatEvaluatedAt(exp.created_at))}</small><div class="experiment-runs">${(exp.tasks || []).map((task) => `<span>${escapeHtml(task.model_name || '待运行')} · ${task.hit_rate}%</span>`).join('') || '<span>尚未关联评测任务</span>'}</div></div>`).join('') : '<p class="muted">暂无实验记录。</p>';
  $('#feedback-count').textContent = `${feedback.length} 条`;
  $('#feedback-table').innerHTML = feedback.length ? `<table class="eval-table"><thead><tr><th>用例</th><th>任务</th><th>分类</th><th>状态</th></tr></thead><tbody>${feedback.map((item) => `<tr><td>${escapeHtml(item.case_id)}<div class="muted">${escapeHtml(item.note || '')}</div></td><td><code>${escapeHtml(item.task_id)}</code></td><td>${escapeHtml(FEEDBACK_CATEGORY_LABELS[item.category] || item.category)}</td><td>${statusBadge(item.status === 'open' ? 'queued' : 'ready')}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">暂无失败用例。</p>';
  const activeTask = data.active;
  const selected = state.evalSelectedTask;
  const runSelect = $('#eval-dataset');
  if (runSelect) {
    const current = runSelect.value;
    runSelect.innerHTML = datasets.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.version)}（${item.case_count} 条）</option>`).join('');
    if (current) runSelect.value = current;
  }
}

async function compareEvaluationTasks() {
  const baseline = $('#eval-baseline')?.value;
  const candidate = $('#eval-candidate')?.value;
  if (!baseline || !candidate) return toast('至少需要两份已完成评测任务');
  if (baseline === candidate) return toast('请选择两份不同任务');
  try {
    const data = await api(`/api/evaluation/compare?baseline=${encodeURIComponent(baseline)}&candidate=${encodeURIComponent(candidate)}`);
    const results = [];
    if (data.fixed?.length) results.push(`<div class="eval-diff-group fixed"><strong>新增修复 ${data.fixed.length}</strong>${data.fixed.map((item) => `<span>${escapeHtml(item.question)}</span>`).join('')}</div>`);
    if (data.regressed?.length) results.push(`<div class="eval-diff-group regressed"><strong>新增回归 ${data.regressed.length}</strong>${data.regressed.map((item) => `<span>${escapeHtml(item.question)}</span>`).join('')}</div>`);
    if (!results.length) results.push('<p class="muted">两份任务的逐用例通过状态没有变化。</p>');
    $('#eval-compare-result').innerHTML = `<div class="eval-compare-metrics"><span>通过数 <strong>${data.delta.passed >= 0 ? '+' : ''}${data.delta.passed}</strong></span><span>通过率 <strong>${data.delta.hit_rate >= 0 ? '+' : ''}${data.delta.hit_rate}%</strong></span><span>执行率 <strong>${data.delta.execution_rate >= 0 ? '+' : ''}${data.delta.execution_rate}%</strong></span><span>平均耗时 <strong>${data.delta.avg_elapsed_ms >= 0 ? '+' : ''}${data.delta.avg_elapsed_ms} 毫秒</strong></span></div>${results.join('')}`;
  } catch (error) {
    toast(error.message);
  }
}

const MODEL_BASE_URLS = {
  openai: 'https://api.openai.com/v1',
  deepseek: 'https://api.deepseek.com/v1',
  qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  siliconflow: 'https://api.siliconflow.cn/v1',
  moonshot: 'https://api.moonshot.cn/v1',
};

function renderModelHeading(model, active) {
  const pill = $('#model-pill');
  if (!pill) return;
  const label = active && model?.model
    ? `当前模型 · ${model.model}`
    : '规则回退 · 未启用模型';
  pill.innerHTML = `<span class="status-dot"></span>${escapeHtml(label)}`;
  pill.classList.toggle('offline', !active);
}

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
      ['状态', active ? '模型生成已启用' : '模型未启用'],
      ['模型', model.model || '未选择'],
      ['服务商', { custom: '自定义兼容接口', openai: 'OpenAI', deepseek: 'DeepSeek', qwen: '通义千问', siliconflow: 'SiliconFlow', moonshot: 'Moonshot', disabled: '未配置' }[model.provider] || model.provider || '未选择'],
      ['接口地址', model.base_url || '未配置'],
      ['访问密钥', model.api_key_configured ? '已保存（服务端）' : '未配置'],
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
    model: $('#model-name').value.trim(),
    api_key: $('#model-api-key').value.trim() || null,
  };
}

function setModelMessage(message, kind = '') {
  const el = $('#model-message');
  if (!el) return;
  el.textContent = message;
  el.className = `model-message ${kind}`;
}

function updateModelKeyState(configured) {
  const input = $('#model-api-key');
  const note = $('#model-key-note');
  if (input) {
    input.placeholder = configured
      ? '已保存，不回显；留空保持现有密钥'
      : 'sk-...';
  }
  if (note) {
    note.textContent = configured
      ? '访问密钥已保存在服务端本地配置文件，浏览器不会回显；留空可继续使用。'
      : '访问密钥仅保存在服务端，不在浏览器回显；保存后无需重复填写。';
  }
}

function applyModelForm(data) {
  const provider = $('#model-provider');
  const baseUrl = $('#model-base-url');
  const modelName = $('#model-name');
  const enabled = $('#model-enabled');
  if (provider) provider.value = data.provider || 'custom';
  if (baseUrl) baseUrl.value = data.base_url || 'https://api.openai.com/v1';
  if (modelName) modelName.value = data.model || '';
  if (enabled) enabled.checked = !!data.enabled;
  updateModelKeyState(!!data.api_key_configured);
  renderModelRuntime(data, data.active);
}

async function loadModelConfig() {
  try {
    const data = await api('/api/model/config');
    state.model = data;
    applyModelForm(data);
    renderModelHeading(data, data.active);
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
    const options = $('#model-options');
    options.innerHTML = '';
    [...new Set(data.models)].forEach((name) => {
      const option = document.createElement('option');
      option.value = name;
      options.appendChild(option);
    });
    if (!$('#model-name').value.trim() && data.models.length === 1) {
      $('#model-name').value = data.models[0];
    }
    setModelMessage(
      `读取到 ${data.models.length} 个模型，点击模型输入框可从列表选择。`,
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
    state.model = data;
    applyModelForm(data);
    $('#model-api-key').value = '';
    setModelMessage(
      data.active
        ? '配置已保存，后续查询将使用该模型。'
        : '配置已保存，当前仍使用规则生成。',
      'ok',
    );
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
  $('#history-btn')?.addEventListener('click', () => toggleWorkspacePanel('session'));
  $('#context-btn')?.addEventListener('click', () => toggleWorkspacePanel('context'));
  $('#session-drawer-close')?.addEventListener('click', closeWorkspacePanels);
  $('#context-drawer-close')?.addEventListener('click', closeWorkspacePanels);
  $('#workspace-backdrop')?.addEventListener('click', closeWorkspacePanels);
  $('#latest-reply-btn')?.addEventListener('click', () => {
    state.threadPinned = true;
    scrollThreadToLatest({ force: true });
  });
  $('#conversation-thread')?.addEventListener('scroll', () => {
    const thread = $('#conversation-thread');
    if (!thread) return;
    const atEnd = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 48;
    if (atEnd || !state.sending) state.threadPinned = atEnd;
    updateLatestReplyButton();
  });
  for (const eventName of ['wheel', 'touchstart', 'pointerdown', 'keydown']) {
    $('#conversation-thread')?.addEventListener(eventName, () => {
      state.threadPinned = false;
      updateLatestReplyButton();
    }, { passive: true });
  }
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeWorkspacePanels();
  });
  $('#overview-refresh-btn')?.addEventListener('click', () => {
    loadOverview();
    loadHealth();
  });
  $('#query-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (state.sending) {
      if (event.submitter === $('#submit-btn')) cancelActiveQuery();
      return;
    }
    submitQuery($('#question').value);
  });
  $('#question').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      $('#query-form').requestSubmit();
    }
  });
  $('#refresh-btn').addEventListener('click', () => {
    const active = $('.view.active')?.id?.replace('view-', '') || 'workspace';
    if (active === 'overview') loadOverview();
    if (active === 'workspace') loadSessions();
    if (active === 'models') loadModelConfig();
    if (active === 'data-assets') loadDataAssets();
    if (active === 'evaluation') loadEvaluationTasks();
    loadHealth();
    toast('已刷新当前页面');
  });
  $('#assets-refresh-btn')?.addEventListener('click', loadDataAssets);
  $('#scheduler-run-btn')?.addEventListener('click', runScheduler);
  $('#quality-run-btn')?.addEventListener('click', runPlatformQuality);
  $$('#data-platform-tabs .platform-tab').forEach((tab) => tab.addEventListener('click', () => platformTab(tab.dataset.platformTab)));
  $$('#eval-platform-tabs .platform-tab').forEach((tab) => tab.addEventListener('click', () => evaluationTab(tab.dataset.evalTab)));
  $('#source-form')?.addEventListener('submit', submitSource);
  $('#quality-rule-form')?.addEventListener('submit', submitQualityRule);
  $('#metric-form')?.addEventListener('submit', submitMetric);
  $('#dataset-form')?.addEventListener('submit', submitDataset);
  $('#experiment-form')?.addEventListener('submit', submitExperiment);
  $('#feedback-form')?.addEventListener('submit', submitFeedback);
  $('#run-eval-btn').addEventListener('click', runEvaluation);
  $('#compare-eval-btn')?.addEventListener('click', compareEvaluationTasks);
  $('#eval-baseline')?.addEventListener('change', () => { state.evalBaseline = $('#eval-baseline').value; });
  $('#eval-candidate')?.addEventListener('change', () => { state.evalCandidate = $('#eval-candidate').value; });
  $('#model-provider')?.addEventListener('change', () => {
    const baseUrl = MODEL_BASE_URLS[$('#model-provider').value];
    if (baseUrl) $('#model-base-url').value = baseUrl;
  });
  $('#model-fetch-btn')?.addEventListener('click', fetchModels);
  $('#model-test-btn')?.addEventListener('click', testModel);
  $('#model-form')?.addEventListener('submit', saveModel);
}

document.addEventListener('DOMContentLoaded', init);
