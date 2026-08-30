/* finesse · workflow client · server facts · explicit approval · bounded motion */
const toast = document.querySelector('#toast');
let toastTimer;
const {
  ProjectApiError, createProjectApiClient, createRetryKeyManager, importFingerprint,
  importThenLoad, isSupportedImportName, isSupportedProposalName, loadInitialProject,
} = window.MarketOpsProjectImport;

const iconGlyphs = {
  activity: '⌁', download: '↓', 'file-spreadsheet': '▦', save: '□', play: '▶', 'badge-check': '✓',
  'arrow-right': '>', 'check': '✓', 'chevron-right': '›', 'file-check': '✓',
  'git-branch': '⑂', history: '↶', info: 'i', library: '▤',
  'mouse-pointer-2': '↖', pencil: '✎', plus: '+', 'refresh-cw': '↻',
  'scan-text': '⌗', send: '↑', 'shield-check': '✓', sparkles: '✦', sun: '☼', moon: '☾', x: '×',
  'layout-dashboard': '▦', 'folder-kanban': '▤', 'book-open-check': '≡', radar: '◉', images: '▧',
  'calendar-days': '□', 'settings-2': '⚙', search: '⌕', 'list-todo': '☷', 'folder-open': '▱', newspaper: '▤', inbox: '▥',
};

function refreshIcons() {
  document.querySelectorAll('[data-lucide]').forEach((icon) => {
    icon.textContent = iconGlyphs[icon.dataset.lucide] || '·';
    icon.setAttribute('aria-hidden', 'true');
  });
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2800);
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-toast]');
  if (button) showToast(button.dataset.toast);
});

const errorCopy = {
  APPROVAL_REQUIRED: '请确认方案已经人工批准。',
  AUTHORIZATION_REQUIRED: '当前浏览器会话无权访问此工作区，请重新完成服务器认证。',
  CLIENT_CAPABILITY_REQUIRED: '当前浏览器缺少完成操作所需的能力。',
  IDEMPOTENCY_CONFLICT: '同一请求标识对应了不同方案。请重新导入或联系管理员核对。',
  INVALID_DOCUMENT: '文件内容未通过服务器结构校验，请检查后重试。',
  INVALID_INPUT: '请求信息不完整，请检查后重试。',
  INVALID_MEDIA_TYPE: '浏览器上传的文件类型无法被服务器接受。',
  INVALID_PROJECT_ID: '当前项目链接无效，请移除链接中的 projectId 后重试。',
  MALFORMED_RESPONSE: '服务器返回了无法验证的响应，页面没有采纳这些事实。',
  NETWORK_ERROR: '无法连接 Server API。提交结果不确定时，页面会先与服务器对账。',
  OBJECT_INTEGRITY_MISMATCH: '服务器文件完整性校验失败，未更新页面事实。',
  OBJECT_WRITE_FAILED: '服务器未能保留文件，可使用同一请求安全重试。',
  PAYLOAD_TOO_LARGE: '至少一个文件超过 25 MiB 限制。',
  PROJECT_NOT_FOUND: '服务器中没有找到这个项目，或当前工作区无权访问。',
  REQUEST_CANCELLED: '本次请求已取消。服务器可能已提交时会先对账。',
  MODEL_NOT_FOUND: '没有找到这个模型配置。',
  MODEL_CONFLICT: '模型配置已被更新，请刷新后重试。',
  MODEL_STORE_FAILED: '模型配置暂时无法读取或保存，请稍后重试。',
  REVIEW_CONFLICT: '审核版本已更新，正在读取服务器最新事实。',
  REVIEW_NOT_FOUND: '没有找到该审核记录，或当前操作者无权访问。',
  UNSUPPORTED_FORMAT: '项目资料限 Markdown、CSV、基础 DOCX；批准方案限 Markdown 或基础 DOCX。',
};

function describeError(error) {
  const code = error instanceof ProjectApiError ? error.code : 'MALFORMED_RESPONSE';
  const base = errorCopy[code] || 'Server API 请求失败，页面未更新项目事实。';
  return error?.requestId ? `${base} 请求编号：${error.requestId}` : base;
}

const api = createProjectApiClient();
const retryKeys = createRetryKeyManager();
const dialog = document.querySelector('#briefDialog');
const importForm = document.querySelector('#briefForm');
const importFormStatus = document.querySelector('#importFormStatus');
const createProjectButton = document.querySelector('#createProjectButton');
const cancelImportButton = document.querySelector('#cancelImportButton');
const importSummary = document.querySelector('#importSummary');
const importSummaryKicker = document.querySelector('#importSummaryKicker');
const importSummaryBadge = document.querySelector('#importSummaryBadge');
const retryProjectLoad = document.querySelector('#retryProjectLoad');
const serverStatusText = document.querySelector('#serverStatusText');
const connectionState = document.querySelector('#connectionState');
let activeImportController = null;

