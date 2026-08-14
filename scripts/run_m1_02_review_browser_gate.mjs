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
  actor: '10000000-0000-4000-8000-000000000007',
});
const SHA = 'a'.repeat(64);
const CREATED_AT = '2026-08-13T12:00:00Z';
const CANDIDATE_IDS = Array.from({ length: 5 }, (_, index) => `20000000-0000-4000-8000-00000000000${index + 1}`);
const RESULT_KEYS = new Set([
  'singleRunAfterUncertainCreate', 'sameKeyReplayObserved', 'citationsRendered',
  'approveModifyRejectCompleted', 'conflictReconciledByGet', 'uncertainDecisionReconciledByGet',
  'historyIsReadOnly', 'themeSwitchPassed', 'responsivePassed', 'noConsoleFailures',
  'noExternalRequests', 'requestBoundaryPassed',
]);

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    if (!argv[index]?.startsWith('--') || argv[index + 1] === undefined) throw new Error('invalid gate arguments');
    values[argv[index].slice(2)] = argv[index + 1];
  }
  if (!values.browser || !values.profile) throw new Error('missing --browser or --profile');
  return values;
}

function delay(milliseconds) { return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds)); }

async function waitUntil(check, label) {
  const deadline = Date.now() + TIMEOUT_MS;
  let lastError;
  while (Date.now() < deadline) {
    try { const result = await check(); if (result) return result; } catch (error) { lastError = error; }
    await delay(50);
  }
  throw new Error(`${label} timed out${lastError ? `: ${lastError.message}` : ''}`);
}

function json(response, status, value, headers = {}) {
  const body = JSON.stringify(value);
  response.writeHead(status, { 'Cache-Control': 'no-store', 'Content-Type': 'application/json', ...headers });
  response.end(body);
}

function errorEnvelope(response, status, code) {
  json(response, status, { code, message: 'Synthetic browser gate failure.', retryable: false, requestId: 'gate-request' });
}

function locationFor(index) {
  return index === 1
    ? { kind: 'markdown_table_cell', line: 12, columnIndex: 2 }
    : { kind: 'line_range', startLine: 3 + index, endLine: 3 + index };
}

function baseCandidates() {
  const kinds = ['deliverable', 'milestone', 'constraint', 'assumption', 'deliverable'];
  return CANDIDATE_IDS.map((candidateId, index) => ({
    ordinal: index + 1,
    candidateId,
    kind: kinds[index],
    text: `Synthetic cited candidate ${index + 1}`,
    classification: index === 3 ? 'hypothesis' : 'fact',
    confidence: 0.91 - index * 0.03,
    sourceCitation: {
      sourceVersionId: IDS.proposalVersion,
      sourceSha256: SHA,
      location: locationFor(index),
      sectionPath: ['Approved proposal', `Section ${index + 1}`],
      quote: `Bounded source quote ${index + 1}`,
    },
  }));
}

function createState() {
  return {
    runCreated: false, createAttempts: 0, createKeys: [], createDropConsumed: false,
    latestVersion: 1, decisions: [], conflictInjected: false, uncertainDecisionInjected: false,
    requests: [], createBodies: [], decisionBodies: [], candidateBase: baseCandidates(),
  };
}

function decisionAt(state, candidateId, version) {
  return [...state.decisions].reverse().find((decision) => decision.candidateId === candidateId && decision.reviewVersion <= version) || null;
}

function runSummary(state) {
  return {
    runId: IDS.run, proposalVersionId: IDS.proposalVersion, proposalSha256: SHA,
    candidateCount: state.candidateBase.length, latestReviewVersion: state.latestVersion, createdAt: CREATED_AT,
  };
}

function reviewDetail(state, selectedVersion = state.latestVersion) {
  const selectedDecision = selectedVersion === 1
    ? null : state.decisions.find((decision) => decision.reviewVersion === selectedVersion) || null;
  return {
    run: runSummary(state), selectedReviewVersion: selectedVersion,
    availableReviewVersions: Array.from({ length: state.latestVersion }, (_, index) => index + 1),
    selectedDecision,
    candidates: state.candidateBase.map((candidate) => {
      const decision = decisionAt(state, candidate.candidateId, selectedVersion);
      return {
        ...candidate,
        review: {
          status: decision?.action || 'pending',
          replacementText: decision?.replacementText || null,
          lastDecision: decision,
        },
      };
    }),
  };
}

