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

  function approvalMatchesTarget(approval, planVersion, scheduleSnapshotId) {
    return Boolean(approval)
      && approval.planVersion === planVersion
      && approval.scheduleSnapshotId === scheduleSnapshotId;
  }

  function latestPlanVersion(plan) {
    return Math.max(...plan.availablePlanVersions);
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
      approval: root.querySelector('#planApproval'),
      approvalState: root.querySelector('#planApprovalState'),
      approvalRecord: root.querySelector('#planApprovalRecord'),
      approvalReason: root.querySelector('#planApprovalReason'),
      approve: root.querySelector('#approveWbsPlan'),
    };
    if (Object.values(elements).some((element) => !element)) throw new Error('Schedule workbench markup is incomplete.');

    const state = { project: null, plan: null, snapshot: null, approval: null, busy: false, historical: false, changes: new Map() };

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
      elements.planVersion.disabled = busy || !state.plan;
      elements.approve.disabled = busy;
      elements.approvalReason.disabled = busy;
      elements.taskList.querySelectorAll('[data-task-field]').forEach((field) => { field.disabled = busy || state.historical; });
    }

    function pendingReview() {
      const detail = getReviewDetail();
      return !detail || detail.selectedReviewVersion !== detail.run.latestReviewVersion
        || detail.candidates.some((candidate) => candidate.review.status === 'pending');
    }

    function reset(message = '先完成一轮人工审核，再从审核快照生成 WBS。') {
      state.plan = null; state.snapshot = null; state.approval = null; state.historical = false; state.changes.clear();
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
      renderApproval();
      onIcons();
    }

    function renderPlan() {
      if (!state.plan) return reset();
      elements.planMeta.textContent = `WBS v${state.plan.selectedPlanVersion} · ${state.plan.tasks.length} 个任务 · ${state.plan.controls.length} 个控制项`;
      const currentVersion = latestPlanVersion(state.plan);
      elements.planVersion.innerHTML = state.plan.availablePlanVersions.map((version) => `<option value="${version}"${version === state.plan.selectedPlanVersion ? ' selected' : ''}>v${version}${version === currentVersion ? ' · 当前' : ' · 历史'}</option>`).join('');
      elements.planVersion.disabled = state.busy;
      elements.taskList.innerHTML = state.plan.tasks.map((task, index) => {
        const changes = state.changes.get(task.taskId) || {};
        const current = (key) => changes[key] ?? task[key] ?? '';
        const disabled = state.historical ? ' disabled' : '';
        const predecessors = current('predecessors');
        const predecessorOptions = state.plan.tasks.filter((candidate) => candidate.taskId !== task.taskId).map((candidate) => `<label title="${escapeHtml(candidate.title)}"><input data-task-field="predecessors" data-predecessor-id="${escapeHtml(candidate.taskId)}" type="checkbox"${predecessors.includes(candidate.taskId) ? ' checked' : ''}${disabled} /><span>${escapeHtml(candidate.title)}</span><small>${escapeHtml(candidate.taskId)}</small></label>`).join('');
        return `<article class="wbs-task-row${Object.keys(changes).length ? ' is-dirty' : ''}" data-task-id="${escapeHtml(task.taskId)}">
          <div class="wbs-task-index">${String(index + 1).padStart(2, '0')}</div>
          <div class="wbs-task-main"><div class="wbs-task-title"><span class="kind-badge">${escapeHtml(task.kind)}</span><strong>${escapeHtml(task.taskId)}</strong>${current('isLocked') ? '<span class="lock-badge">LOCKED</span>' : ''}</div>
            <label>任务标题<input data-task-field="title" value="${escapeHtml(current('title'))}" maxlength="300"${disabled} /></label>
            <small>${escapeHtml(task.sourceText)}</small></div>
          <label>工期<input data-task-field="durationWorkdays" type="number" min="1" step="1" value="${escapeHtml(current('durationWorkdays'))}"${disabled} /></label>
          <label>负责人<input data-task-field="ownerRole" value="${escapeHtml(current('ownerRole'))}" maxlength="200"${disabled} /></label>
          <div class="wbs-task-date"><span>依赖</span><strong>${predecessors.length ? escapeHtml(predecessors.join(', ')) : '无'}</strong></div>
          <label>状态<select data-task-field="status"${disabled}>${Object.entries(STATUS_LABELS).map(([value, label]) => `<option value="${value}"${current('status') === value ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
          <details class="wbs-task-advanced"><summary><span>依赖与日期</span><small>${predecessors.length} 个前置任务 · 缓冲 ${escapeHtml(current('approvedBufferWorkdays'))} 天</small></summary><div class="wbs-task-advanced-grid">
            <fieldset><legend>前置任务</legend><div class="predecessor-options">${predecessorOptions || '<span>当前计划没有其他可选任务</span>'}</div></fieldset>
            <label>计划开始<input data-task-field="plannedStart" type="date" value="${escapeHtml(calendarDate(current('plannedStart')))}"${disabled} /></label>
            <label>计划完成<input data-task-field="plannedFinish" type="date" value="${escapeHtml(calendarDate(current('plannedFinish')))}"${disabled} /></label>
            <label>硬截止<input data-task-field="hardDeadline" type="date" value="${escapeHtml(calendarDate(current('hardDeadline')))}"${disabled} /></label>
            <label>批准缓冲<input data-task-field="approvedBufferWorkdays" type="number" min="0" step="1" value="${escapeHtml(current('approvedBufferWorkdays'))}"${disabled} /></label>
            <label class="lock-control"><input data-task-field="isLocked" type="checkbox"${current('isLocked') ? ' checked' : ''}${disabled} /><span>锁定计划日期</span></label>
          </div></details>
        </article>`;
      }).join('');
      elements.revise.disabled = state.busy || state.historical || state.changes.size === 0;
      elements.recalculate.disabled = state.busy || state.historical || Boolean(state.approval);
      renderSummary();
      renderApproval();
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

    function renderApproval() {
      elements.approval.dataset.state = 'waiting';
      if (!state.plan) {
        elements.approvalState.textContent = 'WAITING';
        elements.approvalRecord.textContent = '生成可执行的排期快照后可进行确认。';
        elements.approvalReason.value = '';
        elements.approvalReason.disabled = true;
        elements.approve.disabled = true;
        return;
      }
      if (state.approval) {
        elements.approval.dataset.state = 'approved';
        elements.approvalState.textContent = 'APPROVED';
        elements.approvalRecord.innerHTML = `<strong>WBS v${state.approval.planVersion} 已确认</strong><span>排期 ${escapeHtml(state.approval.scheduleDigest.slice(0, 12))}... · ${escapeHtml(state.approval.approvedAt)}</span>`;
        elements.approvalReason.value = state.approval.reason;
        elements.approvalReason.disabled = true;
        elements.approve.disabled = true;
        return;
      }
      if (state.historical) {
        elements.approvalState.textContent = 'UNAPPROVED';
        elements.approvalRecord.textContent = `历史 WBS v${state.plan.selectedPlanVersion} 没有批准记录。`;
        elements.approvalReason.value = '';
        elements.approvalReason.disabled = true;
        elements.approve.disabled = true;
        return;
      }
      if (state.changes.size) {
        elements.approvalState.textContent = 'REVISION';
        elements.approvalRecord.textContent = '先保存修订并重新计算排期，才能确认新的执行版本。';
        elements.approvalReason.disabled = true;
        elements.approve.disabled = true;
        return;
      }
      if (!state.snapshot) {
        elements.approvalState.textContent = 'WAITING';
        elements.approvalRecord.textContent = '当前 WBS 尚无排期快照。';
        elements.approvalReason.disabled = true;
        elements.approve.disabled = true;
        return;
      }
      if (state.snapshot.status !== 'ready') {
        elements.approval.dataset.state = 'blocked';
        elements.approvalState.textContent = 'BLOCKED';
        elements.approvalRecord.textContent = `存在 ${state.snapshot.conflicts.length} 个冲突和 ${state.snapshot.deadlineMisses.length} 个截止风险，不能批准。`;
        elements.approvalReason.disabled = true;
        elements.approve.disabled = true;
        return;
      }
      elements.approval.dataset.state = 'ready';
      elements.approvalState.textContent = 'READY';
      elements.approvalRecord.innerHTML = `<strong>可确认 WBS v${state.plan.selectedPlanVersion}</strong><span>排期 ${escapeHtml(state.snapshot.scheduleDigest.slice(0, 12))}...</span>`;
      elements.approvalReason.disabled = state.busy;
      elements.approve.disabled = state.busy || !elements.approvalReason.value.trim();
    }

    async function syncApproval() {
      if (!state.plan) { state.approval = null; return true; }
      try {
        const result = await api.getWbsPlanApproval(state.project.projectId, state.plan.planId, { planVersion: state.plan.selectedPlanVersion });
        state.approval = result.approval;
        return true;
      } catch {
        state.approval = null;
        return false;
      }
    }

    async function approvePlan() {
      if (!state.project || !state.plan || !state.snapshot || state.snapshot.status !== 'ready'
        || state.approval || state.historical || state.changes.size || state.busy) return;
      const reason = elements.approvalReason.value.trim();
      if (!reason) { setStatus('请填写计划确认原因。', 'error'); return; }
      const requestedVersion = state.plan.selectedPlanVersion;
      const requestedSnapshot = state.snapshot.snapshotId;
      setBusy(true); setStatus('正在确认此 WBS 与排期快照...', 'loading');
      try {
        const result = await api.approveWbsPlan(state.project.projectId, state.plan.planId, {
          expectedPlanVersion: requestedVersion,
          scheduleSnapshotId: requestedSnapshot,
          reason,
        });
        state.approval = result.approval;
        setStatus(result.replayed ? '已对账到相同的历史确认记录。' : '计划已确认，可以进入执行准备。', 'ready');
        onToast(result.replayed ? '确认记录已对账' : '计划已确认');
      } catch (error) {
        let reconciled = false;
        if (error?.uncertain || error?.code === 'PLAN_ALREADY_APPROVED') {
          const approvalKnown = await syncApproval();
          reconciled = approvalKnown
            && approvalMatchesTarget(state.approval, requestedVersion, requestedSnapshot);
        }
        if (reconciled) {
          setStatus('请求结果已从服务端确认记录完成对账。', 'ready');
          onToast('确认记录已对账');
        } else if (error?.code === 'PLAN_ALREADY_APPROVED' && state.approval) {
          setStatus('此 WBS 版本已确认另一份排期快照，本次确认未写入。', 'error');
        } else if (error?.code === 'PLAN_CONFLICT') {
          try {
            state.plan = await api.getWbsPlan(state.project.projectId, state.plan.planId);
            state.historical = false; state.snapshot = null; state.changes.clear();
            await syncApproval();
            setStatus('WBS 已更新，当前确认目标已刷新。', 'error');
          } catch { setStatus('WBS 已变化且刷新失败，请稍后重试。', 'error'); }
        } else if (error?.code === 'SCHEDULE_NOT_READY') {
          setStatus('排期仍有冲突或截止风险，不能确认。', 'error');
        } else {
          setStatus('计划确认失败，当前页面未声称已批准。', 'error');
        }
      } finally { setBusy(false); renderPlan(); }
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
        state.plan = result.plan; state.snapshot = null; state.approval = null; state.historical = false; state.changes.clear();
        await syncApproval(); renderPlan();
        setStatus(result.replayed ? '已安全重放相同审核快照，没有创建重复计划。' : 'WBS 已创建，可以开始编辑。', 'ready');
        onToast(result.replayed ? 'WBS 已重放' : 'WBS 已创建');
      } catch (error) { setStatus(error?.code === 'REVIEW_INCOMPLETE' ? '审核尚未完成，计划创建被拒绝。' : 'WBS 创建失败，计划事实未更新。', 'error'); }
      finally { setBusy(false); renderPlan(); }
    }

    async function refreshPlan() {
      if (!state.plan || state.busy) return;
      setBusy(true); setStatus('正在读取服务端最新 WBS 版本...', 'loading');
      try {
        state.plan = await api.getWbsPlan(state.project.projectId, state.plan.planId);
        state.historical = false; state.snapshot = null; state.approval = null; state.changes.clear();
        const approvalKnown = await syncApproval(); renderPlan();
        setStatus(approvalKnown ? '已读取最新 WBS 版本。' : 'WBS 已刷新，但确认状态暂时无法读取。', approvalKnown ? 'ready' : 'error');
      }
      catch (error) { setStatus(error?.code === 'PLAN_NOT_FOUND' ? '服务端找不到该计划。' : 'WBS 读取失败，保留当前页面状态。', 'error'); }
      finally { setBusy(false); renderPlan(); }
    }

    async function revisePlan() {
      if (!state.plan || !state.changes.size || state.busy) return;
      const taskUpdates = [...state.changes.entries()].map(([taskId, changes]) => ({ taskId, changes }));
      setBusy(true); setStatus('正在保存 WBS 修订...', 'loading');
      try {
        state.plan = await api.reviseWbsPlan(state.project.projectId, state.plan.planId, { expectedPlanVersion: state.plan.selectedPlanVersion, taskUpdates });
        state.historical = false; state.snapshot = null; state.approval = null; state.changes.clear(); renderPlan();
        setStatus('WBS 修订已保存，排期快照需要重新计算。', 'ready'); onToast('WBS 修订已保存');
      }
      catch (error) { setStatus(error?.code === 'PLAN_CONFLICT' ? 'WBS 已被其他操作更新，请先刷新再保存。' : 'WBS 修订失败，当前输入仍保留在页面。', 'error'); }
      finally { setBusy(false); renderPlan(); }
    }

    async function recalculate() {
      if (!state.plan || state.busy || state.approval) return;
      const projectStart = calendarDate(elements.projectStart.value);
      const holidays = elements.holidays.value.split(',').map((item) => item.trim()).filter(Boolean);
      if (!projectStart || holidays.some((day) => !calendarDate(day))) { setStatus('项目开始日和节假日必须使用 YYYY-MM-DD。', 'error'); return; }
      setBusy(true); setStatus('正在按当前 WBS 与日历重算排期...', 'loading');
      try { const result = await api.createScheduleSnapshot(state.project.projectId, state.plan.planId, { expectedPlanVersion: state.plan.selectedPlanVersion, projectStart, holidays }); state.snapshot = result.snapshot; renderSummary(); renderApproval(); setStatus(result.replayed ? '已重放相同排期快照。' : '排期已重算，等待人工确认。', 'ready'); onToast('排期快照已更新'); }
      catch (error) { setStatus(error?.code === 'PLAN_CONFLICT' ? 'WBS 版本已变化，请先刷新计划。' : '排期计算失败，未更新快照。', 'error'); }
      finally { setBusy(false); renderPlan(); }
    }

    elements.create.addEventListener('click', createPlan);
    elements.refresh.addEventListener('click', refreshPlan);
    elements.revise.addEventListener('click', revisePlan);
    elements.recalculate.addEventListener('click', recalculate);
    elements.approve.addEventListener('click', approvePlan);
    elements.approvalReason.addEventListener('input', renderApproval);
    elements.taskList.addEventListener('input', (event) => {
      const field = event.target.closest('[data-task-field]'); const row = event.target.closest('[data-task-id]');
      if (!field || !row || !state.plan) return;
      const task = state.plan.tasks.find((item) => item.taskId === row.dataset.taskId); if (!task) return;
      const changes = state.changes.get(task.taskId) || {};
      if (field.dataset.taskField === 'predecessors') changes.predecessors = [...row.querySelectorAll('[data-task-field="predecessors"]:checked')].map((item) => item.dataset.predecessorId);
      else if (field.type === 'number') changes[field.dataset.taskField] = Number(field.value);
      else if (field.type === 'date') changes[field.dataset.taskField] = field.value || null;
      else if (field.type === 'checkbox') changes[field.dataset.taskField] = field.checked;
      else changes[field.dataset.taskField] = field.value;
      state.changes.set(task.taskId, changes); elements.revise.disabled = false; row.classList.add('is-dirty'); renderApproval();
    });
    elements.taskList.addEventListener('change', (event) => { if (event.target.matches('select[data-task-field], input[type="checkbox"][data-task-field]')) event.target.dispatchEvent(new Event('input', { bubbles: true })); });
    elements.planVersion.addEventListener('change', async () => {
      if (!state.plan || state.busy) return;
      const requestedVersion = Number(elements.planVersion.value);
      setBusy(true); setStatus(`正在读取 WBS v${requestedVersion}...`, 'loading');
      try {
        const plan = await api.getWbsPlan(state.project.projectId, state.plan.planId, { planVersion: requestedVersion });
        const approvalResult = await api.getWbsPlanApproval(state.project.projectId, plan.planId, { planVersion: plan.selectedPlanVersion });
        state.plan = plan; state.historical = plan.selectedPlanVersion !== latestPlanVersion(plan); state.snapshot = null; state.approval = approvalResult.approval; state.changes.clear();
        renderPlan();
        setStatus(state.historical ? `已切换到历史 WBS v${plan.selectedPlanVersion}，只读查看。` : '已切换到最新 WBS 版本。', 'ready');
      } catch { renderPlan(); setStatus('WBS 版本读取失败，已保留当前计划。', 'error'); }
      finally { setBusy(false); renderPlan(); }
    });

    return {
      setProject(project) { state.project = project; reset(); },
      clearProject() { state.project = null; reset('请先恢复或创建一个服务端项目。'); },
      snapshot() { return { project: state.project, plan: state.plan, snapshot: state.snapshot, approval: state.approval, historical: state.historical, busy: state.busy }; },
    };
  }

  globalScope.MarketOpsScheduleWorkbench = Object.freeze({ createScheduleWorkbench, escapeHtml, approvalMatchesTarget, latestPlanVersion, STATUS_LABELS });
}(typeof window === 'undefined' ? globalThis : window));