const reviewWorkbench = window.MarketOpsReviewWorkbench.createReviewWorkbench({
  api, root: document, describeError, onToast: showToast, onIcons: refreshIcons,
  onContext(value) { document.querySelector('#assistantContext').textContent = value; },
});
const scheduleWorkbench = window.MarketOpsScheduleWorkbench.createScheduleWorkbench({
  api, root: document, onToast: showToast, onIcons: refreshIcons,
  getReviewDetail() { return reviewWorkbench.snapshot().detail; },
});
const executionWorkbench = window.MarketOpsExecutionWorkbench.createExecutionWorkbench({
  api, root: document, onToast: showToast, onIcons: refreshIcons,
  getScheduleState() { return scheduleWorkbench.snapshot(); },
});

function setServerStatus(message, state = 'idle') {
  serverStatusText.textContent = message;
  serverStatusText.dataset.state = state;
  connectionState.dataset.state = state;
}

function setSummaryState(state, message, options = {}) {
  importSummary.hidden = false;
  importSummary.dataset.state = state;
  importSummary.setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');
  importSummaryKicker.textContent = options.kicker || 'SERVER PROJECT';
  document.querySelector('#importProjectName').textContent = options.name || message;
  document.querySelector('#importProjectFiles').textContent = options.detail || '';
  importSummaryBadge.textContent = options.badge || (state === 'error' ? '需要处理' : '读取中');
  retryProjectLoad.hidden = options.retry !== true;
  refreshIcons();
}

function setEmptyState() {
  importSummary.hidden = true;
  document.querySelector('#breadcrumbProject').textContent = '尚未创建项目';
  setServerStatus('Server API 已连接，当前工作区尚无项目。', 'empty');
  reviewWorkbench.clearProject();
  scheduleWorkbench.clearProject();
  executionWorkbench.clearProject();
  renderDashboard({ items: [] }, null);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
}

function projectStatusLabel(status) {
  const labels = { planning: '规划中', active: '进行中', archived: '已归档', ready: '待执行', completed: '已完成', draft: '草稿', blocked: '已阻塞' };
  return labels[status] || status || '待确认';
}

const dashboardState = {
  period: 'today',
  filter: 'all',
  projects: [],
  selectedProjectId: null,
  selectedProject: null,
  lastUpdated: null,
  refreshing: false,
  signals: { geo: null, content: null, calendar: [], suggestions: [] },
};

const dashboardPeriods = {
  today: { label: '今日', kicker: 'TODAY / NEXT ACTION', focus: '今日重点' },
  week: { label: '本周', kicker: 'THIS WEEK / NEXT ACTION', focus: '本周重点' },
  month: { label: '本月', kicker: 'THIS MONTH / NEXT ACTION', focus: '本月重点' },
};

function dashboardFilterMatch(project) {
  if (dashboardState.filter === 'all') return true;
  if (dashboardState.filter === 'active') return ['planning', 'active'].includes(String(project?.status || '').toLowerCase());
  return String(project?.status || '').toLowerCase() === dashboardState.filter;
}

function formatDashboardTimestamp(value = new Date()) {
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(value);
}

function formatDashboardDate(value = new Date()) {
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', weekday: 'short' }).format(value);
}

function runDashboardTransition(update) {
  if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.startViewTransition(update);
    return;
  }
  update();
}

function dashboardPeriodEnd(period, now = new Date()) {
  const end = new Date(now);
  end.setHours(23, 59, 59, 999);
  if (period === 'week') end.setDate(end.getDate() + ((7 - end.getDay()) % 7));
  if (period === 'month') end.setMonth(end.getMonth() + 1, 0);
  return end;
}

function dashboardPeriodStart(period, now = new Date()) {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  if (period === 'week') start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  if (period === 'month') start.setDate(1);
  return start;
}

function dashboardTaskMatchesPeriod(task) {
  const rawDate = task?.plannedStart || task?.dueDate;
  if (!rawDate) return true;
  const taskDate = new Date(rawDate);
  if (Number.isNaN(taskDate.getTime())) return true;
  const now = new Date();
  const start = dashboardPeriodStart(dashboardState.period, now);
  const end = dashboardPeriodEnd(dashboardState.period, now);
  // Overdue work remains visible in Today as an actionable exception, but does
  // not blur the boundaries of the future week/month views.
  if (taskDate < dashboardPeriodStart('today', now)) {
    return dashboardState.period === 'today' || taskDate >= start;
  }
  return taskDate >= start && taskDate <= end;
}

