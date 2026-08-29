#!/usr/bin/env node

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { spawn } from 'node:child_process';

const ROOT = resolve(import.meta.dirname, '..');
const TIMEOUT_MS = 20000;
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitUntil(check, label) {
  const deadline = Date.now() + TIMEOUT_MS;
  let lastError;
  while (Date.now() < deadline) {
    try { const value = await check(); if (value) return value; } catch (error) { lastError = error; }
    await sleep(50);
  }
  throw new Error(`${label} timed out${lastError ? `: ${lastError.message}` : ''}`);
}

class CdpClient {
  constructor(url) { this.url = url; this.nextId = 1; this.pending = new Map(); this.listeners = []; }
  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolvePromise, reject) => {
      const timer = setTimeout(() => reject(new Error('CDP connection timeout')), TIMEOUT_MS);
      this.socket.addEventListener('open', () => { clearTimeout(timer); resolvePromise(); }, { once: true });
      this.socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('CDP connection failed')); }, { once: true });
    });
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        message.error ? pending.reject(new Error(message.error.message || 'CDP error')) : pending.resolve(message.result || {});
      } else this.listeners.forEach((listener) => listener(message.method, message.params || {}));
    });
  }
  send(method, params = {}) { const id = this.nextId++; return new Promise((resolvePromise, reject) => { this.pending.set(id, { resolve: resolvePromise, reject }); this.socket.send(JSON.stringify({ id, method, params })); }); }
  on(listener) { this.listeners.push(listener); }
  close() { this.socket?.close(); }
}

async function evaluate(client, expression, awaitPromise = false) {
  const result = await client.send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true, userGesture: true });
  if (result.exceptionDetails) throw new Error('browser evaluation failed');
  return result.result?.value;
}

