import { state } from "./state.js";
import { $, $$, fmtNum, toast, api, escapeHtml } from "./utils.js";
import { formatEvaluatedAt } from "./labels.js";

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
  const queryDatabase = data.controls?.query_database || 'commerce.db';
  $('#asset-summary').innerHTML = [
    metricCard('数据源', `${summary.source_count || 0} 个`, `${summary.ready_sources || 0} 个已同步 · 当前查询库 ${queryDatabase}`, summary.source_count > 0),
    metricCard('质量规则', `${summary.rule_count || 0} 条`, `${summary.last_quality ? `${summary.last_quality.passed} 通过 / ${summary.last_quality.failed} 失败` : '尚未运行巡检'}`),
    metricCard('指标目录', `${summary.metric_count || 0} 个`, `${summary.published_metrics || 0} 个已发布`),
    metricCard('字段血缘', `${summary.lineage_edges || 0} 条`, '源系统字段 → 平台资产字段'),
  ].join('');
  $('#source-count').textContent = `${(data.sources || []).length} 个`;
  $('#source-table').innerHTML = (data.sources || []).map((source) => `<tr><td><strong>${escapeHtml(source.name)}</strong><div class="muted">${source.kind === 'csv' ? 'CSV' : source.kind === 'sqlite' ? 'SQLite' : escapeHtml(source.kind)} · ${escapeHtml(source.location)}</div><div class="muted">${source.query_compatibility?.compatible ? 'NL2SQL Schema 兼容' : escapeHtml(source.query_compatibility?.error || '固定五表契约未校验')}</div></td><td>${statusBadge(source.status)}<div class="muted">${escapeHtml(source.last_sync_at || '尚未同步')}</div></td><td>${fmtNum(source.row_count)} 行</td><td>${source.schedule_minutes ? `${source.schedule_minutes} 分钟` : '手动'}</td><td><button class="secondary-btn compact-btn" data-sync-source="${escapeHtml(source.id)}">同步</button>${source.active_for_query ? '<span class="badge">分析数据源</span>' : `<button class="secondary-btn compact-btn" data-activate-source="${escapeHtml(source.id)}" ${source.status !== 'ready' || !source.query_compatibility?.compatible ? 'disabled' : ''}>启用分析</button>`}</td></tr>`).join('');
  $$('[data-sync-source]').forEach((btn) => btn.addEventListener('click', () => syncPlatformSource(btn.dataset.syncSource)));
  $$('[data-activate-source]').forEach((btn) => btn.addEventListener('click', () => activatePlatformSource(btn.dataset.activateSource)));
  const ruleTypeLabels = { not_null: '非空', unique: '唯一', non_negative: '非负', not_orphan: '外键有效', row_min: '最少行数' };
  $('#quality-rule-table').innerHTML = `<table class="eval-table"><thead><tr><th>规则</th><th>类型</th><th>对象</th><th>状态</th></tr></thead><tbody>${(data.rules || []).map((rule) => `<tr><td>${escapeHtml(rule.name)}</td><td>${escapeHtml(ruleTypeLabels[rule.rule_type] || rule.rule_type)}</td><td>${escapeHtml(rule.table_name)}.${escapeHtml(rule.column_name || '')}</td><td>${rule.enabled ? '<span class="badge">启用</span>' : '<span class="badge cancel">停用</span>'}</td></tr>`).join('')}</tbody></table>`;
  $('#quality-run-table').innerHTML = (data.quality_runs || []).length ? `<table class="eval-table"><thead><tr><th>时间</th><th>数据源</th><th>结果</th></tr></thead><tbody>${data.quality_runs.map((run) => `<tr><td>${escapeHtml(formatEvaluatedAt(run.started_at))}</td><td>${escapeHtml(run.source_id)}</td><td><span class="badge ${run.failed ? 'fail' : ''}">${run.passed} 通过 / ${run.failed} 失败</span></td></tr>`).join('')}</tbody></table>` : '<p class="muted">尚无巡检记录。</p>';
  $('#metric-count').textContent = `${(data.metrics || []).length} 个`;
  $('#metric-table').innerHTML = `<table class="eval-table"><thead><tr><th>指标</th><th>粒度</th><th>口径</th><th>负责人</th><th>状态</th></tr></thead><tbody>${(data.metrics || []).map((metric) => `<tr><td><strong>${escapeHtml(metric.name)}</strong></td><td>${escapeHtml(metric.grain)}</td><td>${escapeHtml(metric.definition)}</td><td>${escapeHtml(metric.owner || '—')}</td><td>${statusBadge(metric.status === 'published' ? 'ready' : metric.status === 'deprecated' ? 'cancelled' : 'queued')}</td></tr>`).join('')}</tbody></table>`;
  $('#lineage-count').textContent = `${(data.lineage || []).length} 条`;
  $('#lineage-table').innerHTML = (data.lineage || []).length ? `<table class="eval-table"><thead><tr><th>上游字段</th><th></th><th>平台资产字段</th><th>关系</th></tr></thead><tbody>${data.lineage.slice(0, 100).map((edge) => `<tr><td><code>${escapeHtml(edge.upstream_node)}</code></td><td class="muted">→</td><td><code>${escapeHtml(edge.downstream_node)}</code></td><td>${escapeHtml(edge.relation)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">同步数据源后生成字段级血缘。</p>';
  $('#audit-list').innerHTML = (data.audit || []).map((item) => `<div class="audit-item"><strong>${escapeHtml(item.action)}</strong><small>${escapeHtml(item.actor)} · ${escapeHtml(formatEvaluatedAt(item.created_at))}</small><code>${escapeHtml(item.resource_type)} / ${escapeHtml(item.resource_id)}</code><small>${item.role}</small></div>`).join('');
}

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
  $('#asset-relation-table').innerHTML = `<table class="eval-table"><thead><tr><th>源表</th><th>关系</th><th>目标表</th><th>孤儿记录</th></tr></thead><tbody>${(data.relations || []).map((r) => `<tr><td><code>${escapeHtml(r.source)}.${escapeHtml(r.source_column)}</code></td><td>${escapeHtml(r.description)}</td><td><code>${escapeHtml(r.target)}.${escapeHtml(r.target_column)}</code></td><td><span class="badge ${r.orphan_count ? 'fail' : ''}">${r.orphan_count}</span></td></tr>`).join('')}</tbody></table>`;
}