function updateDashboardSelection() {
  const selection = document.querySelector('#dashboardSelection');
  const project = dashboardState.selectedProject;
  if (!project) {
    selection.hidden = true;
    selection.innerHTML = '';
    return;
  }
  const projectName = project.projectName || project.name || '未命名项目';
  selection.hidden = false;
  selection.innerHTML = `<span class="selection-marker" aria-hidden="true"></span><span class="selection-copy"><small>已选项目</small><strong>${escapeHtml(projectName)}</strong><span>${escapeHtml(projectStatusLabel(project.status))} · ${escapeHtml(project.createdAt ? `创建于 ${project.createdAt.slice(0, 10)}` : '状态来自服务器')}</span></span><button class="button button-quiet" type="button" id="openSelectedProject"><i data-lucide="arrow-right"></i>打开项目</button>`;
  document.querySelector('#openSelectedProject').addEventListener('click', () => {
    if (!project.projectId) return;
    writeProjectIdToUrl(project.projectId);
    navigatePrimaryView('project');
    restoreProject();
  });
  refreshIcons();
}

function setDashboardPeriod(period) {
  if (!dashboardPeriods[period]) return;
  runDashboardTransition(() => {
    dashboardState.period = period;
    document.querySelectorAll('[data-dashboard-period]').forEach((button) => {
      const active = button.dataset.dashboardPeriod === period;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    const copy = dashboardPeriods[period];
    document.querySelector('#focusKicker').textContent = copy.kicker;
    document.querySelector('#focusTitle').textContent = copy.focus;
    document.querySelector('#calendarTitle').textContent = `${copy.label}日程`;
    document.querySelector('#calendarDateLabel').textContent = period === 'today' ? formatDashboardDate() : copy.label;
    renderDashboardTasks(dashboardState.currentDetail || null);
  });
}

function setDashboardFilter(filter) {
  if (!['all', 'active', 'blocked', 'archived'].includes(filter)) return;
  runDashboardTransition(() => {
    dashboardState.filter = filter;
    document.querySelectorAll('[data-dashboard-filter]').forEach((button) => {
      const active = button.dataset.dashboardFilter === filter;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    renderDashboardProjects({ items: dashboardState.projects }, dashboardState.currentDetail || null);
  });
}

function renderDashboardProjects(projects, currentDetail) {
  const list = document.querySelector('#dashboardProjectList');
  const sourceItems = Array.isArray(projects?.items) ? [...projects.items] : [];
  if (currentDetail && !sourceItems.some((item) => item.projectId === currentDetail.projectId)) {
    const current = { projectId: currentDetail.projectId, name: currentDetail.projectName, status: 'active', createdAt: currentDetail.createdAt };
    sourceItems.unshift(current);
  }
  dashboardState.projects = sourceItems;
  const selected = sourceItems.find((item) => item.projectId === dashboardState.selectedProjectId);
  if (!selected || !dashboardFilterMatch(selected)) {
    dashboardState.selectedProjectId = null;
    dashboardState.selectedProject = null;
  } else {
    dashboardState.selectedProject = selected;
  }
  const items = sourceItems.filter(dashboardFilterMatch);
  document.querySelector('#projectCount').textContent = `${items.length} 项`;
  if (!items.length) {
    const message = dashboardState.filter === 'all' ? '尚无服务器项目' : `没有${projectStatusLabel(dashboardState.filter)}项目`;
    list.innerHTML = `<div class="dashboard-empty"><i data-lucide="folder-open"></i><strong>${message}</strong><span>${dashboardState.filter === 'all' ? '从“项目”入口导入已批准方案，建立第一个项目。' : '切换项目筛选，或从项目入口查看完整列表。'}</span></div>`;
    updateDashboardSelection();
    refreshIcons();
    return;
  }
  list.innerHTML = items.slice(0, 8).map((item) => `<button class="dashboard-project-row${item.projectId === dashboardState.selectedProjectId ? ' is-selected' : ''}" type="button" aria-pressed="${item.projectId === dashboardState.selectedProjectId ? 'true' : 'false'}" data-project-id="${escapeHtml(item.projectId)}" data-project-status="${escapeHtml(item.status || 'unknown')}"><span class="dashboard-project-main"><strong>${escapeHtml(item.projectName || item.name || '未命名项目')}</strong><small>${escapeHtml(item.createdAt ? `创建于 ${item.createdAt.slice(0, 10)}` : '状态来自服务器')}</small></span><span class="status-badge">${escapeHtml(projectStatusLabel(item.status))}</span><i data-lucide="chevron-right"></i></button>`).join('');
  list.querySelectorAll('[data-project-id]').forEach((button) => button.addEventListener('click', () => {
    const selected = dashboardState.projects.find((item) => item.projectId === button.dataset.projectId);
    dashboardState.selectedProjectId = button.dataset.projectId;
    dashboardState.selectedProject = selected || null;
    renderDashboardProjects({ items: dashboardState.projects }, dashboardState.currentDetail || null);
    updateDashboardSelection();
  }));
  updateDashboardSelection();
  refreshIcons();
}

function renderDashboardTasks(detail) {
  const execution = executionWorkbench.snapshot().data;
  const tasks = Array.isArray(execution?.tasks) ? execution.tasks : [];
  const periodTasks = tasks.filter((task) => !['completed', 'done'].includes(String(task.status || '').toLowerCase())).filter(dashboardTaskMatchesPeriod);
  const openTasks = periodTasks.slice(0, 5);
  const focusList = document.querySelector('#dashboardFocusList');
  const calendarList = document.querySelector('#dashboardCalendarList');
  const suggestionsList = document.querySelector('#dashboardSuggestionsList');
  const suggestions = Array.isArray(dashboardState.signals.suggestions)
    ? dashboardState.signals.suggestions.filter((item) => dashboardTaskMatchesPeriod({ plannedStart: item.date }))
    : [];
  document.querySelector('#suggestionsCount').textContent = `${suggestions.length} 项`;
  if (!suggestions.length) {
    suggestionsList.innerHTML = '<div class="dashboard-empty"><i data-lucide="inbox"></i><strong>暂无待确认建议</strong><span>研究结果和 GEO 缺口经人工确认后，才会纳入正式日程。</span></div>';
  } else {
    suggestionsList.innerHTML = suggestions.slice(0, 5).map((item) => `<button class="dashboard-task-row dashboard-suggestion-row" type="button"><span class="task-state-dot"></span><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.source)} · ${escapeHtml(item.date)}</small></span><i data-lucide="chevron-right"></i></button>`).join('');
    suggestionsList.querySelectorAll('.dashboard-suggestion-row').forEach((row) => row.addEventListener('click', () => { showToast('请在日程页确认是否纳入'); navigatePrimaryView('calendar'); }));
  }
  const calendarItems = Array.isArray(dashboardState.signals.calendar)
    ? dashboardState.signals.calendar.filter((item) => item.status !== 'completed' && dashboardTaskMatchesPeriod({ plannedStart: item.date }))
    : [];
  const mergedCalendar = [...calendarItems, ...openTasks.map((task) => ({
    title: task.title || task.name || '未命名任务',
    date: task.plannedStart || task.dueDate || '',
    source: detail?.projectName || '项目执行',
  }))];
  document.querySelector('#focusCount').textContent = `${periodTasks.length} 项`;
  if (!openTasks.length && !calendarItems.length) {
    focusList.innerHTML = `<div class="dashboard-empty"><i data-lucide="list-todo"></i><strong>${detail ? '暂无未完成执行任务' : '等待项目任务'}</strong><span>${detail ? '批准计划后，未完成任务会在这里形成今天的行动入口。' : '项目进入执行阶段后，今天要做的事情会出现在这里。'}</span></div>`;
    calendarList.innerHTML = '<div class="dashboard-empty dashboard-empty-compact"><i data-lucide="calendar-days"></i><strong>暂无日程数据</strong><span>批准计划和研究任务会在确认后进入日程。</span></div>';
    refreshIcons();
    return;
  }
  if (!openTasks.length) {
    focusList.innerHTML = '<div class="dashboard-empty"><i data-lucide="list-todo"></i><strong>当前范围暂无执行任务</strong><span>日程事项仍会显示在右侧，项目执行任务可稍后补充。</span></div>';
  }
  if (openTasks.length) {
    focusList.innerHTML = openTasks.map((task) => `<button class="dashboard-task-row" type="button"><span class="task-state-dot"></span><span><strong>${escapeHtml(task.title || task.name || '未命名任务')}</strong><small>${escapeHtml(task.status || '待处理')} · ${escapeHtml(detail?.projectName || '当前项目')}</small></span><i data-lucide="chevron-right"></i></button>`).join('');
    focusList.querySelectorAll('.dashboard-task-row').forEach((row) => row.addEventListener('click', () => {
      showToast('请从项目的执行页更新此任务');
      navigatePrimaryView('project');
      showWorkspace('schedule');
    }));
  }
  calendarList.innerHTML = mergedCalendar.slice(0, 5).map((item) => `<div class="dashboard-calendar-row"><strong>${escapeHtml(item.title || '未命名事项')}</strong><small>${escapeHtml(item.date || '日期待确认')} · ${escapeHtml(item.source || '工作台')}</small></div>`).join('');
  refreshIcons();
}

function renderDashboardSignals() {
  const geoPanel = document.querySelector('.dashboard-geo-panel');
  const geo = dashboardState.signals.geo;
  if (geoPanel) {
    const body = geoPanel.querySelector('.dashboard-empty-compact');
    const heading = geoPanel.querySelector('.panel-heading');
    const badge = heading?.querySelector('.status-badge');
    if (badge) badge.textContent = geo?.configured ? `${geo.snapshots.length} 条快照` : '未配置';
    if (body && geo?.configured) {
      const latest = geo.snapshots[geo.snapshots.length - 1];
      body.innerHTML = `<i data-lucide="radar"></i><strong>${latest ? escapeHtml(latest.visibilityLabel || '最近快照') : '查询集已配置'}</strong><span>${escapeHtml(geo.product)} · ${escapeHtml(geo.market)} · ${latest ? escapeHtml(latest.queryText) : '等待第一条人工观测'}</span><button class="button button-quiet" type="button" data-primary-view="geo">查看 GEO</button>`;
    }
  }
  const intelPanel = document.querySelector('.dashboard-intel-panel');
  const content = dashboardState.signals.content;
  if (intelPanel) {
    const body = intelPanel.querySelector('.dashboard-empty-compact');
    const badge = intelPanel.querySelector('.panel-heading .status-badge');
    if (badge) badge.textContent = content ? `${content.assets} 个资产` : '未连接';
    if (body && content) {
      body.innerHTML = `<i data-lucide="images"></i><strong>${content.brief ? '内容工作区已就绪' : '等待内容 Brief'}</strong><span>${content.brief ? escapeHtml(content.brief.topic) : '创建内容 Brief 后，资产状态会在这里汇总。'} · ${content.assets} 个资产任务</span><button class="button button-quiet" type="button" data-primary-view="content">打开内容与资产</button>`;
    }
  }
  refreshIcons();
}

function renderDashboard(projects, currentDetail) {
  dashboardState.currentDetail = currentDetail;
  renderDashboardProjects(projects, currentDetail);
  renderDashboardTasks(currentDetail);
  renderDashboardSignals();
  dashboardState.lastUpdated = new Date();
  const status = document.querySelector('#dashboardStatus');
  document.querySelector('#dashboardStatusText').textContent = currentDetail ? `已读取 ${currentDetail.projectName} 的服务器事实` : '当前工作区尚未载入项目事实';
  const geoState = dashboardState.signals.geo?.configured ? 'GEO 已接入' : 'GEO 待配置';
  const contentState = dashboardState.signals.content ? '内容已同步' : '内容待同步';
  document.querySelector('#dashboardStatusMeta').textContent = `更新于 ${formatDashboardTimestamp(dashboardState.lastUpdated)} · ${geoState} · ${contentState}`;
  status.dataset.state = 'ready';
  document.querySelector('#calendarDateLabel').textContent = dashboardState.period === 'today' ? formatDashboardDate() : dashboardPeriods[dashboardState.period].label;
  refreshIcons();
}

function setDashboardError(message, title = '项目列表暂时无法读取，已保留上次结果') {
  const status = document.querySelector('#dashboardStatus');
  status.dataset.state = 'error';
  document.querySelector('#dashboardStatusText').textContent = title;
  document.querySelector('#dashboardStatusMeta').textContent = message;
}

async function refreshDashboard(currentDetail = null) {
  if (dashboardState.refreshing) return;
  dashboardState.refreshing = true;
  const refreshButton = document.querySelector('#dashboardRefresh');
  const status = document.querySelector('#dashboardStatus');
  refreshButton.disabled = true;
  document.querySelector('#dashboardView').classList.add('is-refreshing');
  status.dataset.state = 'loading';
  document.querySelector('#dashboardStatusText').textContent = '正在同步总控台...';
  document.querySelector('#dashboardStatusMeta').textContent = '读取服务器项目事实';
  try {
    const projectsPromise = api.listProjects();
    const [projects, geoResult, contentResult, calendarResult, suggestionsResult] = await Promise.all([
      projectsPromise,
      (async () => {
        try {
          const sets = await api.listGeoQuerySets();
          const latest = sets.querySets.at(-1);
          if (!latest) return { configured: false, snapshots: [] };
          const snapshots = await api.listGeoSnapshots(latest.querySetId);
          return { configured: true, product: latest.product, market: latest.market, snapshots: snapshots.snapshots };
        } catch { return null; }
      })(),
      (async () => {
        try {
          const [briefs, assets] = await Promise.all([api.listContentBriefs(), api.listContentAssets()]);
          return { brief: briefs.briefs.at(-1) || null, assets: assets.assets.length };
        } catch { return null; }
      })(),
      (async () => {
        try { return (await api.listCalendarItems('all')).items; } catch { return []; }
      })(),
      (async () => {
        try { return (await api.listScheduleSuggestions()).suggestions; } catch { return []; }
      })(),
    ]);
    dashboardState.signals = { geo: geoResult, content: contentResult, calendar: calendarResult, suggestions: suggestionsResult };
    renderDashboard(projects, currentDetail);
  } catch (error) {
    dashboardState.currentDetail = currentDetail || dashboardState.currentDetail || null;
    const hasKnownProject = dashboardState.projects.length > 0 || Boolean(dashboardState.currentDetail);
    // Keep the last known list and detail rendered. A transport failure is not
    // evidence that the workspace has no projects.
    if (hasKnownProject) {
      renderDashboardProjects({ items: dashboardState.projects }, dashboardState.currentDetail);
    } else {
      const list = document.querySelector('#dashboardProjectList');
      list.innerHTML = '<div class="dashboard-empty"><i data-lucide="folder-open"></i><strong>项目列表读取失败</strong><span>当前无法确认工作区项目，请稍后重试。</span></div>';
      document.querySelector('#projectCount').textContent = '读取失败';
    }
    renderDashboardTasks(dashboardState.currentDetail);
    setDashboardError(describeError(error), hasKnownProject ? undefined : '首次读取项目失败，项目状态未知');
    refreshIcons();
  } finally {
    dashboardState.refreshing = false;
    refreshButton.disabled = false;
    document.querySelector('#dashboardView').classList.remove('is-refreshing');
  }
}

async function renderImportedProject(detail, badge = '服务器已保留') {
  document.querySelector('#importProjectName').textContent = detail.projectName;
  document.querySelector('#importProjectFiles').textContent = `${detail.source.filename} · 已批准方案 v${detail.proposal.proposalVersion}：${detail.proposal.filename}`;
  document.querySelector('#breadcrumbProject').textContent = detail.projectName;
  importSummaryKicker.textContent = 'APPROVED PROPOSAL';
  importSummaryBadge.textContent = badge;
  retryProjectLoad.hidden = true;
  importSummary.dataset.state = 'ready';
  importSummary.setAttribute('aria-busy', 'false');
  importSummary.hidden = false;
  setServerStatus(`已恢复项目：${detail.projectName}`, 'ready');
  refreshIcons();
  await reviewWorkbench.setProject(detail);
  scheduleWorkbench.setProject(detail);
  executionWorkbench.setProject(detail);
  await refreshDashboard(detail);
}

function projectIdFromUrl() { return new URLSearchParams(globalThis.location.search).get('projectId'); }

function writeProjectIdToUrl(projectId) {
  const params = new URLSearchParams(globalThis.location.search);
  params.set('projectId', projectId);
  const nextPath = globalThis.location.pathname + '?' + params.toString() + globalThis.location.hash;
  window.history.replaceState({ projectId }, '', nextPath);
}

function setFormBusy(busy) {
  createProjectButton.disabled = busy;
  importForm.setAttribute('aria-busy', busy ? 'true' : 'false');
  document.querySelectorAll('[data-dialog-close]').forEach((button) => { if (button !== cancelImportButton) button.disabled = busy; });
  cancelImportButton.textContent = busy ? '取消上传' : '取消';
}

document.querySelector('#newBrief').addEventListener('click', () => {
  importForm.reset();
  importForm.elements.proposalVersion.value = '1';
  importFormStatus.textContent = '';
  importFormStatus.className = 'form-status';
  retryKeys.clear();
  dialog.showModal();
  importForm.elements.name.focus();
});

document.querySelectorAll('[data-dialog-close]').forEach((button) => button.addEventListener('click', () => {
  if (activeImportController) activeImportController.abort(); else dialog.close();
}));

dialog.addEventListener('cancel', (event) => {
  if (!activeImportController) return;
  event.preventDefault();
  activeImportController.abort();
});

importForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const sourceFile = importForm.elements.sourceFile.files[0];
  const proposalFile = importForm.elements.proposalFile.files[0];
  if (!sourceFile || !proposalFile || !isSupportedImportName(sourceFile.name) || !isSupportedProposalName(proposalFile.name)) {
    importFormStatus.textContent = errorCopy.UNSUPPORTED_FORMAT;
    importFormStatus.className = 'form-status is-error';
    return;
  }
  const input = {
    projectName: importForm.elements.name.value, proposalVersion: Number(importForm.elements.proposalVersion.value),
    approvalConfirmed: importForm.elements.approvalConfirmed.checked, sourceFile, proposalFile,
  };
  const idempotencyKey = retryKeys.get(importFingerprint(input));
  activeImportController = new AbortController();
  setFormBusy(true);
  importFormStatus.textContent = '正在上传文件并等待服务器提交...';
  importFormStatus.className = 'form-status is-working';
  try {
    const { result, detail } = await importThenLoad(api, input, {
      idempotencyKey, signal: activeImportController.signal,
      onProjectId(projectId) { writeProjectIdToUrl(projectId); importFormStatus.textContent = '服务器已提交，正在读取项目事实...'; },
    });
    await renderImportedProject(detail, result.replayed ? '幂等重放已确认' : '服务器已保留');
    retryKeys.clear();
    dialog.close();
    showToast(result.replayed ? '已确认之前提交的同一项目' : '项目已创建，可开始提取候选');
  } catch (error) {
    importFormStatus.textContent = describeError(error);
    importFormStatus.className = error?.code === 'REQUEST_CANCELLED' ? 'form-status is-cancelled' : 'form-status is-error';
  } finally {
    activeImportController = null;
    setFormBusy(false);
  }
});

async function restoreProject() {
  setSummaryState('loading', '正在读取服务器项目...', { badge: '读取中' });
  setServerStatus('正在连接 Server API...', 'loading');
  try {
    const requestedId = projectIdFromUrl();
    const detail = await loadInitialProject(api, requestedId, { onProjectId: writeProjectIdToUrl });
    if (!detail) { setEmptyState(); return; }
    await renderImportedProject(detail, requestedId ? '刷新后已恢复' : '服务器已恢复');
  } catch (error) {
    const message = describeError(error);
    setSummaryState('error', message, { kicker: 'SERVER API ERROR', badge: error?.status === 401 ? '需要认证' : '读取失败', retry: error?.retryable === true || error?.status === 401 });
    setServerStatus(message, 'error');
    reviewWorkbench.clearProject();
    scheduleWorkbench.clearProject();
    executionWorkbench.clearProject();
    dashboardState.currentDetail = null;
    renderDashboardProjects({ items: dashboardState.projects }, null);
    renderDashboardTasks(null);
    setDashboardError(message, '项目详情读取失败，首页列表保持不变');
  }
}

retryProjectLoad.addEventListener('click', restoreProject);

const themeToggle = document.querySelector('#themeToggle');
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#0b0a0f' : '#f5f3f8';
  themeToggle.setAttribute('aria-label', theme === 'dark' ? '切换浅色主题' : '切换深色主题');
  themeToggle.innerHTML = `<i data-lucide="${theme === 'dark' ? 'sun' : 'moon'}"></i>`;
  refreshIcons();
}
applyTheme(matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
themeToggle.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.documentElement.dataset.theme = next;
    document.startViewTransition(() => applyTheme(next));
  }
  else applyTheme(next);
});

