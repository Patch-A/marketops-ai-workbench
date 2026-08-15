import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const projectSource = fs.readFileSync(new URL('../project-import.js', import.meta.url), 'utf8');
const executionSource = fs.readFileSync(new URL('../execution-workbench.js', import.meta.url), 'utf8');
const context = { globalThis: {}, console, Date, Set, Map, Blob, URLSearchParams };
context.window = context.globalThis;
vm.runInNewContext(projectSource, context, { filename: 'project-import.js' });
vm.runInNewContext(executionSource, context, { filename: 'execution-workbench.js' });

const clientApi = context.globalThis.MarketOpsProjectImport;
const executionApi = context.globalThis.MarketOpsExecutionWorkbench;
const projectId = '10000000-0000-4000-8000-000000000001';
const planId = '10000000-0000-4000-8000-000000000002';
const candidateId = '10000000-0000-4000-8000-000000000003';
const taskId = `candidate:${candidateId}`;

function taskFixture(overrides = {}) {
  return {
    taskId, title: 'Publish launch brief', ownerRole: 'Campaign owner',
    plannedStart: '2026-08-15', plannedFinish: '2026-08-16', status: 'blocked',
    blockerReason: 'Awaiting venue sign-off', actualStart: '2026-08-15', actualFinish: null,
    note: 'Local review', sequenceNo: 2, updatedAt: '2026-08-15T12:00:00Z', ...overrides,
  };
}

function jsonResponse(value, status = 200) {
  return { ok: status >= 200 && status < 300, status, async json() { return value; } };
}

test('execution client reads an exact approved plan version without scope facts', async () => {
  let captured;
  const api = clientApi.createProjectApiClient({
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return jsonResponse({ projectId, planId, planVersion: 1, editable: true, tasks: [taskFixture()] });
    },
    FormDataImpl: class FormData {},
  });
  const result = await api.getExecutionState(projectId, planId, { planVersion: 1 });
  assert.equal(result.tasks[0].sequenceNo, 2);
  assert.equal(captured.url, `/v1/projects/${projectId}/wbs-plans/${planId}/execution?planVersion=1`);
  assert.equal(captured.options.credentials, 'same-origin');
  assert.equal(Object.hasOwn(captured.options, 'body'), false);
});

test('execution client submits only user-editable execution facts', async () => {
  let captured;
  const api = clientApi.createProjectApiClient({
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return jsonResponse({
        update: {
          taskId, sequenceNo: 3, status: 'completed', blockerReason: null,
          actualStart: '2026-08-15', actualFinish: '2026-08-16', note: 'Done',
          updatedAt: '2026-08-15T13:00:00Z',
        }, replayed: false,
      }, 201);
    },
    FormDataImpl: class FormData {},
  });
  const input = {
    expectedPlanVersion: 1, taskId, expectedExecutionSequence: 2, status: 'completed',
    actualStart: '2026-08-15', actualFinish: '2026-08-16', note: 'Done',
  };
  const result = await api.updateExecutionTask(projectId, planId, input);
  assert.equal(result.update.sequenceNo, 3);
  assert.deepEqual(JSON.parse(captured.options.body), input);
  for (const forbidden of ['organizationId', 'workspaceId', 'clientId', 'actorId', 'planVersionId']) {
    assert.equal(Object.hasOwn(JSON.parse(captured.options.body), forbidden), false);
  }
});

test('execution client rejects malformed responses and invalid status requirements', async () => {
  const noNetwork = clientApi.createProjectApiClient({
    fetchImpl: async () => { throw new Error('network should not run'); },
    FormDataImpl: class FormData {},
  });
  await assert.rejects(
    noNetwork.updateExecutionTask(projectId, planId, {
      expectedPlanVersion: 1, taskId, expectedExecutionSequence: 0, status: 'blocked',
    }),
    (error) => error.code === 'INVALID_INPUT',
  );

  const malformed = clientApi.createProjectApiClient({
    fetchImpl: async () => jsonResponse({
      projectId, planId, planVersion: 1, editable: true, tasks: [taskFixture()], actorId: candidateId,
    }),
    FormDataImpl: class FormData {},
  });
  await assert.rejects(
    malformed.getExecutionState(projectId, planId, { planVersion: 1 }),
    (error) => error.code === 'MALFORMED_RESPONSE',
  );
});

test('execution export uses authenticated fetch and returns a local blob filename', async () => {
  let captured;
  const api = clientApi.createProjectApiClient({
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return {
        ok: true,
        status: 200,
        headers: { get(name) {
          if (name === 'content-type') return 'text/csv; charset=utf-8';
          if (name === 'content-disposition') return 'attachment; filename="marketops-execution-v1.csv"';
          return null;
        } },
        async blob() { return new Blob(['taskId,title\n']); },
      };
    },
    FormDataImpl: class FormData {},
  });
  const result = await api.downloadExecutionExport(projectId, planId, 'csv', { planVersion: 1 });
  assert.equal(result.filename, 'marketops-execution-v1.csv');
  assert.equal(captured.url, `/v1/projects/${projectId}/wbs-plans/${planId}/exports/execution.csv?planVersion=1`);
  assert.equal(captured.options.credentials, 'same-origin');
  assert.equal(captured.url.includes('token'), false);
});

test('execution workbench exposes stable labels and escapes task content', () => {
  assert.equal(executionApi.STATUS_LABELS.in_progress, '进行中');
  assert.equal(executionApi.escapeHtml('<task>'), '&lt;task&gt;');
});
