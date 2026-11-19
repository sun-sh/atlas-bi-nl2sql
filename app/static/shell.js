import { state } from "./state.js";
import { $, $$, toast } from "./utils.js";

export function setPageHeading() {
  const headings = {
    workspace: ['Atlas BI', '电商经营分析'],
    overview: ['经营总览', '固定数据集与实时聚合指标'],
    evidence: ['检索与证据', '召回、计划、SQL 与执行链路'],
    'data-assets': ['数据中台', '资产目录、数据源与质量治理'],
    evaluation: ['评测中台', '数据集、实验、任务和失败回流'],
    models: ['模型配置', 'OpenAI 兼容接口与本地规则回退'],
  };
  const [title, subtitle] = headings[state.activeView] || headings.workspace;
  $('#page-title').textContent = title;
  $('#page-subtitle').textContent = subtitle;
}

export function toggleWorkspacePanel(panel) {
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

export function closeWorkspacePanels() {
  $('#view-workspace')?.classList.remove('has-workspace-drawer');
  $$('.workspace-drawer.is-open').forEach((panel) => panel.classList.remove('is-open'));
  $('#history-btn')?.setAttribute('aria-expanded', 'false');
  $('#context-btn')?.setAttribute('aria-expanded', 'false');
}

export function switchView(view) {
  if (!document.querySelector(`#view-${view}`)) view = 'workspace';
  state.activeView = view;
  document.body.classList.toggle('workspace-active', view === 'workspace');
  $$('.nav-item').forEach((x) => x.classList.toggle('active', x.dataset.view === view));
  $$('.view').forEach((x) => x.classList.toggle('active', x.id === `view-${view}`));
  if (window.location.pathname !== `/${view}`) window.history.pushState({}, '', `/${view}`);
  setPageHeading();
  $$('.mobile-nav-item').forEach((x) => x.classList.toggle('active', x.dataset.view === view));
  closeWorkspacePanels();
  window.dispatchEvent(new CustomEvent('atlas:view-change', { detail: { view } }));
  if (view === 'overview') window.dispatchEvent(new CustomEvent('atlas:refresh-overview'));
  if (view === 'workspace') window.dispatchEvent(new CustomEvent('atlas:load-sessions'));
}

export function bindNavigation() {
  $$('.nav-item, .mobile-nav-item').forEach((item) =>
    item.addEventListener('click', () => switchView(item.dataset.view)),
  );
  const initial = window.location.pathname.replace(/^\/|\/$/g, '') || 'workspace';
  switchView(initial);
  window.addEventListener('popstate', () => {
    switchView(window.location.pathname.replace(/^\/|\/$/g, '') || 'workspace');
  });
}

export { toggleWorkspacePanel as togglePanel };