const drawer = document.querySelector('#assistantDrawer');
const scrim = document.querySelector('#drawerScrim');
function setDrawer(open) {
  drawer.classList.toggle('is-open', open);
  drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
  scrim.hidden = !open;
  if (open) document.querySelector('#assistantInput').focus();
}
document.querySelector('#assistantToggle').addEventListener('click', () => setDrawer(true));
document.querySelector('#assistantClose').addEventListener('click', () => setDrawer(false));
scrim.addEventListener('click', () => setDrawer(false));
document.querySelector('#assistantForm').addEventListener('submit', (event) => {
  event.preventDefault();
  const input = document.querySelector('#assistantInput');
  if (!input.value.trim()) return;
  showToast('模型 API 尚未配置；助手不会伪造回答或提交审核决定');
  input.value = '';
});

const projectNav = document.querySelector('#projectNav');
const reviewConsole = document.querySelector('#reviewConsole');
const scheduleConsole = document.querySelector('#scheduleWorkbench');
const executionConsole = document.querySelector('#executionWorkbench');
const dashboardView = document.querySelector('#dashboardView');
const modulePlaceholder = document.querySelector('#modulePlaceholder');
const modelCenter = window.MarketOpsModelCenter.createModelCenter({ api, root: modulePlaceholder, onToast: showToast, onIcons: refreshIcons });
const researchWorkbench = window.MarketOpsResearchWorkbench;
const geoWorkbench = window.MarketOpsGeoWorkbench;
const contentWorkbench = window.MarketOpsContentWorkbench;
const calendarWorkbench = window.MarketOpsCalendarWorkbench;
const primaryNavItems = document.querySelectorAll('.nav-item[data-primary-view]');
const primaryViewLinks = document.querySelectorAll('[data-primary-view]');
const projectWorkspaceTabs = document.querySelectorAll('[data-project-workspace]');
let activePrimaryView = 'dashboard';
let currentProjectWorkspace = 'review';

