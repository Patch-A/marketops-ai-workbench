(function (globalScope) {
  'use strict';

  const state = globalScope.__marketOpsGeoState || { configured: false, querySetId: '', product: '', market: '', language: '', queries: [], snapshots: [], tasks: [] };
  globalScope.__marketOpsGeoState = state;

  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  const safeCitation = (value) => { const text = String(value || '').trim(); try { const parsed = new URL(text); return ['http:', 'https:'].includes(parsed.protocol) ? text : ''; } catch { return ''; } };
  const today = () => new Date().toISOString().slice(0, 10);

  function mount({ root, api = null, onIcons = () => {}, onToast = () => {}, describeError = () => '请求失败，请重试。' }) {
    root.innerHTML = `<section class="module-surface geo-workbench" aria-labelledby="geoWorkbenchTitle">
      <header class="page-heading"><div><span class="console-label">GENERATIVE ENGINE OPTIMIZATION</span><h1 id="geoWorkbenchTitle">GEO 可见性工作台</h1><p>用固定问题集记录搜索与 AI 回答中的观测样本，转成可执行的内容缺口任务。</p></div><span class="status-badge"><i data-lucide="radar"></i>本地观测</span></header>
      <div class="geo-boundary" role="note"><i data-lucide="shield-check"></i><span>一次快照只代表指定平台、查询、语言、地区和时间的样本，不代表普遍排名或全部用户答案。</span></div>
      <div class="geo-grid">
        <form class="geo-panel" id="geoConfigForm"><div class="panel-heading"><div><span class="console-label">QUERY SET</span><h2>固定查询集</h2></div><span id="geoQueryCount">0 / 20</span></div>
          <label>产品或品牌<input name="product" required maxlength="200" placeholder="例如：工业连接器方案"></label>
          <div class="research-field-row"><label>目标市场<input name="market" required maxlength="120" placeholder="印度"></label><label>语言<input name="language" required maxlength="80" placeholder="中文 / English"></label></div>
          <label>查询问题（每行一条，最多 20 条）<textarea name="queries" required rows="8" maxlength="4000" placeholder="印度工业连接器供应商怎么选？&#10;industrial connector supplier India"></textarea></label>
          <button class="button button-primary" type="submit"><i data-lucide="save"></i>保存查询集</button><p class="field-help">查询集保存后保持稳定，后续快照才能比较变化。</p>
        </form>
        <section class="geo-panel" aria-labelledby="geoSnapshotTitle"><div class="panel-heading"><div><span class="console-label">SNAPSHOT</span><h2 id="geoSnapshotTitle">记录一次观测</h2></div><span id="geoSnapshotCount">0 条</span></div>
          <form id="geoSnapshotForm" class="geo-form" hidden><div class="research-field-row"><label>平台<select name="platform"><option>Google Search</option><option>Bing Search</option><option>ChatGPT</option><option>其他（人工记录）</option></select></label><label>查询<select name="query" id="geoQuerySelect"></select></label></div><label>观测结果<select name="visibility"><option value="mentioned">出现目标产品/主题</option><option value="not_mentioned">未出现</option><option value="unclear">无法判断</option></select></label><label>回答或结果摘录<textarea name="observation" required maxlength="2000" rows="4" placeholder="粘贴你看到的回答片段或结果摘要"></textarea></label><div class="research-field-row"><label>观察日期<input name="observedAt" type="date" required></label><label>引用 URL（可选）<input name="citation" type="url" maxlength="1000" placeholder="https://..."></label></div><button class="button button-primary" type="submit"><i data-lucide="plus"></i>保存快照</button></form>
          <div id="geoSnapshotEmpty" class="panel-empty panel-empty-large"><i data-lucide="scan-search"></i><strong>先保存固定查询集</strong><span>然后逐条记录指定平台的回答样本。</span></div><div id="geoSnapshotList" class="geo-list"></div>
        </section>
      </div>
      <section class="geo-panel geo-task-panel" aria-labelledby="geoTaskTitle"><div class="panel-heading"><div><span class="console-label">CONTENT GAPS</span><h2 id="geoTaskTitle">内容缺口任务</h2></div><span id="geoTaskCount">0 项</span></div><div id="geoTaskList" class="geo-task-list"><div class="panel-empty">出现“未出现”或“无法判断”时，这里会生成待确认任务。</div></div></section>
      <p class="claim-boundary"><i data-lucide="info"></i>当前版本只保存人工提供的观测样本；未接入搜索引擎或 AI 平台自动抓取，也不会生成虚构可见性分数。</p>
    </section>`;
    const configForm = root.querySelector('#geoConfigForm'); const snapshotForm = root.querySelector('#geoSnapshotForm');
    const render = () => {
      root.querySelector('#geoQueryCount').textContent = `${state.queries.length} / 20`;
      root.querySelector('#geoSnapshotCount').textContent = `${state.snapshots.length} 条`;
      root.querySelector('#geoTaskCount').textContent = `${state.tasks.length} 项`;
      root.querySelector('#geoSnapshotEmpty').hidden = state.snapshots.length > 0;
      snapshotForm.hidden = !state.configured;
      configForm.elements.product.value = state.product; configForm.elements.market.value = state.market; configForm.elements.language.value = state.language; configForm.elements.queries.value = state.queries.map((item) => item.text).join('\n');
      const querySelect = snapshotForm.elements.query; querySelect.replaceChildren(...state.queries.map((item) => { const option = document.createElement('option'); option.value = item.id || item.queryId; option.textContent = item.text; return option; }));
      root.querySelector('#geoSnapshotList').innerHTML = state.snapshots.slice().reverse().map((item) => `<article class="geo-record"><div><strong>${escape(item.queryText)}</strong><small>${escape(item.platform)} · ${escape(item.observedAt)} · ${escape(item.visibilityLabel)}</small></div><p>${escape(item.observation)}</p>${item.citation ? `<a href="${escape(item.citation)}" target="_blank" rel="noreferrer">查看引用</a>` : ''}</article>`).join('');
      root.querySelector('#geoTaskList').innerHTML = state.tasks.length ? state.tasks.slice().reverse().map((task) => `<article class="geo-task"><span class="status-badge">待确认</span><div><strong>${escape(task.title)}</strong><small>来源：${escape(task.platform)} · ${escape(task.observedAt)}</small></div></article>`).join('') : '<div class="panel-empty">出现“未出现”或“无法判断”时，这里会生成待确认任务。</div>';
      onIcons();
    };
    configForm.addEventListener('submit', async (event) => { event.preventDefault(); const data = new FormData(configForm); const queries = String(data.get('queries') || '').split('\n').map((text) => text.trim()).filter(Boolean).slice(0, 20); if (!queries.length) return; const input = { product: String(data.get('product')).trim(), market: String(data.get('market')).trim(), language: String(data.get('language')).trim(), queries }; try { const querySet = api ? await api.createGeoQuerySet(input) : { querySetId: `local-${Date.now()}`, createdAt: new Date().toISOString(), version: 1, ...input, queries: queries.map((text, index) => ({ queryId: `query-${index + 1}`, text })) }; state.querySetId = querySet.querySetId; state.product = querySet.product; state.market = querySet.market; state.language = querySet.language; state.queries = querySet.queries; state.configured = true; state.snapshots = []; state.tasks = []; onToast(`已保存 ${queries.length} 条固定查询`); render(); } catch (error) { onToast(describeError(error)); } });
    snapshotForm.addEventListener('submit', async (event) => { event.preventDefault(); if (!state.configured) return; const data = new FormData(snapshotForm); const query = state.queries.find((item) => (item.id || item.queryId) === data.get('query')); const visibility = String(data.get('visibility')); const rawCitation = String(data.get('citation') || '').trim(); const citation = safeCitation(rawCitation); if (rawCitation && !citation) { onToast('引用 URL 仅支持 http 或 https'); return; } const labels = { mentioned: '已出现', not_mentioned: '未出现', unclear: '无法判断' }; const input = { platform: String(data.get('platform')), queryId: query?.queryId || query?.id, visibility, observation: String(data.get('observation')).trim(), observedAt: String(data.get('observedAt')), citation }; try { const result = api ? await api.createGeoSnapshot(state.querySetId, input) : { snapshot: { snapshotId: `snapshot-${Date.now()}`, querySetId: state.querySetId, createdAt: new Date().toISOString(), ...input, queryText: query?.text || '' }, task: visibility === 'mentioned' ? null : { taskId: `task-${Date.now()}`, snapshotId: `snapshot-${Date.now()}`, querySetId: state.querySetId, createdAt: new Date().toISOString(), status: 'needs_review', title: `补充“${query?.text || ''}”相关内容与证据`, platform: input.platform, observedAt: input.observedAt } }; const record = { ...result.snapshot, visibilityLabel: labels[visibility] }; state.snapshots.push(record); if (result.task) state.tasks.push(result.task); snapshotForm.reset(); snapshotForm.elements.observedAt.value = today(); onToast('已保存观测快照'); render(); } catch (error) { onToast(describeError(error)); } });
    snapshotForm.elements.observedAt.value = today();
    render();
    if (api) api.listGeoQuerySets().then(async ({ querySets }) => { const latest = querySets.at(-1); if (!latest) return; state.querySetId = latest.querySetId; state.product = latest.product; state.market = latest.market; state.language = latest.language; state.queries = latest.queries; state.configured = true; const result = await api.listGeoSnapshots(latest.querySetId); state.snapshots = result.snapshots.map((item) => ({ ...item, visibilityLabel: { mentioned: '已出现', not_mentioned: '未出现', unclear: '无法判断' }[item.visibility] })); state.tasks = result.tasks; render(); }).catch((error) => onToast(describeError(error)));
  }

  globalScope.MarketOpsGeoWorkbench = { mount };
}(typeof globalThis !== 'undefined' ? globalThis : window));
