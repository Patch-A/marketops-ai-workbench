import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const projectSource = fs.readFileSync(new URL('../project-import.js', import.meta.url), 'utf8');
const scheduleSource = fs.readFileSync(new URL('../schedule-workbench.js', import.meta.url), 'utf8');
const context = { globalThis: {}, console, Date, Set, Map, URLSearchParams };
context.window = context.globalThis;
vm.runInNewContext(projectSource, context, { filename: 'project-import.js' });
vm.runInNewContext(scheduleSource, context, { filename: 'schedule-workbench.js' });

const clientApi = context.globalThis.MarketOpsProjectImport;
const scheduleApi = context.globalThis.MarketOpsScheduleWorkbench;
const projectId = '10000000-0000-4000-8000-000000000001';
const planId = '10000000-0000-4000-8000-000000000002';
const runId = '10000000-0000-4000-8000-000000000003';
const reviewSnapshotId = '10000000-0000-4000-8000-000000000004';
const proposalVersionId = '10000000-0000-4000-8000-000000000005';
const candidateId = '10000000-0000-4000-8000-000000000006';
const digest = 'a'.repeat(64);

function planFixture() {
  return {
    planId, projectId, proposalVersionId, proposalSha256: digest, sourceReviewRunId: runId,
    sourceReviewSnapshotId: reviewSnapshotId, sourceReviewVersion: 2, selectedPlanVersion: 1,
    availablePlanVersions: [1], status: 'draft', planDigest: digest, createdAt: '2026-08-15T00:00:00Z',
    tasks: [{
      taskId: `candidate:${candidateId}`, candidateId, kind: 'deliverable', classification: 'fact',
      sourceText: 'Publish the launch brief.', title: 'Publish the launch brief.', sourceCitation: {},
      reviewStatus: 'approve', durationWorkdays: 2, predecessors: [], ownerRole: 'Owner', plannedStart: null,
      plannedFinish: null, hardDeadline: null, approvedBufferWorkdays: 0, isLocked: false, status: 'not_started',
    }],
    controls: [],
  };
}

function response(value, status = 201) {
  return { ok: status >= 200 && status < 300, status, async json() { return value; } };
}

test('schedule client creates a WBS plan with a closed request body', async () => {
  let captured;
  const api = clientApi.createProjectApiClient({
    fetchImpl: async (url, options) => { captured = { url, options }; return response({ plan: planFixture(), replayed: false }); },
    FormDataImpl: class FormData {},
  });
  const result = await api.createWbsPlan(projectId, { reviewRunId: runId, reviewVersion: 2 });
  assert.equal(result.plan.planId, planId);
  assert.equal(captured.url, `/v1/projects/${projectId}/wbs-plans`);
  assert.deepEqual(JSON.parse(captured.options.body), { reviewRunId: runId, reviewVersion: 2 });
});

test('schedule client rejects non-canonical calendar input before network', async () => {
  const api = clientApi.createProjectApiClient({ fetchImpl: async () => { throw new Error('network should not run'); }, FormDataImpl: class FormData {} });
  await assert.rejects(
    api.createScheduleSnapshot(projectId, planId, { expectedPlanVersion: 1, projectStart: '2026-02-30', holidays: [] }),
    (error) => error.code === 'INVALID_INPUT',
  );
});

test('schedule workbench exports safe labels and escaped text', () => {
  assert.equal(scheduleApi.STATUS_LABELS.in_progress, '进行中');
  assert.equal(scheduleApi.escapeHtml('<task>'), '&lt;task&gt;');
});
