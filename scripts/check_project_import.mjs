import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const {
  ProjectApiError,
  createProjectApiClient,
  createRetryKeyManager,
  importFingerprint,
  importThenLoad,
  isSupportedImportName,
  isSupportedProposalName,
  loadInitialProject,
  normalizeUpload,
  validateProjectDetail,
} = require('../project-import.js');

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const SOURCE_ARTIFACT_ID = '22222222-2222-4222-8222-222222222222';
const SOURCE_VERSION_ID = '33333333-3333-4333-8333-333333333333';
const PROPOSAL_ARTIFACT_ID = '44444444-4444-4444-8444-444444444444';
const PROPOSAL_VERSION_ID = '55555555-5555-4555-8555-555555555555';

function upload(name, content, type = '', lastModified = 1) {
  const blob = new Blob([content], { type });
  Object.defineProperties(blob, {
    name: { value: name },
    lastModified: { value: lastModified },
  });
  return blob;
}

function validInput(overrides = {}) {
  return {
    projectName: 'AI 活动增长方案',
    proposalVersion: 3,
    approvalConfirmed: true,
    sourceFile: upload('brief.md', '# brief\n'),
    proposalFile: upload('proposal.md', '# proposal\n'),
    ...overrides,
  };
}

function importResult(overrides = {}) {
  return {
    projectId: PROJECT_ID,
    sourceArtifactId: SOURCE_ARTIFACT_ID,
    sourceVersionId: SOURCE_VERSION_ID,
    proposalArtifactId: PROPOSAL_ARTIFACT_ID,
    proposalVersionId: PROPOSAL_VERSION_ID,
    manifestSha256: 'a'.repeat(64),
    replayed: false,
    ...overrides,
  };
}

function projectDetail(overrides = {}) {
  return {
    projectId: PROJECT_ID,
    projectName: 'AI 活动增长方案',
    status: 'planning',
    createdAt: '2026-08-10T08:00:00Z',
    source: {
      artifactId: SOURCE_ARTIFACT_ID,
      versionId: SOURCE_VERSION_ID,
      filename: 'brief.md',
      mediaType: 'text/markdown',
      sizeBytes: 8,
    },
    proposal: {
      artifactId: PROPOSAL_ARTIFACT_ID,
      versionId: PROPOSAL_VERSION_ID,
      filename: 'proposal.md',
      mediaType: 'text/markdown',
      sizeBytes: 11,
      proposalVersion: 3,
      approvalStatus: 'approved',
      approvedAt: '2026-08-10T08:00:00Z',
    },
    ...overrides,
  };
}

function serverProjectDetail(overrides = {}) {
  const detail = projectDetail();
  return {
    projectId: detail.projectId,
    name: detail.projectName,
    status: detail.status,
    createdAt: detail.createdAt,
    sourceFile: {
      artifactId: detail.source.artifactId,
      versionId: detail.source.versionId,
      filename: detail.source.filename,
      mediaType: detail.source.mediaType,
      sizeBytes: detail.source.sizeBytes,
    },
    approvedProposal: {
      artifactId: detail.proposal.artifactId,
      versionId: detail.proposal.versionId,
      filename: detail.proposal.filename,
      mediaType: detail.proposal.mediaType,
      sizeBytes: detail.proposal.sizeBytes,
      proposalVersion: detail.proposal.proposalVersion,
      approvalStatus: detail.proposal.approvalStatus,
      approvedAt: detail.proposal.approvedAt,
    },
    ...overrides,
  };
}

function response(status, payload) {
  return {
    status,
    ok: status >= 200 && status < 300,
    async json() {
      if (payload instanceof Error) throw payload;
      return payload;
    },
  };
}

class CapturingFormData {
  constructor() {
    this.parts = [];
  }

  append(...parts) {
    this.parts.push(parts);
  }
}

