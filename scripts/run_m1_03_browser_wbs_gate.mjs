#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdir, readFile, rm } from 'node:fs/promises';
import { extname, resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');
const TIMEOUT_MS = 20_000;
const IDS = Object.freeze({
  project: '10000000-0000-4000-8000-000000000001',
  sourceArtifact: '10000000-0000-4000-8000-000000000002',
  sourceVersion: '10000000-0000-4000-8000-000000000003',
  proposalArtifact: '10000000-0000-4000-8000-000000000004',
  proposalVersion: '10000000-0000-4000-8000-000000000005',
  run: '10000000-0000-4000-8000-000000000006',
  plan: '10000000-0000-4000-8000-000000000007',
  snapshot: '10000000-0000-4000-8000-000000000008',
  readySnapshot: '10000000-0000-4000-8000-000000000009',
  approval: '10000000-0000-4000-8000-000000000010',
  otherReadySnapshot: '10000000-0000-4000-8000-000000000011',
});
const SHA = 'a'.repeat(64);
const CREATED_AT = '2026-08-15T12:00:00Z';
const CANDIDATES = [
  ['20000000-0000-4000-8000-000000000001', 'deliverable', 'Publish the launch brief.'],
  ['20000000-0000-4000-8000-000000000002', 'milestone', 'Approve the event launch.'],
  ['20000000-0000-4000-8000-000000000003', 'constraint', 'Keep the review window fixed.'],
];
const RESULT_KEYS = new Set([
  'wbsCreated', 'wbsReplayStable', 'taskEditSent', 'planConflictHandled',
  'historicalPlanReadOnly', 'scheduleCalculated', 'scheduleReplayStable',
  'scheduleDigestRendered', 'requestBoundaryPassed', 'responsivePassed',
  'blockedApprovalPrevented', 'approvalReconciled', 'approvalRendered',
  'historicalApprovalReadOnly', 'approvalRequestBoundary',
  'differentSnapshotApprovalRejected', 'approvalReadFailurePreservesPlan',
  'versionChronologyLabels',
  'noConsoleFailures', 'noExternalRequests',
]);

function parseArgs(argv) {
  const values = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!argv[i]?.startsWith('--') || argv[i + 1] === undefined) throw new Error('invalid gate arguments');
    values[argv[i].slice(2)] = argv[i + 1];
  }
  if (!values.browser || !values.profile) throw new Error('missing --browser or --profile');
  return values;
}

