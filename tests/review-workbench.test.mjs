import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('../review-workbench.js', import.meta.url), 'utf8');
const context = { globalThis: {}, console };
context.window = context.globalThis;
vm.runInNewContext(source, context, { filename: 'review-workbench.js' });
const api = context.globalThis.MarketOpsReviewWorkbench;

test('review metrics distinguish pending and human decisions', () => {
  assert.deepEqual(JSON.parse(JSON.stringify(api.reviewMetrics([
    { review: { status: 'pending' } },
    { review: { status: 'approve' } },
    { review: { status: 'modify' } },
    { review: { status: 'reject' } },
  ]))), { total: 4, pending: 1, approve: 1, modify: 1, reject: 1 });
});

test('source locations render bounded human-readable coordinates', () => {
  assert.equal(api.describeLocation({ kind: 'line_range', startLine: 4, endLine: 7 }), '第 4-7 行');
  assert.equal(api.describeLocation({ kind: 'csv_cell', row: 3, columnIndex: 2, columnName: 'Owner' }), '第 3 行 / Owner');
  assert.equal(api.describeLocation({ kind: 'docx_table_cell', part: 'document.xml', table: 1, row: 2, column: 3 }), 'document.xml / 表格 1 / 2 行 3 列');
});

test('stable review request key is created once per approved proposal', () => {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  let calls = 0;
  const cryptoImpl = { randomUUID: () => 'uuid-' + (++calls) };
  const project = { projectId: 'project', proposal: { versionId: 'version', sha256: 'hash' } };
  const first = api.getStableRunKey(project, storage, cryptoImpl);
  const second = api.getStableRunKey(project, storage, cryptoImpl);
  assert.equal(first, second);
  assert.equal(calls, 1);
  assert.match(api.runRequestStorageKey(project), /project:version:hash$/);
  api.clearStableRunKey(project, storage);
  assert.notEqual(api.getStableRunKey(project, storage, cryptoImpl), first);
  assert.equal(calls, 2);
});

test('escapeHtml protects cited text before it reaches the review surface', () => {
  assert.equal(api.escapeHtml('<script>alert(1)</script>'), '&lt;script&gt;alert(1)&lt;/script&gt;');
});