assert.equal(isSupportedImportName('brief.md'), true);
assert.equal(isSupportedImportName('schedule.CSV'), true);
assert.equal(isSupportedImportName('approved.docx'), true);
assert.equal(isSupportedImportName('scan.pdf'), false);
assert.equal(isSupportedProposalName('approved.markdown'), true);
assert.equal(isSupportedProposalName('schedule.csv'), false);

for (const [name, expected] of [
  ['brief.md', 'text/markdown'],
  ['brief.markdown', 'text/markdown'],
  ['schedule.csv', 'text/csv'],
  ['approved.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
]) {
  const normalized = normalizeUpload(upload(name, 'content', 'application/octet-stream'));
  assert.equal(normalized.filename, name);
  assert.equal(normalized.mediaType, expected);
  assert.equal(normalized.body.type, expected);
}

const captured = [];
const postClient = createProjectApiClient({
  FormDataImpl: CapturingFormData,
  async fetchImpl(url, options) {
    captured.push({ url, options });
    return response(201, importResult());
  },
});
const created = await postClient.importProject(validInput(), { idempotencyKey: 'browser-request-001' });
assert.deepEqual(created, importResult());
assert.equal(captured.length, 1);
assert.equal(captured[0].url, '/v1/project-imports');
assert.equal(captured[0].options.method, 'POST');
assert.equal(captured[0].options.credentials, 'same-origin');
assert.equal(captured[0].options.headers['Idempotency-Key'], 'browser-request-001');
assert.equal(Object.keys(captured[0].options.headers).some((name) => name.toLowerCase() === 'authorization'), false);
assert.equal(Object.keys(captured[0].options.headers).some((name) => name.toLowerCase() === 'content-type'), false);
assert.deepEqual(
  captured[0].options.body.parts.map((part) => part[0]),
  ['projectName', 'proposalVersion', 'approvalConfirmed', 'sourceFile', 'proposalFile'],
);
assert.equal(captured[0].options.body.parts[3][1].type, 'text/markdown');
assert.equal(captured[0].options.body.parts[4][1].type, 'text/markdown');

await assert.rejects(
  () => postClient.importProject(validInput({ approvalConfirmed: false }), { idempotencyKey: 'browser-request-001' }),
  (error) => error instanceof ProjectApiError && error.code === 'INVALID_INPUT',
);
await assert.rejects(
  () => postClient.importProject(validInput({ proposalFile: upload('proposal.csv', 'x') }), { idempotencyKey: 'browser-request-001' }),
  (error) => error.code === 'INVALID_INPUT',
);

const keys = ['browser-key-1', 'browser-key-2'];
const manager = createRetryKeyManager(() => keys.shift());
const fingerprint = importFingerprint(validInput());
assert.equal(manager.get(fingerprint), 'browser-key-1');
assert.equal(manager.get(fingerprint), 'browser-key-1');
assert.equal(manager.get(importFingerprint(validInput({ projectName: 'changed' }))), 'browser-key-2');
manager.clear();
assert.equal(manager.peek(), null);

const order = [];
let releaseDetail;
const delayedDetail = new Promise((resolve) => { releaseDetail = resolve; });
const flow = importThenLoad({
  async importProject(_input, request) {
    order.push(`post:${request.idempotencyKey}`);
    return importResult();
  },
  async getProject(id) {
    order.push(`get:${id}`);
    return delayedDetail;
  },
}, validInput(), {
  idempotencyKey: 'browser-request-001',
  onProjectId(id) { order.push(`url:${id}`); },
});
let flowSettled = false;
flow.finally(() => { flowSettled = true; });
await Promise.resolve();
assert.equal(flowSettled, false, 'POST result must not settle the render flow before GET detail');
assert.deepEqual(order, [
  'post:browser-request-001',
  `url:${PROJECT_ID}`,
  `get:${PROJECT_ID}`,
]);
releaseDetail(projectDetail());
assert.deepEqual(await flow, { result: importResult(), detail: projectDetail() });

await assert.rejects(
  () => importThenLoad({
    async importProject() { return importResult(); },
    async getProject() { throw new ProjectApiError('PROJECT_NOT_FOUND', 'not found', { status: 404 }); },
  }, validInput(), { idempotencyKey: 'browser-request-001' }),
  (error) => error.code === 'PROJECT_NOT_FOUND' && error.uncertain === true,
  'a detail failure after POST must preserve the retry key because the commit succeeded',
);

const refreshOrder = [];
const restored = await loadInitialProject({
  async importProject() { throw new Error('refresh must not POST'); },
  async listProjects() { throw new Error('URL refresh must not list'); },
  async getProject(id) { refreshOrder.push(`get:${id}`); return projectDetail(); },
}, PROJECT_ID);
assert.equal(restored.projectId, PROJECT_ID);
assert.deepEqual(refreshOrder, [`get:${PROJECT_ID}`]);

const initialOrder = [];
let selectedFromList = '';
const latest = await loadInitialProject({
  async importProject() { throw new Error('initial load must not POST'); },
  async listProjects() {
    initialOrder.push('list');
    return { items: [{ projectId: PROJECT_ID, projectName: 'AI 活动增长方案', status: 'planning', createdAt: '2026-08-10T08:00:00Z' }] };
  },
  async getProject(id) { initialOrder.push(`get:${id}`); return projectDetail(); },
}, null, { onProjectId(id) { selectedFromList = id; } });
assert.equal(latest.projectId, PROJECT_ID);
assert.equal(selectedFromList, PROJECT_ID);
assert.deepEqual(initialOrder, ['list', `get:${PROJECT_ID}`]);
assert.equal(await loadInitialProject({
  async listProjects() { return { items: [] }; },
  async getProject() { throw new Error('empty list must not fetch detail'); },
}, null), null);

const pollutedLocalStorage = globalThis.localStorage;
const pollutedIndexedDb = globalThis.indexedDB;
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: new Proxy({}, { get() { throw new Error('localStorage fact source accessed'); } }) });
Object.defineProperty(globalThis, 'indexedDB', { configurable: true, value: new Proxy({}, { get() { throw new Error('IndexedDB fact source accessed'); } }) });
assert.equal((await loadInitialProject({
  async listProjects() { return { items: [] }; },
  async getProject() { throw new Error('not called'); },
}, null)), null);
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: pollutedLocalStorage });
Object.defineProperty(globalThis, 'indexedDB', { configurable: true, value: pollutedIndexedDb });

