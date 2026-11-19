import { escapeHtml } from "./utils.js";

export const EVAL_LABELS = {
  sales_overview: '销售总览',
  category_analysis: '品类分析',
  refund_analysis: '退款分析',
  marketing_efficiency: '投放效率',
};

export const SQL_SOURCE_LABELS = {
  llm: '模型生成',
  'llm-repair': '模型修复',
  rule: '离线规则',
  'rule-repair': '规则修复',
  'model-failed': '模型未产出',
  'model-repair-failed': '模型修复失败',
  clarification: '需要补充条件',
  unsupported: '不在可回答范围',
  failed: '执行失败',
  unavailable: '未运行',
  error: '异常',
};

export const ATTEMPT_STATUS_LABELS = {
  success: '成功',
  failed: '失败',
};

export const FEEDBACK_CATEGORY_LABELS = {
  wrong_result: '结果错误',
  wrong_sql: 'SQL 口径',
  bad_narrative: '解读问题',
  retrieval: '召回问题',
  other: '其他',
};

export const EVAL_STATUS_LABELS = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
};

export function sqlSourceLabel(value) {
  return SQL_SOURCE_LABELS[value] || '来源未知';
}

export function evalModeLabel(item) {
  return sqlSourceLabel(item.sql_mode);
}

export function evalTaskStatusBadge(status) {
  const cls = status === 'failed' ? 'fail' : status === 'running' || status === 'queued' ? 'pending' : '';
  return `<span class="badge ${cls}">${escapeHtml(EVAL_STATUS_LABELS[status] || status)}</span>`;
}

export function formatEvaluatedAt(value) {
  if (!value) return '未提供';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}
