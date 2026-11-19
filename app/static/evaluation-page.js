import { state } from "./state.js";
import { $, $$, fmtNum, toast, api, escapeHtml } from "./utils.js";
import { EVAL_LABELS, formatEvaluatedAt, evalModeLabel, FEEDBACK_CATEGORY_LABELS, EVAL_STATUS_LABELS, evalTaskStatusBadge } from "./labels.js";

function statusBadge(status) {
  const map = { ready: ['正常', ''], queued: ['待处理', 'pending'], running: ['运行中', 'pending'], cancelled: ['已取消', 'cancel'], failed: ['失败', 'fail'] };
  const [label, cls] = map[status] || [status, ''];
  return `<span class="badge ${cls}">${escapeHtml(label)}</span>`;
}

function renderEvaluationTaskList(data) {
  const tasks = data.tasks || [];
  const selected = state.evalSelectedTask;
  $('#eval-task-count').textContent = tasks.length + ' 份';
  $('#eval-task-list').innerHTML = tasks.length ? tasks.map((task) => {
    const progress = task.progress || {};
    return `<button type="button" class="eval-task-item ${selected === task.id ? 'selected' : ''}" data-eval-task="${escapeHtml(task.id)}">
      <span class="eval-task-main"><strong>${escapeHtml(task.label)}</strong><small>${escapeHtml(task.model_name || '未记录模型')} · ${escapeHtml(formatEvaluatedAt(task.created_at))}</small></span>
      <span class="eval-task-side">${evalTaskStatusBadge(task.status)}<small>${task.status === 'running' ? `${progress.completed || 0}/${progress.total || 0}` : task.total ? `${task.passed}/${task.total} 通过` : '—'}</small></span>
      ${task.status === 'running' ? `<span class="eval-progress"><i style="width:${progress.percent || 0}%"></i></span>` : ''}
    </button>`;
  }).join('') : '<p class="muted">暂无评测任务。</p>';
  $$('[data-eval-task]').forEach((button) => button.addEventListener('click', () => selectEvaluationTask(button.dataset.evalTask)));
}

function renderEvaluationTaskSummary(task) {
  const box = $('#eval-summary');
  if (!box) return;
  if (!task) { box.innerHTML = '<p class="muted">选择一份评测任务查看摘要。</p>'; return; }
  const progress = task.progress || {};
  box.innerHTML = `<div class="eval-summary-main"><h3>${escapeHtml(task.label)}</h3><span>${evalTaskStatusBadge(task.status)}</span></div>
    <div class="eval-summary-grid"><span>状态 <strong>${escapeHtml(EVAL_STATUS_LABELS[task.status] || task.status)}</strong></span>
    <span>答案 <strong>${task.answer_correct ?? task.passed ?? 0}/${task.total || 0}</strong></span>
    <span>计划 <strong>${task.plan_correct ?? 0}/${task.total || 0}</strong></span>
    <span>进度 <strong>${progress.completed || 0}/${progress.total || 0}</strong></span></div>
    ${task.error ? '<p class="eval-error">' + escapeHtml(task.error) + '</p>' : ''}`;
}