for (const errorCase of [
  [401, 'AUTHORIZATION_REQUIRED', false],
  [404, 'PROJECT_NOT_FOUND', false],
  [409, 'IDEMPOTENCY_CONFLICT', false],
  [500, 'DATABASE_WRITE_FAILED', true],
]) {
  const [status, code, retryable] = errorCase;
  const client = createProjectApiClient({
    FormDataImpl: CapturingFormData,
    async fetchImpl() {
      return response(status, { code, message: 'do not expose this server detail', retryable, requestId: 'request-safe' });
    },
  });
  await assert.rejects(
    () => client.getProject(PROJECT_ID),
    (error) => error.code === code
      && error.status === status
      && error.retryable === retryable
      && error.requestId === 'request-safe'
      && !error.message.includes('do not expose'),
  );
}

const malformedClient = createProjectApiClient({
  FormDataImpl: CapturingFormData,
  async fetchImpl() { return response(200, { projectId: PROJECT_ID }); },
});
await assert.rejects(
  () => malformedClient.getProject(PROJECT_ID),
  (error) => error.code === 'MALFORMED_RESPONSE',
);
assert.throws(
  () => validateProjectDetail(serverProjectDetail({ approvedProposal: { ...serverProjectDetail().approvedProposal, approvalStatus: 'pending' } })),
  (error) => error.code === 'MALFORMED_RESPONSE',
);