function delay(ms) { return new Promise((resolvePromise) => setTimeout(resolvePromise, ms)); }
async function stopBrowser(browser, profile) {
  if (process.platform === 'win32') {
    await new Promise((resolvePromise) => {
      const powershell = resolve(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
      const script = "$target = $env:MARKETOPS_GATE_PROFILE; Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($target) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }";
      const killer = spawn(powershell, ['-NoProfile', '-NonInteractive', '-Command', script], {
        env: { ...process.env, MARKETOPS_GATE_PROFILE: profile }, stdio: 'ignore',
      });
      killer.once('exit', (code) => resolvePromise(code === 0));
      killer.once('error', () => resolvePromise(false));
    });
  } else {
    if (browser.exitCode !== null) return;
    const exited = new Promise((resolvePromise) => browser.once('exit', resolvePromise));
    browser.kill();
    await Promise.race([exited, delay(5_000)]);
  }
}
async function waitUntil(check, label) {
  const deadline = Date.now() + TIMEOUT_MS;
  let lastError;
  while (Date.now() < deadline) {
    try { const value = await check(); if (value) return value; } catch (error) { lastError = error; }
    await delay(50);
  }
  throw new Error(`${label} timed out${lastError ? `: ${lastError.message}` : ''}`);
}
function json(response, status, value, headers = {}) {
  response.writeHead(status, { 'Cache-Control': 'no-store', 'Content-Type': 'application/json', ...headers });
  response.end(JSON.stringify(value));
}
function errorEnvelope(response, status, code) {
  json(response, status, { code, message: 'Synthetic browser gate failure.', retryable: false, requestId: 'gate-request' });
}
function locationFor(index) { return { kind: 'line_range', startLine: index + 3, endLine: index + 3 }; }
function citation(index) {
  return { sourceVersionId: IDS.proposalVersion, sourceSha256: SHA, location: locationFor(index), sectionPath: ['Approved proposal', `Section ${index + 1}`], quote: `Synthetic source quote ${index + 1}` };
}
function reviewDecision(index, action, version) {
  const [candidateId] = CANDIDATES[index];
  return {
    decisionId: `30000000-0000-4000-8000-${String(version).padStart(12, '0')}`,
    reviewVersion: version, candidateId, action, reason: `Synthetic human decision ${version}`,
    comment: null, replacementText: action === 'modify' ? 'Human-edited launch milestone.' : null,
    actorId: '40000000-0000-4000-8000-000000000001', createdAt: `2026-08-15T12:0${version}:00Z`,
  };
}
function reviewDetail() {
  const decisions = [reviewDecision(0, 'approve', 2), reviewDecision(1, 'modify', 3), reviewDecision(2, 'approve', 4)];
  return {
    run: { runId: IDS.run, proposalVersionId: IDS.proposalVersion, proposalSha256: SHA, candidateCount: CANDIDATES.length, latestReviewVersion: 4, createdAt: CREATED_AT },
    selectedReviewVersion: 4, availableReviewVersions: [1, 2, 3, 4], selectedDecision: decisions[2],
    candidates: CANDIDATES.map(([candidateId, kind, text], index) => ({
      ordinal: index + 1, candidateId, kind, text, classification: index === 1 ? 'hypothesis' : 'fact', confidence: 0.9 - index * 0.04,
      sourceCitation: citation(index), review: {
        status: decisions[index].action, replacementText: decisions[index].replacementText, lastDecision: decisions[index],
      },
    })),
  };
}
function task(candidateIndex, version = 1, title = CANDIDATES[candidateIndex][2]) {
  const [candidateId, kind] = CANDIDATES[candidateIndex];
  return {
    taskId: `candidate:${candidateId}`, candidateId, kind, classification: candidateIndex === 1 ? 'hypothesis' : 'fact',
    sourceText: CANDIDATES[candidateIndex][2], title, sourceCitation: citation(candidateIndex), reviewStatus: candidateIndex === 1 ? 'modify' : 'approve',
    durationWorkdays: candidateIndex === 0 ? 2 : 1, predecessors: candidateIndex === 1 ? [`candidate:${CANDIDATES[0][0]}`] : [], ownerRole: 'Marketing owner',
    plannedStart: null, plannedFinish: null, hardDeadline: null, approvedBufferWorkdays: 0, isLocked: false, status: 'not_started',
  };
}
function plan(version = 1, title = CANDIDATES[0][2], latestVersion = version) {
  return {
    planId: IDS.plan, projectId: IDS.project, proposalVersionId: IDS.proposalVersion, proposalSha256: SHA,
    sourceReviewRunId: IDS.run, sourceReviewSnapshotId: '50000000-0000-4000-8000-000000000001', sourceReviewVersion: 4,
    selectedPlanVersion: version, availablePlanVersions: Array.from({ length: latestVersion }, (_, index) => index + 1), status: 'draft', planDigest: SHA,
    createdAt: CREATED_AT, tasks: [task(0, version, title), task(1, version)], controls: [{
      candidateId: CANDIDATES[2][0], kind: 'constraint', classification: 'fact', sourceText: CANDIDATES[2][2], text: CANDIDATES[2][2], sourceCitation: citation(2), reviewStatus: 'approve',
    }],
  };
}
function snapshot(status = 'needs_review') {
  const ready = status === 'ready';
  return {
    snapshotId: ready ? IDS.readySnapshot : IDS.snapshot, planId: IDS.plan, planVersion: 2, status, projectStart: ready ? '2026-08-19' : '2026-08-17', holidays: ['2026-08-18'],
    planDigest: SHA, scheduleDigest: (ready ? 'c' : 'b').repeat(64), createdAt: CREATED_AT,
    topologicalOrder: [`candidate:${CANDIDATES[0][0]}`, `candidate:${CANDIDATES[1][0]}`],
    tasks: [{ taskId: `candidate:${CANDIDATES[0][0]}`, plannedStart: '2026-08-17', plannedFinish: '2026-08-19' }, { taskId: `candidate:${CANDIDATES[1][0]}`, plannedStart: '2026-08-20', plannedFinish: '2026-08-20' }],
    conflicts: ready ? [] : [{ code: 'deadline_miss', taskId: `candidate:${CANDIDATES[1][0]}`, message: 'Synthetic deadline review.' }], deadlineMisses: ready ? [] : [{ taskId: `candidate:${CANDIDATES[1][0]}`, hardDeadline: '2026-08-19', plannedFinish: '2026-08-20' }], sourceDateDrift: [],
  };
}
function approval(scheduleSnapshotId = IDS.readySnapshot) {
  const exactTarget = scheduleSnapshotId === IDS.readySnapshot;
  return { approvalId: IDS.approval, planId: IDS.plan, planVersion: 2, scheduleSnapshotId,
    planDigest: SHA, scheduleDigest: (exactTarget ? 'c' : 'd').repeat(64), reason: exactTarget ? 'Ready for synthetic execution' : 'Another operator decision', approvedAt: CREATED_AT };
}
function createState() { return { planVersion: 1, reviseAttempts: 0, scheduleCalls: 0, approvalCalls: 0, approval: null, failApprovalReadVersion: null, requests: [], bodies: { create: [], revise: [], schedule: [], approval: [] } }; }
async function readBody(request) { let raw = ''; for await (const chunk of request) raw += chunk; return raw ? JSON.parse(raw) : {}; }
function projectDetail() {
  return { projectId: IDS.project, name: 'Synthetic WBS Browser Gate', status: 'active', createdAt: CREATED_AT,
    sourceFile: { artifactId: IDS.sourceArtifact, versionId: IDS.sourceVersion, filename: 'source.md', mediaType: 'text/markdown', sizeBytes: 128 },
    approvedProposal: { artifactId: IDS.proposalArtifact, versionId: IDS.proposalVersion, filename: 'approved.md', mediaType: 'text/markdown', sizeBytes: 512, sha256: SHA, proposalVersion: 3, approvalStatus: 'approved', approvedAt: CREATED_AT } };
}
async function handleApi(request, response, state, url) {
  const method = request.method || 'GET'; state.requests.push({ method, path: url.pathname });
  if (method === 'GET' && url.pathname === '/v1/projects') return json(response, 200, { projects: [{ projectId: IDS.project, name: 'Synthetic WBS Browser Gate', status: 'active', approvedProposalVersion: 3, createdAt: CREATED_AT }] });
  if (method === 'GET' && url.pathname === `/v1/projects/${IDS.project}`) return json(response, 200, projectDetail());
  if (method === 'GET' && url.pathname === `/v1/projects/${IDS.project}/extraction-runs`) return json(response, 200, { runs: [{ runId: IDS.run, proposalVersionId: IDS.proposalVersion, proposalSha256: SHA, candidateCount: CANDIDATES.length, latestReviewVersion: 4, createdAt: CREATED_AT }] });
  if (method === 'GET' && url.pathname === `/v1/projects/${IDS.project}/extraction-runs/${IDS.run}`) return json(response, 200, reviewDetail());
  if (method === 'GET' && url.pathname === `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}`) return json(response, 200, plan(Number(url.searchParams.get('planVersion') || state.planVersion), state.planVersion > 1 ? 'Publish the revised launch brief.' : CANDIDATES[0][2], state.planVersion));
  if (method === 'POST' && url.pathname === `/v1/projects/${IDS.project}/wbs-plans`) { const body = await readBody(request); state.bodies.create.push(body); return json(response, 201, { plan: plan(), replayed: state.bodies.create.length > 1 }); }
  if (method === 'POST' && url.pathname === `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}/revisions`) {
    const body = await readBody(request); state.bodies.revise.push(body); state.reviseAttempts += 1;
    if (state.reviseAttempts === 1) { state.planVersion = 2; return errorEnvelope(response, 409, 'PLAN_CONFLICT'); }
    state.planVersion = 2; return json(response, 201, plan(2, 'Publish the revised launch brief.', 2));
  }
  if (method === 'POST' && url.pathname === `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}/schedule-snapshots`) {
    const body = await readBody(request); state.bodies.schedule.push(body); state.scheduleCalls += 1;
    if (Object.keys(body).sort().join(',') !== 'expectedPlanVersion,holidays,projectStart') return errorEnvelope(response, 400, 'INVALID_INPUT');
    const ready = body.projectStart === '2026-08-19';
    return json(response, 201, { snapshot: snapshot(ready ? 'ready' : 'needs_review'), replayed: !ready && state.scheduleCalls > 1 });
  }
  if (method === 'GET' && url.pathname === `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}/approvals`) {
    const requestedVersion = Number(url.searchParams.get('planVersion') || state.planVersion);
    if (requestedVersion === state.failApprovalReadVersion) return errorEnvelope(response, 503, 'APPROVAL_READ_UNAVAILABLE');
    return json(response, 200, { approval: requestedVersion === 2 ? state.approval : null });
  }
  if (method === 'POST' && url.pathname === `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}/approvals`) {
    const body = await readBody(request); state.bodies.approval.push(body); state.approvalCalls += 1;
    if (Object.keys(body).sort().join(',') !== 'expectedPlanVersion,reason,scheduleSnapshotId') return errorEnvelope(response, 400, 'INVALID_INPUT');
    if (state.approvalCalls === 1) {
      state.approval = approval(IDS.otherReadySnapshot);
      return errorEnvelope(response, 409, 'PLAN_ALREADY_APPROVED');
    }
    state.approval = approval();
    if (state.approvalCalls === 2) { response.writeHead(201, { 'Cache-Control': 'no-store', 'Content-Type': 'application/json' }); response.end('{'); return; }
    return json(response, 201, { approval: state.approval, replayed: true });
  }
  return errorEnvelope(response, 404, 'NOT_FOUND');
}
function contentType(pathname) { return ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' })[extname(pathname)] || 'application/octet-stream'; }
async function startServer(state) {
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url || '/', 'http://127.0.0.1');
      if (url.pathname.startsWith('/v1/')) return await handleApi(request, response, state, url);
      const relative = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
      if (!['index.html', 'app.js', 'project-import.js', 'review-workbench.js', 'schedule-workbench.js', 'execution-workbench.js', 'styles.css'].includes(relative)) { response.writeHead(404); response.end(); return; }
      response.writeHead(200, { 'Content-Type': contentType(relative), 'Cache-Control': 'no-store' }); response.end(await readFile(resolve(ROOT, relative)));
    } catch { if (!response.headersSent) errorEnvelope(response, 500, 'GATE_SERVER_ERROR'); else response.destroy(); }
  });
  await new Promise((resolvePromise, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolvePromise); });
  return { server, baseUrl: `http://127.0.0.1:${server.address().port}` };
}
class CdpClient {
  constructor(url) { this.url = url; this.nextId = 1; this.pending = new Map(); this.waiters = []; this.listeners = []; }
  async connect() { this.socket = new WebSocket(this.url); await new Promise((resolvePromise, reject) => { const timeout = setTimeout(() => reject(new Error('CDP connection timed out')), TIMEOUT_MS); this.socket.addEventListener('open', () => { clearTimeout(timeout); resolvePromise(); }, { once: true }); this.socket.addEventListener('error', () => { clearTimeout(timeout); reject(new Error('CDP connection failed')); }, { once: true }); }); this.socket.addEventListener('message', (event) => this.receive(event.data)); }
  receive(raw) { const message = JSON.parse(raw); if (message.id) { const pending = this.pending.get(message.id); if (!pending) return; this.pending.delete(message.id); if (message.error) pending.reject(new Error(message.error.message || 'CDP command failed')); else pending.resolve(message.result || {}); return; } for (const listener of this.listeners) listener(message.method, message.params || {}); for (const waiter of [...this.waiters]) if (waiter.method === message.method && waiter.predicate(message.params || {})) { clearTimeout(waiter.timeout); this.waiters = this.waiters.filter((item) => item !== waiter); waiter.resolve(message.params || {}); } }
  send(method, params = {}) { const id = this.nextId++; return new Promise((resolvePromise, reject) => { this.pending.set(id, { resolve: resolvePromise, reject }); this.socket.send(JSON.stringify({ id, method, params })); }); }
  on(listener) { this.listeners.push(listener); }
  waitFor(method, predicate = () => true) { return new Promise((resolvePromise, reject) => { const waiter = { method, predicate, resolve: resolvePromise, reject }; waiter.timeout = setTimeout(() => { this.waiters = this.waiters.filter((item) => item !== waiter); reject(new Error(`${method} timed out`)); }, TIMEOUT_MS); this.waiters.push(waiter); }); }
  close() { this.socket?.close(); }
}
async function evaluate(client, expression, awaitPromise = false) { const result = await client.send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true, userGesture: true }); if (result.exceptionDetails) throw new Error(`browser evaluation failed: ${result.exceptionDetails.exception?.description || 'exception'}`); return result.result?.value; }
async function navigate(client, url) { const loaded = client.waitFor('Page.loadEventFired'); const result = await client.send('Page.navigate', { url }); if (result.errorText) throw new Error(`navigation failed: ${result.errorText}`); await loaded; }
async function waitExpression(client, expression, label) { return waitUntil(() => evaluate(client, expression), label); }
function click(selector) { return `document.querySelector(${JSON.stringify(selector)})?.click(); true`; }
function validateResult(result) { if (Object.keys(result).length !== RESULT_KEYS.size || Object.keys(result).some((key) => !RESULT_KEYS.has(key))) throw new Error('browser result fields do not match gate contract'); if (Object.values(result).some((value) => value !== true)) throw new Error('browser gate result contains a failed check'); }