const moduleCopy = {
  research: { kicker: 'RESEARCH & INTELLIGENCE', title: '研究与情报', description: '管理有来源的资讯、研究任务和可审核结论。', icon: 'book-open-check', action: '添加研究来源', body: '资讯来源、RSS、官方网页和研究任务尚未配置。完成连接后，每条结论会保留来源、时间和适用范围。' },
  geo: { kicker: 'GENERATIVE ENGINE OPTIMIZATION', title: 'GEO', description: '用固定查询和时间快照观察产品在搜索与 AI 回答中的可见性。', icon: 'radar', action: '配置首个查询集', body: '尚未配置产品、市场、语言或查询集。当前不显示虚构的可见性分数，也不把一次结果当成稳定排名。' },
  content: { kicker: 'CONTENT & ASSETS', title: '内容与资产', description: '管理内容草稿、渠道版本、生成资产和可控的知识接口。', icon: 'images', action: '新建内容任务', body: '内容任务和资产库尚未接入。生图工作页将在这里管理提示词、模型版本、父子版本和人工审核。Obsidian 连接将采用用户选定范围、只读同步优先。' },
  calendar: { kicker: 'SCHEDULE', title: '日程', description: '从项目、研究和 GEO 任务汇总今天、本周和本月的工作。', icon: 'calendar-days', action: '新建日程', body: '当前还没有可汇总的日程事实。批准计划或人工创建任务后，这里会按来源项目显示。' },
  settings: { kicker: 'SETTINGS & CONNECTIONS', title: '设置中心', description: '配置模型、任务匹配、数据源、Obsidian 和工作区边界。', icon: 'settings-2', action: '添加模型', body: '模型和连接设置尚未接入浏览器。密钥只会保留在服务端环境，浏览器不显示明文凭据。' },
};

