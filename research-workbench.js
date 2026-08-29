/* Brief -> cited research -> reviewable proposal draft. */
(function researchWorkbench(globalScope) {
  'use strict';

  const LABELS = { fact: '事实', research_observation: '研究观察', hypothesis: '假设', unknown: '未知', low: '低', medium: '中', high: '高' };
  const setText = (node, value) => { node.textContent = value == null ? '' : String(value); return node; };
  function element(tag, className, value) { const node = document.createElement(tag); if (className) node.className = className; if (value != null) setText(node, value); return node; }

  function setBusy(form, busy, label) {
    form.setAttribute('aria-busy', busy ? 'true' : 'false');
    form.querySelectorAll('button, input, textarea, select').forEach((control) => { control.disabled = busy; });
    const submit = form.querySelector('button[type="submit"]');
    if (!submit) return;
    if (!submit.dataset.defaultLabel) submit.dataset.defaultLabel = submit.textContent.trim();
    setText(submit, busy ? label : submit.dataset.defaultLabel);
  }

  function renderBriefState(root, brief) {
    root.replaceChildren();
    root.append(
      element('strong', null, brief.status === 'ready' ? 'Brief 已就绪' : '需要补充信息'),
      element('p', null, brief.missingQuestions.length ? brief.missingQuestions.join('；') : '核心信息完整，可以添加首条研究来源。'),
    );
  }

  function appendList(root, title, values) {
    const section = element('section', 'draft-section');
    const list = element('ul');
    section.append(element('h3', null, title));
    (Array.isArray(values) && values.length ? values : ['尚未提供']).forEach((value) => list.append(element('li', null, value)));
    section.append(list);
    root.append(section);
  }

  function renderDraft(root, draft, run) {
    root.replaceChildren();
    const summary = element('div', 'draft-summary');
    summary.append(
      element('strong', null, `方案草案 v${draft.version}`),
      element('span', `status-badge draft-status draft-status-${draft.status}`, draft.status === 'approved' ? '已批准' : draft.status === 'rejected' ? '已拒绝' : draft.status === 'needs_revision' ? '待修改' : '待审核'),
      element('p', null, `${draft.sections.market} · ${draft.sections.audience}`),
    );
    const objective = element('section', 'draft-section draft-objective');
    objective.append(element('h3', null, '业务目标'), element('p', null, draft.sections.objective));
    const evidence = element('section', 'draft-section');
    const claims = element('div', 'claim-list');
    evidence.append(element('h3', null, '有来源的判断'));
    (draft.sections.positioning || []).forEach((item) => {
      const row = element('article', 'claim-row'); const meta = element('div', 'claim-meta');
      meta.append(element('span', `claim-type claim-type-${item.classification}`, LABELS[item.classification] || item.classification), element('span', 'claim-confidence', `${LABELS[item.confidence] || item.confidence}置信度`), element('span', 'claim-source-count', `${item.sourceIds.length} 个引用`));
      row.append(meta, element('p', null, item.text));
      (item.sources || []).forEach((source) => {
        const citation = element('details', 'claim-source');
        const summary = element('summary', null, `来源：${source.title}`);
        const sourceBody = element('div', 'claim-source-body');
        const link = element('a', null, source.url); link.href = source.url; link.target = '_blank'; link.rel = 'noreferrer';
        sourceBody.append(link, element('p', null, source.excerpt), element('small', null, `${source.observedAt} · ${source.scope} · ${LABELS[source.confidence] || source.confidence}置信度`));
        citation.append(summary, sourceBody); row.append(citation);
      });
      claims.append(row);
    });
    if (!claims.children.length) claims.append(element('p', 'research-muted', '当前没有形成研究判断。'));
    evidence.append(claims);
    const grid = element('div', 'draft-section-grid');
    appendList(grid, '建议动作', draft.sections.contentIdeas); appendList(grid, '依赖', draft.sections.dependencies); appendList(grid, '风险', draft.sections.risks); appendList(grid, '待确认', draft.sections.unknowns);
    const receipt = element('div', 'research-receipt');
    receipt.append(element('span', null, `${run.sourceCount} 个来源`), element('span', null, `${run.observations.length} 条观察`), element('span', null, '未修改正式项目'));
    root.append(summary, objective, evidence, grid, receipt);
    if (draft.decision) { const decision = element('div', 'decision-receipt'); decision.append(element('strong', null, '人工决定'), element('p', null, draft.decision.reason)); root.append(decision); }
  }

  function mount({ root, api, onIcons, onToast, describeError }) {
    let brief = null; let run = null; let draft = null;
    root.innerHTML = `<section class="module-surface research-workbench" aria-labelledby="researchTitle"><header class="page-heading research-heading"><div><span class="console-label">BRIEF / RESEARCH / DRAFT</span><h1 id="researchTitle">研究与情报</h1><p>把 Brief、来源判断和方案草案放在同一条可审核流程中。</p></div><span class="status-badge"><i data-lucide="database"></i>本地记录</span></header><ol class="research-progress" aria-label="研究流程"><li data-progress="brief" class="is-active"><span>1</span><strong>明确 Brief</strong><small>去标识输入</small></li><li data-progress="source"><span>2</span><strong>添加证据</strong><small>来源与判断</small></li><li data-progress="review"><span>3</span><strong>审核草案</strong><small>人工决定</small></li></ol><div class="research-grid"><form class="research-form" id="researchBriefForm"><div class="research-panel-heading"><div><span class="console-label">INPUT</span><h2>项目 Brief</h2></div><span class="required-note">均为必填</span></div><label>产品名称<input name="productName" required maxlength="4000" placeholder="例如：工业连接器方案"></label><label>产品类型<input name="productType" required maxlength="4000" placeholder="B2B 制造业产品"></label><div class="research-field-row"><label>目标市场<input name="targetMarket" required maxlength="4000" placeholder="印度，英语"></label><label>时间范围<input name="timeframe" required maxlength="4000" placeholder="未来十周"></label></div><label>目标受众<input name="audience" required maxlength="4000" placeholder="制造业采购负责人"></label><label>业务目标<input name="objective" required maxlength="4000" placeholder="形成首轮市场进入方案"></label><label>背景<textarea name="background" required maxlength="4000" rows="3" placeholder="说明当前情况和本轮要解决的问题"></textarea></label><label>约束<textarea name="constraints" maxlength="4000" rows="3" placeholder="每行一条，例如：不使用真实客户资料"></textarea></label><label class="approval-check"><input type="checkbox" name="deidentified" required><span><strong>已完成去标识</strong><small>不含真实联系人、客户名、邮箱、密钥或报价。</small></span></label><button class="button button-primary research-submit" type="submit"><i data-lucide="save"></i>保存并检查 Brief</button></form><section class="research-canvas"><section class="research-step is-active" id="clarifyStep"><div class="research-step-index"><span>01</span><i data-lucide="clipboard-check"></i></div><div><span class="console-label">CLARIFY</span><h2>完整性检查</h2><div id="briefState" class="research-state" role="status" aria-live="polite">填写左侧 Brief 后开始检查。</div></div></section><form class="research-step research-source-step" id="researchRunForm" hidden><div class="research-step-index"><span>02</span><i data-lucide="scan-search"></i></div><div class="research-step-body"><span class="console-label">SOURCE &amp; OBSERVATION</span><h2>来源与研究判断</h2><p class="field-help">当前版本不自动抓取全网。请添加你已核对的公开来源，系统会保留时间、范围和置信度。</p><fieldset><legend>来源证据</legend><label>公开 URL<input name="url" type="url" required maxlength="1000" placeholder="https://example.com/source"></label><label>来源标题<input name="title" required maxlength="300" placeholder="页面或报告标题"></label><label>关键摘录<textarea name="excerpt" required maxlength="2000" rows="3" placeholder="与判断直接相关的原文摘录"></textarea></label><div class="research-field-row"><label>观察时间<input name="observedAt" type="date" required></label><label>适用范围<input name="scope" required maxlength="500" placeholder="国家、行业或时间范围"></label></div></fieldset><fieldset><legend>你的研究判断</legend><label>判断内容<textarea name="claim" required maxlength="1200" rows="3" placeholder="这条来源支持什么判断？"></textarea></label><div class="research-field-row"><label>信息类型<select name="classification"><option value="research_observation">研究观察</option><option value="fact">事实</option><option value="hypothesis">假设</option><option value="unknown">未知</option></select></label><label>置信度<select name="confidence"><option value="medium">中等</option><option value="high">高</option><option value="low">低</option></select></label></div></fieldset><button class="button button-primary research-submit" type="submit"><i data-lucide="sparkles"></i>生成可审核草案</button></div></form><section class="research-step research-review-step" id="draftStep" hidden><div class="research-step-index"><span>03</span><i data-lucide="file-check-2"></i></div><div class="research-step-body"><span class="console-label">HUMAN REVIEW</span><h2>方案草案</h2><div id="draftState" class="research-state" aria-live="polite"></div><form id="draftDecisionForm" class="decision-form"><label for="decisionReason">审核理由</label><textarea id="decisionReason" name="reason" required maxlength="2000" rows="2" placeholder="记录批准依据，或说明需要修改的内容"></textarea><div class="decision-actions"><button class="button button-primary" data-decision="approve" type="submit"><i data-lucide="check"></i>批准</button><button class="button button-quiet" data-decision="revise" type="submit"><i data-lucide="pencil"></i>要求修改</button><button class="button button-quiet button-danger-soft" data-decision="reject" type="submit"><i data-lucide="x"></i>拒绝</button></div></form></div></section></section></div><p class="claim-boundary"><i data-lucide="shield-check"></i><span><strong>人工控制边界</strong> 方案只进入草案审核，不会自动修改正式项目事实、预算、日期或 WBS。</span></p></section>`;
    onIcons();
    const briefForm = root.querySelector('#researchBriefForm'); const runForm = root.querySelector('#researchRunForm'); const decisionForm = root.querySelector('#draftDecisionForm'); const briefState = root.querySelector('#briefState'); const draftStep = root.querySelector('#draftStep'); const draftState = root.querySelector('#draftState'); const progressItems = root.querySelectorAll('[data-progress]');
    const showError = (error, target = briefState) => { const message = describeError(error); onToast(message); target.replaceChildren(element('strong', 'research-error', message)); };
    const advance = (step) => { const index = ['brief', 'source', 'review'].indexOf(step); progressItems.forEach((item, position) => { item.classList.toggle('is-active', position === index); item.classList.toggle('is-complete', position < index); }); };
    briefForm.addEventListener('submit', async (event) => { event.preventDefault(); const data = new FormData(briefForm); setBusy(briefForm, true, '正在保存...'); briefState.replaceChildren(element('span', 'research-loading', '正在检查 Brief 完整性...')); try { brief = await api.createBrief({ deidentified: data.get('deidentified') === 'on', productName: data.get('productName'), productType: data.get('productType'), targetMarket: data.get('targetMarket'), audience: data.get('audience'), objective: data.get('objective'), timeframe: data.get('timeframe'), background: data.get('background'), constraints: String(data.get('constraints') || '').split('\n').map((item) => item.trim()).filter(Boolean) }); renderBriefState(briefState, brief); if (brief.status === 'ready') { runForm.hidden = false; runForm.querySelector('[name="observedAt"]').value = new Date().toISOString().slice(0, 10); advance('source'); onToast('Brief 已保存，可以添加研究来源'); } } catch (error) { showError(error); } finally { setBusy(briefForm, false, '正在保存...'); onIcons(); } });
    runForm.addEventListener('submit', async (event) => { event.preventDefault(); const data = new FormData(runForm); setBusy(runForm, true, '正在生成...'); run = null; draft = null; draftStep.hidden = true; decisionForm.hidden = true; draftState.replaceChildren(element('span', 'research-loading', '正在生成草案...')); try { run = await api.createResearchRun({ briefId: brief.briefId, sources: [{ url: data.get('url'), title: data.get('title'), excerpt: data.get('excerpt'), observedAt: data.get('observedAt'), scope: data.get('scope'), confidence: data.get('confidence') }], observations: [{ claim: data.get('claim'), classification: data.get('classification'), confidence: data.get('confidence'), nextAction: '人工核对来源适用范围' }] }); draft = await api.createProposalDraft({ briefId: brief.briefId, researchRunId: run.runId }); draftStep.hidden = false; decisionForm.hidden = false; renderDraft(draftState, draft, run); advance('review'); onToast('草案已生成，请人工审核'); } catch (error) { showError(error, draftState); if (!draft) { draftStep.hidden = true; decisionForm.hidden = true; } } finally { setBusy(runForm, false, '正在生成...'); onIcons(); } });
    decisionForm.addEventListener('submit', async (event) => { event.preventDefault(); const submitter = event.submitter; if (!submitter || !draft) return; const reason = String(new FormData(decisionForm).get('reason') || '').trim(); if (!reason) { decisionForm.querySelector('[name="reason"]').focus(); onToast('请先填写审核理由'); return; } setBusy(decisionForm, true, '正在提交...'); try { draft = await api.decideProposalDraft(draft.draftId, { expectedVersion: draft.version, action: submitter.dataset.decision, reason }); renderDraft(draftState, draft, run); decisionForm.hidden = true; onToast(draft.status === 'approved' ? '草案已批准' : draft.status === 'rejected' ? '草案已拒绝' : '草案已标记为待修改'); } catch (error) { showError(error, draftState); } finally { setBusy(decisionForm, false, '正在提交...'); onIcons(); } });
  }
  globalScope.MarketOpsResearchWorkbench = { mount };
}(typeof globalThis !== 'undefined' ? globalThis : window));
