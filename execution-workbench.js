/* finesse · approved-plan execution console · append-only updates · authenticated exports */
(function executionWorkbenchModule(globalScope) {
  'use strict';

  const STATUS_LABELS = Object.freeze({
    not_started: '未开始', in_progress: '进行中', blocked: '阻塞', completed: '已完成', cancelled: '已取消',
  });

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  function createExecutionWorkbench(options) {
    const {
      api, root, getScheduleState = () => null, onToast = () => {}, onIcons = () => {},
    } = options;
    if (!api || !root) throw new Error('Execution workbench dependencies are incomplete.');
    const elements = {
      section: root.querySelector('#executionWorkbench'),
      status: root.querySelector('#executionStatus'),
      statusText: root.querySelector('#executionStatusText'),
      meta: root.querySelector('#executionPlanMeta'),
      sync: root.querySelector('#syncExecution'),
      csv: root.querySelector('#exportExecutionCsv'),
      xlsx: root.querySelector('#exportExecutionXlsx'),
      list: root.querySelector('#executionTaskList'),
      progress: root.querySelector('#executionProgress'),
    };
    if (Object.values(elements).some((element) => !element)) throw new Error('Execution workbench markup is incomplete.');

    const state = { project: null, data: null, busy: false };

    function setStatus(message, kind = 'idle') {
      elements.status.dataset.state = kind;
      elements.statusText.textContent = message;
    }

    function setBusy(busy) {
      state.busy = busy;
      elements.section.setAttribute('aria-busy', busy ? 'true' : 'false');
      elements.sync.disabled = busy || !state.project;
      elements.csv.disabled = busy || !state.data;
      elements.xlsx.disabled = busy || !state.data;
      elements.list.querySelectorAll('button, input, textarea, select').forEach((control) => {
        control.disabled = busy || !state.data?.editable;
      });
    }

    function reset(message = '批准当前 WBS 后，可在这里记录执行状态。') {
      state.data = null;
      elements.meta.textContent = '尚未载入批准计划';
      elements.progress.textContent = '0 / 0';
      elements.list.innerHTML = `<div class="execution-empty"><i data-lucide="activity"></i><strong>等待执行计划</strong><span>${escapeHtml(message)}</span></div>`;
      elements.csv.disabled = true;
      elements.xlsx.disabled = true;
      elements.sync.disabled = !state.project;
      setStatus(message, state.project ? 'empty' : 'idle');
      onIcons();
    }

    function currentTarget() {
      const schedule = getScheduleState();
      if (!state.project || !schedule?.plan) return null;
      return {
        projectId: state.project.projectId,
        planId: schedule.plan.planId,
        planVersion: schedule.plan.selectedPlanVersion,
        approved: Boolean(schedule.approval)
          && schedule.approval.planVersion === schedule.plan.selectedPlanVersion,
      };
    }

    function taskRow(task, index) {
      const disabled = !state.data.editable ? ' disabled' : '';
      const blocked = task.status === 'blocked';
      const datesRequired = ['in_progress', 'completed'].includes(task.status);
      return `<article class="execution-row" data-execution-task="${escapeHtml(task.taskId)}">
        <div class="execution-index">${String(index + 1).padStart(2, '0')}</div>
        <div class="execution-task-copy"><strong>${escapeHtml(task.title)}</strong><span>${escapeHtml(task.ownerRole)} · 计划 ${escapeHtml(task.plannedStart || '待定')} → ${escapeHtml(task.plannedFinish || '待定')}</span></div>
        <label>状态<select data-execution-field="status"${disabled}>${Object.entries(STATUS_LABELS).map(([value, label]) => `<option value="${value}"${task.status === value ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
        <label class="execution-blocker"${blocked ? '' : ' hidden'}>阻塞原因<input data-execution-field="blockerReason" maxlength="2000" value="${escapeHtml(task.blockerReason || '')}"${disabled} /></label>
        <label class="execution-date">实际开始<input data-execution-field="actualStart" type="date" value="${escapeHtml(task.actualStart || '')}"${datesRequired ? ' required' : ''}${disabled} /></label>
        <label class="execution-date">实际完成<input data-execution-field="actualFinish" type="date" value="${escapeHtml(task.actualFinish || '')}"${task.status === 'completed' ? ' required' : ''}${disabled} /></label>
        <label class="execution-note">执行备注<input data-execution-field="note" maxlength="4000" value="${escapeHtml(task.note || '')}"${disabled} /></label>
        <div class="execution-commit"><span>SEQ <strong>${task.sequenceNo}</strong></span><button class="icon-button execution-save" data-execution-save type="button" aria-label="保存 ${escapeHtml(task.title)} 的执行状态" title="保存执行状态"${disabled}><i data-lucide="save"></i></button></div>
      </article>`;
    }

    function render() {
      if (!state.data) return reset();
      const completed = state.data.tasks.filter((task) => task.status === 'completed').length;
      const blocked = state.data.tasks.filter((task) => task.status === 'blocked').length;
      elements.meta.textContent = `批准 WBS v${state.data.planVersion} · ${state.data.tasks.length} 个任务${state.data.editable ? '' : ' · 历史只读'}`;
      elements.progress.textContent = `${completed} / ${state.data.tasks.length}`;
      elements.list.innerHTML = state.data.tasks.map(taskRow).join('');
      elements.csv.disabled = state.busy;
      elements.xlsx.disabled = state.busy;
      setStatus(blocked ? `${blocked} 个任务处于阻塞状态，请优先核对原因。` : '执行状态已与服务器事实同步。', blocked ? 'warning' : 'ready');
      onIcons();
    }

    async function refresh() {
      if (state.busy) return;
      const target = currentTarget();
      if (!target) {
        reset('先在上方生成 WBS，并完成确定性排期。');
        return;
      }
      if (!target.approved) {
        reset(`WBS v${target.planVersion} 尚未批准，不能写入执行事实。`);
        return;
      }
      setBusy(true);
      setStatus('正在读取批准计划的最新执行状态…', 'loading');
      try {
        state.data = await api.getExecutionState(target.projectId, target.planId, { planVersion: target.planVersion });
        render();
      } catch (error) {
        state.data = null;
        reset(error?.code === 'PLAN_NOT_APPROVED'
          ? '该 WBS 版本尚未批准，执行入口保持关闭。'
          : '执行状态读取失败；计划和审核事实没有被改动。');
        setStatus(elements.list.textContent.trim(), 'error');
      } finally {
        setBusy(false);
      }
    }

    function rowInput(row, name) {
      return row.querySelector(`[data-execution-field="${name}"]`)?.value.trim() || '';
    }

    async function saveRow(row) {
      if (state.busy || !state.data?.editable) return;
      const task = state.data.tasks.find((item) => item.taskId === row.dataset.executionTask);
      if (!task) return;
      const status = rowInput(row, 'status');
      const blockerReason = rowInput(row, 'blockerReason');
      const actualStart = rowInput(row, 'actualStart');
      const actualFinish = rowInput(row, 'actualFinish');
      const note = rowInput(row, 'note');
      if (status === 'blocked' && !blockerReason) {
        row.querySelector('[data-execution-field="blockerReason"]').focus();
        setStatus('阻塞状态必须填写可处理的阻塞原因。', 'error');
        return;
      }
      if (['in_progress', 'completed'].includes(status) && !actualStart) {
        row.querySelector('[data-execution-field="actualStart"]').focus();
        setStatus('进行中或已完成任务必须填写实际开始日。', 'error');
        return;
      }
      if (status === 'completed' && !actualFinish) {
        row.querySelector('[data-execution-field="actualFinish"]').focus();
        setStatus('已完成任务必须填写实际完成日。', 'error');
        return;
      }
      const input = {
        expectedPlanVersion: state.data.planVersion,
        taskId: task.taskId,
        expectedExecutionSequence: task.sequenceNo,
        status,
      };
      if (status === 'blocked') input.blockerReason = blockerReason;
      if (actualStart) input.actualStart = actualStart;
      if (actualFinish) input.actualFinish = actualFinish;
      if (note) input.note = note;
      setBusy(true);
      row.classList.add('is-submitting');
      setStatus(`正在保存“${task.title}”的执行状态…`, 'loading');
      try {
        const result = await api.updateExecutionTask(state.data.projectId, state.data.planId, input);
        Object.assign(task, result.update);
        render();
        onToast(result.replayed ? '执行状态已对账' : '执行状态已保存');
      } catch (error) {
        if (error?.code === 'EXECUTION_CONFLICT') {
          setBusy(false);
          await refresh();
          const reconciledTask = state.data?.tasks.find((item) => item.taskId === task.taskId);
          if (reconciledTask) Object.assign(reconciledTask, {
            status, blockerReason: status === 'blocked' ? blockerReason : null,
            actualStart: actualStart || null, actualFinish: actualFinish || null, note: note || null,
          });
          render();
          setStatus('执行状态已被更新，页面已重新同步；请核对后再提交。', 'warning');
        } else {
          setStatus('执行状态保存失败，当前输入尚未成为服务器事实。', 'error');
        }
      } finally {
        row.classList.remove('is-submitting');
        setBusy(false);
      }
    }

    async function download(format) {
      if (state.busy || !state.data) return;
      setBusy(true);
      setStatus(`正在生成 ${format.toUpperCase()} 执行清单…`, 'loading');
      try {
        const result = await api.downloadExecutionExport(state.data.projectId, state.data.planId, format, { planVersion: state.data.planVersion });
        const url = URL.createObjectURL(result.blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = result.filename;
        link.hidden = true;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        setStatus(`${format.toUpperCase()} 执行清单已下载。`, 'ready');
        onToast('执行清单已下载');
      } catch {
        setStatus('执行清单下载失败，请保持当前计划版本后重试。', 'error');
      } finally {
        setBusy(false);
      }
    }

    elements.sync.addEventListener('click', refresh);
    elements.csv.addEventListener('click', () => download('csv'));
    elements.xlsx.addEventListener('click', () => download('xlsx'));
    elements.list.addEventListener('click', (event) => {
      const button = event.target.closest('[data-execution-save]');
      const row = event.target.closest('[data-execution-task]');
      if (button && row) saveRow(row);
    });
    elements.list.addEventListener('change', (event) => {
      if (!event.target.matches('[data-execution-field="status"]')) return;
      const row = event.target.closest('[data-execution-task]');
      const status = event.target.value;
      row.querySelector('.execution-blocker').hidden = status !== 'blocked';
      row.querySelector('[data-execution-field="actualStart"]').required = ['in_progress', 'completed'].includes(status);
      row.querySelector('[data-execution-field="actualFinish"]').required = status === 'completed';
    });

    return {
      setProject(project) { state.project = project; reset(); },
      clearProject() { state.project = null; reset('恢复或创建项目后，执行入口才可使用。'); },
      refresh,
      snapshot() { return { project: state.project, data: state.data, busy: state.busy }; },
    };
  }

  globalScope.MarketOpsExecutionWorkbench = Object.freeze({ createExecutionWorkbench, escapeHtml, STATUS_LABELS });
}(typeof window === 'undefined' ? globalThis : window));
