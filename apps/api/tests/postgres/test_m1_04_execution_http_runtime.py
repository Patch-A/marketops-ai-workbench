from __future__ import annotations

import asyncio
import json
import os
import importlib.util
import unittest
from datetime import datetime, timezone

from apps.api.marketops_execution import AsyncpgExecutionRepository, ExecutionService
from apps.api.marketops_import.service import ScopeContext


ADMIN_DSN = os.environ.get("MARKETOPS_TEST_ADMIN_DATABASE_URL", "").strip()
APP_DSN = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "").strip()
HTTP_DEPENDENCIES_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "multipart"))
if HTTP_DEPENDENCIES_AVAILABLE:
    from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app


@unittest.skipUnless(ADMIN_DSN and APP_DSN and HTTP_DEPENDENCIES_AVAILABLE, "PostgreSQL admin/application test URLs and HTTP dependencies are required")
class ExecutionHttpPostgresRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def __getattr__(self, name):
        fixture = self.__dict__.get("fixture")
        if fixture is not None:
            return getattr(fixture, name)
        raise AttributeError(name)

    async def asyncSetUp(self):
        from apps.api.tests.postgres.test_m1_03_schedule_runtime import SchedulePostgresRuntimeTests

        self.fixture = SchedulePostgresRuntimeTests("runTest")
        await self.fixture.asyncSetUp()
        created = await self._create_plan()
        scheduled = await self.schedule_service.calculate_plan_schedule(
            self.project_id,
            created.plan.plan.plan_id,
            created.plan.version.version,
            "2026-08-17",
            (),
            self.schedule_scope,
        )
        await self.schedule_service.approve_plan(
            self.project_id,
            created.plan.plan.plan_id,
            created.plan.version.version,
            scheduled.snapshot.snapshot_id,
            "HTTP runtime approval",
            self.schedule_scope,
        )
        self.plan_id = created.plan.plan.plan_id
        self.plan_version = created.plan.version.version
        self.task_id = created.plan.version.payload["tasks"][0]["taskId"]
        self.token = "runtime-http-token"
        self.app = create_app(static_root=None)
        self.app.state.authenticator = StaticBearerAuthenticator(
            self.token,
            ScopeContext(
                self.organization_id,
                self.workspace_id,
                self.client_id,
                self.actor_id,
            ),
        )
        self.app.state.schedule_service = self.schedule_service
        self.app.state.execution_service = ExecutionService(
            AsyncpgExecutionRepository(self.pool),
            clock=lambda: datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc),
        )
    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def request(self, method, path, payload=None):
        messages = []
        delivered = False
        target_path, _, query = path.partition("?")
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")

        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await self.app(
            {
                "type": "http",
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": target_path,
                "raw_path": target_path.encode(),
                "query_string": query.encode(),
                "headers": [
                    (b"authorization", f"Bearer {self.token}".encode()),
                    *([(b"content-type", b"application/json")] if payload is not None else []),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )
        start = next(item for item in messages if item["type"] == "http.response.start")
        headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in start["headers"]}
        response_body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
        return start["status"], headers, response_body

    async def test_real_fastapi_execution_read_update_and_csv(self):
        prefix = f"/v1/projects/{self.project_id}/wbs-plans/{self.plan_id}"
        status, _, body = await self.request("GET", f"{prefix}/execution?planVersion=1")
        self.assertEqual(status, 200)
        initial = json.loads(body)
        self.assertEqual(initial["planVersion"], 1)
        status, _, body = await self.request(
            "POST",
            f"{prefix}/execution-updates",
            {
                "expectedPlanVersion": 1,
                "expectedExecutionSequence": 0,
                "taskId": self.task_id,
                "status": "in_progress",
                "actualStart": "2026-08-17",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["update"]["status"], "in_progress")
        status, headers, body = await self.request("GET", f"{prefix}/exports/execution.csv?planVersion=1")
        self.assertEqual(status, 200)
        content_type = next(
            (value for key, value in headers.items() if key.lower() == "content-type"),
            "",
        )
        self.assertIn("text/csv", content_type)
        self.assertIn("taskId", body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
