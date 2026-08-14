/* finesse 路 canvas-first WBS editor 路 deterministic schedule states 路 no invented approvals */
(function scheduleWorkbenchModule(globalScope) {
  'use strict';

  const STATUS_LABELS = Object.freeze({
    not_started: '未开始', in_progress: '进行中', blocked: '阻塞', completed: '已完成', cancelled: '已取消',
  });

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  function calendarDate(value) {
    return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '';
  }

  function createScheduleWorkbench(options) {
    const { api, root, onToast = () => {}, onIcons = () => {}, getReviewDetail = () => null } = options;
    if (!api || !root) throw new Error('Schedule workbench dependencies are incomplete.');
    const elements = {
      section: root.querySelector('#scheduleWorkbench'),
      status: root.querySelector('#scheduleStatus'),
      statusText: root.querySelector('#scheduleStatusText'),
      create: root.querySelector('#createWbsPlan'),
      refresh: root.querySelector('#refreshWbsPlan'),
      revise: root.querySelector('#saveWbsRevision'),
      recalculate: root.querySelector('#recalculateSchedule'),
      projectStart: root.querySelector('#scheduleProjectStart'),
      holidays: root.querySelector('#scheduleHolidays'),
      planVersion: root.querySelector('#schedulePlanVersion'),
      taskList: root.querySelector('#wbsTaskList'),
      summary: root.querySelector('#scheduleSummary'),
      planMeta: root.querySelector('#schedulePlanMeta'),
    };
    if (Object.values(elements).some((element) => !element)) throw new Error('Schedule workbench markup is incomplete.');

    const state = { project: null, plan: null, snapshot: null, busy: false, historical: false, changes: new Map() };

    function setStatus(message, kind = 'idle') {
      elements.status.dataset.state = kind;
      elements.statusText.textContent = message;
    }

    function setBusy(busy) {
      state.busy = busy;
      elements.section.setAttribute('aria-busy', busy ? 'true' : 'false');
      [elements.create, elements.refresh, elements.revise, elements.recalculate].forEach((button) => { button.disabled = busy || !state.project; });
      elements.projectStart.disabled = busy || !state.plan;
      elements.holidays.disabled = busy || !state.plan;
    }

    function pendingReview() {
      const detail = getReviewDetail();
      return !detail || detail.selectedReviewVersion !== detail.run.latestReviewVersion
        || detail.candidates.some((candidate) => candidate.review.status === 'pending');
    }

    function reset(message = '先完成一轮人工审核，再从审核快照生成 WBS。') {
      state.plan = null; state.snapshot = null; state.historical = false; state.changes.clear();
      elements.taskList.innerHTML = '<div class="schedule-empty"><i data-lucide="git-branch"></i><strong>尚未载入 WBS</strong><span>' + escapeHtml(message) + '</span></div>';
      elements.summary.innerHTML = '<div class="schedule-empty"><strong>等待排期快照</strong><span>排期会基于明确的项目开始日和节假日计算。</span></div>';
      elements.planMeta.textContent = '未创建计划';
      elements.planVersion.innerHTML = '<option value="">版本 -</option>';
      elements.planVersion.disabled = true;
      elements.create.disabled = !state.project || pendingReview();
      elements.refresh.disabled = true;
      elements.revise.disabled = true;
      elements.recalculate.disabled = true;
      setStatus(message, state.project ? 'empty' : 'idle');
      onIcons();
    }

    function renderPlan() {
      if (!state.plan) return reset();
      elements.planMeta.textContent = `WBS v${state.plan.selectedPlanVersion} · ${state.plan.tasks.length} 个任务 · ${state.plan.controls.length} 个控制项`;
      elements.planVersion.innerHTML = state.plan.availablePlanVersions.map((version) => `<option value="${version}"${version === state.plan.selectedPlanVersion ? ' selected' : ''}>v${version}${version === state.plan.selectedPlanVersion ? ' · 当前' : ' · 历史'}</option>`).join('');
      elements.planVersion.disabled = state.busy;
      elements.taskList.innerHTML = state.plan.tasks.map((task, index) => {
        const changes = state.changes.get(task.taskId) || {};
        const current = (key) => changes[key] ?? task[key] ?? '';
        const disabled = state.historical ? ' disabled' : '';
        return `<article class="wbs-task-row${changes.title || changes.durationWorkdays || changes.ownerRole ? ' is-dirty' : ''}" data-task-id="${escapeHtml(task.taskId)}">
          <div class="wbs-task-index">${String(index + 1).padStart(2, '0')}</div>
          <div class="wbs-task-main"><div class="wbs-task-title"><span class="kind-badge">${escapeHtml(task.kind)}</span><strong>${escapeHtml(task.taskId)}</strong>${task.isLocked ? '<span class="lock-badge">LOCKED</span>' : ''}</div>
            <label>任务标题<input data-task-field="title" value="${escapeHtml(current('title'))}" maxlength="300"${disabled} /></label>
            <small>${escapeHtml(task.sourceText)}</small></div>
          <label>工期<input data-task-field="durationWorkdays" type="number" min="1" step="1" value="${escapeHtml(current('durationWorkdays'))}"${disabled} /></label>
          <label>负责人<input data-task-field="ownerRole" value="${escapeHtml(current('ownerRole'))}" maxlength="200"${disabled} /></label>
          <div class="wbs-task-date"><span>依赖</span><strong>${task.predecessors.length ? escapeHtml(task.predecessors.join(', ')) : '无'}</strong></div>
          <label>状态<select data-task-field="status"${disabled}>${Object.entries(STATUS_LABELS).map(([value, label]) => `<option value="${value}"${current('status') === value ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
        </article>`;
      }).join('');
      elements.revise.disabled = state.busy || state.historical || state.changes.size === 0;
      elements.recalculate.disabled = state.busy || state.historical;
      renderSummary();
      onIcons();
    }

    function renderSummary() {
      if (!state.snapshot) {
        elements.summary.innerHTML = '<div class="schedule-empty"><strong>等待排期快照</strong><span>点击“重算排期”生成确定性结果。</span></div>';
        return;
      }
      const conflicts = state.snapshot.conflicts.length;
      const misses = state.snapshot.deadlineMisses.length;
      elements.summary.innerHTML = `<div class="schedule-result-head"><span class="status-badge">${state.snapshot.status === 'ready' ? 'READY' : 'NEEDS REVIEW'}</span><strong>${escapeHtml(state.snapshot.projectStart)} 起</strong></div>
        <div class="schedule-metrics"><div><span>任务</span><strong>${state.snapshot.tasks.length}</strong></div><div><span>冲突</span><strong class="${conflicts ? 'has-risk' : ''}">${conflicts}</strong></div><div><span>截止风险</span><strong class="${misses ? 'has-risk' : ''}">${misses}</strong></div></div>
        <div class="schedule-result-note">快照 ${escapeHtml(state.snapshot.scheduleDigest.slice(0, 12))}... · v${state.snapshot.planVersion}<br />排期只反映当前 WBS 与日历输入，仍需人工确认后执行。</div>`;
    }

    async function createPlan() {
      const detail = getReviewDetail();
      if (!state.project || !detail || pendingReview() || state.busy) {
        setStatus('需要先完成最新审核版本中的全部人工决定。', 'error');
        return;
      }
      setBusy(true); setStatus('正在从已审核快照创建 WBS...', 'loading');
      try {
        const result = await api.createWbsPlan(state.project.projectId, { reviewRunId: detail.run.runId, reviewVersion: detail.run.latestReviewVersion });
        state.plan = result.plan; state.snapshot = null; state.changes.clear(); renderPlan();
        setStatus(result.replayed ? '已安全重放相同审核快照，没有创建重复计划。' : 'WBS 已创建，可以开始编辑。', 'ready');
        onToast(result.replayed ? 'WBS 已重放' : 'WBS 已创建');
      } catch (error) { setStatus(error?.code === 'REVIEW_INCOMPLETE' ? '审核尚未完成，计划创建被拒绝。' : 'WBS 创建失败，计划事实未更新。', 'error'); }
      finally { setBusy(false); renderPlan(); }
    }

    async function refreshPlan() {
      if (!state.plan || state.busy) return;
      setBusy(true); setStatus('正在读取服务端最新 WBS 版本...', 'loading');
      try { state.plan = await api.getWbsPlan(state.project.projectId, state.plan.planId); state.changes.clear(); renderPlan(); setStatus('已读取最新 WBS 版本。', 'ready'); }
      catch (error) { setStatus(error?.code === 'PLAN_NOT_FOUND' ? '服务端找不到该计划。' : 'WBS 读取失败，保留当前页面状态。', 'error'); }
      finally { setBusy(false); renderPlan(); }
    }

    async function revisePlan() {
      if (!state.plan || !state.changes.size || state.busy) return;
      const taskUpdates = [...state.changes.entries()].map(([taskId, changes]) => ({ taskId, changes }));
      setBusy(true); setStatus('正在保存 WBS 修订...', 'loading');
      try { state.plan = await api.reviseWbsPlan(state.project.projectId, state.plan.planId, { expectedPlanVersion: state.plan.selectedPlanVersion, taskUpdates }); state.changes.clear(); renderPlan(); setStatus('WBS 修订已保存，排期快照需要重新计算。', 'ready'); onToast('WBS 修订已保存'); }
      catch (error) { setStatus(error?.code === 'PLAN_CONFLICT' ? 'WBS 已被其他操作更新，请先刷新再保存。' : 'WBS 修订失败，当前输入仍保留在页面。', 'error'); }
      finally { setBusy(false); renderPlan(); }
    }

    async function recalculate() {
      if (!state.plan || state.busy) return;
      const projectStart = calendarDate(elements.projectStart.value);
      const holidays = elements.holidays.value.split(',').map((item) => item.trim()).filter(Boolean);
      if (!projectStart || holidays.some((day) => !calendarDate(day))) { setStatus('项目开始日和节假日必须使用 YYYY-MM-DD。', 'error'); return; }
      setBusy(true); setStatus('正在按当前 WBS 与日历重算排期...', 'loading');
      try { const result = await api.createScheduleSnapshot(state.project.projectId, state.plan.planId, { expectedPlanVersion: state.plan.selectedPlanVersion, projectStart, holidays }); state.snapshot = result.snapshot; renderSummary(); setStatus(result.replayed ? '已重放相同排期快照。' : '排期已重算，等待人工确认。', 'ready'); onToast('排期快照已更新'); }
      catch (error) { setStatus(error?.code === 'PLAN_CONFLICT' ? 'WBS 版本已变化，请先刷新计划。' : '排期计算失败，未更新快照。', 'error'); }
      finally { setBusy(false); renderPlan(); }
    }

    elements.create.addEventListener('click', createPlan);
    elements.refresh.addEventListener('click', refreshPlan);
    elements.revise.addEventListener('click', revisePlan);
    elements.recalculate.addEventListener('click', recalculate);
    elements.taskList.addEventListener('input', (event) => {
      const field = event.target.closest('[data-task-field]'); const row = event.target.closest('[data-task-id]');
      if (!field || !row || !state.plan) return;
      const task = state.plan.tasks.find((item) => item.taskId === row.dataset.taskId); if (!task) return;
      const changes = state.changes.get(task.taskId) || {}; changes[field.dataset.taskField] = field.type === 'number' ? Number(field.value) : field.value; state.changes.set(task.taskId, changes); elements.revise.disabled = false; row.classList.add('is-dirty');
    });
    elements.taskList.addEventListener('change', (event) => { if (event.target.matches('[data-task-field="status"]')) event.target.dispatchEvent(new Event('input', { bubbles: true })); });
    elements.planVersion.addEventListener('change', () => { if (state.plan) api.getWbsPlan(state.project.projectId, state.plan.planId, { planVersion: Number(elements.planVersion.value) }).then((plan) => { state.plan = plan; state.snapshot = null; state.changes.clear(); renderPlan(); setStatus(`已切换到历史 WBS v${plan.selectedPlanVersion}，只读查看。`, 'ready'); }).catch(() => setStatus('历史 WBS 读取失败。', 'error')); });

    return {
      setProject(project) { state.project = project; reset(); },
      clearProject() { state.project = null; reset('请先恢复或创建一个服务端项目。'); },
      snapshot() { return { project: state.project, plan: state.plan, snapshot: state.snapshot, busy: state.busy }; },
    };
  }

  globalScope.MarketOpsScheduleWorkbench = Object.freeze({ createScheduleWorkbench, escapeHtml, STATUS_LABELS });
}(typeof window === 'undefined' ? globalThis : window));
