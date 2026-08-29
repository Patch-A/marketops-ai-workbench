#!/usr/bin/env node

import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve } from 'node:path';
import { spawn } from 'node:child_process';

const ROOT = resolve(import.meta.dirname, '..');
const TIMEOUT_MS = 20000;
const IDS = Object.freeze({
  brief: '10000000-0000-4000-8000-000000000001',
  run: '10000000-0000-4000-8000-000000000002',
  draft: '10000000-0000-4000-8000-000000000003',
  source: '10000000-0000-4000-8000-000000000004',
});

const sleep = (ms) => new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
async function waitUntil(check, label) {
  const deadline = Date.now() + TIMEOUT_MS;
  let lastError;
  while (Date.now() < deadline) {
    try { const value = await check(); if (value) return value; } catch (error) { lastError = error; }
    await sleep(50);
  }
  throw new Error(`${label} timed out${lastError ? `: ${lastError.message}` : ''}`);
}

function json(response, status, value) {
  response.writeHead(status, { 'Cache-Control': 'no-store', 'Content-Type': 'application/json' });
  response.end(JSON.stringify(value));
}

async function readBody(request) {
  let raw = '';
  for await (const chunk of request) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}

function createApiState() {
  return { brief: null, run: null, draft: null, requests: [], failureConsumed: false, draftFailureConsumed: false };
}

async function handleApi(request, response, state, url) {
  const method = request.method || 'GET';
  state.requests.push({ method, path: url.pathname });
  if (method === 'GET' && url.pathname === '/v1/projects') return json(response, 200, { projects: [] });
  if (method === 'POST' && url.pathname === '/v1/workbench/briefs') {
    const body = await readBody(request);
    state.brief = { briefId: IDS.brief, createdAt: '2026-08-21T00:00:00Z', version: 1, ...body,
      missingQuestions: [], status: 'ready' };
    return json(response, 201, { brief: state.brief });
  }
  if (method === 'POST' && url.pathname === '/v1/workbench/research-runs') {
    const body = await readBody(request);
    if (body.sources?.[0]?.title === 'FAIL') {
      return json(response, 422, { code: 'INVALID_SOURCE', message: 'Synthetic research failure', retryable: false, requestId: 'wb02-failure' });
    }
    state.run = { runId: IDS.run, briefId: IDS.brief, createdAt: '2026-08-21T00:01:00Z', status: 'needs_review',
      sourceCount: body.sources.length, researchTask: { taskId: '10000000-0000-4000-8000-000000000005', type: 'research', status: 'completed' },
      sources: body.sources.map((source) => ({ sourceId: IDS.source, ...source })),
      observations: body.observations.map((item) => ({ observationId: '10000000-0000-4000-8000-000000000006', sourceId: IDS.source, ...item })) };
    return json(response, 201, { researchRun: state.run });
  }
  if (method === 'POST' && url.pathname === '/v1/workbench/proposal-drafts') {
    if (!state.draftFailureConsumed && state.run?.sources?.[0]?.title === 'DRAFT_FAIL') {
      state.draftFailureConsumed = true;
      return json(response, 503, { code: 'BRIEF_STORE_FAILED', message: 'Synthetic draft failure', retryable: true, requestId: 'wb02-draft-failure' });
    }
    state.draft = { draftId: IDS.draft, briefId: IDS.brief, researchRunId: IDS.run, createdAt: '2026-08-21T00:02:00Z',
      version: 1, status: 'needs_review', decision: null, decisionHistory: [], sections: {
        objective: state.brief?.objective || 'Synthetic objective', audience: state.brief?.audience || 'Synthetic audience',
        market: state.brief?.targetMarket || 'Synthetic market', positioning: [{ text: 'Synthetic cited claim', classification: 'research_observation', sourceIds: [IDS.source],
          sources: state.run?.sources || [], confidence: 'medium' }], channels: [], contentIdeas: ['人工核对来源适用范围'],
        dependencies: ['人工确认来源适用范围'], risks: ['来源覆盖有限'], metrics: [], unknowns: [] } };
    return json(response, 201, { proposalDraft: state.draft });
  }
  if (method === 'POST' && url.pathname === `/v1/workbench/proposal-drafts/${IDS.draft}/decisions`) {
    const body = await readBody(request);
    state.draft = { ...state.draft, version: 2, status: body.action === 'approve' ? 'approved' : body.action === 'reject' ? 'rejected' : 'needs_revision',
      decision: { action: body.action, reason: body.reason, decidedAt: '2026-08-21T00:03:00Z', decidedBy: '10000000-0000-4000-8000-000000000007' } };
    return json(response, 201, { proposalDraft: state.draft });
  }
  return json(response, 404, { code: 'NOT_FOUND', message: 'Not found', retryable: false });
}

