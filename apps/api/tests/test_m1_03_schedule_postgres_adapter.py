from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_import.migrations import validate_no_transaction_control
from apps.api.marketops_schedule.postgres import (
    AsyncpgScheduleTransaction,
    SchedulePostgresError,
)
from apps.api.marketops_schedule.service import (
    ScheduleAuditEvent,
    ScheduleScopeContext,
    ScheduleSnapshot,
    WbsPlan,
    WbsPlanVersion,
)


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "apps/api/migrations/0004_wbs_schedule.sql"


class FakeConnection:
    def __init__(self):
        self.executed = []
        self.fetchrows = []
        self.fetches = []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "INSERT 0 1"

    async def fetchrow(self, query, *args):
        self.fetchrows.append((query, args))
        return None

    async def fetch(self, query, *args):
        self.fetches.append((query, args))
        return []


class FailingConnection(FakeConnection):
    async def execute(self, query, *args):
        raise RuntimeError("postgresql://secret:password@localhost/db task source")


class M103SchedulePostgresAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.organization_id = str(uuid4())
        self.workspace_id = str(uuid4())
        self.client_id = str(uuid4())
        self.project_id = str(uuid4())
        self.actor_id = str(uuid4())
        self.artifact_id = str(uuid4())
        self.proposal_version_id = str(uuid4())
        self.run_id = str(uuid4())
        self.review_snapshot_id = str(uuid4())
        self.plan_id = str(uuid4())
        self.plan_version_id = str(uuid4())
        self.created = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
        self.scope = ScheduleScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        self.plan = WbsPlan(
            self.plan_id,
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.project_id,
            self.artifact_id,
            self.proposal_version_id,
            "a" * 64,
            self.run_id,
            self.review_snapshot_id,
            2,
            self.actor_id,
            self.created,
        )
        self.candidate_id = str(uuid4())
        self.payload = {
            "schemaVersion": 1,
            "planVersion": 1,
            "status": "draft",
            "projectId": self.project_id,
            "proposal": {
                "versionId": self.proposal_version_id,
                "sha256": "a" * 64,
            },
            "sourceReviewRunId": self.run_id,
            "sourceReviewVersion": 2,
            "tasks": [
                {
                    "taskId": f"candidate:{self.candidate_id}",
                    "candidateId": self.candidate_id,
                    "kind": "deliverable",
                    "title": "Publish launch brief",
                    "durationWorkdays": 1,
                    "predecessors": [],
                    "ownerRole": "",
                    "plannedStart": None,
                    "plannedFinish": None,
                    "hardDeadline": None,
                    "approvedBufferWorkdays": 0,
                    "isLocked": False,
                    "status": "not_started",
                    "sourceCitation": {
                        "sourceVersionId": self.proposal_version_id,
                        "sourceSha256": "a" * 64,
                        "location": {"kind": "line_range", "startLine": 1, "endLine": 1},
                        "sectionPath": ["Plan"],
                        "quote": "Publish launch brief",
                    },
                }
            ],
            "controls": [],
        }
        encoded = json.dumps(
            self.payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.digest = hashlib.sha256(encoded).hexdigest()
        self.version = WbsPlanVersion(
            self.plan_version_id,
            self.plan_id,
            1,
            "draft",
            self.payload,
            self.digest,
            self.actor_id,
            self.created,
        )

    async def test_insert_batch_uses_normalized_rows_and_sanitized_values(self):
        connection = FakeConnection()
        transaction = AsyncpgScheduleTransaction(
            connection, self.scope, self.project_id
        )
        await transaction.insert_plan(self.plan)
        await transaction.insert_plan_version(self.version)
        await transaction.insert_plan_tasks(self.version)
        schedule_payload = {
            "planVersion": 1,
            "planDigest": self.digest,
            "scheduleDigest": "b" * 64,
            "projectStart": "2026-08-17",
            "calendar": {"workweek": "monday-friday", "holidays": []},
            "status": "ready",
        }
        await transaction.insert_schedule(
            ScheduleSnapshot(
                str(uuid4()),
                self.plan_id,
                self.plan_version_id,
                1,
                "2026-08-17",
                (),
                "ready",
                self.digest,
                "b" * 64,
                schedule_payload,
                self.actor_id,
                self.created,
            )
        )
        await transaction.append_audit_event(
            ScheduleAuditEvent(
                str(uuid4()),
                self.project_id,
                self.actor_id,
                "wbs_plan_created",
                "wbs_plan",
                self.plan_id,
                {"planVersion": 1},
                self.created,
            )
        )
        self.assertEqual(len(connection.executed), 5)
        task_query, task_args = connection.executed[2]
        self.assertIn("INSERT INTO marketops.wbs_tasks", task_query)
        self.assertEqual(task_args[7], f"candidate:{self.candidate_id}")
        self.assertEqual(task_args[21], self.proposal_version_id)
        self.assertEqual(task_args[22], bytes.fromhex("a" * 64))

    async def test_write_failure_does_not_expose_database_or_source_text(self):
        transaction = AsyncpgScheduleTransaction(
            FailingConnection(), self.scope, self.project_id
        )
        with self.assertRaises(SchedulePostgresError) as raised:
            await transaction.insert_plan(self.plan)
        self.assertEqual(str(raised.exception), "schedule database write failed")
        self.assertNotIn("password", str(raised.exception))
        self.assertNotIn("task source", str(raised.exception))

    def test_migration_freezes_append_only_forced_rls_contract(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        validate_no_transaction_control(sql, MIGRATION.name)
        for table in (
            "wbs_plans",
            "wbs_plan_versions",
            "wbs_tasks",
            "schedule_snapshots",
        ):
            with self.subTest(table=table):
                self.assertIn(f"CREATE TABLE marketops.{table}", sql)
                self.assertIn(
                    f"ALTER TABLE marketops.{table} FORCE ROW LEVEL SECURITY;", sql
                )
                self.assertIn(
                    f"CREATE TRIGGER {table}_immutable", sql
                )
                self.assertIn(f"REVOKE ALL ON marketops.{table} FROM PUBLIC;", sql)
        for invariant in (
            "WBS source identity is inconsistent",
            "WBS source is not the current approved proposal",
            "WBS source review is incomplete",
            "WBS plan versions must be contiguous",
            "plan_payload->>'planVersion' IS DISTINCT FROM version_record.plan_version::text",
            "WBS task batch is incomplete",
            "WBS task is not backed by an accepted review candidate",
            "schedule snapshot is inconsistent with its plan version",
        ):
            self.assertIn(invariant, sql)
        self.assertIn(
            "candidate.run_id = plan_record.source_review_run_id",
            sql,
        )
        self.assertIn("IF TG_TABLE_NAME = 'wbs_plan_versions' THEN", sql)
        self.assertIn("ELSIF TG_TABLE_NAME = 'wbs_tasks' THEN", sql)
        self.assertNotIn(
            "CASE WHEN TG_TABLE_NAME = 'wbs_plan_versions' THEN NEW.id ELSE NEW.plan_version_id END",
            sql,
        )
        self.assertRegex(
            sql,
            r"FROM marketops\.wbs_tasks AS task\s+WHERE task\.plan_version_id = "
            r"version_record\.id\s+AND \([\s\S]+?OR NOT EXISTS \(\s+SELECT 1\s+"
            r"FROM marketops\.extraction_candidates AS candidate",
        )
        self.assertNotRegex(sql.lower(), r"\bgrant\b")


if __name__ == "__main__":
    unittest.main()