async function run(args) {
  const state = createState(); const profile = resolve(args.profile); await rm(profile, { recursive: true, force: true }); await mkdir(profile, { recursive: true });
  const { server, baseUrl } = await startServer(state); const browser = spawn(resolve(args.browser), ['--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-extensions', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'ignore'] });
  let client;
  try {
    const port = await waitUntil(async () => (await readFile(resolve(profile, 'DevToolsActivePort'), 'utf8')).trim().split(/\r?\n/)[0], 'Chrome DevTools port');
    const target = await waitUntil(async () => { const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' }); return response.ok ? response.json() : null; }, 'Chrome target');
    client = new CdpClient(target.webSocketDebuggerUrl); await client.connect();
    const consoleFailures = []; const externalRequests = [];
    client.on((method, params) => { if (method === 'Runtime.exceptionThrown') consoleFailures.push('runtime'); if (method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(params.type)) consoleFailures.push(`console-${params.type}`); if (method === 'Network.requestWillBeSent') { const url = new URL(params.request.url); if (/^https?:$/.test(url.protocol) && url.origin !== baseUrl) externalRequests.push(url.origin); } });
    await Promise.all([client.send('Page.enable'), client.send('Runtime.enable'), client.send('Network.enable')]); await navigate(client, `${baseUrl}/?projectId=${IDS.project}`);
    await waitExpression(client, "document.querySelector('#scheduleWorkbench')?.dataset.state === 'empty'", 'schedule empty state');
    await evaluate(client, click('#scheduleNav')); await waitExpression(client, "document.querySelector('#createWbsPlan')?.disabled === false", 'create WBS enabled');
    await evaluate(client, click('#createWbsPlan')); await waitExpression(client, "document.querySelector('[data-task-id]') && document.querySelector('#schedulePlanVersion')?.value === '1'", 'WBS created');
    const wbsCreated = state.bodies.create.length === 1 && state.bodies.create[0].reviewRunId === IDS.run && state.bodies.create[0].reviewVersion === 4;
    await evaluate(client, click('#createWbsPlan')); await waitExpression(client, "document.querySelector('[data-task-id]')", 'WBS replay');
    const wbsReplayStable = state.bodies.create.length === 2 && state.bodies.create[1].reviewRunId === IDS.run;
    await evaluate(client, `(() => {
      const row = document.querySelector('[data-task-id="candidate:${CANDIDATES[0][0]}"]');
      const values = {
        title: 'Publish the revised launch brief.', plannedStart: '2026-08-17',
        plannedFinish: '2026-08-19', hardDeadline: '2026-08-20', approvedBufferWorkdays: '1',
      };
      for (const [field, value] of Object.entries(values)) {
        const input = row.querySelector('[data-task-field="' + field + '"]');
        input.value = value; input.dispatchEvent(new Event('input', { bubbles: true }));
      }
      const lock = row.querySelector('[data-task-field="isLocked"]');
      lock.checked = true; lock.dispatchEvent(new Event('input', { bubbles: true }));
      const dependency = document.querySelector('[data-task-id="candidate:${CANDIDATES[1][0]}"] [data-task-field="predecessors"]');
      dependency.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    })()`);
    await waitExpression(client, "document.querySelector('#saveWbsRevision')?.disabled === false", 'WBS revision enabled');
    await evaluate(client, click('#saveWbsRevision')); await waitExpression(client, "document.querySelector('#scheduleStatus')?.dataset.state === 'error'", 'plan conflict');
    const conflictBeforeRefresh = state.requests.filter((request) => request.method === 'GET' && request.path.endsWith(`/wbs-plans/${IDS.plan}`)).length;
    await evaluate(client, click('#refreshWbsPlan')); await waitExpression(client, "document.querySelector('#schedulePlanVersion')?.value === '2'", 'plan conflict refresh');
    const conflictAfterRefresh = state.requests.filter((request) => request.method === 'GET' && request.path.endsWith(`/wbs-plans/${IDS.plan}`)).length;
    const planConflictHandled = state.reviseAttempts === 1 && conflictAfterRefresh > conflictBeforeRefresh;
    await evaluate(client, "document.querySelector('#schedulePlanVersion').value = '1'; document.querySelector('#schedulePlanVersion').dispatchEvent(new Event('change')); true");
    await waitExpression(client, "document.querySelector('#schedulePlanVersion')?.value === '1' && document.querySelector('#scheduleWorkbench')?.getAttribute('aria-busy') === 'false' && document.querySelector('#scheduleStatusText')?.textContent.includes('历史 WBS v1')", 'historical plan');
    const historicalPlanReadOnly = await evaluate(client, "Boolean([...document.querySelectorAll('[data-task-field]')].every((field) => field.disabled))");
    await evaluate(client, "document.querySelector('#schedulePlanVersion').value = '2'; document.querySelector('#schedulePlanVersion').dispatchEvent(new Event('change')); true");
    try { await waitExpression(client, "document.querySelector('#schedulePlanVersion')?.value === '2' && [...document.querySelectorAll('[data-task-field]')].every((field) => field.disabled === false)", 'current plan'); }
    catch (error) { const debug = await evaluate(client, "({ version: document.querySelector('#schedulePlanVersion')?.value, status: document.querySelector('#scheduleStatusText')?.textContent, busy: document.querySelector('#scheduleWorkbench')?.getAttribute('aria-busy'), disabled: [...document.querySelectorAll('[data-task-field]')].filter((field) => field.disabled).length, total: document.querySelectorAll('[data-task-field]').length })"); throw new Error(`${error.message}; state=${JSON.stringify(debug)}`); }
    const revisedTasks = Object.fromEntries((state.bodies.revise[0]?.taskUpdates || []).map((item) => [item.taskId, item.changes]));
    const primaryChanges = revisedTasks[`candidate:${CANDIDATES[0][0]}`] || {};
    const dependencyChanges = revisedTasks[`candidate:${CANDIDATES[1][0]}`] || {};
    const taskEditSent = state.bodies.revise.length === 1 && state.bodies.revise[0].expectedPlanVersion === 1
      && primaryChanges.title === 'Publish the revised launch brief.' && primaryChanges.plannedStart === '2026-08-17'
      && primaryChanges.plannedFinish === '2026-08-19' && primaryChanges.hardDeadline === '2026-08-20'
      && primaryChanges.approvedBufferWorkdays === 1 && primaryChanges.isLocked === true
      && dependencyChanges.predecessors?.[0] === `candidate:${CANDIDATES[0][0]}`;
    await evaluate(client, "document.querySelector('#scheduleProjectStart').value = '2026-08-17'; document.querySelector('#scheduleHolidays').value = '2026-08-18'; document.querySelector('#scheduleProjectStart').dispatchEvent(new Event('input', { bubbles: true })); true");
    const scheduleInputs = await evaluate(client, "({ start: document.querySelector('#scheduleProjectStart')?.value, holidays: document.querySelector('#scheduleHolidays')?.value, version: document.querySelector('#schedulePlanVersion')?.value, historical: [...document.querySelectorAll('[data-task-field]')].some((field) => field.disabled) })");
    if (scheduleInputs.start !== '2026-08-17' || scheduleInputs.holidays !== '2026-08-18' || scheduleInputs.version !== '2' || scheduleInputs.historical) throw new Error(`schedule inputs invalid: ${JSON.stringify(scheduleInputs)}`);
    await waitExpression(client, "document.querySelector('#recalculateSchedule')?.disabled === false", 'schedule enabled'); await evaluate(client, click('#recalculateSchedule')); await waitExpression(client, "['ready','error'].includes(document.querySelector('#scheduleStatus')?.dataset.state)", 'schedule response');
    const scheduleState = await evaluate(client, "({ state: document.querySelector('#scheduleStatus')?.dataset.state, text: document.querySelector('#scheduleStatusText')?.textContent, summary: document.querySelector('#scheduleSummary')?.textContent })");
    if (scheduleState.state !== 'ready') throw new Error(`schedule response failed: ${scheduleState.text}; body=${JSON.stringify(state.bodies.schedule)}`);
    await waitExpression(client, "document.querySelector('#scheduleSummary .schedule-result-note')?.textContent.includes('bbbbbbbbbbbb')", 'schedule summary');
    const scheduleCalculated = state.bodies.schedule.length === 1 && state.bodies.schedule[0].expectedPlanVersion === 2;
    const blockedApprovalPrevented = await evaluate(client, "document.querySelector('#planApprovalState')?.textContent === 'BLOCKED' && document.querySelector('#approveWbsPlan')?.disabled === true");
    await evaluate(client, click('#recalculateSchedule')); await waitExpression(client, "document.querySelector('#scheduleSummary .schedule-result-note')?.textContent.includes('bbbbbbbbbbbb')", 'schedule replay');
    const scheduleReplayStable = state.bodies.schedule.length === 2; const scheduleDigestRendered = await evaluate(client, "document.querySelector('#scheduleSummary')?.textContent.includes('bbbbbbbbbbbb')");
    await evaluate(client, "document.querySelector('#scheduleProjectStart').value = '2026-08-19'; true");
    await evaluate(client, click('#recalculateSchedule')); await waitExpression(client, "document.querySelector('#planApprovalState')?.textContent === 'READY'", 'ready approval state');
    await evaluate(client, "document.querySelector('#planApprovalReason').value = 'Ready for synthetic execution'; document.querySelector('#planApprovalReason').dispatchEvent(new Event('input', { bubbles: true })); true");
    await waitExpression(client, "document.querySelector('#approveWbsPlan')?.disabled === false", 'approval enabled');
    await evaluate(client, click('#approveWbsPlan')); await waitExpression(client, "document.querySelector('#scheduleStatusText')?.textContent.includes('另一份排期快照')", 'different snapshot approval conflict');
    const differentSnapshotApprovalRejected = state.approvalCalls === 1 && state.approval?.scheduleSnapshotId === IDS.otherReadySnapshot
      && await evaluate(client, "document.querySelector('#scheduleStatus')?.dataset.state === 'error' && document.querySelector('#planApprovalState')?.textContent === 'APPROVED'");
    state.approval = null;
    await evaluate(client, click('#refreshWbsPlan')); await waitExpression(client, "document.querySelector('#planApprovalState')?.textContent === 'WAITING'", 'approval conflict reset');
    await evaluate(client, "document.querySelector('#scheduleProjectStart').value = '2026-08-19'; true");
    await evaluate(client, click('#recalculateSchedule')); await waitExpression(client, "document.querySelector('#planApprovalState')?.textContent === 'READY'", 'approval retry ready');
    await evaluate(client, "document.querySelector('#planApprovalReason').value = 'Ready for synthetic execution'; document.querySelector('#planApprovalReason').dispatchEvent(new Event('input', { bubbles: true })); true");
    const approvalReadsBefore = state.requests.filter((request) => request.method === 'GET' && request.path.endsWith('/approvals')).length;
    await evaluate(client, click('#approveWbsPlan')); await waitExpression(client, "document.querySelector('#planApprovalState')?.textContent === 'APPROVED'", 'approval reconciliation');
    const approvalReadsAfter = state.requests.filter((request) => request.method === 'GET' && request.path.endsWith('/approvals')).length;
    const approvalReconciled = state.approvalCalls === 2 && approvalReadsAfter > approvalReadsBefore;
    const approvalRendered = await evaluate(client, "document.querySelector('#planApprovalRecord')?.textContent.includes('cccccccccccc') && document.querySelector('#planApprovalReason')?.value === 'Ready for synthetic execution' && document.querySelector('#planApprovalReason')?.disabled === true");
    await evaluate(client, "document.querySelector('#schedulePlanVersion').value = '1'; document.querySelector('#schedulePlanVersion').dispatchEvent(new Event('change')); true");
    await waitExpression(client, "document.querySelector('#planApprovalState')?.textContent === 'UNAPPROVED'", 'historical approval state');
    const historicalApprovalReadOnly = await evaluate(client, "document.querySelector('#approveWbsPlan')?.disabled === true && document.querySelector('#planApprovalReason')?.disabled === true");
    const versionChronologyLabels = await evaluate(client, "(() => { const options = [...document.querySelector('#schedulePlanVersion').options]; return options.find((item) => item.value === '1')?.textContent.includes('历史') && options.find((item) => item.value === '2')?.textContent.includes('当前'); })()");
    state.failApprovalReadVersion = 2;
    await evaluate(client, "document.querySelector('#schedulePlanVersion').value = '2'; document.querySelector('#schedulePlanVersion').dispatchEvent(new Event('change')); true");
    await waitExpression(client, "document.querySelector('#scheduleStatusText')?.textContent.includes('已保留当前计划')", 'approval read failure');
    const approvalReadFailurePreservesPlan = await evaluate(client, "document.querySelector('#schedulePlanVersion')?.value === '1' && [...document.querySelectorAll('[data-task-field]')].every((field) => field.disabled)");
    state.failApprovalReadVersion = null;
    await evaluate(client, "document.querySelector('#schedulePlanVersion').value = '2'; document.querySelector('#schedulePlanVersion').dispatchEvent(new Event('change')); true");
    await waitExpression(client, "document.querySelector('#planApprovalState')?.textContent === 'APPROVED'", 'current approval state');
    const allowedCreate = new Set(['reviewRunId', 'reviewVersion']); const allowedRevision = new Set(['expectedPlanVersion', 'taskUpdates']); const allowedSchedule = new Set(['expectedPlanVersion', 'projectStart', 'holidays']); const allowedApproval = new Set(['expectedPlanVersion', 'scheduleSnapshotId', 'reason']);
    const exact = (body, keys) => Object.keys(body).sort().join(',') === [...keys].sort().join(',');
    const approvalRequestBoundary = state.bodies.approval.length === 2 && state.bodies.approval.every((body) => exact(body, allowedApproval)) && state.bodies.approval.every((body) => !Object.keys(body).some((key) => /scope|actor|digest|timestamp|approved/i.test(key)));
    const requestBoundaryPassed = state.bodies.create.every((body) => exact(body, allowedCreate)) && state.bodies.revise.every((body) => exact(body, allowedRevision)) && state.bodies.schedule.every((body) => exact(body, allowedSchedule)) && approvalRequestBoundary && !state.requests.some((request) => /scope|actor|digest|timestamp/i.test(request.path));
    let responsivePassed = true; for (const width of [375, 1440]) { await client.send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: width < 768 }); const layout = await evaluate(client, "(() => { const visible = [...document.querySelectorAll('button:not([hidden]),input:not([hidden]),select:not([hidden]),textarea:not([hidden])')].filter((element) => { const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0; }); const approval = document.querySelector('#planApproval')?.getBoundingClientRect(); return { noOverflow: document.documentElement.scrollWidth <= innerWidth, approvalFits: Boolean(approval && approval.left >= 0 && approval.right <= innerWidth), visible: visible.length > 0 && visible.every((element) => { const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0; }) }; })()"); responsivePassed &&= layout.noOverflow && layout.approvalFits && layout.visible; }
    const result = { wbsCreated, wbsReplayStable, taskEditSent, planConflictHandled, historicalPlanReadOnly, scheduleCalculated, scheduleReplayStable, scheduleDigestRendered, requestBoundaryPassed, responsivePassed, blockedApprovalPrevented, approvalReconciled, approvalRendered, historicalApprovalReadOnly, approvalRequestBoundary, differentSnapshotApprovalRejected, approvalReadFailurePreservesPlan, versionChronologyLabels, noConsoleFailures: consoleFailures.length === 0, noExternalRequests: externalRequests.length === 0 };
    try { validateResult(result); } catch (error) { throw new Error(`${error.message}; result=${JSON.stringify(result)}`); }
    return result;
  } finally { client?.close(); await stopBrowser(browser, profile); await new Promise((resolvePromise) => server.close(resolvePromise)); await rm(profile, { recursive: true, force: true, maxRetries: 50, retryDelay: 200 }); }
}
if (process.argv[2] === '--self-test') { validateResult(Object.fromEntries([...RESULT_KEYS].map((key) => [key, true]))); process.stdout.write('M1-03 browser WBS gate contract passed\n'); } else { try { process.stdout.write(`${JSON.stringify(await run(parseArgs(process.argv.slice(2))))}\n`); } catch (error) { process.stderr.write(`M1-03 browser WBS gate failed: ${error.message}\n`); process.exitCode = 1; } }
