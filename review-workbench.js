/* finesse · review state machine · cited candidates · reconcile on conflict · no model decisions */
(function reviewWorkbenchModule(globalScope) {
  'use strict';

  const KIND_LABELS = Object.freeze({
    deliverable: '交付物',
    milestone: '里程碑',
    constraint: '约束',
    assumption: '假设',
  });
  const STATUS_LABELS = Object.freeze({
    pending: '待处理',
    approve: '已接受',
    modify: '已修改',
    reject: '已拒绝',
  });

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  function reviewMetrics(candidates) {
    const metrics = { total: candidates.length, pending: 0, approve: 0, modify: 0, reject: 0 };
    candidates.forEach((candidate) => { metrics[candidate.review.status] += 1; });
    return metrics;
  }

  function describeLocation(location) {
    const descriptions = {
      line_range: () => `第 ${location.startLine}-${location.endLine} 行`,
      csv_range: () => `第 ${location.startRow}-${location.endRow} 行`,
      docx_paragraph: () => `${location.part} / 正文 ${location.bodyIndex} / 段落 ${location.paragraph}`,
      docx_table: () => `${location.part} / 正文 ${location.bodyIndex} / 表格 ${location.table}`,
      markdown_table_cell: () => `第 ${location.line} 行 / 第 ${location.columnIndex} 列`,
      csv_cell: () => `第 ${location.row} 行 / ${location.columnName || `第 ${location.columnIndex} 列`}`,
      docx_table_cell: () => `${location.part} / 表格 ${location.table} / ${location.row} 行 ${location.column} 列`,
    };
    return descriptions[location.kind]?.() || '位置不可识别';
  }

  function runRequestIdentity(project) {
    return `marketops.review.request.v1:${project.projectId}:${project.proposal.versionId}:${project.proposal.sha256}`;
  }

  function getStableRunKey(project, requestKeys, cryptoImpl) {
    const identity = runRequestIdentity(project);
    let value = requestKeys.get(identity) || '';
    if (value.length < 8 || value.length > 200) {
      value = `review-${cryptoImpl.randomUUID()}`;
      requestKeys.set(identity, value);
    }
    return value;
  }

  function clearStableRunKey(project, requestKeys) {
    requestKeys.delete(runRequestIdentity(project));
  }

  function isReconciledRun(loadSucceeded, detail, expectedRunId) {
    return loadSucceeded === true && detail?.run?.runId === expectedRunId;
  }

  function createReviewWorkbench(options) {
    const { api, root, cryptoImpl = globalScope.crypto,
      describeError = () => '请求失败。', onToast = () => {}, onIcons = () => {}, onContext = () => {} } = options;
    if (!api || !root || !cryptoImpl?.randomUUID) throw new Error('Review workbench dependencies are incomplete.');

    const elements = {
      console: root.querySelector('#reviewConsole'), status: root.querySelector('#reviewStatus'),
      statusText: root.querySelector('#reviewStatusText'), create: root.querySelector('#createReview'),
      refresh: root.querySelector('#refreshReviews'), runList: root.querySelector('#runList'),
      runCount: root.querySelector('#runCount'), candidateList: root.querySelector('#candidateList'),
      decision: root.querySelector('#decisionContent'), version: root.querySelector('#reviewVersionSelect'),
      total: root.querySelector('#metricTotal'), pending: root.querySelector('#metricPending'),
      approved: root.querySelector('#metricApproved'), modified: root.querySelector('#metricModified'),
      rejected: root.querySelector('#metricRejected'),
    };
    const state = { project: null, runs: [], detail: null, candidateId: null, filter: 'all', busy: false };
    // Keep uncertain create retries stable for this page without persisting browser state.
    const requestKeys = new Map();

    function setStatus(message, status = 'idle') {
      elements.status.dataset.state = status;
      elements.statusText.textContent = message;
    }

    function setBusy(busy, message) {
      state.busy = busy;
      elements.console.setAttribute('aria-busy', busy ? 'true' : 'false');
      elements.create.disabled = busy || !state.project;
      elements.refresh.disabled = busy || !state.project;
      if (message) setStatus(message, 'loading');
    }

    function renderMetrics(candidates = []) {
      const metrics = reviewMetrics(candidates);
      elements.total.textContent = metrics.total;
      elements.pending.textContent = metrics.pending;
      elements.approved.textContent = metrics.approve;
      elements.modified.textContent = metrics.modify;
      elements.rejected.textContent = metrics.reject;
    }

    function resetPanels(message = '恢复项目后，可读取既有记录或发起一次确定性提取。') {
      state.runs = [];
      state.detail = null;
      state.candidateId = null;
      elements.runCount.textContent = '0';
      elements.runList.innerHTML = '<div class="panel-empty">尚无提取记录</div>';
      elements.candidateList.innerHTML = `<div class="panel-empty panel-empty-large"><i data-lucide="scan-text"></i><strong>等待服务器候选</strong><span>${escapeHtml(message)}</span></div>`;
      elements.decision.innerHTML = '<div class="panel-empty panel-empty-large"><i data-lucide="mouse-pointer-2"></i><strong>选择一个候选</strong><span>这里会显示引用、来源位置和人工决定。</span></div>';
      elements.version.innerHTML = '<option>版本 -</option>';
      elements.version.disabled = true;
      renderMetrics();
      onContext('尚未选择候选');
      onIcons();
    }

    function renderRuns() {
      elements.runCount.textContent = String(state.runs.length);
      if (!state.runs.length) {
        elements.runList.innerHTML = '<div class="panel-empty">尚无提取记录</div>';
        return;
      }
      const activeId = state.detail?.run.runId;
      elements.runList.innerHTML = state.runs.map((run, index) => `
        <button type="button" class="run-item${run.runId === activeId ? ' is-active' : ''}" data-run-id="${escapeHtml(run.runId)}">
          <span><b>RUN ${String(state.runs.length - index).padStart(2, '0')}</b><em>v${run.latestReviewVersion}</em></span>
          <strong>${run.candidateCount} 个候选</strong>
          <small>${escapeHtml(new Date(run.createdAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }))}</small>
        </button>`).join('');
    }

    function currentCandidate() {
      return state.detail?.candidates.find((candidate) => candidate.candidateId === state.candidateId) || null;
    }

    function renderCandidates() {
      if (!state.detail) return;
      const candidates = state.detail.candidates.filter((candidate) => state.filter === 'all' || candidate.review.status === state.filter);
      if (!candidates.length) {
        elements.candidateList.innerHTML = '<div class="panel-empty panel-empty-large"><strong>没有符合筛选条件的候选</strong><span>切换到“全部”查看完整快照。</span></div>';
        return;
      }
      elements.candidateList.innerHTML = candidates.map((candidate) => `
        <button type="button" class="candidate-item${candidate.candidateId === state.candidateId ? ' is-active' : ''}" data-candidate-id="${escapeHtml(candidate.candidateId)}">
          <span class="candidate-index">${String(candidate.ordinal).padStart(2, '0')}</span>
          <span class="candidate-copy"><span><em class="kind-badge kind-${candidate.kind}">${KIND_LABELS[candidate.kind]}</em><em class="classification-badge">${candidate.classification}</em></span><strong>${escapeHtml(candidate.review.replacementText || candidate.text)}</strong><small>${escapeHtml(candidate.sourceCitation.sectionPath.join(' / ') || '未命名章节')}</small></span>
          <span class="review-badge status-${candidate.review.status}">${STATUS_LABELS[candidate.review.status]}</span>
        </button>`).join('');
    }

    function renderVersionSelect() {
      const latest = state.detail.run.latestReviewVersion;
      elements.version.innerHTML = state.detail.availableReviewVersions.map((version) => `<option value="${version}"${version === state.detail.selectedReviewVersion ? ' selected' : ''}>v${version}${version === latest ? ' · 最新' : ' · 历史'}</option>`).join('');
      elements.version.disabled = state.busy;
    }

    function decisionForm(candidate) {
      const historical = state.detail.selectedReviewVersion !== state.detail.run.latestReviewVersion;
      if (historical) return '<div class="history-notice"><i data-lucide="history"></i><div><strong>正在查看不可变历史版本</strong><p>切换到最新版本后才能提交新的人工决定。</p></div></div>';
      return `<form class="decision-form" id="decisionForm">
        <input type="hidden" name="candidateId" value="${escapeHtml(candidate.candidateId)}" />
        <fieldset><legend>人工决定</legend><div class="decision-actions">
          <label><input type="radio" name="action" value="approve" checked /><span><i data-lucide="check"></i>接受</span></label>
          <label><input type="radio" name="action" value="modify" /><span><i data-lucide="pencil"></i>修改</span></label>
          <label><input type="radio" name="action" value="reject" /><span><i data-lucide="x"></i>拒绝</span></label>
        </div></fieldset>
        <label class="replacement-field" hidden>替换文本<textarea name="replacementText" rows="4" maxlength="10000">${escapeHtml(candidate.review.replacementText || candidate.text)}</textarea></label>
        <label>决定理由<textarea name="reason" rows="3" maxlength="2000" required placeholder="记录接受、修改或拒绝的依据"></textarea></label>
        <label>补充说明（可选）<textarea name="comment" rows="2" maxlength="4000" placeholder="仅记录与本次决定有关的信息"></textarea></label>
        <button class="button button-primary decision-submit" type="submit"><i data-lucide="send"></i>提交为 v${state.detail.run.latestReviewVersion + 1}</button>
      </form>`;
    }

    function renderDecision() {
      const candidate = currentCandidate();
      if (!candidate) {
        elements.decision.innerHTML = '<div class="panel-empty panel-empty-large"><i data-lucide="mouse-pointer-2"></i><strong>选择一个候选</strong><span>这里会显示引用、来源位置和人工决定。</span></div>';
        onContext('尚未选择候选');
        onIcons();
        return;
      }
      const citation = candidate.sourceCitation;
      elements.decision.innerHTML = `
        <article class="candidate-detail">
          <div class="detail-labels"><span class="kind-badge kind-${candidate.kind}">${KIND_LABELS[candidate.kind]}</span><span class="classification-badge">${candidate.classification}</span><span class="confidence">置信度 ${Math.round(candidate.confidence * 100)}%</span></div>
          <h4>${escapeHtml(candidate.text)}</h4>
          ${candidate.review.replacementText ? `<div class="replacement-preview"><span>人工修改文本</span><p>${escapeHtml(candidate.review.replacementText)}</p></div>` : ''}
          <section class="citation-card"><div><span class="eyebrow">SOURCE CITATION</span><strong>${escapeHtml(citation.sectionPath.join(' / ') || '未命名章节')}</strong><small>${escapeHtml(describeLocation(citation.location))}</small></div><blockquote>${escapeHtml(citation.quote)}</blockquote><code title="来源 SHA-256">${escapeHtml(citation.sourceSha256.slice(0, 12))}...</code></section>
          ${candidate.review.lastDecision ? `<div class="last-decision"><span>上次人工决定 · v${candidate.review.lastDecision.reviewVersion}</span><strong>${STATUS_LABELS[candidate.review.status]}</strong><p>${escapeHtml(candidate.review.lastDecision.reason)}</p></div>` : ''}
        </article>
        ${decisionForm(candidate)}`;
      onContext(`${KIND_LABELS[candidate.kind]} · ${candidate.classification} · ${STATUS_LABELS[candidate.review.status]}`);
      onIcons();
    }

    function renderDetail(detail, preferredCandidateId) {
      state.detail = detail;
      const runIndex = state.runs.findIndex((run) => run.runId === detail.run.runId);
      if (runIndex >= 0) state.runs.splice(runIndex, 1, detail.run);
      state.candidateId = detail.candidates.some((candidate) => candidate.candidateId === preferredCandidateId)
        ? preferredCandidateId : detail.candidates[0]?.candidateId || null;
      renderRuns();
      renderMetrics(detail.candidates);
      renderVersionSelect();
      renderCandidates();
      renderDecision();
      elements.console.dataset.state = 'ready';
    }

    async function loadDetail(runId, reviewVersion, options = {}) {
      setBusy(true, options.message || '正在读取完整候选快照...');
      try {
        const request = reviewVersion === undefined ? {} : { reviewVersion };
        const detail = await api.getReview(state.project.projectId, runId, request);
        renderDetail(detail, options.candidateId || state.candidateId);
        setStatus(detail.selectedReviewVersion === detail.run.latestReviewVersion
          ? `已读取最新审核版本 v${detail.selectedReviewVersion}。`
          : `正在查看历史版本 v${detail.selectedReviewVersion}；该快照不可修改。`, 'ready');
        return true;
      } catch (error) {
        setStatus(describeError(error), 'error');
        return false;
      } finally {
        setBusy(false);
        if (state.detail) renderVersionSelect();
      }
    }

    async function loadRuns(options = {}) {
      if (!state.project) return;
      setBusy(true, options.message || '正在读取服务器审阅记录...');
      try {
        const response = await api.listReviewRuns(state.project.projectId, { limit: 20 });
        state.runs = response.runs;
        renderRuns();
        if (!state.runs.length) {
          resetPanels('当前方案还没有提取记录。点击“提取候选”开始。');
          elements.console.dataset.state = 'empty';
          setStatus('当前方案还没有提取记录。', 'empty');
          return true;
        }
        const requestedRunId = options.runId || state.runs[0].runId;
        const loaded = await loadDetail(requestedRunId, undefined, { message: '正在读取最新审核版本...' });
        return isReconciledRun(loaded, state.detail, requestedRunId);
      } catch (error) {
        elements.console.dataset.state = 'error';
        setStatus(describeError(error), 'error');
        return false;
      } finally {
        setBusy(false);
      }
    }

    async function createRun() {
      if (!state.project || state.busy) return;
      setBusy(true, '正在从已批准方案提取引用候选...');
      try {
        const result = await api.createReviewRun(state.project.projectId, {
          expectedProposalVersionId: state.project.proposal.versionId,
          expectedProposalSha256: state.project.proposal.sha256,
        }, { idempotencyKey: getStableRunKey(state.project, requestKeys, cryptoImpl) });
        const reconciled = await loadRuns({ runId: result.runId, message: result.replayed ? '正在核对已存在的同一提取记录...' : '提取已提交，正在读取完整快照...' });
        if (!reconciled) return;
        clearStableRunKey(state.project, requestKeys);
        onToast(result.replayed ? '已安全重放同一提取记录，没有创建重复 run' : `已提取 ${result.candidateCount} 个引用候选`);
      } catch (error) {
        setStatus(describeError(error), 'error');
      } finally {
        setBusy(false);
      }
    }

    async function reconcileDecision(error, candidateId) {
      const isConflict = error?.code === 'REVIEW_CONFLICT' || error?.status === 409;
      if (!isConflict && error?.uncertain !== true) throw error;
      await loadDetail(state.detail.run.runId, undefined, { candidateId, message: isConflict ? '版本冲突，正在读取服务器最新事实...' : '提交结果不确定，正在与服务器对账...' });
      const candidate = currentCandidate();
      if (candidate?.review.status !== 'pending') {
        setStatus('已从服务器确认最新决定，未盲目重复提交。', 'ready');
      } else if (isConflict) {
        setStatus('其他决定已先提交。已更新到最新版本，请核对后重新决定。', 'conflict');
      } else {
        setStatus('服务器未确认本次决定。已保持最新事实，请核对后再操作。', 'error');
      }
    }

    async function submitDecision(form) {
      if (state.busy || !state.detail) return;
      const data = new FormData(form);
      const candidateId = String(data.get('candidateId'));
      const action = String(data.get('action'));
      const input = {
        expectedReviewVersion: state.detail.run.latestReviewVersion,
        candidateId,
        action,
        reason: String(data.get('reason') || '').trim(),
      };
      const comment = String(data.get('comment') || '').trim();
      if (comment) input.comment = comment;
      if (action === 'modify') input.replacementText = String(data.get('replacementText') || '').trim();
      if (!input.reason || (action === 'modify' && !input.replacementText)) {
        setStatus(action === 'modify' ? '修改决定需要替换文本和理由。' : '请填写决定理由。', 'error');
        return;
      }
      setBusy(true, `正在提交人工决定并创建 v${input.expectedReviewVersion + 1}...`);
      try {
        await api.decideReview(state.project.projectId, state.detail.run.runId, input);
        await loadDetail(state.detail.run.runId, undefined, { candidateId, message: '决定已提交，正在读取服务器最新版本...' });
        onToast(`人工决定已记录为 v${input.expectedReviewVersion + 1}`);
      } catch (error) {
        try { await reconcileDecision(error, candidateId); } catch (unreconciled) { setStatus(describeError(unreconciled), 'error'); }
      } finally {
        setBusy(false);
      }
    }

    elements.create.addEventListener('click', createRun);
    elements.refresh.addEventListener('click', () => loadRuns({ message: '正在刷新服务器审阅记录...' }));
    elements.runList.addEventListener('click', (event) => {
      const button = event.target.closest('[data-run-id]');
      if (button && !state.busy) loadDetail(button.dataset.runId);
    });
    elements.candidateList.addEventListener('click', (event) => {
      const button = event.target.closest('[data-candidate-id]');
      if (!button) return;
      state.candidateId = button.dataset.candidateId;
      renderCandidates();
      renderDecision();
    });
    elements.version.addEventListener('change', () => {
      if (state.detail) loadDetail(state.detail.run.runId, Number(elements.version.value), { candidateId: state.candidateId });
    });
    root.querySelectorAll('[data-review-filter]').forEach((button) => button.addEventListener('click', () => {
      state.filter = button.dataset.reviewFilter;
      root.querySelectorAll('[data-review-filter]').forEach((item) => item.classList.toggle('is-active', item === button));
      renderCandidates();
    }));
    elements.decision.addEventListener('change', (event) => {
      if (event.target.name !== 'action') return;
      const replacement = elements.decision.querySelector('.replacement-field');
      if (replacement) replacement.hidden = event.target.value !== 'modify';
    });
    elements.decision.addEventListener('submit', (event) => {
      if (event.target.id !== 'decisionForm') return;
      event.preventDefault();
      submitDecision(event.target);
    });

    resetPanels();
    return {
      async setProject(project) {
        state.project = project;
        elements.create.disabled = false;
        elements.refresh.disabled = false;
        elements.console.dataset.state = 'loading';
        await loadRuns();
      },
      clearProject() {
        state.project = null;
        elements.create.disabled = true;
        elements.refresh.disabled = true;
        elements.console.dataset.state = 'unavailable';
        resetPanels();
        setStatus('请先恢复或创建一个服务器项目。', 'idle');
      },
      snapshot() { return { ...state, runs: [...state.runs] }; },
    };
  }

  globalScope.MarketOpsReviewWorkbench = Object.freeze({
    clearStableRunKey, createReviewWorkbench, describeLocation, escapeHtml, getStableRunKey, isReconciledRun, reviewMetrics, runRequestIdentity,
  });
})(typeof window === 'undefined' ? globalThis : window);
