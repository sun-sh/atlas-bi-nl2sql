const state = { overview: null, lastResult: null, examples: [] };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const fmtMoney = (v) => '¥' + Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
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
      metricCard('累计实付销售额', fmtMoney(data.revenue), '2025.10 – 2026.02 · 已支付订单', true),
      metricCard('有效订单数', fmtNum(data.orders), `覆盖 ${fmtNum(data.customers)} 位客户`),
      metricCard('平均客单价', fmtMoney(data.aov), '实付金额 / 有效订单'),
      metricCard('退款率', data.refund_rate + '%', `${fmtNum(data.refund_orders)} 笔已通过退款`, true),
    ].join('');
    renderTrend(data.trend);
    renderChannels(data.channel);
  } catch (error) {
    toast(error.message);
  }
}

function renderTrend(rows) {
  const max = Math.max(...(rows || []).map((r) => Number(r.revenue)), 1);
  $('#trend-chart').innerHTML = (rows || []).map((row) => {
    const h = Math.max(3, Number(row.revenue) / max * 135);
    return `<div class="bar-wrap" data-label="${row.date} · ${fmtMoney(row.revenue)}"><div class="bar" style="height:${h}px"></div></div>`;
  }).join('');
}

function renderChannels(rows) {
  const max = Math.max(...(rows || []).map((r) => Number(r.revenue)), 1);
  $('#channel-list').innerHTML = (rows || []).map((row) => `
    <div class="channel-row">
      <span>${row.channel}</span>
      <span class="channel-track"><span class="channel-fill" style="width:${Number(row.revenue) / max * 100}%"></span></span>
      <span class="channel-value">${fmtMoney(row.revenue)}</span>
    </div>`).join('');
}

async function loadHealth() {
  try {
    const data = await api('/api/health');
    $('#health-text').textContent = data.database ? '数据服务正常' : '数据库缺失';
    const r = data.retrieval;
    const semanticLabel = r.qdrant_enabled || r.semantic_backend !== 'local fallback'
      ? 'BGE 语义召回'
      : '语义降级';
    const lexicalLabel = r.elasticsearch_enabled ? 'ES BM25' : '本地 IDF';
    $('#retrieval-meta').textContent = `${semanticLabel} + ${lexicalLabel} · 只读 SQL`;
  } catch {
    $('#health-text').textContent = '连接失败';
  }
}

async function loadExamples() {
  const data = await api('/api/examples');
  state.examples = data.examples;
  $('#example-list').innerHTML = data.examples.map((q) => `<button class="example-chip" data-question="${q}">${q}</button>`).join('');
  $$('.example-chip').forEach((btn) => btn.addEventListener('click', () => {
    $('#question').value = btn.dataset.question;
    $('#question').focus();
  }));
}

function renderResult(result) {
  state.lastResult = result;
  const cols = result.columns || [];
  const rows = result.rows || [];
  const firstDimension = cols[0];
  const preferred = ['销售额', '客单价', 'ROI', '退款率', '销量', '退款金额', '投放花费'];
  const numericCol = preferred.find((c) => cols.includes(c)) || cols.find((c) => rows.some((r) => typeof r[c] === 'number'));
  const max = numericCol ? Math.max(...rows.map((r) => Math.abs(Number(r[numericCol]) || 0)), 1) : 1;
  const bars = rows.slice(0, 8).map((row) => `
    <div class="chart-row">
      <span>${row[firstDimension]}</span>
      <span class="chart-track"><span class="chart-fill" style="width:${(Math.abs(Number(row[numericCol]) || 0) / max * 100).toFixed(1)}%"></span></span>
      <span class="chart-num">${numericCol && typeof row[numericCol] === 'number' ? row[numericCol].toLocaleString('zh-CN') : ''}</span>
    </div>`).join('');
  const table = rows.length ? `
    <div class="table-wrap"><table class="result-table">
      <thead><tr>${cols.map((c) => `<th>${c}</th>`).join('')}</tr></thead>
      <tbody>${rows.slice(0, 30).map((row) => `<tr>${cols.map((c) => `<td>${row[c] ?? ''}</td>`).join('')}</tr>`).join('')}</tbody>
    </table></div>` : '<p class="muted">没有匹配数据。</p>';
  const attempts = result.attempts || [];
  $('#result-panel').innerHTML = `
    <div class="result-answer"><strong>分析结论</strong><br>${escapeHtml(result.answer)}</div>
    ${(result.warnings || []).length ? `<div class="result-meta">${result.warnings.map((w) => `<span class="small-pill">${escapeHtml(w)}</span>`).join('')}</div>` : ''}
    <div class="result-meta">
      <span class="small-pill">${{ llm: 'LLM 生成', 'llm-repair': 'LLM 修复', 'rule-repair': '规则回退修复' }[result.mode] || '规则生成'}</span>
      <span class="small-pill">${result.plan.intent}</span>
      <span class="small-pill">${result.plan.time_range}</span>
      <span class="small-pill">${result.elapsed_ms} ms</span>
      <span class="small-pill">${attempts.length} 次执行</span>
    </div>
    ${numericCol && rows.length ? `<div class="chart-box"><div class="chart-title">${numericCol} · 按 ${firstDimension} 展示</div><div class="chart-bars">${bars}</div></div>` : ''}
    ${table}
    <details class="accordion"><summary><span>查看生成的 SQL</span><span>⌄</span></summary><div class="sql-block">${escapeHtml(result.sql)}</div></details>
    <details class="accordion"><summary><span>执行与自愈轨迹</span><span>⌄</span></summary><div class="trace">${attempts.map((a) => `<div class="trace-item"><span class="trace-mark ${a.status === 'failed' ? 'bad' : ''}">${a.status === 'failed' ? '!' : '✓'}</span><span>第 ${a.attempt} 次 · ${a.status} · ${a.elapsed_ms} ms${a.error ? ' · ' + a.error : ''}</span></div>`).join('')}</div></details>
    <details class="accordion"><summary><span>Schema 召回依据</span><span>⌄</span></summary><div class="trace">${(result.retrieval || []).map((hit) => `<div class="trace-item"><span class="trace-mark">◎</span><span>${hit.label}（${hit.table}）· 融合分 ${hit.score} · 语义 ${hit.vector_score} · 关键词 ${hit.lexical_score}</span></div>`).join('')}</div></details>`;
  renderEvidence(result);
}

