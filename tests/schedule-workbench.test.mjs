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
const scheduleId = '10000000-0000-4000-8000-000000000007';
const approvalId = '10000000-0000-4000-8000-000000000008';
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

test('schedule client accepts calendar dates used by the browser and server contract', async () => {
  let captured;
  const api = clientApi.createProjectApiClient({
    fetchImpl: async (url, options) => { captured = { url, options }; return response({ snapshot: { snapshotId: '10000000-0000-4000-8000-000000000007', planId, planVersion: 1, status: 'ready', projectStart: '2026-08-17', holidays: ['2026-08-18'], planDigest: digest, scheduleDigest: digest, createdAt: '2026-08-15T00:00:00Z', topologicalOrder: [], tasks: [{}], conflicts: [], deadlineMisses: [], sourceDateDrift: [] }, replayed: false }); },
    FormDataImpl: class FormData {},
  });
  const result = await api.createScheduleSnapshot(projectId, planId, { expectedPlanVersion: 1, projectStart: '2026-08-17', holidays: ['2026-08-18'] });
  assert.equal(result.snapshot.projectStart, '2026-08-17');
  assert.deepEqual(JSON.parse(captured.options.body), { expectedPlanVersion: 1, projectStart: '2026-08-17', holidays: ['2026-08-18'] });
});

test('schedule client sends the complete editable WBS field set without server-owned facts', async () => {
  let captured;
  const revised = { ...planFixture(), selectedPlanVersion: 2, availablePlanVersions: [1, 2] };
  const api = clientApi.createProjectApiClient({
    fetchImpl: async (url, options) => { captured = { url, options }; return response(revised); },
    FormDataImpl: class FormData {},
  });
  const changes = {
    title: 'Revised title', durationWorkdays: 3, predecessors: [], ownerRole: 'Campaign owner',
    plannedStart: '2026-08-17', plannedFinish: '2026-08-19', hardDeadline: '2026-08-20',
    approvedBufferWorkdays: 1, isLocked: true, status: 'in_progress',
  };
  const result = await api.reviseWbsPlan(projectId, planId, { expectedPlanVersion: 1, taskUpdates: [{ taskId: `candidate:${candidateId}`, changes }] });
  const body = JSON.parse(captured.options.body);
  assert.equal(result.selectedPlanVersion, 2);
  assert.deepEqual(body, { expectedPlanVersion: 1, taskUpdates: [{ taskId: `candidate:${candidateId}`, changes }] });
  assert.equal(Object.hasOwn(body, 'scope'), false);
  assert.equal(Object.hasOwn(body, 'actorId'), false);
  assert.equal(Object.hasOwn(body, 'planDigest'), false);
});

test('plan approval client sends only the human decision fields and reconciles by version', async () => {
  const calls = [];
  const approval = {
    approvalId, planId, planVersion: 1, scheduleSnapshotId: scheduleId,
    planDigest: digest, scheduleDigest: digest, reason: 'Ready for execution',
    approvedAt: '2026-08-15T00:00:00Z',
  };
  const api = clientApi.createProjectApiClient({
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return url.includes('?planVersion=1')
        ? response({ approval }, 200)
        : response({ approval, replayed: false });
    },
    FormDataImpl: class FormData {},
  });
  const result = await api.approveWbsPlan(projectId, planId, {
    expectedPlanVersion: 1, scheduleSnapshotId: scheduleId, reason: '  Ready for execution  ',
  });
  assert.equal(result.approval.approvalId, approvalId);
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    expectedPlanVersion: 1, scheduleSnapshotId: scheduleId, reason: 'Ready for execution',
  });
  for (const forbidden of ['actorId', 'approvedAt', 'approvedBy', 'planDigest', 'scheduleDigest']) {
    assert.equal(Object.hasOwn(JSON.parse(calls[0].options.body), forbidden), false);
  }
  const read = await api.getWbsPlanApproval(projectId, planId, { planVersion: 1 });
  assert.equal(read.approval.approvalId, approvalId);
  assert.equal(calls[1].url, `/v1/projects/${projectId}/wbs-plans/${planId}/approvals?planVersion=1`);
});

test('plan approval client rejects actor and scope fields in a malformed response', async () => {
  const api = clientApi.createProjectApiClient({
    fetchImpl: async () => response({ approval: {
      approvalId, planId, planVersion: 1, scheduleSnapshotId: scheduleId,
      planDigest: digest, scheduleDigest: digest, reason: 'Ready',
      approvedAt: '2026-08-15T00:00:00Z', actorId: candidateId,
    }, replayed: false }),
    FormDataImpl: class FormData {},
  });
  await assert.rejects(
    api.approveWbsPlan(projectId, planId, { expectedPlanVersion: 1, scheduleSnapshotId: scheduleId, reason: 'Ready' }),
    (error) => error.code === 'MALFORMED_RESPONSE' && error.uncertain === true,
  );
});

test('schedule workbench exports safe labels and escaped text', () => {
  assert.equal(scheduleApi.STATUS_LABELS.in_progress, '进行中');
  assert.equal(scheduleApi.escapeHtml('<task>'), '&lt;task&gt;');
});

test('approval reconciliation requires the exact requested version and snapshot', () => {
  const approval = { planVersion: 2, scheduleSnapshotId: scheduleId };
  assert.equal(scheduleApi.approvalMatchesTarget(approval, 2, scheduleId), true);
  assert.equal(scheduleApi.approvalMatchesTarget(approval, 1, scheduleId), false);
  assert.equal(scheduleApi.approvalMatchesTarget(approval, 2, approvalId), false);
  assert.equal(scheduleApi.approvalMatchesTarget(null, 2, scheduleId), false);
});

test('latest plan version is independent of the selected historical version', () => {
  assert.equal(scheduleApi.latestPlanVersion({ selectedPlanVersion: 1, availablePlanVersions: [1, 2, 3] }), 3);
});
