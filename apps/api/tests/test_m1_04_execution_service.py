from __future__ import annotations

import asyncio
import csv
import io
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zipfile import ZipFile

from apps.api.marketops_execution import (
    ExecutionFailure,
    ExecutionScopeContext,
    ExecutionService,
    ExecutionState,
    ExecutionUpdateRequest,
    ExecutionUpdateResult,
)


SCOPE = ExecutionScopeContext(
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
    "00000000-0000-4000-8000-000000000004",
)
PROJECT = "00000000-0000-4000-8000-000000000005"
PLAN = "00000000-0000-4000-8000-000000000006"
VERSION = "00000000-0000-4000-8000-000000000007"
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self):
        self.approved = True
        self.requests = []

    async def assert_approved_plan(self, *args):
        if not self.approved:
            raise ExecutionFailure("PLAN_NOT_APPROVED", "not approved")

    @asynccontextmanager
    async def transaction(self, scope, project_id):
        yield self

    async def append_update(self, request, scope, updated_at):
        self.requests.append(request)
        return ExecutionUpdateResult(
            ExecutionState(
                request.task_id,
                request.expected_sequence + 1,
                request.status,
                request.blocker_reason,
                request.actual_start,
                request.actual_finish,
                request.note,
                scope.actor_id,
                updated_at,
            )
        )

    async def list_states(self, *args):
        return {}


class ExecutionServiceTests(unittest.TestCase):
    def service(self, repository=None):
        return ExecutionService(repository or FakeRepository(), clock=lambda: NOW)

    def request(self, **changes):
        values = {
            "project_id": PROJECT,
            "plan_id": PLAN,
            "plan_version_id": VERSION,
            "task_id": "candidate:task",
            "expected_sequence": 0,
            "status": "in_progress",
            "actual_start": "2026-08-17",
        }
        values.update(changes)
        return ExecutionUpdateRequest(**values)

    def test_update_requires_approved_plan_and_valid_blocker(self):
        repository = FakeRepository()
        result = asyncio.run(
            self.service(repository).update_task(self.request(), SCOPE)
        )
        self.assertEqual(result.update.sequence_no, 1)
        repository.approved = False
        with self.assertRaisesRegex(ExecutionFailure, "PLAN_NOT_APPROVED"):
            asyncio.run(
                self.service(repository).update_task(self.request(), SCOPE)
            )
        with self.assertRaisesRegex(ExecutionFailure, "blocked tasks"):
            asyncio.run(
                self.service().update_task(
                    self.request(status="blocked", blocker_reason=None), SCOPE
                )
            )

    def test_actual_finish_cannot_precede_start(self):
        with self.assertRaisesRegex(ExecutionFailure, "cannot precede"):
            asyncio.run(
                self.service().update_task(
                    self.request(
                        status="completed",
                        actual_start="2026-08-18",
                        actual_finish="2026-08-17",
                    ),
                    SCOPE,
                )
            )

    def test_actual_dates_require_canonical_full_date(self):
        for invalid in ("20260817", "2026-8-17", "2026-02-30"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ExecutionFailure, "YYYY-MM-DD"):
                    asyncio.run(
                        self.service().update_task(
                            self.request(actual_start=invalid), SCOPE
                        )
                    )

    def test_csv_and_xlsx_exports_are_readable_and_bounded(self):
        rows = [
            {
                "taskId": "candidate:task",
                "title": "=DANGEROUS()",
                "status": "blocked",
                "blockerReason": "Venue confirmation",
                "plannedStart": "2026-08-17",
                "plannedFinish": "2026-08-17",
                "actualStart": "2026-08-17",
                "actualFinish": None,
                "ownerRole": "Producer",
                "sourceCandidateId": "candidate-id",
            }
        ]
        csv_bytes = ExecutionService.export_csv(rows)
        parsed = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
        self.assertEqual(parsed[0]["status"], "blocked")
        self.assertEqual(parsed[0]["title"], "'=DANGEROUS()")
        xlsx_bytes = ExecutionService.export_xlsx(rows)
        with ZipFile(io.BytesIO(xlsx_bytes)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "[Content_Types].xml",
                    "_rels/.rels",
                    "xl/workbook.xml",
                    "xl/_rels/workbook.xml.rels",
                    "xl/worksheets/sheet1.xml",
                },
            )
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("Venue confirmation", sheet)
            self.assertIn("'=DANGEROUS()", sheet)
            self.assertNotIn("organization_id", sheet)


if __name__ == "__main__":
    unittest.main()