function projectDetail() {
  return {
    projectId: IDS.project, name: 'M1-02 Synthetic Review', status: 'active', createdAt: CREATED_AT,
    sourceFile: { artifactId: IDS.sourceArtifact, versionId: IDS.sourceVersion, filename: 'source.md', mediaType: 'text/markdown', sizeBytes: 128 },
    approvedProposal: {
      artifactId: IDS.proposalArtifact, versionId: IDS.proposalVersion, filename: 'approved.md',
      mediaType: 'text/markdown', sizeBytes: 512, sha256: SHA, proposalVersion: 3,
      approvalStatus: 'approved', approvedAt: CREATED_AT,
    },
  };
}

async function readBody(request) {
  let raw = '';
  for await (const chunk of request) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}

function createDecision(state, input, overrides = {}) {
  state.latestVersion += 1;
  const decision = {
    decisionId: `30000000-0000-4000-8000-${String(state.latestVersion).padStart(12, '0')}`,
    reviewVersion: state.latestVersion, candidateId: input.candidateId, action: input.action,
    reason: input.reason, comment: input.comment || null,
    replacementText: input.action === 'modify' ? input.replacementText : null,
    actorId: IDS.actor, createdAt: `2026-08-13T12:${String(state.latestVersion).padStart(2, '0')}:00Z`,
    ...overrides,
  };
  state.decisions.push(decision);
  return decision;
}

async function handleApi(request, response, state, url) {
  const method = request.method || 'GET';
  state.requests.push({ method, path: url.pathname });
  if (method === 'GET' && url.pathname === '/v1/projects') {
    return json(response, 200, { projects: [{ projectId: IDS.project, name: 'M1-02 Synthetic Review', status: 'active', approvedProposalVersion: 3, createdAt: CREATED_AT }] });
  }
  if (method === 'GET' && url.pathname === `/v1/projects/${IDS.project}`) return json(response, 200, projectDetail());
  if (method === 'GET' && url.pathname === `/v1/projects/${IDS.project}/extraction-runs`) {
    return json(response, 200, { runs: state.runCreated ? [runSummary(state)] : [] });
  }
  if (method === 'POST' && url.pathname === `/v1/projects/${IDS.project}/extraction-runs`) {
    const body = await readBody(request);
    state.createBodies.push(body);
    const key = String(request.headers['idempotency-key'] || '');
    state.createAttempts += 1;
    state.createKeys.push(key);
    if (Object.keys(body).sort().join(',') !== 'expectedProposalSha256,expectedProposalVersionId'
      || body.expectedProposalVersionId !== IDS.proposalVersion || body.expectedProposalSha256 !== SHA || key.length < 8) {
      return errorEnvelope(response, 400, 'INVALID_INPUT');
    }
    const replayed = state.runCreated;
    state.runCreated = true;
    if (!state.createDropConsumed) {
      state.createDropConsumed = true;
      json(response, 200, { uncertain: true });
      return;
    }
    return json(response, replayed ? 200 : 201, {
      runId: IDS.run, reviewVersion: 1, candidateCount: state.candidateBase.length,
      proposalVersionId: IDS.proposalVersion, proposalSha256: SHA, replayed, createdAt: CREATED_AT,
    }, { Location: `/v1/projects/${IDS.project}/extraction-runs/${IDS.run}` });
  }
  if (method === 'GET' && url.pathname === `/v1/projects/${IDS.project}/extraction-runs/${IDS.run}`) {
    const selected = url.searchParams.has('reviewVersion') ? Number(url.searchParams.get('reviewVersion')) : state.latestVersion;
    return json(response, 200, reviewDetail(state, selected));
  }
  if (method === 'POST' && url.pathname === `/v1/projects/${IDS.project}/extraction-runs/${IDS.run}/decisions`) {
    const body = await readBody(request);
    state.decisionBodies.push(body);
    const expectedKeys = new Set(['expectedReviewVersion', 'candidateId', 'action', 'reason', 'comment', 'replacementText']);
    if (Object.keys(body).some((key) => !expectedKeys.has(key))) return errorEnvelope(response, 400, 'INVALID_INPUT');
    if (body.candidateId === CANDIDATE_IDS[3] && !state.conflictInjected) {
      state.conflictInjected = true;
      createDecision(state, { candidateId: CANDIDATE_IDS[0], action: 'approve', reason: 'Concurrent reviewer decision' });
      return errorEnvelope(response, 409, 'REVIEW_CONFLICT');
    }
    if (body.expectedReviewVersion !== state.latestVersion) return errorEnvelope(response, 409, 'REVIEW_CONFLICT');
    const decision = createDecision(state, body);
    if (body.candidateId === CANDIDATE_IDS[4] && !state.uncertainDecisionInjected) {
      state.uncertainDecisionInjected = true;
      json(response, 200, { uncertain: true });
      return;
    }
    return json(response, 201, { runId: IDS.run, reviewVersion: state.latestVersion, decision }, {
      Location: `/v1/projects/${IDS.project}/extraction-runs/${IDS.run}?reviewVersion=${state.latestVersion}`,
    });
  }
  return errorEnvelope(response, 404, 'NOT_FOUND');
}