function renderModule(view) {
  if (view === 'settings') {
    modelCenter.mount();
    return;
  }
  if (view === 'research') {
    researchWorkbench.mount({ root: modulePlaceholder, api, onToast: showToast, onIcons: refreshIcons, describeError });
    return;
  }
  if (view === 'geo') {
    geoWorkbench.mount({ root: modulePlaceholder, api, onToast: showToast, onIcons: refreshIcons, describeError });
    return;
  }
  if (view === 'content') { contentWorkbench.mount({ root: modulePlaceholder, api, onToast: showToast, onIcons: refreshIcons }); return; }
  if (view === 'calendar') { calendarWorkbench.mount({ root: modulePlaceholder, api, onToast: showToast, onIcons: refreshIcons }); return; }
  const copy = moduleCopy[view] || moduleCopy.research;
  modulePlaceholder.innerHTML = `<section class="module-surface"><header class="page-heading"><div><h1><span class="console-label">${copy.kicker}</span>${copy.title}</h1><p>${copy.description}</p></div><button class="button button-primary" type="button" data-toast="${copy.action}将在对应连接和数据契约完成后开放"><i data-lucide="plus"></i>${copy.action}</button></header><div class="module-status" role="status"><span class="status-signal" aria-hidden="true"><i></i></span><strong>准备中</strong><span>${copy.body}</span></div><div class="module-outline"><div><span class="console-label">PAGE CONTRACT</span><h2>这一页会维护自己的正式状态</h2><p>首页只聚合和跳转；来源、版本、审核、失败和撤回都在本工作区完成。接入前不会用静态演示数据填充。</p></div><i data-lucide="${copy.icon}"></i></div><p class="claim-boundary"><i data-lucide="shield-check"></i>当前为工作台壳和空态；尚未声称外部平台、模型或知识库已连接。</p></section>`;
  refreshIcons();
}

