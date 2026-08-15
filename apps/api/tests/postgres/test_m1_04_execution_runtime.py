from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apps.api.marketops_execution import (
    AsyncpgExecutionRepository,
    ExecutionFailure,
    ExecutionScopeContext,
    ExecutionService,
    ExecutionUpdateRequest,
)



ADMIN_DSN = os.environ.get("MARKETOPS_TEST_ADMIN_DATABASE_URL", "").strip()
APP_DSN = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(
    ADMIN_DSN and APP_DSN,
    "PostgreSQL admin/application test URLs are required",
)
class ExecutionPostgresRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def __getattr__(self, name):
        fixture = self.__dict__.get("fixture")
        if fixture is not None:
            return getattr(fixture, name)
        raise AttributeError(name)

    async def asyncSetUp(self):
        from apps.api.tests.postgres.test_m1_03_schedule_runtime import SchedulePostgresRuntimeTests

        self.fixture = SchedulePostgresRuntimeTests("runTest")
        await self.fixture.asyncSetUp()
        self.execution_repository = AsyncpgExecutionRepository(self.pool)
        self.execution_service = ExecutionService(
            self.execution_repository,
            clock=lambda: datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc),
        )
        created = await self._create_plan()
        scheduled = await self.schedule_service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            created.plan.version.version,
            "2026-08-17",
            (),
            self.schedule_scope,
        )
        approved = await self.schedule_service.approve_plan(
            self.project_id,
            created.plan.plan.plan_id,
            created.plan.version.version,
            scheduled.snapshot.snapshot_id,
            "Execution runtime approval",
            self.schedule_scope,
        )
        self.plan_id = created.plan.plan.plan_id
        self.plan_version_id = created.plan.version.version_id
        self.task_id = created.plan.version.payload["tasks"][0]["taskId"]
        self.execution_scope = ExecutionScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        self.assertIsNotNone(approved.approval.approval_id)

    async def asyncTearDown(self):
        if hasattr(self, "admin"):
            await self.admin.execute("SET session_replication_role = replica")
            try:
                await self.admin.execute(
                    "DELETE FROM marketops.wbs_task_execution_updates WHERE project_id = $1",
                    self.project_id,
                )
            finally:
                await self.admin.execute("SET session_replication_role = origin")
        await self.fixture.asyncTearDown()

    def request(self, sequence: int, status: str, **kwargs):
        return ExecutionUpdateRequest(
            project_id=self.project_id,
            plan_id=self.plan_id,
            plan_version_id=self.plan_version_id,
            task_id=self.task_id,
            expected_sequence=sequence,
            status=status,
            **kwargs,
        )

    async def test_real_postgres_append_replay_conflict_and_audit_atomicity(self):
        started = await self.execution_service.update_task(
            self.request(0, "in_progress", actual_start="2026-08-17"),
            self.execution_scope,
        )
        blocked = await self.execution_service.update_task(
            self.request(
                1,
                "blocked",
                blocker_reason="Waiting for approval dependency",
                actual_start="2026-08-17",
            ),
            self.execution_scope,
        )
        replay = await self.execution_service.update_task(
            self.request(
                1,
                "blocked",
                blocker_reason="Waiting for approval dependency",
                actual_start="2026-08-17",
            ),
            self.execution_scope,
        )
        self.assertEqual((started.update.sequence_no, blocked.update.sequence_no), (1, 2))
        self.assertTrue(replay.replayed)
        with self.assertRaisesRegex(ExecutionFailure, "EXECUTION_CONFLICT"):
            await self.execution_service.update_task(
                self.request(1, "cancelled"), self.execution_scope
            )
        completed = await self.execution_service.update_task(
            self.request(2, "completed", actual_start="2026-08-17", actual_finish="2026-08-18"),
            self.execution_scope,
        )
        self.assertEqual(completed.update.sequence_no, 3)
        states = await self.execution_service.read_states(
            self.project_id, self.plan_version_id, self.execution_scope
        )
        self.assertEqual(states[self.task_id].status, "completed")
        row = await self.admin.fetchrow(
            "SELECT count(*) AS updates FROM marketops.wbs_task_execution_updates WHERE project_id = $1",
            self.project_id,
        )
        audit = await self.admin.fetchrow(
            "SELECT count(*) AS events FROM marketops.audit_events WHERE project_id = $1 AND action = 'wbs_task_execution_updated'",
            self.project_id,
        )
        self.assertEqual(row["updates"], 3)
        self.assertEqual(audit["events"], 3)

    async def test_real_postgres_append_only_and_forced_rls(self):
        await self.execution_service.update_task(
            self.request(0, "in_progress", actual_start="2026-08-17"),
            self.execution_scope,
        )
        with self.assertRaises(Exception):
            await self.admin.execute(
                "UPDATE marketops.wbs_task_execution_updates SET note = 'tampered' WHERE project_id = $1",
                self.project_id,
            )
        other_scope = ExecutionScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            "00000000-0000-0000-0000-000000000001",
        )
        hidden = await self.execution_service.read_states(
            self.project_id, self.plan_version_id, other_scope
        )
        self.assertEqual(hidden, {})

    async def test_real_postgres_concurrent_same_sequence_has_one_winner(self):
        results = await asyncio.gather(
            self.execution_service.update_task(
                self.request(0, "in_progress", actual_start="2026-08-17"),
                self.execution_scope,
            ),
            self.execution_service.update_task(
                self.request(0, "cancelled"),
                self.execution_scope,
            ),
            return_exceptions=True,
        )

        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(successes[0].update.sequence_no, 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ExecutionFailure)
        self.assertEqual(failures[0].code, "EXECUTION_CONFLICT")

        counts = await self.admin.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM marketops.wbs_task_execution_updates
                 WHERE project_id = $1) AS updates,
                (SELECT count(*) FROM marketops.audit_events
                 WHERE project_id = $1
                   AND action = 'wbs_task_execution_updated') AS events
            """,
            self.project_id,
        )
        self.assertEqual((counts["updates"], counts["events"]), (1, 1))

    async def test_real_postgres_revision_wins_before_execution_write(self):
        plan_locked = asyncio.Event()
        allow_revision = asyncio.Event()
        original_repository = self.schedule_service.repository
        revision = None
        execution = None

        class PausingScheduleRepository:
            @asynccontextmanager
            async def transaction(inner_self, scope, project_id):
                async with original_repository.transaction(scope, project_id) as tx:
                    class PausingTransaction:
                        def __getattr__(inner_transaction, name):
                            return getattr(tx, name)

                        async def get_plan_for_update(inner_transaction, plan_id):
                            current = await tx.get_plan_for_update(plan_id)
                            plan_locked.set()
                            await allow_revision.wait()
                            return current

                    yield PausingTransaction()

        self.schedule_service.repository = PausingScheduleRepository()
        try:
            revision = asyncio.create_task(
                self.schedule_service.revise_plan(
                    self.project_id,
                    self.plan_id,
                    1,
                    (
                        {
                            "taskId": self.task_id,
                            "changes": {"durationWorkdays": 2},
                        },
                    ),
                    self.schedule_scope,
                )
            )
            await asyncio.wait_for(plan_locked.wait(), timeout=2)
            execution = asyncio.create_task(
                self.execution_service.update_task(
                    self.request(0, "in_progress", actual_start="2026-08-17"),
                    self.execution_scope,
                )
            )
            await asyncio.sleep(0.05)
            self.assertFalse(execution.done())
            allow_revision.set()
            revised = await asyncio.wait_for(revision, timeout=5)
            self.assertEqual(revised.version.version, 2)
            with self.assertRaises(ExecutionFailure) as raised:
                await asyncio.wait_for(execution, timeout=5)
            self.assertEqual(raised.exception.code, "EXECUTION_CONFLICT")
        finally:
            allow_revision.set()
            self.schedule_service.repository = original_repository
            pending = [
                task
                for task in (revision, execution)
                if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        counts = await self.admin.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM marketops.wbs_task_execution_updates
                 WHERE project_id = $1) AS updates,
                (SELECT count(*) FROM marketops.audit_events
                 WHERE project_id = $1
                   AND action = 'wbs_task_execution_updated') AS events
            """,
            self.project_id,
        )
        self.assertEqual((counts["updates"], counts["events"]), (0, 0))

    async def test_real_postgres_audit_failure_rolls_back_execution_update(self):
        suffix = self.project_id.replace("-", "")
        function_name = f"reject_execution_audit_{suffix}"
        trigger_name = f"reject_execution_audit_{suffix}"
        await self.admin.execute(
            f"""
            CREATE FUNCTION marketops.{function_name}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'forced execution audit failure';
            END;
            $$
            """
        )
        try:
            await self.admin.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE INSERT ON marketops.audit_events
                FOR EACH ROW
                WHEN (
                    NEW.project_id = '{self.project_id}'::uuid
                    AND NEW.action = 'wbs_task_execution_updated'
                )
                EXECUTE FUNCTION marketops.{function_name}()
                """
            )
            with self.assertRaises(ExecutionFailure) as raised:
                await self.execution_service.update_task(
                    self.request(0, "in_progress", actual_start="2026-08-17"),
                    self.execution_scope,
                )
            self.assertEqual(raised.exception.code, "EXECUTION_WRITE_FAILED")

            counts = await self.admin.fetchrow(
                """
                SELECT
                    (SELECT count(*) FROM marketops.wbs_task_execution_updates
                     WHERE project_id = $1) AS updates,
                    (SELECT count(*) FROM marketops.audit_events
                     WHERE project_id = $1
                       AND action = 'wbs_task_execution_updated') AS events
                """,
                self.project_id,
            )
            self.assertEqual((counts["updates"], counts["events"]), (0, 0))
        finally:
            await self.admin.execute(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON marketops.audit_events"
            )
            await self.admin.execute(
                f"DROP FUNCTION IF EXISTS marketops.{function_name}()"
            )


if __name__ == "__main__":
    unittest.main()