async function loadEvaluationTasks(options = {}) {
  try {
    const [data, datasets, experiments, feedback, candidates] = await Promise.all([
      api('/api/evaluation/tasks'), api('/api/evaluation/datasets'),
      api('/api/evaluation/experiments'), api('/api/evaluation/feedback'),
      api('/api/evaluation/regression-candidates'),
    ]);
    state.datasets = datasets.datasets || [];
    state.experiments = experiments.experiments || [];
    state.feedback = feedback.feedback || [];
    state.regressionCandidates = candidates.candidates || [];
    renderEvaluationPlatform(data);
    state.evalTasksData = data;
    renderEvaluationTaskList(data);
    const active = data.active;
    const preferred = active?.id || state.evalSelectedTask || data.tasks?.[0]?.id;
    if (preferred) await selectEvaluationTask(preferred, { silent: true });
    else { renderEvaluationTaskSummary(null); renderEvaluationReport(null); }
    const button = $('#run-eval-btn');
    if (button) { button.disabled = Boolean(active); button.textContent = active ? '评测运行中…' : '提交评测任务'; }
    if (state.evalPollTimer) clearTimeout(state.evalPollTimer);
    if (active) state.evalPollTimer = setTimeout(() => loadEvaluationTasks({ polled: true }), 3000);
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
    const task = await api('/api/evaluation/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workers: 3, dataset_id: $('#eval-dataset')?.value || 'dataset_v3_100' }) });
    state.evalSelectedTask = task.id;
    toast('评测任务已创建，后台运行中');
    await loadEvaluationTasks();
  } catch (error) { toast(error.message); button.disabled = false; button.textContent = '提交评测任务'; }
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
  $('#experiment-list').innerHTML = experiments.length ? experiments.map((exp) => `<div class="experiment-card"><strong>${escapeHtml(exp.name)}</strong><p>${escapeHtml(exp.hypothesis || '暂无实验假设')}</p><small>${escapeHtml(exp.dataset_id)} · ${escapeHtml(formatEvaluatedAt(exp.created_at))}</small><div class="experiment-runs">${(exp.tasks || []).map((task) => `<span>${escapeHtml(task.model_name || '待运行')} · A ${task.answer_correct_rate ?? task.hit_rate ?? 0}% / P ${task.plan_correct_rate ?? 0}%</span>`).join('') || '<span>尚未关联评测任务</span>'}</div></div>`).join('') : '<p class="muted">暂无实验记录。</p>';
  $('#feedback-table').innerHTML = feedback.length ? `<table class="eval-table"><thead><tr><th>用例</th><th>任务</th><th>分类</th><th>状态</th></tr></thead><tbody>${feedback.map((item) => `<tr><td>${escapeHtml(item.case_id)}<div class="muted">${escapeHtml(item.note || '')}</div></td><td><code>${escapeHtml(item.task_id)}</code></td><td>${escapeHtml(FEEDBACK_CATEGORY_LABELS[item.category] || item.category)}</td><td>${statusBadge(item.status === 'open' ? 'queued' : 'ready')}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">暂无失败反馈。</p>';
  const candidates = state.regressionCandidates || [];
  const candidateTable = $('#candidate-table');
  if (candidateTable) candidateTable.innerHTML = candidates.length ? `<table class="eval-table"><thead><tr><th>来源用例</th><th>预期指标</th><th>失败原因</th><th>状态</th><th></th></tr></thead><tbody>${candidates.map((item) => `<tr><td>${escapeHtml(item.question)}<div class="muted">${escapeHtml(item.source_case_id)}</div></td><td>${escapeHtml(item.expected_metric || '待补充')}</td><td>${escapeHtml(item.error || '')}</td><td>${statusBadge(item.status === 'pending' ? 'queued' : 'ready')}</td><td>${item.status === 'pending' ? `<button class="secondary-btn compact-btn" data-approve-candidate="${escapeHtml(item.id)}">批准</button>` : `<code>${escapeHtml(item.dataset_id || '')}</code>`}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">暂无失败候选。先在任务详情中导入失败用例。</p>';
  const runSelect = $('#eval-dataset');
  if (runSelect) { const current = runSelect.value; runSelect.innerHTML = datasets.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.version)}（${item.case_count} 条）</option>`).join(''); if (current) runSelect.value = current; }
}

async function selectEvaluationTask(taskId, options = {}) {
  state.evalSelectedTask = taskId;
  const task = await api(`/api/evaluation/tasks/${encodeURIComponent(taskId)}`);
  renderEvaluationTaskSummary(task);
  renderEvaluationReport(task);
  if (!options.silent) renderEvaluationTaskList(state.evalTasksData || { tasks: [] });
  return task;
}

function renderEvaluationReport(task) {
  const report = task?.report;
  if (!report || !report.cases) { $('#eval-count').textContent = task?.status === 'running' ? '运行中' : '暂无结果'; $('#eval-table').innerHTML = `<p class="muted">${task ? escapeHtml(EVAL_STATUS_LABELS[task.status] || task.status) + '，完成后在这里查看逐条结果。' : '选择一份评测任务后查看。'}</p>`; return; }
  $('#eval-count').textContent = report.available ? `答案 ${report.answer_correct ?? report.passed}/${report.total} · 计划 ${report.plan_correct ?? 0}/${report.total} · SQL 执行率 ${report.execution_rate ?? 0}%` : '模型未就绪';
  const rows = report.cases.map((item) => `<tr><td>${item.setup_question ? `<div class="muted">前置：${escapeHtml(item.setup_question)}</div>` : ''}${escapeHtml(item.question)}</td><td>${escapeHtml(EVAL_LABELS[item.intent] || item.intent)}</td><td>${item.model_used ? '<span class="badge">模型完成</span>' : '<span class="badge fail">模型未完成</span>'}<div class="muted">${escapeHtml(evalModeLabel(item))}</div></td><td><span class="badge ${item.answer_correct ?? item.hit ? '' : 'fail'}">答案 ${(item.answer_correct ?? item.hit) ? '通过' : '未通过'}</span><div class="muted">计划 ${(item.plan_correct ?? false) ? '通过' : '未通过'}</div></td><td>${escapeHtml(item.attempts)} 次 / ${escapeHtml(item.elapsed_ms)} 毫秒</td><td class="eval-result-cell">${item.error ? `<span class="eval-error">${escapeHtml(item.error)}</span>` : '<span class="eval-pass">模型结果符合验收值</span>'}</td></tr>`).join('');
  $('#eval-table').innerHTML = `<table class="eval-table"><thead><tr><th>黄金问题</th><th>意图</th><th>SQL 来源</th><th>答案 / 计划</th><th>执行 / 耗时</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table>`;
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
    $('#eval-compare-result').innerHTML = `<div class="eval-compare-metrics"><span>答案正确数 <strong>${data.delta.answer_correct >= 0 ? '+' : ''}${data.delta.answer_correct}</strong></span><span>答案正确率 <strong>${data.delta.answer_correct_rate >= 0 ? '+' : ''}${data.delta.answer_correct_rate}%</strong></span><span>计划正确率 <strong>${data.delta.plan_correct_rate >= 0 ? '+' : ''}${data.delta.plan_correct_rate}%</strong></span><span>执行率 <strong>${data.delta.execution_rate >= 0 ? '+' : ''}${data.delta.execution_rate}%</strong></span><span>平均耗时 <strong>${data.delta.avg_elapsed_ms >= 0 ? '+' : ''}${data.delta.avg_elapsed_ms} 毫秒</strong></span></div>${results.join('')}`;
  } catch (error) { toast(error.message); }
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


async function ingestRegressionCandidates() {
  const taskId = state.evalSelectedTask;
  if (!taskId) return toast('请先选择评测任务');
  try { const result = await api(`/api/evaluation/tasks/${encodeURIComponent(taskId)}/regression-candidates`, { method: 'POST' }); toast(`已生成 ${result.candidates?.length || 0} 条失败候选`); await loadEvaluationTasks(); }
  catch (error) { toast(error.message); }
}

async function approveRegressionCandidate(candidateId) {
  try { await api('/api/evaluation/regression-candidates/approve', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ candidate_ids: [candidateId], name: '失败回流集' }) }); toast('候选已批准'); await loadEvaluationTasks(); }
  catch (error) { toast(error.message); }
}


export {
  loadEvaluationTasks,
  runEvaluation,
  evaluationTab,
  renderEvaluationPlatform,
  compareEvaluationTasks,
  cancelEvaluation,
  retryEvaluation,
  submitDataset,
  submitExperiment,
  submitFeedback,
  selectEvaluationTask,
  ingestRegressionCandidates,
  approveRegressionCandidate,
};