function escapeHtml(text) {
  return String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderEvidence(result) {
  const hits = result.retrieval || [];
  $('#evidence-content').innerHTML = hits.map((hit, index) => `
    <article class="panel schema-card">
      <div class="schema-head"><div><div class="panel-kicker">RECALL #${index + 1}</div><div class="schema-name">${hit.label} · ${hit.table}</div></div><span class="badge">融合 ${hit.score}</span></div>
      <p class="schema-desc">${hit.description}</p>
      <div class="score-grid"><div class="score-box"><div class="score-label">语义分数</div><div class="score-value">${hit.vector_score}</div></div><div class="score-box"><div class="score-label">关键词分数</div><div class="score-value">${hit.lexical_score}</div></div><div class="score-box"><div class="score-label">命中词</div><div class="score-value" style="font-size:10px">${hit.matched_terms.join(' / ') || '—'}</div></div></div>
    </article>`).join('') + `
    <article class="panel schema-card"><div class="panel-kicker">QUERY TRACE</div><div class="schema-name" style="margin-top:7px">执行轨迹与安全门禁</div>
    <div class="trace" style="margin-top:14px">${(result.attempts || []).map((a) => `<div class="trace-item"><span class="trace-mark ${a.status === 'failed' ? 'bad' : ''}">${a.status === 'failed' ? '!' : '✓'}</span><span>attempt ${a.attempt} · ${a.status} · ${a.elapsed_ms} ms ${a.error ? '· ' + a.error : ''}</span></div>`).join('')}</div>
    <div class="sql-block" style="margin-top:12px">${escapeHtml(result.sql)}</div></article>`;
}

async function submitQuery(question) {
  if (!question || question.trim().length < 2) {
    toast('请输入一个完整问题');
    return;
  }
  const button = $('#submit-btn');
  button.disabled = true;
  button.querySelector('span').textContent = '分析中…';
  $('#result-panel').innerHTML = '<div class="empty-state"><div class="empty-orbit"><div class="orbit-core">···</div></div><h3>正在检索与执行</h3><p>双路召回 Schema，生成 SQL，并进行安全校验。</p></div>';
  try {
    const result = await api('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    renderResult(result);
    toast(`分析完成 · ${result.elapsed_ms} ms`);
  } catch (error) {
    $('#result-panel').innerHTML = `<div class="empty-state"><div class="orbit-core" style="background:#d87436;color:#fff">!</div><h3>分析失败</h3><p>${escapeHtml(error.message)}</p></div>`;
    toast(error.message);
  } finally {
    button.disabled = false;
    button.querySelector('span').textContent = '开始分析';
  }
}

function bindNavigation() {
  $$('.nav-item').forEach((item) => item.addEventListener('click', () => {
    $$('.nav-item').forEach((x) => x.classList.remove('active'));
    $$('.view').forEach((x) => x.classList.remove('active'));
    item.classList.add('active');
    $(`#view-${item.dataset.view}`).classList.add('active');
  }));
}

async function loadEvaluation() {
  const button = $('#run-eval-btn');
  button.disabled = true;
  button.textContent = '运行中…';
  try {
    const data = await api('/api/evaluation');
    $('#eval-summary').innerHTML = [
      metricCard('执行成功率', data.execution_rate + '%', `${data.passed}/${data.total} 条通过`, true),
      metricCard('结果命中率', data.hit_rate + '%', '关键结果值符合预期'),
      metricCard('平均响应时间', data.avg_elapsed_ms + ' ms', '本地运行 · 含检索与 SQL'),
      metricCard('覆盖意图', data.intents + ' 类', '销售 / 品类 / 退款 / ROI / 趋势'),
    ].join('');
    $('#eval-table').innerHTML = `<table class="eval-table"><thead><tr><th>用例</th><th>意图</th><th>执行</th><th>结果命中</th><th>耗时</th></tr></thead><tbody>${data.cases.map((x) => `<tr><td>${x.question}</td><td>${x.intent}</td><td><span class="badge ${x.executed ? '' : 'fail'}">${x.executed ? '通过' : '失败'}</span></td><td><span class="badge ${x.hit ? '' : 'fail'}">${x.hit ? '命中' : '未命中'}</span></td><td>${x.elapsed_ms} ms</td></tr>`).join('')}</tbody></table>`;
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = '运行评测';
  }
}

function init() {
  bindNavigation();
  loadHealth();
  loadOverview();
  loadExamples();
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
  $('#refresh-btn').addEventListener('click', () => { loadOverview(); loadHealth(); });
  $('#run-eval-btn').addEventListener('click', loadEvaluation);
}

document.addEventListener('DOMContentLoaded', init);
