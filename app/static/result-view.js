import { state } from "./state.js";
import { $, fmtMoney, fmtNum, escapeHtml } from "./utils.js";
import { sqlSourceLabel, ATTEMPT_STATUS_LABELS } from "./labels.js";

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
    <div class="result-answer${result.outcome && result.outcome !== 'answered' ? ' result-guard' : ''}"><strong>${result.outcome === 'clarification' ? '需要补充信息' : result.outcome === 'unsupported' ? '暂不能回答' : result.outcome === 'failed' ? '查询未完成' : '分析结论'}</strong> <span class="narrative-tag">${result.outcome === 'failed' ? '执行失败' : result.outcome && result.outcome !== 'answered' ? '范围检查' : result.narrative?.mode === 'model' ? `模型解读 · ${escapeHtml(result.narrative.model || '已完成')}` : '确定性摘要'}</span><div class="narrative-text">${escapeHtml(result.answer).replace(/\n/g, '<br>')}</div></div>
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

function renderGuardResult(result, options = {}) {
  const isClarification = result.outcome === 'clarification';
  const isFailure = result.outcome === 'failed';
  const title = isClarification ? '需要补充信息' : isFailure ? '查询未完成' : '暂不能回答';
  const kind = isClarification ? 'clarification' : isFailure ? 'failed' : 'unsupported';
  const body = `
    <div class="result-answer result-guard ${kind}">
      <strong>${title}</strong>
      <div class="narrative-text">${escapeHtml(result.answer).replace(/\n/g, '<br>')}</div>
    </div>
    <div class="result-meta">
      <span class="small-pill">${isClarification ? '范围检查 · 需要补充条件' : isFailure ? '执行失败 · 未产出业务结论' : '范围检查 · 只读请求'}</span>
      <span class="small-pill">${escapeHtml(result.elapsed_ms)} 毫秒</span>
    </div>`;
  const thread = $('#conversation-thread');
  if (!thread) return;
  if (thread.querySelector('.conversation-empty')) thread.innerHTML = '';
  const liveCard =
    options.card ||
    (!options.append && state.activeQueryCard && state.activeQueryCard.isConnected
      ? state.activeQueryCard
      : null);
  const markup = `
      <div class="turn-question"><span class="turn-label">你</span><div>${escapeHtml(result.question)}</div></div>
      <div class="turn-meta"><span>未执行 SQL</span><span>${escapeHtml(result.elapsed_ms)} 毫秒</span></div>
      <div class="turn-answer"><div class="turn-label assistant-label">助手</div><div class="assistant-content">${body}</div></div>`;
  if (liveCard) {
    liveCard.classList.remove('is-streaming');
    liveCard.removeAttribute('data-live-turn');
    liveCard.innerHTML = markup;
    state.activeQueryCard = null;
  } else {
    thread.insertAdjacentHTML('beforeend', `<article class="turn-card">${markup}</article>`);
  }
  state.lastResult = result;
  renderEvidence(result);
}

function renderResult(result, options = {}) {
  state.lastResult = result;
  if (result.outcome && result.outcome !== 'answered') {
    renderGuardResult(result, options);
    return;
  }
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

export {
  attemptLine,
  renderResultBody,
  renderPendingTurn,
  updateStreamStage,
  renderGuardResult,
  renderResult,
  setTurnNarrative,
  setNarrativeTag,
  renderEvidence,
  followNarrative,
  renderQueryError,
  scrollThreadToLatest,
  updateLatestReplyButton,
};


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
  tag.textContent = narrative?.mode === 'guard'
    ? '范围检查'
    : model
      ? `模型解读 · ${narrative.model || '流式'}`
      : '确定性摘要';
  tag.classList.toggle('model', model);
}

function renderEvidence(result) {
  const target = $('#evidence-content');
  if (!target || !result) return;
  const plan = result.plan || {};
  const retrieval = result.retrieval || [];
  const queryRows = retrieval
    .map((item) => `<div class="evidence-row"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.table)}</span><span class="small-pill">${Number(item.score || 0).toFixed(3)}</span></div>`)
    .join('');
  const attempts = (result.attempts || [])
    .map((attempt) => attemptLine(attempt, true))
    .join('');
  target.innerHTML = `
    <div class="evidence-grid">
      <section class="panel evidence-panel">
        <div class="panel-head"><div><h2>Schema 召回</h2><p class="muted">${retrieval.length} 个候选表</p></div><span class="badge">Top-4</span></div>
        <div class="evidence-list">${queryRows || '<p class="muted">没有检索证据。</p>'}</div>
      </section>
      <section class="panel evidence-panel">
        <div class="panel-head"><div><h2>查询计划</h2><p class="muted">语义层校验后的结构化计划</p></div><span class="badge">${escapeHtml(plan.context?.planner_mode || 'rule')}</span></div>
        <div class="context-grid">
          <div><span>意图</span><strong>${escapeHtml(plan.intent || '—')}</strong></div>
          <div><span>指标</span><strong>${escapeHtml((plan.metrics || []).join(' / ') || '—')}</strong></div>
          <div><span>维度</span><strong>${escapeHtml((plan.dimensions || []).join(' / ') || '标量')}</strong></div>
          <div><span>表</span><strong>${escapeHtml((plan.tables || []).join(' / ') || '—')}</strong></div>
        </div>
      </section>
    </div>
    <section class="panel evidence-panel">
      <div class="panel-head"><div><h2>执行证据</h2><p class="muted">SQL 来源：${escapeHtml(sqlSourceLabel(result.sql_mode || result.mode))}</p></div><span class="small-pill">${escapeHtml(result.outcome)}${' · '}${escapeHtml(result.elapsed_ms)}${' 毫秒'}</span></div>
      <details class="result-evidence" open><summary>只读 SQL</summary><pre class="sql-block">${escapeHtml(result.sql || '未执行 SQL')}</pre></details>
      <div class="trace-list">${attempts || '<p class="muted">没有执行尝试。</p>'}</div>
    </section>`;
}

function followNarrative(card) {
  if (!card || !state.threadPinned) return;
  scrollThreadToLatest({ force: true });
}

function renderQueryError(card, message) {
  if (!card) return;
  card.classList.remove('is-streaming');
  card.removeAttribute('data-live-turn');
  card.innerHTML = `
    <div class="turn-question"><span class="turn-label">你</span><div>${escapeHtml(card.dataset.question || '')}</div></div>
    <div class="turn-answer"><div class="turn-label assistant-label">助手</div><div class="assistant-content result-answer result-guard failed"><strong>查询未完成</strong><div class="narrative-text">${escapeHtml(message)}</div></div></div>`;
}

function scrollThreadToLatest({ force = false } = {}) {
  const thread = $('#conversation-thread');
  if (!thread || (!force && !state.threadPinned)) return;
  thread.scrollTop = thread.scrollHeight;
  state.threadPinned = true;
  updateLatestReplyButton();
}

function updateLatestReplyButton() {
  const thread = $('#conversation-thread');
  const button = $('#latest-reply-btn');
  if (!thread || !button) return;
  const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 48;
  button.classList.toggle('visible', !atBottom);
}

