const toast = document.querySelector('#toast');
let toastTimer;
const {
  ProjectApiError,
  createProjectApiClient,
  createRetryKeyManager,
  importFingerprint,
  importThenLoad,
  isSupportedImportName,
  isSupportedProposalName,
  loadInitialProject,
} = window.MarketOpsProjectImport;

const iconGlyphs = {
  'arrow-right': '>',
  'arrow-up-right': '↗',
  'check': '✓',
  'check-circle-2': '✓',
  'chevron-down': '⌄',
  'chevron-right': '›',
  'chevrons-up-down': '↕',
  'download': '↓',
  'file-check-2': '✓',
  'flask-conical': '△',
  'folder-kanban': '□',
  'folder-plus': '+',
  'info': 'i',
  'library': '▤',
  'loader-circle': '○',
  'mic': '•',
  'more-horizontal': '···',
  'paperclip': '⌁',
  'plus': '+',
  'radar': '◎',
  'refresh-cw': '↻',
  'search': '⌕',
  'settings-2': '⚙',
  'shield-check': '✓',
  'sparkles': '✦',
  'square': '■',
  'sun-medium': '☼',
  'thumbs-up': '✓',
  'x': '×',
};

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2600);
}

function refreshIcons() {
  document.querySelectorAll('[data-lucide]').forEach((icon) => {
    icon.textContent = iconGlyphs[icon.dataset.lucide] || '·';
    icon.setAttribute('aria-hidden', 'true');
  });
}

document.querySelectorAll('[data-toast]').forEach((button) => {
  button.addEventListener('click', () => showToast(button.dataset.toast));
});

document.querySelectorAll('.nav-item[data-view]').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach((item) => item.classList.remove('is-active'));
    button.classList.add('is-active');
    showToast(`${button.querySelector('span')?.textContent || '视图'}将在下一阶段接入完整数据`);
  });
});

document.querySelectorAll('.recent-project').forEach((button) => {
  button.addEventListener('click', () => {
    showToast('最近项目仍为界面示例，服务器项目只从导入摘要进入');
  });
});

function selectTab(name) {
  document.querySelectorAll('[data-tab]').forEach((item) => item.classList.toggle('is-active', item.dataset.tab === name));
  const labels = { brief: 'Brief', research: '证据研究', strategy: '策略草稿', activation: '激活计划', review: '交付与复盘' };
  showToast(`已打开 ${labels[name] || name} 阶段`);
}

document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => selectTab(button.dataset.tab)));

document.querySelectorAll('.radar-item').forEach((item) => {
  item.addEventListener('click', () => showToast(`已加入工作实验：「${item.dataset.experiment}」`));
});

document.querySelectorAll('.check-toggle').forEach((button) => {
  button.addEventListener('click', () => {
    const card = button.closest('.evidence-card');
    const selected = card.classList.toggle('selected');
    button.setAttribute('aria-label', selected ? '取消采用此来源' : '采用此来源');
    button.innerHTML = selected ? '<i data-lucide="check"></i>' : '<i data-lucide="plus"></i>';
    refreshIcons();
    showToast(selected ? '证据已加入策略上下文' : '证据已从策略上下文移除');
  });
});

document.querySelector('#generateStrategy').addEventListener('click', (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.innerHTML = '<i data-lucide="loader-circle"></i> 正在整理证据';
  refreshIcons();
  showToast('策略助手正在基于已采用证据生成草稿…');
  setTimeout(() => {
    button.disabled = false;
    button.innerHTML = '<i data-lucide="check"></i> 草稿已更新';
    refreshIcons();
    showToast('草稿已更新，下一步需要你评审');
  }, 1200);
});

document.querySelector('#approveIdea').addEventListener('click', (event) => {
  const button = event.currentTarget;
  button.innerHTML = '<i data-lucide="check"></i> 已采用';
  button.classList.add('is-approved');
  button.disabled = true;
  refreshIcons();
  showToast('已记录为人工决定：采用「证据交换站」方向');
});

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
const privacyNote = document.querySelector('.privacy-note span');
if (privacyNote) privacyNote.textContent = '项目资料由当前部署的 Server API 按工作区范围保留';

const api = createProjectApiClient();
const retryKeys = createRetryKeyManager();
let activeImportController = null;

const errorCopy = {
  APPROVAL_REQUIRED: '请确认方案已经人工批准。',
  AUTHORIZATION_REQUIRED: '当前浏览器会话无权访问此工作区，请重新完成服务器认证。',
  CLIENT_CAPABILITY_REQUIRED: '当前浏览器缺少完成导入所需的能力。',
  IDEMPOTENCY_CONFLICT: '同一次重试对应了不同内容，请关闭窗口后重新发起导入。',
  INVALID_DOCUMENT: '文件内容未通过服务器结构校验，请检查后重试。',
  INVALID_INPUT: '导入信息不完整，请检查项目名、方案版本和文件。',
  INVALID_MEDIA_TYPE: '浏览器上传的文件类型无法被服务器接受。',
  INVALID_PROJECT_ID: '当前项目链接无效，请移除链接中的 projectId 后重试。',
  MALFORMED_RESPONSE: '服务器返回了无法验证的响应，未更新页面项目事实。',
  NETWORK_ERROR: '无法连接 Server API。可直接重试，系统会沿用同一请求标识。',
  OBJECT_INTEGRITY_MISMATCH: '服务器文件完整性校验失败，未更新页面项目事实。',
  OBJECT_WRITE_FAILED: '服务器未能保留文件。可直接重试。',
  PAYLOAD_TOO_LARGE: '至少一个文件超过 25 MiB 限制。',
  PROJECT_NOT_FOUND: '服务器中没有找到这个项目，或当前工作区无权访问。',
  REQUEST_CANCELLED: '本次请求已取消。若服务器可能已收到文件，可直接重试。',
  UNSUPPORTED_FORMAT: '文件格式不支持。项目资料限 Markdown、CSV、基础 DOCX；批准方案限 Markdown 或基础 DOCX。',
};

