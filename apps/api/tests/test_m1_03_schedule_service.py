from __future__ import annotations

import hashlib
import json
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation
from apps.api.marketops_review import (
    ApprovedProposal,
    ReviewCandidateView,
    ReviewDecision,
    ReviewReadModel,
    ReviewRun,
    ReviewSnapshot,
    ReviewSnapshotItem,
)
from apps.api.marketops_schedule import (
    CreatePlanRequest,
    PlanReadModel,
    ScheduleScopeContext,
    ScheduleService,
    ScheduleServiceFailure,
)


class FakeReviewService:
    def __init__(self, model):
        self.model = model
        self.calls = []

    async def read_review(self, project_id, run_id, scope, *, review_version=None):
        self.calls.append((project_id, run_id, scope, review_version))
        return self.model


class MemoryScheduleRepository:
    def __init__(self, approved):
        self.approved = approved
        self.plans = {}
        self.versions = {}
        self.sources = {}
        self.schedules = {}
        self.approvals = {}
        self.audits = []
        self.tasks = []

    @asynccontextmanager
    async def transaction(self, scope, project_id):
        self.scope = scope
        self.project_id = project_id
        yield self

    async def get_plan(self, scope, project_id, plan_id, plan_version):
        plan = self.plans.get(plan_id)
        if plan is None:
            return None
        history = self.versions[plan_id]
        version = history[-1] if plan_version is None else history[plan_version - 1]
        return PlanReadModel(plan, version, tuple(item.version for item in history))

    async def get_plan_approval(
        self, scope, project_id, plan_id, plan_version_id
    ):
        return self.approvals.get(plan_version_id)

    async def get_approved_proposal_for_update(self, project_id):
        return self.approved

    async def get_plan_by_source_for_update(self, review_run_id, review_version):
        plan_id = self.sources.get((review_run_id, review_version))
        return None if plan_id is None else await self.get_plan(
            self.scope, self.project_id, plan_id, None
        )

    async def get_plan_for_update(self, plan_id):
        return await self.get_plan(self.scope, self.project_id, plan_id, None)

    async def insert_plan(self, plan):
        self.plans[plan.plan_id] = plan
        self.versions[plan.plan_id] = []
        self.sources[(plan.source_review_run_id, plan.source_review_version)] = plan.plan_id

    async def insert_plan_version(self, version):
        self.versions[version.plan_id].append(version)

    async def insert_plan_tasks(self, version):
        self.tasks.append((version.plan_id, version.version, len(version.payload["tasks"])))

    async def get_schedule_by_digest(self, plan_version_id, schedule_digest):
        return self.schedules.get((plan_version_id, schedule_digest))

    async def get_schedule_for_update(self, schedule_snapshot_id):
        return next(
            (
                snapshot
                for snapshot in self.schedules.values()
                if snapshot.snapshot_id == schedule_snapshot_id
            ),
            None,
        )

    async def get_approval_by_plan_version(self, plan_version_id):
        return self.approvals.get(plan_version_id)

    async def insert_schedule(self, snapshot):
        self.schedules[(snapshot.plan_version_id, snapshot.schedule_digest)] = snapshot

    async def insert_approval(self, approval):
        self.approvals[approval.plan_version_id] = approval

    async def append_audit_event(self, event):
        self.audits.append(event)