async function activatePlatformSource(sourceId) {
  try {
    await api(`/api/data-platform/sources/${encodeURIComponent(sourceId)}/activate`, {
      method: 'POST',
    });
    toast('已切换为分析数据源');
    await loadDataAssets();
  } catch (error) {
    toast(error.message);
  }
}

async function submitSource(event) {
  event.preventDefault();
  const message = $('#source-message');
  try {
    await api('/api/data-platform/sources', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ name: $('#source-name').value, kind: $('#source-kind').value, location: $('#source-location').value, dsn: $('#source-dsn').value, sync_mode: $('#source-kind').value === 'csv' ? 'ingest' : 'snapshot', sensitivity: $('#source-sensitivity').value, schedule_minutes: Number($('#source-schedule').value || 0) }),
    });
    message.textContent = '数据源已注册，可点击“同步”建立首批快照，再启用为分析数据源。';
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

async function ingestRegressionCandidates() {
  const taskId = state.evalSelectedTask || $('#eval-baseline')?.value;
  if (!taskId) return toast('请先选择一个评测任务');
  try {
    const data = await api(`/api/evaluation/tasks/${encodeURIComponent(taskId)}/regression-candidates`, { method: 'POST' });
    toast(`已生成 ${data.count} 条待审核候选`);
    await loadEvaluationTasks();
  } catch (error) {
    toast(error.message);
  }
}

async function approveRegressionCandidate(candidateId) {
  const name = `失败回流 · ${new Date().toISOString().slice(0, 16).replace('T', ' ')}`;
  try {
    const data = await api('/api/evaluation/regression-candidates/approve', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({candidate_ids: [candidateId], name, version: 'v1'}),
    });
    toast(`已生成回归集 ${data.case_count} 条`);
    await loadEvaluationTasks();
  } catch (error) {
    toast(error.message);
  }
}


export {
  loadDataAssets,
  platformTab,
  statusBadge,
  renderDataPlatform,
  renderDataAssets,
  syncPlatformSource,
  runPlatformQuality,
  runScheduler,
  activatePlatformSource,
  submitSource,
  submitQualityRule,
  submitMetric,
};
