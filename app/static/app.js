import { state } from "./state.js";
import { $, $$, fmtMoney, fmtNum, toast, api, escapeHtml } from "./utils.js";
import { metricCard } from "./overview-render.js";
import { loadModelConfig, renderModelHeading, renderModelRuntime, MODEL_BASE_URLS, fetchModels, testModel, saveModel } from "./model-page.js";
import { loadDataAssets, platformTab, runPlatformQuality, runScheduler, submitSource, submitQualityRule, submitMetric, renderDataPlatform } from "./data-platform-page.js";
import { loadEvaluationTasks, evaluationTab, runEvaluation, compareEvaluationTasks, submitDataset, submitExperiment, submitFeedback, ingestRegressionCandidates } from "./evaluation-page.js";
import { createSession, loadSessions, cancelActiveQuery, submitQuery } from "./sessions.js";
import { scrollThreadToLatest, updateLatestReplyButton } from "./result-view.js";
import { switchView, bindNavigation, setPageHeading, toggleWorkspacePanel, closeWorkspacePanels } from "./shell.js";

function formatDatasetRange(dataset) {
  const start = String(dataset?.start || '').slice(0, 7).replace('-', '.');
  const end = String(dataset?.end || '').slice(0, 7).replace('-', '.');
  return start && end ? `${start} – ${end}` : '当前数据范围';
}

async function loadOverview() {
  try {
    const data = await api('/api/overview');
    state.overview = data;
    const datasetRange = $('#dataset-note-range');
    if (datasetRange) datasetRange.textContent = formatDatasetRange(data.dataset).replace(/./g, '.');
    const reference = $('#dataset-note-reference');
    if (reference && data.dataset?.reference_date) reference.textContent = data.dataset.reference_date;
    $('#metrics-grid').innerHTML = [
      metricCard(
        '累计实付销售额',
        fmtMoney(data.revenue),
        `${formatDatasetRange(data.dataset)} · 已支付订单`,
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
    renderChannels(data.channel, data.channel_metric);
  } catch (error) {
    toast(error.message);
  }
}

function renderTrend(rows) {
  const max = Math.max(...(rows || []).map((r) => Number(r.revenue)), 1);
  $('#trend-chart').innerHTML = (rows || [])
    .map((row) => {
      const h = Math.max(3, (Number(row.revenue) / max) * 135);
      return `<div class="bar-wrap" data-label="${escapeHtml(row.date)} · ${escapeHtml(fmtMoney(row.revenue))}"><div class="bar" style="height:${h}px"></div></div>`;
    })
    .join('');
}

function renderChannels(rows, metric = '销售额') {
  const key = metric === '订单数' ? 'orders' : 'revenue';
  const max = Math.max(...(rows || []).map((r) => Number(r[key])), 1);
  $('#channel-list').innerHTML = (rows || [])
    .map(
      (row) => `
    <div class="channel-row">
      <span>${escapeHtml(row.channel)}</span>
      <span class="channel-track"><span class="channel-fill" style="width:${(Number(row[key]) / max) * 100}%"></span></span>
      <span class="channel-value">${key === 'orders' ? fmtNum(row[key]) : fmtMoney(row[key])}</span>
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
      (q) => `<button class="example-chip" data-question="${escapeHtml(q)}">${escapeHtml(q)}</button>`,
    )
    .join('');
  $$('.example-chip').forEach((btn) =>
    btn.addEventListener('click', () => {
      $('#question').value = btn.dataset.question;
      $('#question').focus();
    }),
  );
}


function init() {
window.addEventListener('atlas:view-change', (event) => {
  const view = event.detail?.view;
  if (view === 'models') loadModelConfig();
  if (view === 'data-assets') loadDataAssets();
  if (view === 'evaluation') loadEvaluationTasks();
});
window.addEventListener('atlas:load-sessions', () => loadSessions());
window.addEventListener('atlas:refresh-overview', () => loadOverview());
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
  $('#save-admin-token')?.addEventListener('click', () => {
    const value = $('#admin-token')?.value.trim() || '';
    if (value) window.localStorage.setItem('atlas_admin_token', value);
    else window.localStorage.removeItem('atlas_admin_token');
    toast(value ? '管理令牌已保存在当前浏览器' : '已清除管理令牌');
  });
  $('#source-form')?.addEventListener('submit', submitSource);
  $('#quality-rule-form')?.addEventListener('submit', submitQualityRule);
  $('#metric-form')?.addEventListener('submit', submitMetric);
  $('#dataset-form')?.addEventListener('submit', submitDataset);
  $('#experiment-form')?.addEventListener('submit', submitExperiment);
  $('#feedback-form')?.addEventListener('submit', submitFeedback);
  $('#ingest-candidates-btn')?.addEventListener('click', ingestRegressionCandidates);
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
