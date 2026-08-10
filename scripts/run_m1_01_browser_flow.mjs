#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { mkdir, readFile, rm } from 'node:fs/promises';
import { resolve } from 'node:path';

const POLL_MS = 50;
const TIMEOUT_MS = 20_000;

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!name?.startsWith('--') || value === undefined) throw new Error('invalid browser gate arguments');
    values[name.slice(2)] = value;
  }
  for (const field of ['browser', 'base-url', 'source', 'proposal', 'profile']) {
    if (!values[field]) throw new Error(`missing --${field}`);
  }
  return values;
}

function delay(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

async function waitUntil(check, label, timeout = TIMEOUT_MS) {
  const deadline = Date.now() + timeout;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await delay(POLL_MS);
  }
  throw new Error(`${label} timed out${lastError ? `: ${lastError.message}` : ''}`);
}

class CdpClient {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.waiters = [];
    this.listeners = [];
  }

  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolvePromise, reject) => {
      const timeout = setTimeout(() => reject(new Error('CDP websocket open timed out')), TIMEOUT_MS);
      this.socket.addEventListener('open', () => {
        clearTimeout(timeout);
        resolvePromise();
      }, { once: true });
      this.socket.addEventListener('error', () => {
        clearTimeout(timeout);
        reject(new Error('CDP websocket failed'));
      }, { once: true });
    });
    this.socket.addEventListener('message', (event) => this.receive(event.data));
    this.socket.addEventListener('close', () => {
      for (const { reject } of this.pending.values()) reject(new Error('CDP websocket closed'));
      this.pending.clear();
    });
  }

  receive(raw) {
    const message = JSON.parse(raw);
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message || 'CDP command failed'));
      else pending.resolve(message.result || {});
      return;
    }
    if (!message.method) return;
    for (const listener of this.listeners) listener(message.method, message.params || {});
    const retained = [];
    for (const waiter of this.waiters) {
      if (waiter.method === message.method && waiter.predicate(message.params || {})) {
        clearTimeout(waiter.timeout);
        waiter.resolve(message.params || {});
      } else retained.push(waiter);
    }
    this.waiters = retained;
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolvePromise, reject) => {
      this.pending.set(id, { resolve: resolvePromise, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  on(listener) {
    this.listeners.push(listener);
  }

  waitFor(method, predicate = () => true, timeoutMs = TIMEOUT_MS) {
    return new Promise((resolvePromise, reject) => {
      const waiter = {
        method,
        predicate,
        resolve: resolvePromise,
        reject,
        timeout: setTimeout(() => {
          this.waiters = this.waiters.filter((item) => item !== waiter);
          reject(new Error(`${method} timed out`));
        }, timeoutMs),
      };
      this.waiters.push(waiter);
    });
  }

  close() {
    this.socket?.close();
  }
}

async function evaluate(client, expression, { awaitPromise = false } = {}) {
  const result = await client.send('Runtime.evaluate', {
    expression,
    awaitPromise,
    returnByValue: true,
    userGesture: true,
  });
  if (result.exceptionDetails) throw new Error('browser evaluation failed');
  return result.result?.value;
}

async function waitForExpression(client, expression, label) {
  return waitUntil(() => evaluate(client, expression), label);
}

async function navigate(client, url) {
  const loaded = client.waitFor('Page.loadEventFired');
  const result = await client.send('Page.navigate', { url });
  if (result.errorText) throw new Error(`navigation failed: ${result.errorText}`);
  await loaded;
}

async function reload(client) {
  const loaded = client.waitFor('Page.loadEventFired');
  await client.send('Page.reload', { ignoreCache: true });
  await loaded;
}

async function setFileInput(client, selector, files) {
  const document = await client.send('DOM.getDocument', { depth: 1 });
  const input = await client.send('DOM.querySelector', {
    nodeId: document.root.nodeId,
    selector,
  });
  if (!input.nodeId) throw new Error(`file input not found: ${selector}`);
  await client.send('DOM.setFileInputFiles', { nodeId: input.nodeId, files });
}

function safePath(url, origin) {
  try {
    const parsed = new URL(url);
    if (parsed.origin !== origin) return `external:${parsed.origin}`;
    return parsed.pathname;
  } catch {
    return 'invalid-url';
  }
}

function matchesExpectedConnectionResetLog(entry, path, expected) {
  const text = entry?.text || '';
  return entry?.source === 'network'
    && path === expected.path
    && entry?.networkRequestId === expected.requestId
    && expected.errorText === 'net::ERR_CONNECTION_RESET'
    && text.includes('net::ERR_CONNECTION_RESET');
}

function classifyNetworkFailureEvent(method, params, origin, expected, requestPaths) {
  if (method === 'Network.loadingFailed') {
    const path = requestPaths.get(params.requestId) || 'unknown';
    if (params.requestId === expected.requestId) {
      if (params.errorText === 'net::ERR_CONNECTION_RESET') {
        return { kind: 'expected-loading-failed', path };
      }
      return { kind: 'failure', category: `network-loading-failed:${path}:${params.errorText || 'unknown'}` };
    }
    if (params.canceled) return { kind: 'ignored-cancellation' };
    return { kind: 'failure', category: `network-loading-failed:${path}:${params.errorText || 'unknown'}` };
  }
  if (method === 'Log.entryAdded' && ['error', 'warning'].includes(params.entry?.level)) {
    const entry = params.entry;
    const source = entry?.source || 'unknown';
    const path = safePath(entry?.url || '', origin);
    if (matchesExpectedConnectionResetLog(entry, path, expected)) {
      return { kind: 'expected-log', path, category: `log-${entry.level}:network:${path}` };
    }
    if (
      source === 'network'
      && path === expected.path
      && entry?.networkRequestId === expected.requestId
      && !expected.errorText
      && (entry?.text || '').includes('net::ERR_CONNECTION_RESET')
    ) return { kind: 'pending-expected-log', path };
    return { kind: 'failure', category: `log-${entry?.level}:${source}:${path}` };
  }
  return { kind: 'not-network-failure' };
}

function runNetworkFailureMatcherSelfTest() {
  const origin = 'http://127.0.0.1:4173';
  const expected = {
    path: '/v1/projects/project-1',
    requestId: 'network-1',
    errorText: 'net::ERR_CONNECTION_RESET',
  };
  const entry = {
    level: 'error',
    source: 'network',
    networkRequestId: 'network-1',
    url: `${origin}/v1/projects/project-1`,
    text: 'Failed to load resource: net::ERR_CONNECTION_RESET',
  };
  const requestPaths = new Map([
    ['network-1', expected.path],
    ['network-2', expected.path],
  ]);
  const accepted = [
    ['Network.loadingFailed', { requestId: 'network-1', errorText: 'net::ERR_CONNECTION_RESET' }, 'expected-loading-failed'],
    ['Log.entryAdded', { entry }, 'expected-log'],
  ];
  for (const [method, params, kind] of accepted) {
    const result = classifyNetworkFailureEvent(method, params, origin, expected, requestPaths);
    if (result.kind !== kind) throw new Error(`expected ${kind}, received ${result.kind}`);
  }
  const rejected = [
    ['Network.loadingFailed', { requestId: 'network-1', errorText: 'net::ERR_CERT_INVALID' }],
    ['Network.loadingFailed', { requestId: 'network-1', errorText: 'net::ERR_NAME_NOT_RESOLVED' }],
    ['Network.loadingFailed', { requestId: 'network-2', errorText: 'net::ERR_CONNECTION_RESET' }],
    ['Log.entryAdded', { entry: { ...entry, text: 'Failed to load resource: net::ERR_CERT_INVALID' } }],
    ['Log.entryAdded', { entry: { ...entry, text: 'Failed to load resource: net::ERR_NAME_NOT_RESOLVED' } }],
    ['Log.entryAdded', { entry: { ...entry, networkRequestId: 'network-2' } }],
    ['Log.entryAdded', { entry: { ...entry, source: 'security' } }],
  ];
  for (const [method, params] of rejected) {
    const result = classifyNetworkFailureEvent(method, params, origin, expected, requestPaths);
    if (result.kind !== 'failure') {
      throw new Error(`network failure classifier accepted ${result.kind}`);
    }
  }
}

async function verifyStaticAssets(baseUrl, username, token) {
  const authorization = `Basic ${Buffer.from(`${username}:${token}`).toString('base64')}`;
  for (const asset of ['', 'index.html', 'app.js', 'project-import.js', 'styles.css']) {
    const response = await fetch(new URL(asset, `${baseUrl}/`), {
      headers: { Authorization: authorization },
    });
    if (!response.ok) throw new Error(`protected static asset failed: ${asset || '/'}`);
    const content = await response.text();
    if (content.includes(token)) throw new Error('deployment token appeared in a static asset');
  }
}

async function run(args) {
  const username = process.env.MARKETOPS_BROWSER_USERNAME || '';
  const token = process.env.MARKETOPS_BROWSER_TOKEN || '';
  if (!username || !token) throw new Error('browser credentials are unavailable');
  const baseUrl = new URL(args['base-url']).origin;
  const profile = resolve(args.profile);
  await rm(profile, { recursive: true, force: true });
  await mkdir(profile, { recursive: true });
  await verifyStaticAssets(baseUrl, username, token);

  const browser = spawn(resolve(args.browser), [
    '--headless=new',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--disable-extensions',
    '--disable-features=OptimizationHints,MediaRouter,Translate',
    '--remote-debugging-port=0',
    `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore'] });

  let client;
  let primaryFailure;
  try {
    const activePort = await waitUntil(async () => {
      const value = await readFile(resolve(profile, 'DevToolsActivePort'), 'utf8');
      const [port] = value.trim().split(/\r?\n/);
      return /^\d+$/.test(port) ? port : null;
    }, 'Chrome DevTools port');
    const target = await waitUntil(async () => {
      const response = await fetch(`http://127.0.0.1:${activePort}/json/new?about:blank`, { method: 'PUT' });
      return response.ok ? response.json() : null;
    }, 'Chrome target');
    client = new CdpClient(target.webSocketDebuggerUrl);
    await client.connect();

    const origin = new URL(baseUrl).origin;
    const requests = [];
    const requestPaths = new Map();
    const externalRequests = [];
    const consoleFailures = [];
    let authChallenges = 0;
    let failNextProjectRead = false;
    let expectedNetworkFailurePath = '';
    let expectedNetworkFailureRequestId = '';
    let expectedNetworkFailureErrorText = '';
    let pendingExpectedNetworkFailureLog;
    let expectedNetworkFailureLogConsumed = false;
    let asynchronousFailure;

    client.on((method, params) => {
      Promise.resolve().then(async () => {
        if (method === 'Fetch.authRequired') {
          const requestUrl = params.request?.url || '';
          if (new URL(requestUrl).origin !== origin) {
            await client.send('Fetch.continueWithAuth', {
              requestId: params.requestId,
              authChallengeResponse: { response: 'CancelAuth' },
            });
            return;
          }
          authChallenges += 1;
          await client.send('Fetch.continueWithAuth', {
            requestId: params.requestId,
            authChallengeResponse: {
              response: 'ProvideCredentials',
              username,
              password: token,
            },
          });
        } else if (method === 'Fetch.requestPaused') {
          const request = params.request || {};
          const parsed = new URL(request.url);
          if (
            failNextProjectRead
            && parsed.origin === origin
            && request.method === 'GET'
            && parsed.pathname.startsWith('/v1/projects')
          ) {
            failNextProjectRead = false;
            expectedNetworkFailurePath = parsed.pathname;
            expectedNetworkFailureRequestId = params.networkId || '';
            if (!expectedNetworkFailureRequestId) {
              throw new Error('injected network failure did not expose a network request id');
            }
            await client.send('Fetch.failRequest', {
              requestId: params.requestId,
              errorReason: 'ConnectionReset',
            });
          } else {
            await client.send('Fetch.continueRequest', { requestId: params.requestId });
          }
        } else if (method === 'Network.loadingFailed') {
          const expected = {
            path: expectedNetworkFailurePath,
            requestId: expectedNetworkFailureRequestId,
            errorText: expectedNetworkFailureErrorText,
          };
          const outcome = classifyNetworkFailureEvent(method, params, origin, expected, requestPaths);
          if (outcome.kind === 'expected-loading-failed') {
            expectedNetworkFailureErrorText = params.errorText || '';
            if (pendingExpectedNetworkFailureLog) {
              const resolvedExpected = {
                path: expectedNetworkFailurePath,
                requestId: expectedNetworkFailureRequestId,
                errorText: expectedNetworkFailureErrorText,
              };
              if (!matchesExpectedConnectionResetLog(
                pendingExpectedNetworkFailureLog.entry,
                pendingExpectedNetworkFailureLog.path,
                resolvedExpected,
              )) throw new Error('injected network failure log did not match its request and error');
              expectedNetworkFailureLogConsumed = true;
              pendingExpectedNetworkFailureLog = undefined;
            }
          } else if (outcome.kind === 'failure') consoleFailures.push(outcome.category);
        } else if (method === 'Network.requestWillBeSent') {
          const path = safePath(params.request?.url || '', origin);
          requestPaths.set(params.requestId, path);
          if (path.startsWith('external:')) externalRequests.push(path);
          else requests.push({ method: params.request?.method, path });
        } else if (method === 'Runtime.exceptionThrown') {
          consoleFailures.push('runtime-exception');
        } else if (method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(params.type)) {
          consoleFailures.push(`console-${params.type}`);
        } else if (method === 'Log.entryAdded' && ['error', 'warning'].includes(params.entry?.level)) {
          const path = safePath(params.entry?.url || '', origin);
          const expected = {
            path: expectedNetworkFailurePath,
            requestId: expectedNetworkFailureRequestId,
            errorText: expectedNetworkFailureErrorText,
          };
          const outcome = classifyNetworkFailureEvent(method, params, origin, expected, requestPaths);
          if (!expectedNetworkFailureLogConsumed && outcome.kind === 'expected-log') {
            expectedNetworkFailureLogConsumed = true;
          } else if (!expectedNetworkFailureLogConsumed && outcome.kind === 'pending-expected-log') {
            pendingExpectedNetworkFailureLog = { entry: params.entry, path };
          } else {
            consoleFailures.push(outcome.category);
          }
        }
      }).catch((error) => {
        asynchronousFailure = error;
      });
    });

    await Promise.all([
      client.send('Page.enable'),
      client.send('Runtime.enable'),
      client.send('DOM.enable'),
      client.send('Network.enable'),
      client.send('Log.enable'),
      client.send('Fetch.enable', { handleAuthRequests: true, patterns: [{ urlPattern: '*' }] }),
    ]);

    await navigate(client, `${baseUrl}/`);
    await waitForExpression(client, "document.querySelector('#serverStatusText')?.dataset.state === 'empty'", 'empty server state');
    if (asynchronousFailure) throw asynchronousFailure;

    await evaluate(client, "document.querySelector('#newBrief').click(); true");
    await evaluate(client, `(() => {
      const form = document.querySelector('#briefForm');
      form.elements.name.value = 'WP5D Browser Cutover';
      form.elements.proposalVersion.value = '3';
      form.elements.approvalConfirmed.checked = true;
      return true;
    })()`);
    await setFileInput(client, 'input[name="sourceFile"]', [resolve(args.source)]);
    await setFileInput(client, 'input[name="proposalFile"]', [resolve(args.proposal)]);
    await evaluate(client, "document.querySelector('#briefForm').requestSubmit(); true");
    await waitForExpression(
      client,
      "document.querySelector('#importSummary')?.dataset.state === 'ready' && document.querySelector('#importProjectName')?.textContent === 'WP5D Browser Cutover'",
      'server-created project summary',
    );
    const projectId = await evaluate(client, "new URL(location.href).searchParams.get('projectId')");
    if (!projectId) throw new Error('server project id was not written to navigation state');
    const postCount = requests.filter((item) => item.method === 'POST' && item.path === '/v1/project-imports').length;
    const detailCountAfterCreate = requests.filter((item) => item.method === 'GET' && item.path === `/v1/projects/${projectId}`).length;
    if (postCount !== 1 || detailCountAfterCreate < 1) throw new Error('POST did not transition through a server detail GET');

    await evaluate(client, `new Promise((resolvePromise, reject) => {
      localStorage.setItem('marketops.projects.v1', JSON.stringify([{ name: 'CONTRADICTORY LOCAL PROJECT' }]));
      const request = indexedDB.open('marketops-files-v1', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('files');
      request.onsuccess = () => {
        const transaction = request.result.transaction('files', 'readwrite');
        transaction.objectStore('files').put('contradictory', 'local-only');
        transaction.oncomplete = () => { request.result.close(); resolvePromise(true); };
        transaction.onerror = () => reject(transaction.error);
      };
      request.onerror = () => reject(request.error);
    })`, { awaitPromise: true });
    await reload(client);
    await waitForExpression(client, "document.querySelector('#importSummary')?.dataset.state === 'ready' && document.querySelector('#importProjectName')?.textContent === 'WP5D Browser Cutover'", 'refresh after local-store pollution');
    if (requests.filter((item) => item.method === 'POST' && item.path === '/v1/project-imports').length !== 1) {
      throw new Error('browser refresh repeated the import POST');
    }

    failNextProjectRead = true;
    await reload(client);
    await waitForExpression(client, "document.querySelector('#importSummary')?.dataset.state === 'error'", 'network failure state');
    await evaluate(client, "document.querySelector('#retryProjectLoad').click(); true");
    await waitForExpression(client, "document.querySelector('#importSummary')?.dataset.state === 'ready' && document.querySelector('#importProjectName')?.textContent === 'WP5D Browser Cutover'", 'network retry recovery');
    if (expectedNetworkFailurePath !== `/v1/projects/${projectId}`) {
      throw new Error('injected network failure did not target the server project read');
    }
    if (!expectedNetworkFailureRequestId || expectedNetworkFailureErrorText !== 'net::ERR_CONNECTION_RESET') {
      throw new Error('injected network failure was not bound to the expected request and error');
    }

    await client.send('Storage.clearDataForOrigin', {
      origin,
      storageTypes: 'local_storage,indexeddb',
    });
    await reload(client);
    await waitForExpression(client, "document.querySelector('#importSummary')?.dataset.state === 'ready' && document.querySelector('#importProjectName')?.textContent === 'WP5D Browser Cutover'", 'refresh after browser storage removal');

    await navigate(client, `${baseUrl}/`);
    await waitForExpression(client, "document.querySelector('#importSummary')?.dataset.state === 'ready' && document.querySelector('#importProjectName')?.textContent === 'WP5D Browser Cutover'", 'root latest-project recovery');
    const rootRecoveredId = await evaluate(client, "new URL(location.href).searchParams.get('projectId')");
    if (rootRecoveredId !== projectId) throw new Error('root recovery selected a different project');

    const viewportResults = {};
    for (const width of [375, 768, 1024, 1440]) {
      await client.send('Emulation.setDeviceMetricsOverride', {
        width,
        height: 900,
        deviceScaleFactor: 1,
        mobile: width < 768,
      });
      await evaluate(client, "document.querySelector('#newBrief').click(); true");
      const layout = await evaluate(client, `(() => {
        const dialog = document.querySelector('#briefDialog').getBoundingClientRect();
        const button = document.querySelector('#createProjectButton').getBoundingClientRect();
        return {
          viewport: innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          dialogLeft: dialog.left,
          dialogRight: dialog.right,
          buttonLeft: button.left,
          buttonRight: button.right,
        };
      })()`);
      await evaluate(client, "document.querySelector('#briefDialog').close(); true");
      viewportResults[width] = layout.scrollWidth <= layout.viewport
        && layout.dialogLeft >= 0
        && layout.dialogRight <= layout.viewport
        && layout.buttonLeft >= layout.dialogLeft
        && layout.buttonRight <= layout.dialogRight;
    }
    if (Object.values(viewportResults).some((value) => value !== true)) throw new Error('responsive layout overflowed');

    const browserState = await evaluate(client, `(async () => ({
      href: location.href,
      html: document.documentElement.outerHTML,
      local: Object.fromEntries(Object.keys(localStorage).map((key) => [key, localStorage.getItem(key)])),
      databases: (await indexedDB.databases()).map((item) => item.name),
      resources: performance.getEntriesByType('resource').map((item) => item.name),
    }))()`, { awaitPromise: true });
    const serializedState = JSON.stringify(browserState);
    if (serializedState.includes(token)) throw new Error('deployment token entered browser-visible state');
    if (browserState.href.includes('@')) throw new Error('credentials appeared in the browser URL');
    if (Object.keys(browserState.local).length !== 0 || browserState.databases.includes('marketops-files-v1')) {
      throw new Error('legacy browser project storage survived cutover');
    }
    if (externalRequests.length !== 0) throw new Error('browser requested an external executable or asset');
    if (consoleFailures.length !== 0) {
      const categories = [...new Set(consoleFailures)].sort().join(',');
      throw new Error(`browser emitted console/runtime failures: ${categories}`);
    }
    if (authChallenges < 1) throw new Error('browser-native HTTP authentication was not exercised');
    if (asynchronousFailure) throw asynchronousFailure;

    return {
      authChallengeObserved: true,
      importPostCount: 1,
      detailGetAfterCreate: detailCountAfterCreate >= 1,
      refreshDidNotRepeatPost: true,
      localStorageIgnored: true,
      indexedDbIgnored: true,
      networkFailureDidNotFallback: true,
      retryRecoveredFromServer: true,
      rootRecoveredLatestProject: true,
      noExternalRequests: true,
      noConsoleFailures: true,
      credentialAbsentFromBrowserState: true,
      viewportResults,
    };
  } catch (error) {
    primaryFailure = error;
    throw error;
  } finally {
    client?.close();
    browser.kill();
    if (browser.exitCode === null && browser.signalCode === null) {
      await Promise.race([
        new Promise((resolvePromise) => browser.once('exit', resolvePromise)),
        delay(2_000).then(() => browser.kill('SIGKILL')),
      ]);
    }
    try {
      await rm(profile, {
        recursive: true,
        force: true,
        maxRetries: 10,
        retryDelay: 200,
      });
    } catch (cleanupError) {
      if (!primaryFailure) {
        const code = cleanupError?.code || 'unknown';
        throw new Error(`browser profile cleanup failed: ${code}`);
      }
    }
  }
}

if (process.argv[2] === '--self-test-network-failure-matcher') {
  runNetworkFailureMatcherSelfTest();
  process.stdout.write('browser network failure matcher passed\n');
} else {
  try {
    const result = await run(parseArgs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stderr.write(`browser gate failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}