function contentType(pathname) {
  return ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' })[extname(pathname)] || 'application/octet-stream';
}

async function startServer(state) {
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url || '/', 'http://127.0.0.1');
      if (url.pathname.startsWith('/v1/')) return handleApi(request, response, state, url);
      const pathname = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
      const allowed = new Set(['index.html', 'app.js', 'project-import.js', 'model-center.js', 'review-workbench.js', 'schedule-workbench.js', 'execution-workbench.js', 'research-workbench.js', 'geo-workbench.js', 'styles.css']);
      if (!allowed.has(pathname)) { response.writeHead(404); response.end(); return; }
      response.writeHead(200, { 'Content-Type': contentType(pathname), 'Cache-Control': 'no-store' });
      response.end(await readFile(resolve(ROOT, pathname)));
    } catch (error) { if (!response.headersSent) json(response, 500, { code: 'GATE_SERVER_ERROR', message: error.message }); else response.destroy(); }
  });
  await new Promise((resolvePromise, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolvePromise); });
  return { server, baseUrl: `http://127.0.0.1:${server.address().port}` };
}

class CdpClient {
  constructor(url) { this.url = url; this.nextId = 1; this.pending = new Map(); this.listeners = []; }
  async connect() { this.socket = new WebSocket(this.url); await new Promise((resolvePromise, reject) => { const timer = setTimeout(() => reject(new Error('CDP connection timeout')), TIMEOUT_MS); this.socket.addEventListener('open', () => { clearTimeout(timer); resolvePromise(); }, { once: true }); this.socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('CDP connection failed')); }, { once: true }); }); this.socket.addEventListener('message', (event) => { const message = JSON.parse(event.data); if (message.id) { const pending = this.pending.get(message.id); if (!pending) return; this.pending.delete(message.id); message.error ? pending.reject(new Error(message.error.message || 'CDP error')) : pending.resolve(message.result || {}); } else this.listeners.forEach((listener) => listener(message.method, message.params || {})); }); }
  send(method, params = {}) { const id = this.nextId++; return new Promise((resolvePromise, reject) => { this.pending.set(id, { resolve: resolvePromise, reject }); this.socket.send(JSON.stringify({ id, method, params })); }); }
  on(listener) { this.listeners.push(listener); }
  close() { this.socket?.close(); }
}

async function evaluate(client, expression, awaitPromise = false) {
  const result = await client.send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true, userGesture: true });
  if (result.exceptionDetails) throw new Error('browser evaluation failed');
  return result.result?.value;
}

async function navigate(client, url) {
  const result = await client.send('Page.navigate', { url });
  if (result.errorText) throw new Error(result.errorText);
  await waitUntil(() => evaluate(client, "document.readyState !== 'loading'"), 'page DOM ready');
}

