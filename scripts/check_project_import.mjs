import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const {
  createArtifactMetadata,
  createProjectRecord,
  isSupportedImportName,
  listProjectRecords,
  retainFileRecord,
  saveProjectRecord,
  sha256Blob,
  verifyRetainedProjectFiles,
  validateProjectRecord,
} = require('../project-import.js');

assert.equal(
  await sha256Blob(new Blob(['hello'])),
  '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824',
);

assert.equal(isSupportedImportName('proposal.md'), true);
assert.equal(isSupportedImportName('schedule.CSV'), true);
assert.equal(isSupportedImportName('approved.docx'), true);
assert.equal(isSupportedImportName('scan.pdf'), false);
assert.equal(isSupportedImportName('deck.pptx'), false);

assert.deepEqual(
  createArtifactMetadata({ id: 'source-001', name: 'brief.md', type: 'text/markdown', size: 12, sha256: 'c'.repeat(64) }),
  {
    id: 'source-001',
    name: 'brief.md',
    type: 'text/markdown',
    size: 12,
    sha256: 'c'.repeat(64),
    retained: true,
  },
);

const retained = [];
await retainFileRecord(
  { name: 'brief.md', type: 'text/markdown', size: 12 },
  'source-001',
  { async put(value) { retained.push(value); } },
  '2026-08-08T10:00:00.000Z',
);
assert.deepEqual(retained, [{
  id: 'source-001',
  file: { name: 'brief.md', type: 'text/markdown', size: 12 },
  name: 'brief.md',
  type: 'text/markdown',
  size: 12,
  retainedAt: '2026-08-08T10:00:00.000Z',
}]);

function validInput(overrides = {}) {
  return {
    id: 'project-001',
    name: 'AI 活动增长方案',
    clientName: '示例客户',
    sourceFile: {
      id: 'artifact-brief-v1',
      name: 'brief.md',
      type: 'text/markdown',
      size: 5,
      sha256: '29a8825bd242f14386ee528d76e0e8f1e38f3c8c4047d7b2d6df7493368a17d0',
      retained: true,
    },
    approvedProposal: {
      id: 'artifact-proposal-v2',
      version: 2,
      name: 'proposal-v2.md',
      sha256: 'ecd1378bc9dc130008f00d58db5d26f60db55934a49b949af7e6f6a8da2a2beb',
      status: 'approved',
      retained: true,
      approvedAt: '2026-08-08T10:00:00.000Z',
    },
    createdAt: '2026-08-08T10:00:00.000Z',
    ...overrides,
  };
}

const record = createProjectRecord(validInput());
assert.equal(record.schemaVersion, 1);
assert.equal(record.status, 'planning');
assert.equal(record.sourceFile.retained, true);
assert.equal(record.approvedProposal.status, 'approved');
assert.equal(record.approvedProposal.retained, true);
assert.equal(validateProjectRecord(record), true);

assert.equal(await verifyRetainedProjectFiles(record, {
  async get(id) {
    if (id === record.sourceFile.id) return { name: 'brief.md', size: 5, file: new Blob(['brief']) };
    if (id === record.approvedProposal.id) return { name: 'proposal-v2.md', size: 8, file: new Blob(['proposal']) };
    return undefined;
  },
}), true);
await assert.rejects(
  () => verifyRetainedProjectFiles(record, { async get(id) { return id === record.sourceFile.id ? { name: 'brief.md', size: 5, file: new Blob(['brief']) } : undefined; } }),
  /approved proposal file is missing/i,
);
await assert.rejects(
  () => verifyRetainedProjectFiles(record, {
    async get(id) {
      if (id === record.sourceFile.id) return { name: 'brief.md', size: 5, file: new Blob(['tampered']) };
      return { name: 'proposal-v2.md', size: 8, file: new Blob(['proposal']) };
    },
  }),
  /hash does not match/i,
);

assert.throws(
  () => createProjectRecord(validInput({ approvedProposal: { ...validInput().approvedProposal, status: 'draft' } })),
  /approved proposal/i,
);
assert.throws(
  () => createProjectRecord(validInput({ sourceFile: { ...validInput().sourceFile, retained: false } })),
  /retained/i,
);
assert.throws(
  () => createProjectRecord(validInput({ approvedProposal: { ...validInput().approvedProposal, retained: false } })),
  /retained/i,
);
assert.throws(
  () => createProjectRecord(validInput({ approvedProposal: { ...validInput().approvedProposal, name: 'proposal.pdf' } })),
  /unsupported approved proposal format/i,
);

const store = new Map();
const storage = {
  getItem(key) { return store.has(key) ? store.get(key) : null; },
  setItem(key, value) { store.set(key, value); },
};
assert.deepEqual(listProjectRecords(storage), []);
saveProjectRecord(record, storage);
assert.deepEqual(listProjectRecords(storage), [record]);
saveProjectRecord({ ...record, name: '更新后的方案' }, storage);
assert.equal(listProjectRecords(storage)[0].name, '更新后的方案');

console.log('Project import contract passed: valid record, approval gate, retention gate, and local persistence.');