const readClient = createProjectApiClient({
  FormDataImpl: CapturingFormData,
  async fetchImpl(url) {
    if (url === '/v1/projects') {
      return response(200, {
        projects: [{
          projectId: PROJECT_ID,
          name: 'AI 活动增长方案',
          status: 'planning',
          approvedProposalVersion: 3,
          createdAt: '2026-08-10T08:00:00Z',
        }],
      });
    }
    return response(200, serverProjectDetail());
  },
});
assert.deepEqual(await readClient.listProjects(), {
  items: [{
    projectId: PROJECT_ID,
    projectName: 'AI 活动增长方案',
    status: 'planning',
    approvedProposalVersion: 3,
    createdAt: '2026-08-10T08:00:00Z',
  }],
});
assert.deepEqual(await readClient.getProject(PROJECT_ID), projectDetail());
for (const payload of [
  { ...serverProjectDetail(), storageKey: 'forbidden' },
  { projects: [], credentials: 'forbidden' },
]) {
  const strictClient = createProjectApiClient({
    FormDataImpl: CapturingFormData,
    async fetchImpl() { return response(200, payload); },
  });
  await assert.rejects(
    () => ('projects' in payload ? strictClient.listProjects() : strictClient.getProject(PROJECT_ID)),
    (error) => error.code === 'MALFORMED_RESPONSE',
  );
}

const abortedClient = createProjectApiClient({
  FormDataImpl: CapturingFormData,
  async fetchImpl() {
    const error = new Error('cancelled');
    error.name = 'AbortError';
    throw error;
  },
});
await assert.rejects(
  () => abortedClient.importProject(validInput(), { idempotencyKey: 'browser-request-001' }),
  (error) => error.code === 'REQUEST_CANCELLED' && error.retryable === true && error.uncertain === true,
);

const appSource = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
const moduleSource = fs.readFileSync(new URL('../project-import.js', import.meta.url), 'utf8');
const htmlSource = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const styleSource = fs.readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
for (const forbidden of ['localStorage', 'indexedDB', 'marketops.projects.v1', 'marketops-files-v1']) {
  assert.equal(appSource.includes(forbidden), false, `app.js must not use ${forbidden}`);
  assert.equal(moduleSource.includes(forbidden), false, `project-import.js must not use ${forbidden}`);
}
for (const forbidden of ['clientName', 'sha256Blob', 'createProjectRecord', 'createArtifactMetadata']) {
  assert.equal(appSource.includes(forbidden), false, `browser flow must not retain legacy field ${forbidden}`);
}
assert.equal(/<script[^>]+src=["']https?:\/\//i.test(htmlSource), false, 'the authenticated page must not execute external scripts');
assert.equal(htmlSource.includes('unpkg.com'), false);
assert.equal(htmlSource.includes('clientName'), false);
assert.equal(styleSource.includes('@import url('), false, 'the authenticated page must not make external font imports');
assert.equal(/["']Authorization["']\s*:/i.test(appSource), false, 'app.js must not set an authorization header');
assert.equal(/["']Authorization["']\s*:/i.test(moduleSource), false, 'the browser client must not set an authorization header');
assert.equal(/Bearer\s+[A-Za-z0-9]/i.test(`${appSource}\n${moduleSource}`), false, 'browser sources must not contain a bearer credential');
assert.match(appSource, /await importThenLoad\([\s\S]*renderImportedProject\(detail/);
assert.match(appSource, /loadInitialProject\(api, requestedId/);
for (const state of ['loading', 'empty', 'ready', 'error', 'REQUEST_CANCELLED', 'AUTHORIZATION_REQUIRED', 'PROJECT_NOT_FOUND']) {
  assert.equal(appSource.includes(state), true, `app.js must expose ${state} state`);
}

console.log('Project import Server API contract passed: strict POST/GET, refresh recovery, stable retry, local-store independence, and failure states.');
