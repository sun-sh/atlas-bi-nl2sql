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
  threadPinned: true,
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
      <span class="small-pill">${{ llm: '模型生成', 'llm-repair': '模型修复', rule: '规则回退', 'rule-repair': '规则修复', 'model-failed': '模型未产出', 'model-repair-failed': '模型修复失败' }[result.mode] || '来源未知'}</span>
      <span class="small-pill">${attempts.length} 次执行 · ${escapeHtml(result.elapsed_ms)} ms</span>
      ${(result.plan?.inherited || []).map((item) => `<span class="small-pill inherited-pill">继承 · ${escapeHtml(item)}</span>`).join('')}
    </div>
    ${numericCol && firstDimension && rows.length > 1 ? `<div class="chart-box"><div class="chart-title">${escapeHtml(numericCol)} · 按 ${escapeHtml(firstDimension)} 展示${rows.length > 8 ? ' · 前 8 项，完整结果见数据依据' : ''}</div><div class="chart-bars">${bars}</div></div>` : ''}
    <details class="accordion result-evidence"><summary><span>数据依据与 SQL</span><span>⌄</span></summary>
      <div class="evidence-detail">
        <div class="evidence-detail-head"><span>结果明细</span><span>${rows.length} 行${result.truncated ? ' · 已达到查询行数上限' : ''}</span></div>
        ${table}
        <div class="evidence-detail-head"><span>生成的只读 SQL</span><span>${escapeHtml(result.sql_mode || result.mode || '')}</span></div>
        <div class="sql-block">${escapeHtml(result.sql)}</div>
        <div class="evidence-detail-head"><span>执行轨迹与 Schema 召回</span><span>${attempts.length} 次执行</span></div>
        <div class="trace">${attempts.map((a) => `<div class="trace-item"><span class="trace-mark ${a.status === 'failed' ? 'bad' : ''}">${a.status === 'failed' ? '!' : '✓'}</span><span>第 ${escapeHtml(a.attempt)} 次 · ${escapeHtml(a.status)} · ${escapeHtml(a.elapsed_ms)} ms${a.error ? ' · ' + escapeHtml(a.error) : ''}</span></div>`).join('')}${(result.retrieval || []).map((hit) => `<div class="trace-item"><span class="trace-mark">◎</span><span>召回 ${escapeHtml(hit.label)}（${escapeHtml(hit.table)}）· 融合 ${escapeHtml(hit.score)} · 语义 ${escapeHtml(hit.vector_score)} · 关键词 ${escapeHtml(hit.lexical_score)}</span></div>`).join('')}</div>
      </div>
    </details>`;
}

function renderPendingTurn(question) {
  const thread = $('#conversation-thread');
  if (!thread) return null;
  thread.querySelector('.conversation-empty')?.remove();
  const stages = [
    ['retrieve', '召回 Schema'],
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
        <div class="turn-label assistant-label">AI</div>
        <div class="assistant-content">
          <div class="stream-head">
            <span class="stream-pulse"></span>
            <strong class="stream-title">正在分析</strong>
            <span class="stream-detail">准备检索 Schema</span>
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
      <div class="turn-meta"><span>${escapeHtml(result.plan?.time_range || '')}</span><span>${escapeHtml(result.elapsed_ms)} ms</span></div>
      <div class="turn-answer"><div class="turn-label assistant-label">AI</div><div class="assistant-content">${body}</div></div>`;
    state.activeQueryCard = null;
  } else {
    thread.insertAdjacentHTML(
      'beforeend',
      `
      <article class="turn-card">
        <div class="turn-question"><span class="turn-label">你</span><div>${escapeHtml(result.question)}</div></div>
        <div class="turn-meta"><span>${escapeHtml(result.plan?.time_range || '')}</span><span>${escapeHtml(result.elapsed_ms)} ms</span></div>
        <div class="turn-answer"><div class="turn-label assistant-label">AI</div><div class="assistant-content">${body}</div></div>
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

function setPageHeading(view) {
  const headings = {
    workspace: ['ATLAS / ANALYSIS', '今天想分析什么？', '直接问经营问题，回答会留在当前会话里；需要时再查看筛选条件和数据依据。'],
    overview: ['ATLAS / OVERVIEW', '经营总览', '演示数据覆盖 2025 年 10 月至 2026 年 2 月。需要了解指标变化，可在分析会话中继续提问。'],
    evidence: ['ATLAS / EVIDENCE', '每条结论，都能回到数据依据', '查看最近一次查询召回的业务表、融合分数、只读 SQL 与执行轨迹。'],
    evaluation: ['ATLAS / EVALUATION', '用黄金问题验收模型链路', '每条用例都由真实模型生成 SQL、执行查询并解读结果；没有完成就如实判失败。'],
    models: ['ATLAS / MODELS', '选择本次分析使用的模型', '连接 OpenAI 兼容接口；保存后，后续查询会自动使用当前生效模型。'],
  };
  const [eyebrow, title, subtitle] = headings[view] || headings.workspace;
  $('#page-eyebrow').textContent = eyebrow;
  $('#page-title').textContent = title;
  $('#page-subtitle').textContent = subtitle;
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
      <div class="schema-head"><div><div class="panel-kicker">RECALL #${index + 1}</div><div class="schema-name">${hit.label} · ${hit.table}</div></div><span class="badge">融合 ${hit.score}</span></div>
      <p class="schema-desc">${hit.description}</p>
      <div class="score-grid"><div class="score-box"><div class="score-label">语义分数</div><div class="score-value">${hit.vector_score}</div></div><div class="score-box"><div class="score-label">关键词分数</div><div class="score-value">${hit.lexical_score}</div></div><div class="score-box"><div class="score-label">命中词</div><div class="score-value" style="font-size:12px">${hit.matched_terms.join(' / ') || '—'}</div></div></div>
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
          <div class="conversation-empty-mark">AI</div>
              <h3>直接问一个经营问题</h3>
                  <p>例如“上个月各渠道 ROI”，再追问“那华东呢”。时间、指标和筛选条件会按会话继承。</p>
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
      <div class="turn-label assistant-label">AI</div>
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
            turnMeta.innerHTML = `<span>${escapeHtml(result.plan?.time_range || '')}</span><span>${escapeHtml(result.elapsed_ms)} ms</span>`;
          }
          card?.classList.remove('is-streaming');
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
  button.disabled = true;
  button.querySelector('span').textContent = '分析中…';
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
    toast(`分析完成 · ${result.elapsed_ms} ms`);
  } catch (error) {
    state.activeQueryCard = null;
    toast(error.message);
    renderQueryError(liveCard, error.message);
    state.threadPinned = true;
    scrollThreadToLatest({ force: true });
  } finally {
    state.sending = false;
    $$('#new-session-btn, #reset-context-btn, .session-item, #refresh-btn').forEach(el => { el.disabled = false; });
    button.disabled = false;
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
  setPageHeading(view);
  $$('.mobile-nav-item').forEach((x) =>
    x.classList.toggle('active', x.dataset.view === view),
  );
  closeWorkspacePanels();
  if (view === 'models') loadModelConfig();
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
  return (
    {
      llm: '模型生成',
      'llm-repair': '模型修复',
      'model-failed': '模型未产出',
      'model-repair-failed': '模型修复失败',
      unavailable: '未运行',
      error: '异常',
    }[item.sql_mode] || item.sql_mode || '未知'
  );
}

function renderEvaluation(data) {
  const model = data.model || {};
  const modelLabel = model.model
    ? `${model.model}${model.active ? '' : ' · 未启用'}`
    : '未配置模型';
  $('#eval-meta').innerHTML = `
    <span>评测模式 <strong>严格模型链路</strong></span>
    <span>模型 <strong>${escapeHtml(modelLabel)}</strong></span>
    <span>数据集 <strong>${escapeHtml(data.dataset_range || '固定数据集')}</strong></span>
    <span>结果时间 <strong>${escapeHtml(formatEvaluatedAt(data.evaluated_at))}</strong></span>`;
  $('#eval-summary').innerHTML = [
    metricCard(
      '模型通过率',
      `${data.hit_rate ?? 0}%`,
      `${data.passed}/${data.total} 条模型结果通过`,
      true,
    ),
    metricCard(
      'SQL 执行率',
      `${data.execution_rate ?? 0}%`,
      `${(data.cases || []).filter((item) => item.executed).length}/${data.total} 条模型 SQL 成功执行`,
    ),
    metricCard(
      '平均响应时间',
      `${data.avg_elapsed_ms ?? 0} ms`,
      '真实模型 · 含检索、SQL 与修复',
    ),
    metricCard(
      '覆盖意图',
      `${data.intents ?? 0} 类`,
      '销售 / 品类 / 退款 / ROI / 趋势',
    ),
  ].join('');
  $('#eval-count').textContent = data.available
    ? `${data.passed}/${data.total} 通过`
    : '尚未运行';
  const rows = (data.cases || [])
    .map((item) => {
      const modelBadge = item.model_used
        ? '<span class="badge">模型完成</span>'
        : '<span class="badge fail">模型未完成</span>';
      return `
    <tr>
      <td>${escapeHtml(item.question)}</td>
      <td>${escapeHtml(EVAL_LABELS[item.intent] || item.intent)}</td>
      <td>${modelBadge}<div class="muted">${escapeHtml(evalModeLabel(item))}</div></td>
      <td><span class="badge ${item.hit ? '' : 'fail'}">${item.hit ? '通过' : '未通过'}</span></td>
      <td>${escapeHtml(item.attempts)} 次 / ${escapeHtml(item.elapsed_ms)} ms</td>
      <td class="eval-result-cell">${
        item.error
          ? `<span class="eval-error">${escapeHtml(item.error)}</span>`
          : '<span class="eval-pass">模型结果符合验收值</span>'
      }</td>
    </tr>`;
    })
    .join('');
  $('#eval-table').innerHTML = rows
    ? `<table class="eval-table"><thead><tr><th>黄金问题</th><th>意图</th><th>SQL 来源</th><th>模型验收</th><th>执行 / 耗时</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<p class="muted">当前评测集没有返回用例。</p>';
}

async function loadEvaluation() {
  const button = $('#run-eval-btn');
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = '运行中…';
  try {
    const data = await api('/api/evaluation');
    renderEvaluation(data);
    toast(data.available ? `模型评测完成 · ${data.passed}/${data.total} 通过` : "模型未启用，无法运行模型评测");
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
      ['服务商', model.provider || '未选择'],
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
      ? '已保存，不回显；留空保持现有 Key'
      : 'sk-...';
  }
  if (note) {
    note.textContent = configured
      ? 'API Key 已保存在服务端本地配置文件，浏览器不会回显；留空可继续使用。'
      : 'API Key 仅保存在服务端，不在浏览器回显；保存后无需重复填写。';
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
    loadHealth();
    toast('已刷新当前页面');
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