function setServerStatus(message, state = 'idle') {
  if (!serverStatusText) return;
  serverStatusText.textContent = message;
  serverStatusText.dataset.state = state;
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
  setServerStatus('Server API 已连接，当前工作区尚无项目', 'empty');
  document.querySelector('#breadcrumbProject').textContent = '尚未创建项目';
}

function renderImportedProject(detail, badge = '服务器已保留') {
  document.querySelector('#importProjectName').textContent = detail.projectName;
  document.querySelector('#importProjectFiles').textContent = `${detail.source.filename} · 已批准方案 v${detail.proposal.proposalVersion}：${detail.proposal.filename}`;
  document.querySelector('#breadcrumbProject').textContent = detail.projectName;
  document.querySelector('#focusTitle').textContent = detail.projectName;
  const sourceCount = document.querySelector('.source-line span');
  if (sourceCount) sourceCount.textContent = '2 个服务器文件版本已保留';
  importSummaryKicker.textContent = 'APPROVED PROPOSAL';
  importSummaryBadge.textContent = badge;
  retryProjectLoad.hidden = true;
  importSummary.dataset.state = 'ready';
  importSummary.setAttribute('aria-busy', 'false');
  importSummary.hidden = false;
  setServerStatus(`Server API 已恢复项目：${detail.projectName}`, 'ready');
  refreshIcons();
}

function projectIdFromUrl() {
  return new URL(window.location.href).searchParams.get('projectId');
}

function writeProjectIdToUrl(projectId) {
  const url = new URL(window.location.href);
  url.searchParams.set('projectId', projectId);
  window.history.replaceState({ projectId }, '', url);
}

function describeError(error) {
  const code = error instanceof ProjectApiError ? error.code : 'MALFORMED_RESPONSE';
  const base = errorCopy[code] || 'Server API 请求失败，页面未更新项目事实。';
  return error?.requestId ? `${base} 请求编号：${error.requestId}` : base;
}

function setFormBusy(busy) {
  createProjectButton.disabled = busy;
  importForm.setAttribute('aria-busy', busy ? 'true' : 'false');
  document.querySelectorAll('[data-dialog-close]').forEach((button) => {
    if (button !== cancelImportButton) button.disabled = busy;
  });
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
  if (activeImportController) {
    activeImportController.abort();
    return;
  }
  dialog.close();
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
    importFormStatus.textContent = '文件格式不支持。项目资料限 Markdown、CSV、基础 DOCX；批准方案限 Markdown 或基础 DOCX。';
    importFormStatus.className = 'form-status is-error';
    return;
  }

  const input = {
    projectName: importForm.elements.name.value,
    proposalVersion: Number(importForm.elements.proposalVersion.value),
    approvalConfirmed: importForm.elements.approvalConfirmed.checked,
    sourceFile,
    proposalFile,
  };
  const idempotencyKey = retryKeys.get(importFingerprint(input));
  activeImportController = new AbortController();
  setFormBusy(true);
  importFormStatus.textContent = '正在上传文件并等待服务器提交…';
  importFormStatus.className = 'form-status is-working';

  try {
    const { result, detail } = await importThenLoad(api, input, {
      idempotencyKey,
      signal: activeImportController.signal,
      onProjectId(projectId) {
        writeProjectIdToUrl(projectId);
        importFormStatus.textContent = '服务器已提交，正在读取项目事实…';
      },
    });
    renderImportedProject(detail, result.replayed ? '幂等重放已确认' : '服务器已保留');
    retryKeys.clear();
    dialog.close();
    showToast(result.replayed ? '已确认之前提交的同一项目' : '项目已创建，服务器已保留两个文件版本');
  } catch (error) {
    importFormStatus.textContent = describeError(error);
    importFormStatus.className = error?.code === 'REQUEST_CANCELLED' ? 'form-status is-cancelled' : 'form-status is-error';
  } finally {
    activeImportController = null;
    setFormBusy(false);
  }
});

async function restoreProject() {
  setSummaryState('loading', '正在读取服务器项目…', { badge: '读取中' });
  setServerStatus('正在连接 Server API…', 'loading');
  try {
    const requestedId = projectIdFromUrl();
    const detail = await loadInitialProject(api, requestedId, { onProjectId: writeProjectIdToUrl });
    if (!detail) {
      setEmptyState();
      return;
    }
    renderImportedProject(detail, requestedId ? '刷新后已恢复' : '服务器已恢复');
  } catch (error) {
    const message = describeError(error);
    setSummaryState('error', message, {
      kicker: 'SERVER API ERROR',
      badge: error?.status === 401 ? '需要认证' : '读取失败',
      retry: error?.retryable === true || error?.status === 401,
    });
    setServerStatus(message, 'error');
  }
}

retryProjectLoad.addEventListener('click', restoreProject);
restoreProject();

document.querySelector('.voice-button').addEventListener('click', () => showToast('语音输入入口已预留，接入转写模型后可用'));
refreshIcons();
