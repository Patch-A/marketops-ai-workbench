const toast = document.querySelector('#toast');
let toastTimer;
const {
  createArtifactMetadata,
  createProjectRecord,
  isSupportedImportName,
  listProjectRecords,
  openIndexedDbFileStore,
  retainFileRecord,
  saveProjectRecord,
  sha256Blob,
  verifyRetainedProjectFiles,
} = window.MarketOpsProjectImport;

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2600);
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
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
    document.querySelectorAll('.recent-project').forEach((item) => item.classList.remove('is-selected'));
    button.classList.add('is-selected');
    const project = button.dataset.project;
    document.querySelector('#breadcrumbProject').textContent = project;
    showToast(`已切换到「${project}」`);
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
const privacyNote = document.querySelector('.privacy-note span');
if (privacyNote) privacyNote.textContent = '当前原型使用浏览器本地存储，工作区隔离尚未实现';

function renderImportedProject(record) {
  const summary = document.querySelector('#importSummary');
  document.querySelector('#importProjectName').textContent = record.name;
  document.querySelector('#importProjectFiles').textContent = `${record.sourceFile.name} · 已批准方案 v${record.approvedProposal.version}：${record.approvedProposal.name}`;
  document.querySelector('#breadcrumbProject').textContent = record.name;
  document.querySelector('#focusTitle').textContent = record.name;
  const sourceCount = document.querySelector('.source-line span');
  if (sourceCount) sourceCount.textContent = '2 个原始文件版本已保留';
  summary.hidden = false;
  refreshIcons();
}

document.querySelector('#newBrief').addEventListener('click', () => {
  importForm.reset();
  importForm.elements.proposalVersion.value = '1';
  importFormStatus.textContent = '';
  importFormStatus.className = 'form-status';
  dialog.showModal();
});

document.querySelectorAll('[data-dialog-close]').forEach((button) => button.addEventListener('click', () => dialog.close()));

importForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const sourceFile = importForm.elements.sourceFile.files[0];
  const proposalFile = importForm.elements.proposalFile.files[0];
  const proposalIsSupported = proposalFile && /\.(?:md|markdown|docx)$/i.test(proposalFile.name);

  if (!sourceFile || !proposalFile || !isSupportedImportName(sourceFile.name) || !proposalIsSupported) {
    importFormStatus.textContent = '文件格式不支持。项目资料限 Markdown、CSV、基础 DOCX；批准方案限 Markdown 或基础 DOCX。';
    importFormStatus.className = 'form-status is-error';
    return;
  }

  createProjectButton.disabled = true;
  importFormStatus.textContent = '正在计算文件哈希并保留到本地浏览器…';
  importFormStatus.className = 'form-status is-working';

  try {
    const now = new Date().toISOString();
    const randomId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const projectId = `project-${randomId}`;
    const sourceArtifactId = `${projectId}-source-1`;
    const proposalArtifactId = `${projectId}-proposal-${importForm.elements.proposalVersion.value}`;
    const [sourceHash, proposalHash, fileStore] = await Promise.all([
      sha256Blob(sourceFile),
      sha256Blob(proposalFile),
      openIndexedDbFileStore(),
    ]);

    await Promise.all([
      retainFileRecord(sourceFile, sourceArtifactId, fileStore, now),
      retainFileRecord(proposalFile, proposalArtifactId, fileStore, now),
    ]);

    const record = createProjectRecord({
      id: projectId,
      name: importForm.elements.name.value,
      clientName: importForm.elements.clientName.value,
      sourceFile: createArtifactMetadata({
        id: sourceArtifactId,
        name: sourceFile.name,
        type: sourceFile.type,
        size: sourceFile.size,
        sha256: sourceHash,
      }),
      approvedProposal: {
        id: proposalArtifactId,
        version: Number(importForm.elements.proposalVersion.value),
        name: proposalFile.name,
        sha256: proposalHash,
        status: 'approved',
        retained: true,
        approvedAt: now,
      },
      createdAt: now,
    });

    await verifyRetainedProjectFiles(record, fileStore);
    saveProjectRecord(record);
    renderImportedProject(record);
    dialog.close();
    showToast('项目已创建，原始资料与已批准方案版本已保留');
  } catch (error) {
    importFormStatus.textContent = `导入失败：${error.message} 请检查浏览器存储权限后重试。`;
    importFormStatus.className = 'form-status is-error';
  } finally {
    createProjectButton.disabled = false;
  }
});

(async () => {
  try {
    const storedProjects = listProjectRecords();
    if (storedProjects.length === 0) return;
    const fileStore = await openIndexedDbFileStore();
    const latestProject = storedProjects.at(-1);
    await verifyRetainedProjectFiles(latestProject, fileStore);
    renderImportedProject(latestProject);
  } catch {
    showToast('本地项目文件无法定位，请重新导入源文件与批准方案');
  }
})();

document.querySelector('.voice-button').addEventListener('click', () => showToast('语音输入入口已预留，接入转写模型后可用'));
refreshIcons();