function contentType(pathname) {
  return ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' })[extname(pathname)] || 'application/octet-stream';
}

async function startServer(state) {
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url || '/', 'http://127.0.0.1');
      if (url.pathname.startsWith('/v1/')) return await handleApi(request, response, state, url);
      const relative = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
      if (!['index.html', 'app.js', 'project-import.js', 'review-workbench.js', 'schedule-workbench.js', 'styles.css'].includes(relative)) {
        response.writeHead(404); response.end(); return;
      }
      const body = await readFile(resolve(ROOT, relative));
      response.writeHead(200, { 'Content-Type': contentType(relative), 'Cache-Control': 'no-store' });
      response.end(body);
    } catch {
      if (!response.headersSent) errorEnvelope(response, 500, 'GATE_SERVER_ERROR');
      else response.destroy();
    }
  });
  await new Promise((resolvePromise, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolvePromise); });
  const address = server.address();
  return { server, baseUrl: `http://127.0.0.1:${address.port}` };
}

class CdpClient {
  constructor(url) { this.url = url; this.nextId = 1; this.pending = new Map(); this.waiters = []; this.listeners = []; }
  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolvePromise, reject) => {
      const timeout = setTimeout(() => reject(new Error('CDP connection timed out')), TIMEOUT_MS);
      this.socket.addEventListener('open', () => { clearTimeout(timeout); resolvePromise(); }, { once: true });
      this.socket.addEventListener('error', () => { clearTimeout(timeout); reject(new Error('CDP connection failed')); }, { once: true });
    });
    this.socket.addEventListener('message', (event) => this.receive(event.data));
  }
  receive(raw) {
    const message = JSON.parse(raw);
    if (message.id) {
      const pending = this.pending.get(message.id); if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message || 'CDP command failed')); else pending.resolve(message.result || {});
      return;
    }
    for (const listener of this.listeners) listener(message.method, message.params || {});
    for (const waiter of [...this.waiters]) {
      if (waiter.method === message.method && waiter.predicate(message.params || {})) {
        clearTimeout(waiter.timeout); this.waiters = this.waiters.filter((item) => item !== waiter); waiter.resolve(message.params || {});
      }
    }
  }
  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolvePromise, reject) => { this.pending.set(id, { resolve: resolvePromise, reject }); this.socket.send(JSON.stringify({ id, method, params })); });
  }
  on(listener) { this.listeners.push(listener); }
  waitFor(method, predicate = () => true) {
    return new Promise((resolvePromise, reject) => {
      const waiter = { method, predicate, resolve: resolvePromise, reject };
      waiter.timeout = setTimeout(() => { this.waiters = this.waiters.filter((item) => item !== waiter); reject(new Error(`${method} timed out`)); }, TIMEOUT_MS);
      this.waiters.push(waiter);
    });
  }
  close() { this.socket?.close(); }
}

async function evaluate(client, expression, awaitPromise = false) {
  const result = await client.send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true, userGesture: true });
  if (result.exceptionDetails) {
    const type = result.exceptionDetails.exception?.className || 'Error';
    const line = Number.isInteger(result.exceptionDetails.lineNumber) ? result.exceptionDetails.lineNumber : -1;
    throw new Error(`browser evaluation failed: ${type} at line ${line}`);
  }
  return result.result?.value;
}

async function waitForExpression(client, expression, label) { return waitUntil(() => evaluate(client, expression), label); }

async function navigate(client, url) {
  const loaded = client.waitFor('Page.loadEventFired');
  const result = await client.send('Page.navigate', { url });
  if (result.errorText) throw new Error(`navigation failed: ${result.errorText}`);
  await loaded;
}

function click(selector) { return `document.querySelector(${JSON.stringify(selector)})?.click(); true`; }

