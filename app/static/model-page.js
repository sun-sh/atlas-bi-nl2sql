import { state } from "./state.js";
import { $, toast, api, escapeHtml } from "./utils.js";

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
    : '离线规则 · 未启用模型';
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


export {
  MODEL_BASE_URLS,
  loadModelConfig,
  fetchModels,
  testModel,
  saveModel,
  renderModelHeading,
  renderModelRuntime,
};
