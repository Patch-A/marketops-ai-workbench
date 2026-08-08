const toast = document.querySelector('#toast');
let toastTimer;

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
document.querySelector('#newBrief').addEventListener('click', () => dialog.showModal());
document.querySelector('#briefForm').addEventListener('submit', (event) => {
  event.preventDefault();
  dialog.close();
  showToast('新项目已创建，正在进入 brief 采集');
});

document.querySelector('.voice-button').addEventListener('click', () => showToast('语音输入入口已预留，接入转写模型后可用'));
refreshIcons();
