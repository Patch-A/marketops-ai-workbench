from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation
from apps.api.marketops_review import (
    AsyncpgReviewRepository,
    CreateReviewRunRequest,
    ReviewRequest,
    ReviewScopeContext,
    ReviewService,
)
from apps.api.marketops_schedule import (
    AsyncpgScheduleRepository,
    CreatePlanRequest,
    ScheduleScopeContext,
    ScheduleService,
    ScheduleServiceFailure,
)


ADMIN_DSN = os.environ.get("MARKETOPS_TEST_ADMIN_DATABASE_URL", "").strip()
APP_DSN = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "").strip()
REQUIRE = os.environ.get("MARKETOPS_REQUIRE_TEST_PREREQUISITES") == "1"


class FailingScheduleRepository:
    def __init__(self, delegate):
        self.delegate = delegate

    @asynccontextmanager
    async def transaction(self, scope, project_id):
        async with self.delegate.transaction(scope, project_id) as transaction:
            yield FailingScheduleTransaction(transaction)

    async def get_plan(self, *args, **kwargs):
        return await self.delegate.get_plan(*args, **kwargs)


class FailingScheduleTransaction:
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def insert_plan_tasks(self, version):
        raise RuntimeError("synthetic schedule task failure")


@unittest.skipUnless(
    ADMIN_DSN and APP_DSN,
    "PostgreSQL admin/application test URLs are required",
)
class SchedulePostgresRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import asyncpg
        except ImportError as error:
            if REQUIRE:
                self.fail(f"asyncpg prerequisite is required: {error}")
            self.skipTest("asyncpg is unavailable")
        self.asyncpg = asyncpg
        self.organization_id = str(uuid4())
        self.workspace_id = str(uuid4())
        self.client_id = str(uuid4())
        self.project_id = str(uuid4())
        self.actor_id = str(uuid4())
        self.artifact_id = str(uuid4())
        self.version_id = str(uuid4())
        self.source_hash = "a" * 64
        self.review_scope = ReviewScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        self.schedule_scope = ScheduleScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        self.admin = await asyncpg.connect(ADMIN_DSN)
        self.pool = await asyncpg.create_pool(dsn=APP_DSN, min_size=1, max_size=4)
        await self._seed_project()
        self.review_service = ReviewService(
            repository=AsyncpgReviewRepository(self.pool),
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        self.schedule_repository = AsyncpgScheduleRepository(self.pool)
        self.schedule_service = ScheduleService(
            repository=self.schedule_repository,
            review_service=self.review_service,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        self.candidates = (
            self._candidate("deliverable", "Publish the runtime launch brief."),
            self._candidate("milestone", "Approve the runtime launch checkpoint."),
        )
        created = await self.review_service.create_run(
            CreateReviewRunRequest(
                self.project_id,
                self.artifact_id,
                self.version_id,
                1,
                self.source_hash,
                self.candidates,
            ),
            self.review_scope,
        )
        await self.review_service.review_candidate(
            self.project_id,
            created.run.run_id,
            ReviewRequest(1, self.candidates[0].candidate_id, "approve", "runtime WBS"),
            self.review_scope,
        )
        await self.review_service.review_candidate(
            self.project_id,
            created.run.run_id,
            ReviewRequest(2, self.candidates[1].candidate_id, "approve", "runtime WBS"),
            self.review_scope,
        )
        self.run_id = created.run.run_id

    async def asyncTearDown(self):
        if hasattr(self, "pool"):
            await self.pool.close()
        if hasattr(self, "admin"):
            try:
                await self.admin.execute("SET session_replication_role = replica")
                for table, column, value in (
                    ("audit_events", "project_id", self.project_id),
                    ("schedule_snapshots", "project_id", self.project_id),
                    ("wbs_tasks", "project_id", self.project_id),
                    ("wbs_plan_versions", "project_id", self.project_id),
                    ("wbs_plans", "project_id", self.project_id),
                    ("review_decisions", "project_id", self.project_id),
                    ("review_snapshot_items", "project_id", self.project_id),
                    ("review_snapshots", "project_id", self.project_id),
                    ("extraction_candidates", "project_id", self.project_id),
                    ("extraction_runs", "project_id", self.project_id),
                    ("artifact_versions", "project_id", self.project_id),
                    ("artifacts", "project_id", self.project_id),
                    ("projects", "id", self.project_id),
                    ("clients", "id", self.client_id),
                    ("workspaces", "id", self.workspace_id),
                    ("organizations", "id", self.organization_id),
                ):
                    await self.admin.execute(
                        f"DELETE FROM marketops.{table} WHERE {column} = $1", value
                    )
            finally:
                await self.admin.execute("SET session_replication_role = origin")
                await self.admin.close()

    async def _seed_project(self):
        async with self.admin.transaction():
            await self.admin.execute(
                "INSERT INTO marketops.organizations (id, name) VALUES ($1, 'Schedule runtime')",
                self.organization_id,
            )
            await self.admin.execute(
                "INSERT INTO marketops.workspaces (id, organization_id, name, kind) VALUES ($1, $2, 'Schedule runtime', 'personal')",
                self.workspace_id,
                self.organization_id,
            )
            await self.admin.execute(
                "INSERT INTO marketops.clients (id, organization_id, workspace_id, name) VALUES ($1, $2, $3, 'Schedule runtime')",
                self.client_id,
                self.organization_id,
                self.workspace_id,
            )
            await self.admin.execute(
                """
                INSERT INTO marketops.projects (
                    id, organization_id, workspace_id, client_id, name,
                    import_request_id, import_manifest_sha256,
                    approved_proposal_artifact_id, approved_proposal_version_id,
                    approved_proposal_number, created_by
                ) VALUES ($1, $2, $3, $4, 'Schedule runtime', $5, $6, $7, $8, 1, $9)
                """,
                self.project_id,
                self.organization_id,
                self.workspace_id,
                self.client_id,
                f"schedule-runtime-{self.project_id}",
                bytes.fromhex("b" * 64),
                self.artifact_id,
                self.version_id,
                self.actor_id,
            )
            await self.admin.execute(
                """
                INSERT INTO marketops.artifacts (
                    id, organization_id, workspace_id, client_id, project_id, kind, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'proposal', $6)
                """,
                self.artifact_id,
                self.organization_id,
                self.workspace_id,
                self.client_id,
                self.project_id,
                self.actor_id,
            )
            await self.admin.execute(
                """
                INSERT INTO marketops.artifact_versions (
                    id, organization_id, workspace_id, client_id, project_id,
                    artifact_id, proposal_version, original_filename, media_type,
                    byte_size, storage_key, sha256, approval_status,
                    approved_by, approved_at, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, 1, 'proposal.md', 'text/markdown',
                    1, $7, $8, 'approved', $9, $10, $9
                )
                """,
                self.version_id,
                self.organization_id,
                self.workspace_id,
                self.client_id,
                self.project_id,
                self.artifact_id,
                f"schedule-runtime/{self.version_id}",
                bytes.fromhex(self.source_hash),
                self.actor_id,
                datetime.now(timezone.utc),
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
                ("Runtime",),
                text,
            ),
        )

    async def _create_plan(self, repository=None):
        service = self.schedule_service if repository is None else ScheduleService(
            repository=repository,
            review_service=self.review_service,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        return await service.create_plan(
            CreatePlanRequest(self.project_id, self.run_id, 3), self.schedule_scope
        )

    async def test_persisted_plan_revision_schedule_and_replay_are_scoped(self):
        created = await self._create_plan()
        replay = await self._create_plan()
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan.plan.plan_id, created.plan.plan.plan_id)
        task_id = created.plan.version.payload["tasks"][0]["taskId"]
        revised = await self.schedule_service.revise_plan(
            self.project_id,
            created.plan.plan.plan_id,
            1,
            ({"taskId": task_id, "changes": {"durationWorkdays": 3}},),
            self.schedule_scope,
        )
        self.assertEqual(revised.version.version, 2)
        first = await self.schedule_service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            2,
            "2026-08-17",
            ("2026-08-20",),
            self.schedule_scope,
        )
        second = await self.schedule_service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            2,
            "2026-08-17",
            ("2026-08-20",),
            self.schedule_scope,
        )
        self.assertTrue(second.replayed)
        self.assertEqual(first.snapshot.schedule_digest, second.snapshot.schedule_digest)
        loaded = await self.schedule_service.read_plan(
            self.project_id, created.plan.plan.plan_id, self.schedule_scope
        )
        self.assertEqual(loaded.version.version, 2)
        other_scope = ScheduleScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            str(uuid4()),
        )
        self.assertEqual(
            await self.schedule_repository.get_plan(
                other_scope,
                self.project_id,
                created.plan.plan.plan_id,
                None,
            ),
            None,
        )

    async def test_concurrent_same_source_replays_and_stale_revision_loses(self):
        results = await asyncio.gather(self._create_plan(), self._create_plan())
        self.assertEqual(sum(result.replayed for result in results), 1)
        self.assertEqual(len({result.plan.plan.plan_id for result in results}), 1)
        plan_id = results[0].plan.plan.plan_id
        task_id = results[0].plan.version.payload["tasks"][0]["taskId"]

        async def revise(duration):
            try:
                result = await self.schedule_service.revise_plan(
                    self.project_id,
                    plan_id,
                    1,
                    ({"taskId": task_id, "changes": {"durationWorkdays": duration}},),
                    self.schedule_scope,
                )
                return result.version.version
            except ScheduleServiceFailure as failure:
                return failure.code

        self.assertCountEqual(await asyncio.gather(revise(2), revise(4)), [2, "PLAN_CONFLICT"])

    async def test_failed_task_insert_rolls_back_plan_and_version(self):
        failing = FailingScheduleRepository(self.schedule_repository)
        with self.assertRaises(ScheduleServiceFailure) as raised:
            await self._create_plan(failing)
        self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")
        counts = await self.admin.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM marketops.wbs_plans WHERE project_id = $1) AS plans,
              (SELECT count(*) FROM marketops.wbs_plan_versions WHERE project_id = $1) AS versions,
              (SELECT count(*) FROM marketops.wbs_tasks WHERE project_id = $1) AS tasks,
              (SELECT count(*) FROM marketops.schedule_snapshots WHERE project_id = $1) AS schedules
            """,
            self.project_id,
        )
        self.assertEqual(dict(counts), {"plans": 0, "versions": 0, "tasks": 0, "schedules": 0})


if __name__ == "__main__":
    unittest.main()