function decisionExpression(candidateIndex, action, reason, replacementText = '') {
  return `(() => {
    document.querySelector('[data-candidate-id="${CANDIDATE_IDS[candidateIndex]}"]').click();
    const form = document.querySelector('#decisionForm');
    const actionInput = form.querySelector('input[name="action"][value="${action}"]');
    actionInput.checked = true;
    actionInput.dispatchEvent(new Event('change', { bubbles: true }));
    form.elements.reason.value = ${JSON.stringify(reason)};
    if (${JSON.stringify(action)} === 'modify') form.elements.replacementText.value = ${JSON.stringify(replacementText)};
    form.requestSubmit();
    return true;
  })()`;
}

function validateResult(result) {
  if (Object.keys(result).length !== RESULT_KEYS.size || Object.keys(result).some((key) => !RESULT_KEYS.has(key))) throw new Error('browser result fields do not match the gate contract');
  if (Object.values(result).some((value) => value !== true)) throw new Error('browser gate result contains a failed check');
}

async function run(args) {
  const state = createState();
  const profile = resolve(args.profile);
  await rm(profile, { recursive: true, force: true });
  await mkdir(profile, { recursive: true });
  const { server, baseUrl } = await startServer(state);
  const browser = spawn(resolve(args.browser), [
    '--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
    '--disable-component-update', '--disable-sync', '--disable-extensions', '--remote-debugging-port=0',
    `--user-data-dir=${profile}`, 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore'] });
  let client;
  try {
    const port = await waitUntil(async () => (await readFile(resolve(profile, 'DevToolsActivePort'), 'utf8')).trim().split(/\r?\n/)[0], 'Chrome DevTools port');
    const target = await waitUntil(async () => {
      const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' });
      return response.ok ? response.json() : null;
    }, 'Chrome target');
    client = new CdpClient(target.webSocketDebuggerUrl);
    await client.connect();
    const consoleFailures = [];
    const externalRequests = [];
    client.on((method, params) => {
      if (method === 'Runtime.exceptionThrown') consoleFailures.push('runtime-exception');
      if (method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(params.type)) consoleFailures.push(`console-${params.type}`);
      if (method === 'Network.requestWillBeSent') {
        const url = new URL(params.request.url);
        if (url.origin !== baseUrl) externalRequests.push(url.origin);
      }
    });
    await Promise.all([client.send('Page.enable'), client.send('Runtime.enable'), client.send('Network.enable')]);
    await navigate(client, `${baseUrl}/`);
    await waitForExpression(client, "document.querySelector('#reviewConsole')?.dataset.state === 'empty'", 'empty review state');

    await evaluate(client, click('#createReview'));
    await waitForExpression(client, "document.querySelector('#reviewStatus')?.dataset.state === 'error'", 'uncertain create state');
    const firstKey = state.createKeys[0];
    await evaluate(client, click('#createReview'));
    await waitForExpression(client, "document.querySelector('#reviewConsole')?.dataset.state === 'ready'", 'replayed review state');
    const replayPassed = state.runCreated && state.createAttempts === 2 && state.createKeys[0] === state.createKeys[1];
    const browserStorageEmpty = await evaluate(client, "localStorage.length === 0 && sessionStorage.length === 0 && document.cookie === ''");
    const citationsRendered = await evaluate(client, "document.querySelector('.citation-card blockquote')?.textContent.includes('Bounded source quote')");

    await evaluate(client, decisionExpression(0, 'approve', 'Approved by gate'));
    await waitForExpression(client, "document.querySelector('#metricApproved')?.textContent === '1'", 'approve decision');
    await evaluate(client, decisionExpression(1, 'modify', 'Modified by gate', 'Human replacement text'));
    await waitForExpression(client, "document.querySelector('#metricModified')?.textContent === '1'", 'modify decision');
    await evaluate(client, decisionExpression(2, 'reject', 'Rejected by gate'));
    await waitForExpression(client, "document.querySelector('#metricRejected')?.textContent === '1'", 'reject decision');

    const getCountBeforeConflict = state.requests.filter((item) => item.method === 'GET' && item.path.endsWith(`/${IDS.run}`)).length;
    await evaluate(client, decisionExpression(3, 'approve', 'Conflict attempt'));
    await waitForExpression(client, "document.querySelector('#reviewStatus')?.dataset.state === 'conflict'", 'conflict reconciliation');
    const getCountAfterConflict = state.requests.filter((item) => item.method === 'GET' && item.path.endsWith(`/${IDS.run}`)).length;

    const getCountBeforeUncertain = getCountAfterConflict;
    await evaluate(client, decisionExpression(4, 'approve', 'Uncertain but committed'));
    await waitForExpression(client, "document.querySelector('#reviewStatusText')?.textContent.includes('未盲目重复提交')", 'uncertain decision reconciliation');
    const getCountAfterUncertain = state.requests.filter((item) => item.method === 'GET' && item.path.endsWith(`/${IDS.run}`)).length;

    await evaluate(client, "document.querySelector('#reviewVersionSelect').value='1'; document.querySelector('#reviewVersionSelect').dispatchEvent(new Event('change')); true");
    await waitForExpression(client, "document.querySelector('.history-notice') && !document.querySelector('#decisionForm')", 'history read-only state');
    const historyReadOnly = await evaluate(client, "Boolean(document.querySelector('.history-notice') && !document.querySelector('#decisionForm'))");

    const previousTheme = await evaluate(client, "document.documentElement.dataset.theme");
    await evaluate(client, click('#themeToggle'));
    const themeSwitchPassed = await waitForExpression(client, `document.documentElement.dataset.theme !== ${JSON.stringify(previousTheme)}`, 'theme switch');

    let responsivePassed = true;
    for (const width of [375, 1440]) {
      await client.send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: width < 768 });
      const layout = await evaluate(client, `(() => ({
        noOverflow: document.documentElement.scrollWidth <= innerWidth,
        controls: [...document.querySelectorAll('button:not([hidden]),select:not([hidden])')].filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        }).every((element) => {
          const rect = element.getBoundingClientRect();
          return innerWidth > 760 || Math.min(rect.width, rect.height) >= 44;
        }),
      }))()`);
      responsivePassed &&= layout.noOverflow && layout.controls;
    }

    const allowedDecisionKeys = new Set(['expectedReviewVersion', 'candidateId', 'action', 'reason', 'comment', 'replacementText']);
    const exactCreateKeys = 'expectedProposalSha256,expectedProposalVersionId';
    const createBodiesValid = state.createBodies.length === 2 && state.createBodies.every((body) => (
      Object.keys(body).sort().join(',') === exactCreateKeys
      && body.expectedProposalVersionId === IDS.proposalVersion
      && body.expectedProposalSha256 === SHA
    ));
    const decisionBodiesValid = state.decisionBodies.length === 5 && state.decisionBodies.every((body) => (
      ['expectedReviewVersion', 'candidateId', 'action', 'reason'].every((key) => Object.hasOwn(body, key))
      && Object.keys(body).every((key) => allowedDecisionKeys.has(key))
      && CANDIDATE_IDS.includes(body.candidateId)
      && ['approve', 'modify', 'reject'].includes(body.action)
      && !Object.hasOwn(body, 'sourceCitation')
      && !Object.hasOwn(body, 'objectPath')
      && !Object.hasOwn(body, 'parserBlocks')
      && !Object.hasOwn(body, 'scope')
    ));
    const requestBoundaryPassed = state.createKeys.every((key) => key && key.length >= 8)
      && createBodiesValid && decisionBodiesValid
      && !state.requests.some((request) => /candidate|citation|object|parser|scope/i.test(request.path));
    const result = {
      singleRunAfterUncertainCreate: state.runCreated === true,
      sameKeyReplayObserved: replayPassed && browserStorageEmpty,
      citationsRendered: citationsRendered === true,
      approveModifyRejectCompleted: ['approve', 'modify', 'reject'].every((action) => state.decisions.some((decision) => decision.action === action)),
      conflictReconciledByGet: state.conflictInjected && getCountAfterConflict > getCountBeforeConflict,
      uncertainDecisionReconciledByGet: state.uncertainDecisionInjected && getCountAfterUncertain > getCountBeforeUncertain,
      historyIsReadOnly: historyReadOnly === true,
      themeSwitchPassed: themeSwitchPassed === true,
      responsivePassed,
      noConsoleFailures: consoleFailures.length === 0,
      noExternalRequests: externalRequests.length === 0,
      requestBoundaryPassed,
    };
    validateResult(result);
    return result;
  } finally {
    client?.close();
    browser.kill();
    await new Promise((resolvePromise) => server.close(resolvePromise));
    await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  }
}

if (process.argv[2] === '--self-test') {
  validateResult(Object.fromEntries([...RESULT_KEYS].map((key) => [key, true])));
  process.stdout.write('M1-02 browser gate contract passed\n');
} else {
  try {
    const result = await run(parseArgs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stderr.write(`M1-02 browser gate failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}