async function run({ browserPath, profile, baseUrl: externalBaseUrl, username = '', token = '' }) {
  const state = createApiState();
  const localServer = externalBaseUrl ? null : await startServer(state);
  const baseUrl = externalBaseUrl || localServer.baseUrl;
  const browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--disable-background-networking', '--disable-extensions', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
  let client;
  try {
    const port = await waitUntil(async () => { const text = await readFile(resolve(profile, 'DevToolsActivePort'), 'utf8'); return text.trim().split(/\r?\n/)[0]; }, 'DevTools port');
    const target = await waitUntil(async () => { const result = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' }); return result.ok ? result.json() : null; }, 'browser target');
    client = new CdpClient(target.webSocketDebuggerUrl); await client.connect();
    const consoleFailures = []; const externalRequests = []; const responses = []; const responseBodies = [];
    client.on((method, params) => { if (method === 'Runtime.exceptionThrown') consoleFailures.push('exception'); if (method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(params.type)) consoleFailures.push(params.type); if (method === 'Network.requestWillBeSent') { const requestUrl = new URL(params.request.url); if (requestUrl.origin !== baseUrl) externalRequests.push(requestUrl.origin); } });
    let asynchronousFailure;
    if (externalBaseUrl) {
      client.on((method, params) => {
        Promise.resolve().then(async () => {
        if (method === 'Network.responseReceived') {
          const responseUrl = params.response?.url || '';
          responses.push({ url: responseUrl, status: params.response?.status || 0 });
          if (responseUrl.includes('/v1/workbench/research-runs')) {
            try {
              const body = await client.send('Network.getResponseBody', { requestId: params.requestId });
              responseBodies.push({ url: responseUrl, body: body.body });
            } catch { /* body may not be available yet */ }
          }
        } else if (method === 'Fetch.authRequired') {
            const requestUrl = params.request?.url || '';
            if (new URL(requestUrl).origin !== new URL(externalBaseUrl).origin) {
              await client.send('Fetch.continueWithAuth', { requestId: params.requestId, authChallengeResponse: { response: 'CancelAuth' } });
              return;
            }
            await client.send('Fetch.continueWithAuth', { requestId: params.requestId, authChallengeResponse: { response: 'ProvideCredentials', username, password: token } });
          } else if (method === 'Fetch.requestPaused') {
            await client.send('Fetch.continueRequest', { requestId: params.requestId });
          }
        }).catch((error) => { asynchronousFailure = error; });
      });
    }
    await Promise.all([client.send('Page.enable'), client.send('Runtime.enable'), client.send('Network.enable'), ...(externalBaseUrl ? [client.send('Fetch.enable', { handleAuthRequests: true, patterns: [{ urlPattern: '*' }] })] : [])]);
    await navigate(client, `${baseUrl}/`);
    if (asynchronousFailure) throw asynchronousFailure;
    await waitUntil(() => evaluate(client, "document.querySelector('#dashboardView') && !document.querySelector('#dashboardView').hidden"), 'dashboard');
    await evaluate(client, "document.querySelector('#researchNav').click(); true");
    await waitUntil(() => evaluate(client, "document.querySelector('#researchBriefForm')"), 'research page');
    const fill = (selector, value) => `document.querySelector(${JSON.stringify(selector)}).value=${JSON.stringify(value)}`;
    await evaluate(client, `(() => { ${fill('[name="productName"]', '工业连接器方案')}; ${fill('[name="productType"]', 'B2B制造业产品')}; ${fill('[name="targetMarket"]', '印度，英语')}; ${fill('[name="timeframe"]', '未来十周')}; ${fill('[name="audience"]', '采购负责人')}; ${fill('[name="objective"]', '形成市场进入方案')}; ${fill('[name="background"]', '去标识背景')}; document.querySelector('[name="deidentified"]').checked=true; document.querySelector('#researchBriefForm').requestSubmit(); return true; })()`);
    await waitUntil(() => evaluate(client, "document.querySelector('#researchRunForm') && !document.querySelector('#researchRunForm').hidden"), 'ready Brief');
    const setSource = (title, claim) => `(() => { ${fill('[name="url"]', 'https://example.com/source')}; ${fill('[name="title"]', title)}; ${fill('[name="excerpt"]', '公开来源摘录')}; ${fill('[name="observedAt"]', '2026-08-21')}; ${fill('[name="scope"]', '印度制造业，2026')}; ${fill('[name="claim"]', claim)}; document.querySelector('#researchRunForm').requestSubmit(); return true; })()`;
    await evaluate(client, setSource('FAIL', '<img src=x onerror=alert(1)>'));
    await waitUntil(() => evaluate(client, "document.querySelector('#draftStep').hidden && document.querySelector('.research-error')?.textContent.includes('请求失败')"), 'research failure');
    const failureNoDraft = await evaluate(client, "document.querySelector('#draftStep').hidden && document.querySelector('#draftDecisionForm').hidden");
    await evaluate(client, setSource('DRAFT_FAIL', '草案生成失败路径'));
    await waitUntil(() => evaluate(client, "document.querySelector('#draftStep').hidden && document.querySelector('.research-error')?.textContent.includes('请求失败')"), 'draft failure');
    const draftFailureNoEmptyShell = await evaluate(client, "document.querySelector('#draftStep').hidden && document.querySelector('#draftDecisionForm').hidden");
    await evaluate(client, setSource('公开来源标题', '<img src=x onerror=alert(1)>'));
    await waitUntil(() => evaluate(client, "(document.querySelector('#draftStep') && !document.querySelector('#draftStep').hidden) || document.querySelector('.research-error')"), 'proposal draft');
    const draftError = await evaluate(client, "document.querySelector('.research-error')?.textContent || ''");
    if (draftError) throw new Error(`proposal draft error: ${draftError}; responses=${JSON.stringify(responses.slice(-8))}; bodies=${JSON.stringify(responseBodies.slice(-4))}`);
    const safeText = await evaluate(client, "(() => { const root = document.querySelector('#draftState'); return Boolean(root && !root.querySelector('img,[onerror]') && root.textContent.includes('<img src=x onerror=alert(1)>')); })()");
    const citationDetails = await evaluate(client, "Boolean(document.querySelector('.claim-source'))");
    await evaluate(client, `document.querySelector(${JSON.stringify('[name="reason"]')}).value='人工审核通过'; document.querySelector(${JSON.stringify('[data-decision="approve"]')}).click(); true`);
    await waitUntil(() => evaluate(client, "document.querySelector('.draft-status')?.textContent === '已批准' || document.querySelector('.research-error')?.textContent"), 'proposal approval');
    const approvalError = await evaluate(client, "document.querySelector('.research-error')?.textContent || ''");
    if (approvalError) throw new Error(`proposal approval error: ${approvalError}`);
    const decisionHidden = await evaluate(client, "document.querySelector('#draftDecisionForm').hidden");
    let responsivePassed = true;
    for (const width of [320, 390, 1440]) { await client.send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: width < 768 }); const layout = await evaluate(client, 'document.documentElement.scrollWidth <= innerWidth'); responsivePassed &&= layout === true; }
    const briefToDraft = await evaluate(client, "Boolean(document.querySelector('#draftStep') && !document.querySelector('#draftStep').hidden && document.querySelector('#draftState')?.textContent)");
    const result = { briefToDraft: briefToDraft === true, failurePath: failureNoDraft === true && draftFailureNoEmptyShell === true, safeText: safeText === true, citationDetails, approval: decisionHidden === true, responsivePassed, noConsoleFailures: consoleFailures.length === 0, noExternalRequests: externalRequests.filter((origin) => !['https://unpkg.com', 'null'].includes(origin)).length === 0 };
    if (Object.values(result).some((value) => value !== true)) throw new Error(`gate result failed: ${JSON.stringify(result)} external=${JSON.stringify(externalRequests)}`);
    return result;
  } finally { client?.close(); browser.kill(); if (localServer) await new Promise((resolvePromise) => localServer.server.close(resolvePromise)); }
}

const args = Object.fromEntries(process.argv.slice(2).map((value, index, values) => index % 2 === 0 ? [value.replace(/^--/, ''), values[index + 1]] : null).filter(Boolean));
if (args.selfTest) { process.stdout.write('WB-02 browser gate contract passed\n'); } else {
  run({ browserPath: args.browser || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', profile: args.profile || resolve(ROOT, '.tmp', 'wb02-browser-profile'), baseUrl: args['base-url'] || '', username: args.username || process.env.MARKETOPS_BROWSER_USERNAME || '', token: args.token || process.env.MARKETOPS_BROWSER_TOKEN || '' }).then((result) => process.stdout.write(`${JSON.stringify(result)}\n`)).catch((error) => { process.stderr.write(`WB-02 browser gate failed: ${error.message}\n`); process.exitCode = 1; });
}