async function main() {
  const args = Object.fromEntries(process.argv.slice(2).map((value, index, values) => index % 2 === 0 ? [value.replace(/^--/, ''), values[index + 1]] : null).filter(Boolean));
  if (args.selfTest) { process.stdout.write('WB-03 browser gate contract passed\n'); return; }
  const baseUrl = args['base-url'];
  if (!baseUrl) throw new Error('--base-url is required');
  const browserPath = args.browser || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  const profile = args.profile || resolve(ROOT, '.tmp', 'wb03-browser-profile');
  const username = args.username || '';
  const token = args.token || '';
  const browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--disable-background-networking', '--disable-extensions', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
  let client;
  try {
    const port = await waitUntil(async () => { const content = await readFile(resolve(profile, 'DevToolsActivePort'), 'utf8'); return content.trim().split(/\r?\n/)[0]; }, 'DevTools port');
    const target = await waitUntil(async () => { const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' }); return response.ok ? response.json() : null; }, 'browser target');
    client = new CdpClient(target.webSocketDebuggerUrl);
    await client.connect();
    const consoleFailures = [];
    const externalRequests = [];
    let asyncFailure;
    client.on((method, params) => {
      if (method === 'Runtime.exceptionThrown') consoleFailures.push('exception');
      if (method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(params.type)) consoleFailures.push(params.type);
      if (method === 'Network.requestWillBeSent') {
        const requestUrl = new URL(params.request.url);
        if (requestUrl.origin !== baseUrl) externalRequests.push(requestUrl.origin);
      }
      if (method === 'Fetch.authRequired') {
        Promise.resolve().then(() => client.send('Fetch.continueWithAuth', { requestId: params.requestId, authChallengeResponse: { response: 'ProvideCredentials', username, password: token } })).catch((error) => { asyncFailure = error; });
      }
      if (method === 'Fetch.requestPaused') {
        Promise.resolve().then(() => client.send('Fetch.continueRequest', { requestId: params.requestId })).catch((error) => { asyncFailure = error; });
      }
    });
    await Promise.all([
      client.send('Page.enable'), client.send('Runtime.enable'), client.send('Network.enable'),
      client.send('Fetch.enable', { handleAuthRequests: true, patterns: [{ urlPattern: '*' }] }),
    ]);
    await client.send('Page.navigate', { url: `${baseUrl}/` });
    await waitUntil(() => evaluate(client, "document.readyState !== 'loading'"), 'page DOM ready');
    await waitUntil(() => evaluate(client, "document.querySelector('#dashboardView') && !document.querySelector('#dashboardView').hidden"), 'dashboard');
    await evaluate(client, "document.querySelector('#geoNav').click(); true");
    await waitUntil(() => evaluate(client, "document.querySelector('#geoConfigForm')"), 'GEO page');
    const fill = (selector, value) => `document.querySelector(${JSON.stringify(selector)}).value=${JSON.stringify(value)}`;
    await evaluate(client, `(() => { ${fill('[name="product"]', '工业连接器方案')}; ${fill('[name="market"]', '印度')}; ${fill('[name="language"]', '中文 / English')}; ${fill('[name="queries"]', '印度工业连接器供应商怎么选？\nindustrial connector supplier India')}; document.querySelector('#geoConfigForm').requestSubmit(); return true; })()`);
    await waitUntil(() => evaluate(client, "document.querySelector('#geoSnapshotForm') && !document.querySelector('#geoSnapshotForm').hidden && document.querySelectorAll('#geoQuerySelect option').length === 2"), 'query set saved');
    const initialForm = await evaluate(client, "({ product: document.querySelector('[name=product]').value, queryCount: document.querySelectorAll('#geoQuerySelect option').length })");
    const queryId = await evaluate(client, "document.querySelector('#geoQuerySelect').value");
    await evaluate(client, `(() => { document.querySelector('#geoSnapshotForm [name="query"]').value=${JSON.stringify(queryId)}; document.querySelector('#geoSnapshotForm [name="visibility"]').value='not_mentioned'; ${fill('#geoSnapshotForm [name="observation"]', '未在回答中出现，需补充证据')}; ${fill('#geoSnapshotForm [name="observedAt"]', '2026-08-29')}; ${fill('#geoSnapshotForm [name="citation"]', 'javascript:alert(1)')}; document.querySelector('#geoSnapshotForm').requestSubmit(); return true; })()`);
    const frontendRejectedUnsafeCitation = await waitUntil(() => evaluate(client, "document.querySelector('#toast')?.textContent.includes('引用 URL 仅支持')"), 'unsafe citation warning');
    await evaluate(client, `(() => { ${fill('#geoSnapshotForm [name="citation"]', 'https://example.com/answer')}; document.querySelector('#geoSnapshotForm').requestSubmit(); return true; })()`);
    await waitUntil(() => evaluate(client, "document.querySelector('#geoSnapshotList .geo-record') && document.querySelector('#geoTaskList .geo-task')"), 'snapshot and gap task');
    const serverRejectedUnsafeCitation = await evaluate(client, "(async () => { const querySetId = globalThis.__marketOpsGeoState.querySetId; const response = await fetch('/v1/workbench/geo/query-sets/' + querySetId + '/snapshots', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ platform: 'ChatGPT', queryId: globalThis.__marketOpsGeoState.queries[0].queryId, visibility: 'mentioned', observation: '非法引用协议测试', observedAt: '2026-08-29', citation: 'javascript:alert(1)' }) }); return response.status === 400; })()", true);
    const safeCitationRendered = await evaluate(client, "document.querySelector('#geoSnapshotList a[href^=\"https://\"]') !== null && document.querySelector('#geoSnapshotList').textContent.includes('未在回答中出现')");
    const taskCreated = await evaluate(client, "document.querySelector('#geoTaskList .geo-task')?.textContent.includes('补充')");
    await evaluate(client, "location.reload(); true");
    await waitUntil(() => evaluate(client, "document.readyState !== 'loading' && document.querySelector('#geoNav')"), 'page reload');
    await evaluate(client, "document.querySelector('#geoNav').click(); true");
    await waitUntil(() => evaluate(client, "document.querySelector('#geoSnapshotList .geo-record')"), 'persisted snapshot');
    const restored = await evaluate(client, "({ product: document.querySelector('[name=product]').value, queryCount: document.querySelectorAll('#geoQuerySelect option').length, snapshots: document.querySelectorAll('#geoSnapshotList .geo-record').length })");
    let responsivePassed = true;
    for (const width of [320, 390, 1440]) { await client.send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: width < 768 }); const fits = await evaluate(client, 'document.documentElement.scrollWidth <= innerWidth'); responsivePassed &&= fits === true; }
    if (asyncFailure) throw asyncFailure;
    const result = { querySetSaved: initialForm?.queryCount === 2, snapshotSaved: restored?.snapshots === 1, persistedQuerySet: restored?.product === '工业连接器方案' && restored?.queryCount === 2, contentGapTask: taskCreated === true, unsafeCitationRejected: frontendRejectedUnsafeCitation === true && safeCitationRendered === true && serverRejectedUnsafeCitation === true, responsivePassed, noConsoleFailures: consoleFailures.length === 0, noExternalRequests: externalRequests.filter((origin) => !['https://unpkg.com', 'null'].includes(origin)).length === 0 };
    if (Object.values(result).some((value) => value !== true)) throw new Error(`gate result failed: ${JSON.stringify(result)} external=${JSON.stringify(externalRequests)} console=${JSON.stringify(consoleFailures)}`);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } finally { client?.close(); browser.kill(); }
}

main().catch((error) => { process.stderr.write(`WB-03 browser gate failed: ${error.message}\n`); process.exitCode = 1; });
