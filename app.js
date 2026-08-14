/* finesse · workflow client · server facts · explicit approval · bounded motion */
const toast = document.querySelector('#toast');
let toastTimer;
const {
  ProjectApiError, createProjectApiClient, createRetryKeyManager, importFingerprint,
  importThenLoad, isSupportedImportName, isSupportedProposalName, loadInitialProject,
} = window.MarketOpsProjectImport;

const iconGlyphs = {
  'arrow-right': '>', 'check': '✓', 'chevron-right': '›', 'file-check': '✓',
  'git-branch': '⑂', history: '↶', info: 'i', library: '▤',
  'mouse-pointer-2': '↖', pencil: '✎', plus: '+', 'refresh-cw': '↻',
  'scan-text': '⌗', send: '↑', 'shield-check': '✓', sparkles: '✦', sun: '☼', moon: '☾', x: '×',
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

document.querySelectorAll('[data-toast]').forEach((button) => button.addEventListener('click', () => showToast(button.dataset.toast)));

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
  if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(() => applyTheme(next));
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

const scheduleNav = document.querySelector('#scheduleNav');
const reviewNav = document.querySelector('.nav-item.is-active');
const reviewConsole = document.querySelector('#reviewConsole');
const scheduleConsole = document.querySelector('#scheduleWorkbench');
function showWorkspace(view) {
  const schedule = view === 'schedule';
  reviewConsole.hidden = schedule;
  scheduleConsole.hidden = !schedule;
  scheduleNav.classList.toggle('is-active', schedule);
  reviewNav.classList.toggle('is-active', !schedule);
  document.querySelector('.page-heading h1').lastChild.textContent = schedule ? '执行排期工作台' : '交付物审阅台';
  document.querySelector('.page-heading p').textContent = schedule ? '编辑 WBS、检查依赖，并按明确日历重算排期。' : '核对来源、修订候选，并生成可追溯的人工作业。';
}
scheduleNav.addEventListener('click', () => showWorkspace('schedule'));
reviewNav.addEventListener('click', () => showWorkspace('review'));
showWorkspace('review');

refreshIcons();
restoreProject();
