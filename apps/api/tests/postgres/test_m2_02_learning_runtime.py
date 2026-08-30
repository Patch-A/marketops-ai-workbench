from __future__ import annotations

import os
import importlib.util
import json
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from apps.api.marketops_execution import (
    AsyncpgExecutionRepository,
    ExecutionScopeContext,
    ExecutionService,
    ExecutionUpdateRequest,
)
from apps.api.marketops_learning import (
    AsyncpgLearningRepository,
    FeedbackSourceReference,
    LearningApplicationService,
    LearningFailure,
    LearningScopeContext,
    OutcomeInput,
    RetrospectiveInput,
)
RUNTIME_DEPENDENCIES_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("asyncpg", "fastapi", "multipart"))
if RUNTIME_DEPENDENCIES_AVAILABLE:
    from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
    from apps.api.marketops_import.service import ScopeContext
    from apps.api.tests.test_m1_02_review_http import asgi_request


ADMIN_DSN = os.environ.get("MARKETOPS_TEST_ADMIN_DATABASE_URL", "").strip()
APP_DSN = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(
    ADMIN_DSN and APP_DSN and RUNTIME_DEPENDENCIES_AVAILABLE,
    "PostgreSQL admin/application URLs and runtime dependencies are required",
)
class LearningPostgresRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from apps.api.tests.postgres.test_m1_03_schedule_runtime import (
            SchedulePostgresRuntimeTests,
        )

        self.fixture = SchedulePostgresRuntimeTests("runTest")
        await self.fixture.asyncSetUp()
        self.admin = self.fixture.admin
        self.pool = self.fixture.pool
        created = await self.fixture._create_plan()
        scheduled = await self.fixture.schedule_service.calculate_plan_schedule(
            self.fixture.project_id,
            created.plan.plan.plan_id,
            created.plan.version.version,
            "2026-08-17",
            (),
            self.fixture.schedule_scope,
        )
        approval = await self.fixture.schedule_service.approve_plan(
            self.fixture.project_id,
            created.plan.plan.plan_id,
            created.plan.version.version,
            scheduled.snapshot.snapshot_id,
            "Learning runtime approval",
            self.fixture.schedule_scope,
        )
        self.project_id = self.fixture.project_id
        self.plan_id = created.plan.plan.plan_id
        self.plan_version_id = created.plan.version.version_id
        self.plan_version = created.plan.version.version
        self.plan_digest = created.plan.version.digest
        self.schedule_snapshot_id = scheduled.snapshot.snapshot_id
        self.approval_id = approval.approval.approval_id
        self.scope = LearningScopeContext(
            self.fixture.organization_id,
            self.fixture.workspace_id,
            self.fixture.client_id,
            self.fixture.actor_id,
        )
        self.execution = ExecutionService(
            AsyncpgExecutionRepository(self.pool),
            clock=lambda: datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        )
        self.execution_scope = ExecutionScopeContext(
            self.fixture.organization_id,
            self.fixture.workspace_id,
            self.fixture.client_id,
            self.fixture.actor_id,
        )
        self.execution_ids: dict[str, str] = {}
        for index, task in enumerate(created.plan.version.payload["tasks"], start=1):
            task_id = task["taskId"]
            if index == 1:
                update = ExecutionUpdateRequest(
                    self.project_id,
                    self.plan_id,
                    self.plan_version_id,
                    task_id,
                    0,
                    "completed",
                    actual_start="2026-08-17",
                    actual_finish="2026-08-18",
                )
            else:
                update = ExecutionUpdateRequest(
                    self.project_id,
                    self.plan_id,
                    self.plan_version_id,
                    task_id,
                    0,
                    "cancelled",
                )
            await self.execution.update_task(update, self.execution_scope)
            self.execution_ids[task_id] = str(await self.admin.fetchval(
                "SELECT id FROM marketops.wbs_task_execution_updates "
                "WHERE project_id = $1 AND plan_version_id = $2 AND task_id = $3 "
                "ORDER BY sequence_no DESC LIMIT 1",
                self.project_id,
                self.plan_version_id,
                task_id,
            ))
        self.repository = AsyncpgLearningRepository(
            self.pool,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
        )
        self.service = LearningApplicationService(
            self.repository,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
        )

    async def asyncTearDown(self) -> None:
        if hasattr(self, "admin"):
            await self.admin.execute("SET session_replication_role = replica")
            try:
                for table in (
                    "knowledge_item_evidence",
                    "knowledge_item_versions",
                    "knowledge_items",
                    "project_retrospectives",
                    "project_outcomes",
                    "project_capsules",
                ):
                    await self.admin.execute(
                        f"DELETE FROM marketops.{table} WHERE project_id = $1",
                        self.fixture.project_id,
                    )
            finally:
                await self.admin.execute("SET session_replication_role = origin")
        await self.fixture.asyncTearDown()

    def feedback(self, actual_value: str = "42") -> tuple[tuple[OutcomeInput, ...], tuple[RetrospectiveInput, ...]]:
        completed_task_id = next(iter(self.execution_ids))
        outcomes = (
            OutcomeInput(
                "qualified leads",
                "30",
                actual_value,
                "count",
                FeedbackSourceReference(
                    "artifact_version",
                    self.fixture.version_id,
                    self.project_id,
                    "0" * 64,
                ),
            ),
        )
        retrospectives = (
            RetrospectiveInput(
                "A rehearsal uncovered a missing venue handoff.",
                "process_observation",
                True,
                (
                    FeedbackSourceReference(
                        "task_execution",
                        self.execution_ids[completed_task_id],
                        self.project_id,
                        "0" * 64,
                    ),
                ),
            ),
        )
        return outcomes, retrospectives

    async def test_finalization_replay_versions_reads_source_binding_and_rls(self) -> None:
        outcomes, retrospectives = self.feedback()
        first = await self.service.finalize_capsule(
            self.project_id, outcomes, retrospectives, self.scope
        )
        replay = await self.service.finalize_capsule(
            self.project_id, outcomes, retrospectives, self.scope
        )
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.capsule.capsule_id, replay.capsule.capsule_id)
        self.assertGreaterEqual(first.capsule.knowledge_count, 3)

        stored_source = await self.admin.fetchval(
            "SELECT encode(source_binding_sha256, 'hex') FROM marketops.project_outcomes "
            "WHERE capsule_id = $1",
            first.capsule.capsule_id,
        )
        self.assertEqual(stored_source, self.fixture.source_hash)
        self.assertEqual(
            await self.admin.fetchval(
                "SELECT count(*) FROM marketops.audit_events "
                "WHERE target_id = $1 AND action = 'project_capsule.finalized'",
                first.capsule.capsule_id,
            ),
            1,
        )

        loaded = await self.service.read_capsule(
            self.project_id, first.capsule.capsule_id, self.scope
        )
        self.assertEqual(loaded.knowledge_items, first.capsule.knowledge_items)
        summaries = await self.service.list_capsules(self.project_id, self.scope)
        self.assertEqual([item.capsule_version for item in summaries], [1])
        knowledge = await self.service.list_knowledge(self.project_id, self.scope)
        self.assertEqual(len(knowledge), first.capsule.knowledge_count)
        detailed = await self.service.read_knowledge(
            self.project_id, knowledge[0].knowledge_id, self.scope
        )
        self.assertEqual(len(detailed.versions), 1)
        self.assertTrue(detailed.evidence)

        changed_outcomes, changed_retrospectives = self.feedback("43")
        changed = await self.service.finalize_capsule(
            self.project_id, changed_outcomes, changed_retrospectives, self.scope
        )
        self.assertFalse(changed.replayed)
        self.assertEqual(changed.capsule.capsule_version, 2)
        self.assertEqual(
            [item.capsule_version for item in await self.service.list_capsules(self.project_id, self.scope)],
            [2, 1],
        )

        other_scope = LearningScopeContext(
            self.fixture.organization_id,
            self.fixture.workspace_id,
            self.fixture.client_id,
            str(uuid4()),
        )
        self.assertEqual(
            await self.repository.list_capsules(other_scope, self.project_id), ()
        )

    async def test_authenticated_http_finalization_and_history_reads(self) -> None:
        token = "runtime-learning-token"
        app = create_app(
            authenticator=StaticBearerAuthenticator(
                token,
                ScopeContext(
                    self.fixture.organization_id,
                    self.fixture.workspace_id,
                    self.fixture.client_id,
                    self.fixture.actor_id,
                ),
            ),
            learning_service=self.service,
            request_id_factory=lambda: "runtime-learning-request",
        )
        headers = [
            ("Authorization", f"Bearer {token}"),
            ("Content-Type", "application/json"),
        ]
        body = json.dumps(
            {
                "outcomes": [
                    {
                        "metric": "qualified leads",
                        "plannedValue": "30",
                        "actualValue": "42",
                        "unit": "count",
                        "source": {
                            "sourceType": "artifact_version",
                            "sourceId": self.fixture.version_id,
                        },
                    }
                ],
                "retrospectives": [],
            }
        ).encode()
        created = await asgi_request(
            app, "POST", f"/v1/projects/{self.project_id}/capsules", headers, body
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["cache-control"], "no-store")
        capsule_id = created.json()["capsule"]["capsuleId"]
        self.assertEqual(created.json()["capsule"]["knowledge"][0]["scope"], "project")
        binding = created.json()["capsule"]["binding"]
        self.assertEqual(binding["proposal"]["artifactId"], self.fixture.artifact_id)
        self.assertEqual(binding["proposal"]["versionId"], self.fixture.version_id)
        self.assertEqual(binding["proposal"]["sha256"], self.fixture.source_hash)
        self.assertEqual(binding["plan"]["planId"], self.plan_id)
        self.assertEqual(binding["plan"]["versionId"], self.plan_version_id)
        self.assertEqual(binding["plan"]["digest"], self.plan_digest)
        self.assertEqual(binding["approval"]["approvalId"], self.approval_id)
        self.assertEqual(binding["schedule"]["snapshotId"], self.schedule_snapshot_id)

        listed = await asgi_request(
            app, "GET", f"/v1/projects/{self.project_id}/capsules",
            [("Authorization", f"Bearer {token}")],
        )
        detail = await asgi_request(
            app, "GET", f"/v1/projects/{self.project_id}/capsules/{capsule_id}",
            [("Authorization", f"Bearer {token}")],
        )
        knowledge_id = detail.json()["capsule"]["knowledge"][0]["knowledgeId"]
        knowledge = await asgi_request(
            app, "GET", f"/v1/projects/{self.project_id}/knowledge/{knowledge_id}",
            [("Authorization", f"Bearer {token}")],
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["capsules"][0]["capsuleId"], capsule_id)
        self.assertEqual(knowledge.status_code, 200)
        self.assertEqual(knowledge.json()["knowledge"]["status"], "candidate")
        self.assertTrue(knowledge.json()["evidence"])

    async def test_deferred_trigger_rejects_cross_paired_approved_plan_facts(self) -> None:
        task_id = (await self.fixture.schedule_service.read_plan(
            self.project_id, self.plan_id, self.fixture.schedule_scope
        )).version.payload["tasks"][0]["taskId"]
        revised = await self.fixture.schedule_service.revise_plan(
            self.project_id,
            self.plan_id,
            self.plan_version,
            ({"taskId": task_id, "changes": {"durationWorkdays": 3}},),
            self.fixture.schedule_scope,
        )
        second_schedule = await self.fixture.schedule_service.calculate_plan_schedule(
            self.project_id,
            self.plan_id,
            revised.version.version,
            "2026-08-19",
            (),
            self.fixture.schedule_scope,
        )
        second_approval = await self.fixture.schedule_service.approve_plan(
            self.project_id,
            self.plan_id,
            revised.version.version,
            second_schedule.snapshot.snapshot_id,
            "Learning integrity second approval",
            self.fixture.schedule_scope,
        )

        with self.assertRaises(self.fixture.asyncpg.CheckViolationError) as raised:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    await connection.fetchrow(
                        "SELECT set_config('app.workspace_id',$1,true), "
                        "set_config('app.client_id',$2,true), "
                        "set_config('app.project_id',$3,true), "
                        "set_config('app.actor_id',$4,true)",
                        self.fixture.workspace_id,
                        self.fixture.client_id,
                        self.project_id,
                        self.fixture.actor_id,
                    )
                    await connection.execute(
                        """
                        INSERT INTO marketops.project_capsules (
                            id, organization_id, workspace_id, client_id, project_id,
                            proposal_artifact_id, proposal_version_id, proposal_version,
                            proposal_sha256, plan_id, plan_version_id, plan_version,
                            plan_digest, approval_id, schedule_snapshot_id, schedule_digest,
                            capsule_version, status, capsule_digest, payload, outcome_count,
                            retrospective_count, knowledge_count, created_by, created_at
                        ) VALUES (
                            $1,$2,$3,$4,$5,$6,$7,1,decode($8,'hex'),$9,$10,$11,
                            decode($12,'hex'),$13,$14,decode($15,'hex'),99,'ready',
                            decode($16,'hex'),'{}'::jsonb,0,0,0,$17,$18
                        )
                        """,
                        str(uuid4()),
                        self.fixture.organization_id,
                        self.fixture.workspace_id,
                        self.fixture.client_id,
                        self.project_id,
                        self.fixture.artifact_id,
                        self.fixture.version_id,
                        self.fixture.source_hash,
                        self.plan_id,
                        self.plan_version_id,
                        self.plan_version,
                        self.plan_digest,
                        second_approval.approval.approval_id,
                        second_schedule.snapshot.snapshot_id,
                        second_schedule.snapshot.schedule_digest,
                        "e" * 64,
                        self.fixture.actor_id,
                        datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
                    )
        self.assertEqual(raised.exception.sqlstate, "23514")
        self.assertEqual(
            str(raised.exception),
            "project capsule source bindings are inconsistent or not approved",
        )
        self.assertEqual(
            await self.admin.fetchval(
                "SELECT count(*) FROM marketops.project_capsules WHERE project_id = $1",
                self.project_id,
            ),
            0,
        )

    async def test_deferred_trigger_rejects_non_current_approved_proposal(self) -> None:
        alternate_artifact_id = str(uuid4())
        alternate_version_id = str(uuid4())
        alternate_sha256 = "b" * 64
        await self.admin.execute(
            """
            INSERT INTO marketops.artifacts (
                id, organization_id, workspace_id, client_id, project_id, kind, created_by
            ) VALUES ($1,$2,$3,$4,$5,'proposal',$6)
            """,
            alternate_artifact_id,
            self.fixture.organization_id,
            self.fixture.workspace_id,
            self.fixture.client_id,
            self.project_id,
            self.fixture.actor_id,
        )
        await self.admin.execute(
            """
            INSERT INTO marketops.artifact_versions (
                id, organization_id, workspace_id, client_id, project_id, artifact_id,
                proposal_version, original_filename, media_type, byte_size, storage_key,
                sha256, approval_status, approved_by, approved_at, created_by
            ) VALUES ($1,$2,$3,$4,$5,$6,2,'alternate.md','text/markdown',1,$7,
                decode($8,'hex'),'approved',$9,$10,$9)
            """,
            alternate_version_id,
            self.fixture.organization_id,
            self.fixture.workspace_id,
            self.fixture.client_id,
            self.project_id,
            alternate_artifact_id,
            f"learning-alternate/{alternate_version_id}",
            alternate_sha256,
            self.fixture.actor_id,
            datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
        )

        with self.assertRaises(self.fixture.asyncpg.CheckViolationError) as raised:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    await connection.fetchrow(
                        "SELECT set_config('app.workspace_id',$1,true), "
                        "set_config('app.client_id',$2,true), "
                        "set_config('app.project_id',$3,true), "
                        "set_config('app.actor_id',$4,true)",
                        self.fixture.workspace_id,
                        self.fixture.client_id,
                        self.project_id,
                        self.fixture.actor_id,
                    )
                    await connection.execute(
                        """
                        INSERT INTO marketops.project_capsules (
                            id, organization_id, workspace_id, client_id, project_id,
                            proposal_artifact_id, proposal_version_id, proposal_version,
                            proposal_sha256, plan_id, plan_version_id, plan_version,
                            plan_digest, approval_id, schedule_snapshot_id, schedule_digest,
                            capsule_version, status, capsule_digest, payload, outcome_count,
                            retrospective_count, knowledge_count, created_by, created_at
                        ) VALUES (
                            $1,$2,$3,$4,$5,$6,$7,2,decode($8,'hex'),$9,$10,$11,
                            decode($12,'hex'),$13,$14,decode($15,'hex'),98,'ready',
                            decode($16,'hex'),'{}'::jsonb,0,0,0,$17,$18
                        )
                        """,
                        str(uuid4()),
                        self.fixture.organization_id,
                        self.fixture.workspace_id,
                        self.fixture.client_id,
                        self.project_id,
                        alternate_artifact_id,
                        alternate_version_id,
                        alternate_sha256,
                        self.plan_id,
                        self.plan_version_id,
                        self.plan_version,
                        self.plan_digest,
                        self.approval_id,
                        self.schedule_snapshot_id,
                        (await self.admin.fetchval(
                            "SELECT encode(schedule_digest, 'hex') FROM marketops.schedule_snapshots WHERE id = $1",
                            self.schedule_snapshot_id,
                        )),
                        "f" * 64,
                        self.fixture.actor_id,
                        datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
                    )
        self.assertEqual(raised.exception.sqlstate, "23514")
        self.assertEqual(
            str(raised.exception),
            "project capsule source bindings are inconsistent or not approved",
        )

    async def test_missing_or_foreign_source_fails_without_publication(self) -> None:
        outcomes, retrospectives = self.feedback()
        invalid_outcome = OutcomeInput(
            outcomes[0].metric,
            outcomes[0].planned_value,
            outcomes[0].actual_value,
            outcomes[0].unit,
            FeedbackSourceReference(
                "artifact_version", str(uuid4()), self.project_id, "0" * 64
            ),
        )
        with self.assertRaises(LearningFailure) as raised:
            await self.service.finalize_capsule(
                self.project_id, (invalid_outcome,), retrospectives, self.scope
            )
        self.assertEqual(raised.exception.code, "CAPSULE_NOT_READY")
        self.assertEqual(
            await self.admin.fetchval(
                "SELECT count(*) FROM marketops.project_capsules WHERE project_id = $1",
                self.project_id,
            ),
            0,
        )

        await self.admin.execute("SET session_replication_role = replica")
        try:
            await self.admin.execute(
                "DELETE FROM marketops.wbs_task_execution_updates WHERE project_id = $1",
                self.project_id,
            )
        finally:
            await self.admin.execute("SET session_replication_role = origin")
        retrospective_with_artifact = RetrospectiveInput(
            retrospectives[0].finding,
            retrospectives[0].classification,
            retrospectives[0].reusable_candidate,
            (outcomes[0].source,),
        )
        with self.assertRaises(LearningFailure) as raised:
            await self.service.finalize_capsule(
                self.project_id, outcomes, (retrospective_with_artifact,), self.scope
            )
        self.assertEqual(raised.exception.code, "INVALID_INPUT")
        self.assertEqual(
            await self.admin.fetchval(
                "SELECT count(*) FROM marketops.project_capsules WHERE project_id = $1",
                self.project_id,
            ),
            0,
        )

    async def test_audit_failure_rolls_back_every_learning_row(self) -> None:
        function_name = f"reject_learning_audit_{self.project_id.replace('-', '')}"
        trigger_name = function_name
        await self.admin.execute(
            f"""
            CREATE FUNCTION marketops.{function_name}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.project_id = '{self.project_id}'::uuid
                   AND NEW.action = 'project_capsule.finalized' THEN
                    RAISE EXCEPTION 'forced learning audit failure';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER {trigger_name}
            BEFORE INSERT ON marketops.audit_events
            FOR EACH ROW EXECUTE FUNCTION marketops.{function_name}();
            """
        )
        try:
            outcomes, retrospectives = self.feedback()
            with self.assertRaises(LearningFailure) as raised:
                await self.service.finalize_capsule(
                    self.project_id, outcomes, retrospectives, self.scope
                )
            self.assertEqual(raised.exception.code, "LEARNING_WRITE_FAILED")
            self.assertNotIn("forced learning audit failure", str(raised.exception))
        finally:
            await self.admin.execute(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON marketops.audit_events; "
                f"DROP FUNCTION IF EXISTS marketops.{function_name}()"
            )
        counts = await self.admin.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM marketops.project_capsules WHERE project_id = $1) AS capsules,
                (SELECT count(*) FROM marketops.project_outcomes WHERE project_id = $1) AS outcomes,
                (SELECT count(*) FROM marketops.project_retrospectives WHERE project_id = $1) AS retrospectives,
                (SELECT count(*) FROM marketops.knowledge_items WHERE project_id = $1) AS knowledge,
                (SELECT count(*) FROM marketops.knowledge_item_evidence WHERE project_id = $1) AS evidence,
                (SELECT count(*) FROM marketops.audit_events WHERE project_id = $1 AND action = 'project_capsule.finalized') AS audits
            """,
            self.project_id,
        )
        self.assertEqual(tuple(counts.values()), (0, 0, 0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