function setPrimaryNav(view) {
  primaryNavItems.forEach((item) => item.classList.toggle('is-active', item.dataset.primaryView === view));
}

function showPrimaryView(view) {
  const project = view === 'project';
  activePrimaryView = view;
  document.body.dataset.primaryView = view;
  dashboardView.hidden = view !== 'dashboard';
  modulePlaceholder.hidden = view === 'dashboard' || project;
  if (view !== 'dashboard' && !project) renderModule(view);
  setPrimaryNav(view);
  document.querySelector('#breadcrumbSection').textContent = view === 'dashboard' ? '总控台' : (project ? '项目' : (moduleCopy[view]?.title || view));
  if (project) showWorkspace(currentProjectWorkspace);
  else if (view === 'dashboard') document.querySelector('#breadcrumbProject').textContent = document.querySelector('#breadcrumbProject').textContent || '当前工作区';
  else document.querySelector('#breadcrumbProject').textContent = '工作区模块';
}

function navigatePrimaryView(view) {
  runDashboardTransition(() => showPrimaryView(view));
}

function showWorkspace(view) {
  const schedule = view === 'schedule';
  currentProjectWorkspace = schedule ? 'schedule' : 'review';
  reviewConsole.hidden = schedule;
  scheduleConsole.hidden = !schedule;
  executionConsole.hidden = !schedule;
  projectWorkspaceTabs.forEach((tab) => {
    const active = tab.dataset.projectWorkspace === currentProjectWorkspace;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  if (activePrimaryView === 'project') setPrimaryNav('project');
  document.querySelector('#projectHeading h1').lastChild.textContent = schedule ? '执行排期工作台' : '交付物审阅台';
  document.querySelector('#projectHeading p').textContent = schedule ? '编辑 WBS、检查依赖，并按明确日历重算排期。' : '核对来源、修订候选，并生成可追溯的人工决定。';
}

primaryViewLinks.forEach((item) => item.addEventListener('click', () => navigatePrimaryView(item.dataset.primaryView)));
projectWorkspaceTabs.forEach((tab) => tab.addEventListener('click', () => showWorkspace(tab.dataset.projectWorkspace)));
document.querySelectorAll('[data-primary-action="research"]').forEach((item) => item.addEventListener('click', () => showPrimaryView('research')));
document.querySelectorAll('[data-dashboard-period]').forEach((button) => button.addEventListener('click', () => setDashboardPeriod(button.dataset.dashboardPeriod)));
document.querySelectorAll('[data-dashboard-filter]').forEach((button) => button.addEventListener('click', () => setDashboardFilter(button.dataset.dashboardFilter)));
document.querySelector('#dashboardRefresh').addEventListener('click', () => refreshDashboard(dashboardState.currentDetail || null));
document.querySelector('#dashboardNewBrief').addEventListener('click', () => document.querySelector('#newBrief').click());
showWorkspace('review');
showPrimaryView('dashboard');

refreshIcons();
restoreProject();
