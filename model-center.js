/* WB-01 · server-side model profile registry */
(function bootstrapModelCenter(globalScope) {
  'use strict';

  const CAPABILITIES = [
    ['chat', '对话'], ['structured_output', '结构化输出'], ['tools', '工具调用'],
    ['vision', '视觉'], ['image_generation', '生图'], ['image_edit', '图片编辑'], ['embeddings', '嵌入'],
  ];
  const TASKS = [['research', '研究'], ['outline', '提纲'], ['review', '审核'], ['keyword_cluster', '关键词聚类'], ['image_generation', '生图'], ['embedding', '嵌入']];

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  }

  function createModelCenter({ api, root, onToast = () => {}, onIcons = () => {} }) {
    const state = { profiles: [], selectedId: null, editing: false, loading: false, saving: false, saveError: '', match: null };

    function setStatus(message, kind = 'idle') {
      const node = root.querySelector('#modelCenterStatus');
      if (!node) return;
      node.dataset.state = kind;
      node.textContent = message;
    }

    function profileById(id) { return state.profiles.find((profile) => profile.profileId === id) || null; }

    function renderList() {
      const list = root.querySelector('#modelProfileList');
      const count = root.querySelector('#modelProfileCount');
      if (count) count.textContent = `${state.profiles.length} 个`;
      if (!list) return;
      if (state.loading) {
        list.innerHTML = '<div class="panel-empty">正在读取模型配置...</div>';
        return;
      }
      if (!state.profiles.length) {
        list.innerHTML = '<div class="panel-empty panel-empty-large"><i data-lucide="settings-2"></i><strong>尚未添加模型</strong><span>先登记模型元数据和服务端环境变量名，工作台不会接收明文密钥。</span></div>';
        onIcons();
        return;
      }
      list.innerHTML = state.profiles.map((profile) => `<button class="model-profile-row${profile.profileId === state.selectedId ? ' is-selected' : ''}" type="button" data-model-id="${escapeHtml(profile.profileId)}"><span><strong>${escapeHtml(profile.displayName)}</strong><small>${escapeHtml(profile.provider)} · ${escapeHtml(profile.modelName)}</small></span><span class="model-profile-state"><span class="status-badge status-${escapeHtml(profile.status)}">${profile.status === 'enabled' ? '已启用' : '已停用'}</span><small>${profile.credentialConfigured ? '凭据已配置' : '凭据未配置'}</small></span><i data-lucide="chevron-right"></i></button>`).join('');
      list.querySelectorAll('[data-model-id]').forEach((button) => button.addEventListener('click', () => {
        state.selectedId = button.dataset.modelId;
        state.editing = false;
        state.saveError = '';
        state.match = null;
        render();
      }));
      onIcons();
    }

    function renderEditor() {
      const editor = root.querySelector('#modelProfileEditor');
      if (!editor) return;
      const profile = profileById(state.selectedId);
      if (!state.editing && !profile) {
        editor.innerHTML = '<div class="panel-empty panel-empty-large"><i data-lucide="settings-2"></i><strong>选择一个模型</strong><span>这里会显示能力、端点、数据保留说明和匹配测试。</span></div>';
        onIcons();
        return;
      }
      const value = profile || { provider: '', displayName: '', protocol: 'openai-compatible', endpoint: 'http://127.0.0.1:4446/v1', modelName: '', capabilities: ['chat'], contextWindow: 32000, region: '本机私有部署', dataRetention: '由本机部署策略决定', credentialRef: 'env:', status: 'enabled' };
      editor.innerHTML = `<form class="model-profile-form" id="modelProfileForm"><div class="editor-heading"><div><span class="console-label">MODEL PROFILE</span><h2>${profile ? '编辑模型' : '添加模型'}</h2></div><span class="status-badge">${profile ? `${escapeHtml(profile.health)} · ${profile.credentialConfigured ? '凭据已配置' : '凭据未配置'}` : '未验证'}</span></div><div class="form-grid"><label>供应商<input name="provider" required maxlength="100" value="${escapeHtml(value.provider)}"></label><label>显示名称<input name="displayName" required maxlength="120" value="${escapeHtml(value.displayName)}"></label></div><div class="form-grid"><label>协议<select name="protocol"><option value="openai-compatible">OpenAI-compatible</option></select></label><label>模型名<input name="modelName" required maxlength="200" value="${escapeHtml(value.modelName)}"></label></div><label>服务端 Endpoint<input name="endpoint" required maxlength="500" value="${escapeHtml(value.endpoint)}"><small>远程地址必须 HTTPS；本机 HTTP 只允许回环地址。</small></label><label>服务端环境变量名<input name="credentialRef" required maxlength="140" value="${escapeHtml(value.credentialRef)}"><small>只保存 env: 引用，不在浏览器输入或保存 API Key。</small></label><fieldset><legend>能力标签</legend><div class="model-capability-grid">${CAPABILITIES.map(([key, label]) => `<label><input type="checkbox" name="capabilities" value="${key}" ${value.capabilities.includes(key) ? 'checked' : ''}>${label}</label>`).join('')}</div></fieldset><div class="form-grid"><label>上下文窗口<input name="contextWindow" type="number" min="1000" max="2000000" value="${value.contextWindow ?? ''}"></label><label>状态<select name="status"><option value="enabled" ${value.status === 'enabled' ? 'selected' : ''}>启用</option><option value="disabled" ${value.status === 'disabled' ? 'selected' : ''}>停用</option></select></label></div><div class="form-grid"><label>区域<input name="region" required maxlength="80" value="${escapeHtml(value.region)}"></label><label>数据保留说明<input name="dataRetention" required maxlength="200" value="${escapeHtml(value.dataRetention)}"></label></div><p class="form-status${state.saveError ? ' is-error' : ''}" id="modelProfileFormStatus">${escapeHtml(state.saveError)}</p><div class="editor-actions"><button class="button button-quiet" type="button" id="cancelModelEdit">取消</button><button class="button button-primary" type="submit">${profile ? '保存修改' : '添加模型'}</button></div></form>`;
      editor.querySelector('#cancelModelEdit').addEventListener('click', () => { state.editing = false; state.saveError = ''; render(); });
      editor.querySelector('#modelProfileForm').addEventListener('submit', (event) => saveProfile(event, profile));
      onIcons();
    }

    function renderMatch() {
      const node = root.querySelector('#modelMatchResult');
      if (!node) return;
      if (!state.match) { node.innerHTML = '<div class="panel-empty">运行一次任务匹配后，这里会显示首选模型、备用模型和理由。</div>'; return; }
      node.innerHTML = `<div class="match-result" data-state="${state.match.status}"><strong>${state.match.status === 'matched' ? '已找到匹配' : '暂无匹配模型'}</strong><span>${escapeHtml(state.match.reasons.join('；'))}</span>${state.match.preferred ? `<small>首选：${escapeHtml(state.match.preferred.displayName)} · ${escapeHtml(state.match.preferred.modelName)}</small>` : ''}${state.match.backup ? `<small>备用：${escapeHtml(state.match.backup.displayName)} · ${escapeHtml(state.match.backup.modelName)}</small>` : ''}</div>`;
    }

    function render() { renderList(); renderEditor(); renderMatch(); }

    async function load() {
      state.loading = true; renderList(); setStatus('正在读取模型配置...', 'loading');
      try {
        const result = await api.listModelProfiles();
        state.profiles = result.profiles;
        if (!profileById(state.selectedId)) state.selectedId = state.profiles[0]?.profileId || null;
        setStatus(`已读取 ${state.profiles.length} 个模型配置；全部连接仍需单独验证。`, 'ready');
      } catch (error) {
        setStatus(error.message || '模型配置暂时无法读取。', 'error');
      } finally { state.loading = false; render(); }
    }

    async function saveProfile(event, profile) {
      event.preventDefault();
      if (state.saving) return;
      const form = event.currentTarget;
      const data = new FormData(form);
      const input = {
        provider: data.get('provider'), displayName: data.get('displayName'), protocol: data.get('protocol'), endpoint: data.get('endpoint'), modelName: data.get('modelName'),
        capabilities: data.getAll('capabilities'), contextWindow: data.get('contextWindow') ? Number(data.get('contextWindow')) : null, region: data.get('region'), dataRetention: data.get('dataRetention'), credentialRef: data.get('credentialRef'), status: data.get('status'),
      };
      if (profile) input.expectedVersion = profile.version;
      state.saveError = '';
      state.saving = true;
      const status = form.querySelector('#modelProfileFormStatus');
      status.textContent = '正在保存模型配置...'; status.className = 'form-status is-working';
      try {
        const saved = profile ? await api.updateModelProfile(profile.profileId, input) : await api.createModelProfile(input);
        state.profiles = profile ? state.profiles.map((item) => item.profileId === saved.profileId ? saved : item) : [...state.profiles, saved];
        state.selectedId = saved.profileId; state.editing = false; onToast(profile ? '模型配置已更新' : '模型配置已添加');
        setStatus('配置已保存；连接状态仍为“未验证”，不会自动调用模型。', 'ready');
      } catch (error) {
        state.saveError = error.message || '模型配置保存失败，请刷新后重试。';
      } finally { state.saving = false; render(); }
    }

    async function matchTask() {
      const taskType = root.querySelector('#modelMatchTask').value;
      const button = root.querySelector('#runModelMatch');
      button.disabled = true;
      try { state.match = await api.matchModelTask(taskType); }
      catch (error) { setStatus(error.message || '任务匹配失败。', 'error'); }
      finally { button.disabled = false; renderMatch(); }
    }

    function mount() {
      root.innerHTML = `<section class="model-center module-surface"><header class="page-heading"><div><h1><span class="console-label">SETTINGS / MODEL CENTER</span>模型与连接</h1><p>登记模型能力和服务端凭据引用，为研究、审核、关键词和生图任务提供可解释匹配。</p></div><button class="button button-primary" type="button" id="addModelProfile"><i data-lucide="plus"></i>添加模型</button></header><div class="module-status" id="modelCenterStatus" role="status">等待读取模型配置...</div><div class="model-center-layout"><aside class="model-profile-panel"><div class="panel-heading"><div><span class="console-label">PROFILES</span><h2>模型配置</h2></div><span id="modelProfileCount">${state.profiles.length} 个</span></div><div id="modelProfileList" class="model-profile-list"></div></aside><section id="modelProfileEditor" class="model-editor"></section><aside class="model-match-panel"><div class="panel-heading"><div><span class="console-label">TASK MATCHING</span><h2>任务匹配</h2></div></div><label>任务类型<select id="modelMatchTask">${TASKS.map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></label><button class="button button-quiet" type="button" id="runModelMatch">运行匹配</button><div id="modelMatchResult"></div><p class="claim-boundary"><i data-lucide="shield-check"></i>当前只登记和匹配模型元数据，不执行外部模型调用。</p></aside></div></section>`;
      root.querySelector('#addModelProfile').addEventListener('click', () => { state.editing = true; state.selectedId = null; state.saveError = ''; render(); });
      root.querySelector('#runModelMatch').addEventListener('click', matchTask);
      render(); load(); onIcons();
    }

    return { mount, snapshot: () => ({ ...state, profiles: [...state.profiles] }) };
  }

  globalScope.MarketOpsModelCenter = { createModelCenter };
}(typeof globalThis !== 'undefined' ? globalThis : window));