class M103ScheduleServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.organization_id = str(uuid4())
        self.workspace_id = str(uuid4())
        self.client_id = str(uuid4())
        self.actor_id = str(uuid4())
        self.project_id = str(uuid4())
        self.artifact_id = str(uuid4())
        self.version_id = str(uuid4())
        self.run_id = str(uuid4())
        self.snapshot_id = str(uuid4())
        self.source_hash = "a" * 64
        self.scope = ScheduleScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        self.review = self._review_model()
        self.review_service = FakeReviewService(self.review)
        self.repository = MemoryScheduleRepository(
            ApprovedProposal(
                self.organization_id,
                self.workspace_id,
                self.client_id,
                self.project_id,
                self.artifact_id,
                self.version_id,
                1,
                self.source_hash,
            )
        )
        self.service = ScheduleService(
            repository=self.repository,
            review_service=self.review_service,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc),
        )

    def _candidate(self, kind, text):
        return Candidate(
            str(uuid4()),
            kind,
            text,
            "fact",
            0.95,
            SourceCitation(
                self.version_id,
                self.source_hash,
                SourceLocation.from_mapping(
                    {"kind": "line_range", "startLine": 1, "endLine": 1}
                ),
                ("Plan",),
                text,
            ),
        )

    def _review_model(self, *, pending=False):
        task = self._candidate("deliverable", "Publish the launch brief.")
        control = self._candidate("constraint", "Do not publish before approval.")
        created = datetime(2026, 8, 14, 7, 0, tzinfo=timezone.utc)
        task_decision = ReviewDecision(
            str(uuid4()),
            self.run_id,
            2,
            task.candidate_id,
            "approve",
            "Required deliverable",
            None,
            None,
            self.actor_id,
            created,
        )
        control_decision = None if pending else ReviewDecision(
            str(uuid4()),
            self.run_id,
            3,
            control.candidate_id,
            "reject",
            "Handled outside WBS",
            None,
            None,
            self.actor_id,
            created,
        )
        control_status = "pending" if pending else "reject"
        version = 2 if pending else 3
        run = ReviewRun(
            self.run_id,
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.project_id,
            self.artifact_id,
            self.version_id,
            1,
            self.source_hash,
            2,
            self.actor_id,
            created,
        )
        snapshot = ReviewSnapshot(
            self.snapshot_id,
            self.run_id,
            version,
            (
                ReviewSnapshotItem(task.candidate_id, "approve"),
                ReviewSnapshotItem(control.candidate_id, control_status),
            ),
            self.actor_id,
            created,
        )
        return ReviewReadModel(
            run,
            snapshot,
            (
                ReviewCandidateView(1, task, "approve", None, task_decision),
                ReviewCandidateView(2, control, control_status, None, control_decision),
            ),
            tuple(range(1, version + 1)),
            control_decision or task_decision,
        )

    async def _create(self):
        return await self.service.create_plan(
            CreatePlanRequest(self.project_id, self.run_id, 3), self.scope
        )

    async def test_create_plan_binds_server_review_and_replays_same_source(self):
        created = await self._create()
        replay = await self._create()
        self.assertFalse(created.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan.plan.plan_id, created.plan.plan.plan_id)
        self.assertEqual(created.plan.plan.source_review_snapshot_id, self.snapshot_id)
        self.assertEqual(created.plan.version.payload["sourceReviewVersion"], 3)
        self.assertEqual(len(created.plan.version.payload["tasks"]), 1)
        self.assertEqual(len(self.repository.audits), 1)
        self.assertEqual(self.repository.tasks, [(created.plan.plan.plan_id, 1, 1)])

    async def test_read_rejects_payload_version_drift(self):
        created = await self._create()
        current = self.repository.versions[created.plan.plan.plan_id][-1]
        payload = dict(current.payload)
        payload["planVersion"] = current.version + 1
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.repository.versions[created.plan.plan.plan_id][-1] = replace(
            current, payload=payload, digest=digest
        )

        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self.service.read_plan(
                self.project_id, created.plan.plan.plan_id, self.scope
            )
        self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")

    async def test_incomplete_review_fails_before_persistence(self):
        self.review_service.model = self._review_model(pending=True)
        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self.service.create_plan(
                CreatePlanRequest(self.project_id, self.run_id, 2), self.scope
            )
        self.assertEqual(raised.exception.code, "REVIEW_INCOMPLETE")
        self.assertEqual(self.repository.plans, {})

    async def test_current_approved_proposal_must_match_review_source(self):
        self.repository.approved = ApprovedProposal(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.project_id,
            self.artifact_id,
            str(uuid4()),
            2,
            "b" * 64,
        )
        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self._create()
        self.assertEqual(raised.exception.code, "SOURCE_CONFLICT")
        self.assertEqual(self.repository.plans, {})

    async def test_revision_is_append_only_and_rejects_stale_version(self):
        created = await self._create()
        task_id = created.plan.version.payload["tasks"][0]["taskId"]
        revised = await self.service.revise_plan(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            ({"taskId": task_id, "changes": {"durationWorkdays": 3}},),
            self.scope,
        )
        self.assertEqual(revised.version.version, 2)
        self.assertEqual(revised.version.payload["tasks"][0]["durationWorkdays"], 3)
        self.assertEqual(revised.available_versions, (1, 2))
        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self.service.revise_plan(
                self.project_id,
                created.plan.plan.plan_id,
                1,
                ({"taskId": task_id, "changes": {"durationWorkdays": 4}},),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "PLAN_CONFLICT")

    async def test_schedule_is_persisted_once_and_stale_version_fails(self):
        created = await self._create()
        first = await self.service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            "2026-08-17",
            ("2026-08-20",),
            self.scope,
        )
        replay = await self.service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            "2026-08-17",
            ("2026-08-20",),
            self.scope,
        )
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.snapshot.schedule_digest, replay.snapshot.schedule_digest)
        task_id = created.plan.version.payload["tasks"][0]["taskId"]
        await self.service.revise_plan(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            ({"taskId": task_id, "changes": {"durationWorkdays": 2}},),
            self.scope,
        )
        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self.service.calculate_plan_schedule(
                self.project_id,
                created.plan.plan.plan_id,
                1,
                "2026-08-17",
                (),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "PLAN_CONFLICT")

    async def test_ready_schedule_approval_is_append_only_and_replays(self):
        created = await self._create()
        scheduled = await self.service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            "2026-08-17",
            (),
            self.scope,
        )
        approved = await self.service.approve_plan(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            scheduled.snapshot.snapshot_id,
            "  Ready for execution  ",
            self.scope,
        )
        def reject_new_id():
            raise RuntimeError("replay must not allocate")

        self.service.id_factory = reject_new_id
        replay = await self.service.approve_plan(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            scheduled.snapshot.snapshot_id,
            "A later retry cannot rewrite the original reason",
            self.scope,
        )
        self.assertFalse(approved.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.approval.approval_id, approved.approval.approval_id)
        self.assertEqual(replay.approval.reason, "Ready for execution")
        self.assertEqual(replay.approval.plan_digest, created.plan.version.digest)
        self.assertEqual(
            replay.approval.schedule_digest, scheduled.snapshot.schedule_digest
        )
        self.assertEqual(self.repository.audits[-1].action, "wbs_plan_approved")

    async def test_needs_review_schedule_cannot_be_approved(self):
        created = await self._create()
        scheduled = await self.service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            "2026-08-17",
            (),
            self.scope,
        )
        blocked_payload = dict(scheduled.snapshot.payload)
        blocked_payload["status"] = "needs_review"
        blocked = replace(
            scheduled.snapshot, status="needs_review", payload=blocked_payload
        )
        self.repository.schedules[
            (blocked.plan_version_id, blocked.schedule_digest)
        ] = blocked

        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self.service.approve_plan(
                self.project_id,
                created.plan.plan.plan_id,
                1,
                blocked.snapshot_id,
                "Approve despite risk",
                self.scope,
            )
        self.assertEqual(raised.exception.code, "SCHEDULE_NOT_READY")
        self.assertEqual(self.repository.approvals, {})

    async def test_revision_preserves_historical_approval_and_new_version_is_unapproved(self):
        created = await self._create()
        scheduled = await self.service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            "2026-08-17",
            (),
            self.scope,
        )
        approval = await self.service.approve_plan(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            scheduled.snapshot.snapshot_id,
            "Version one accepted",
            self.scope,
        )
        task_id = created.plan.version.payload["tasks"][0]["taskId"]
        await self.service.revise_plan(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            ({"taskId": task_id, "changes": {"durationWorkdays": 2}},),
            self.scope,
        )

        historical = await self.service.read_plan_approval(
            self.project_id,
            created.plan.plan.plan_id,
            self.scope,
            plan_version=1,
        )
        current = await self.service.read_plan_approval(
            self.project_id, created.plan.plan.plan_id, self.scope
        )
        self.assertEqual(historical, approval.approval)
        self.assertIsNone(current)

        self.repository.approvals[created.plan.version.version_id] = replace(
            approval.approval, schedule_digest="not-a-digest"
        )
        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self.service.read_plan_approval(
                self.project_id,
                created.plan.plan.plan_id,
                self.scope,
                plan_version=1,
            )
        self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")

    async def test_approval_rejects_stale_version_wrong_snapshot_and_empty_reason(self):
        created = await self._create()
        scheduled = await self.service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            "2026-08-17",
            (),
            self.scope,
        )
        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self.service.approve_plan(
                self.project_id,
                created.plan.plan.plan_id,
                1,
                str(uuid4()),
                "Ready",
                self.scope,
            )
        self.assertEqual(raised.exception.code, "SCHEDULE_NOT_FOUND")

        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self.service.approve_plan(
                self.project_id,
                created.plan.plan.plan_id,
                1,
                scheduled.snapshot.snapshot_id,
                "   ",
                self.scope,
            )
        self.assertEqual(raised.exception.code, "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
