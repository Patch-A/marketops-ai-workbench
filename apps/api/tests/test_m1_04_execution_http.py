from __future__ import annotations

import asyncio
import importlib.util
import json
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone

from apps.api.marketops_execution import (
    ExecutionFailure,
    ExecutionScopeContext,
    ExecutionService,
    ExecutionState,
    ExecutionUpdateResult,
)
from apps.api.marketops_import.service import ScopeContext
from apps.api.marketops_schedule import PlanApproval, PlanReadModel, WbsPlan, WbsPlanVersion


ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"
CLIENT_ID = "00000000-0000-4000-8000-000000000003"
PROJECT_ID = "00000000-0000-4000-8000-000000000004"
ACTOR_ID = "00000000-0000-4000-8000-000000000005"
PLAN_ID = "00000000-0000-4000-8000-000000000006"
PLAN_VERSION_ID = "00000000-0000-4000-8000-000000000007"
TASK_UUID = "00000000-0000-4000-8000-000000000008"
TASK_ID = f"candidate:{TASK_UUID}"
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

HTTP_DEPENDENCIES_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "multipart"))
if HTTP_DEPENDENCIES_AVAILABLE:
    from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app


@dataclass
class AsgiResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def json(self):
        return json.loads(self.body)


async def asgi_request(app, method, target, headers, body=b""):
    messages = []
    delivered = False
    path, _, query = target.partition("?")

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query.encode("ascii"),
            "root_path": "",
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in headers
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    start = next(item for item in messages if item["type"] == "http.response.start")
    return AsgiResponse(
        start["status"],
        {
            name.decode("latin-1"): value.decode("latin-1")
            for name, value in start["headers"]
        },
        b"".join(
            item.get("body", b"")
            for item in messages
            if item["type"] == "http.response.body"
        ),
    )


@unittest.skipUnless(HTTP_DEPENDENCIES_AVAILABLE, "FastAPI and python-multipart are required for execution HTTP tests")
class M104ExecutionHttpTests(unittest.TestCase):
    def setUp(self):
        scope = ScopeContext(ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID)
        self.schedule = FakeScheduleService(_plan_model(), _approval())
        self.execution = FakeExecutionService()
        self.app = create_app(
            authenticator=StaticBearerAuthenticator("test-token", scope),
            schedule_service=self.schedule,
            execution_service=self.execution,
            request_id_factory=lambda: "request-id",
        )

    def request(self, method, target, body=None, *, authenticated=True):
        headers = []
        if authenticated:
            headers.append(("Authorization", "Bearer test-token"))
        raw = b""
        if body is not None:
            headers.append(("Content-Type", "application/json"))
            raw = json.dumps(body).encode("utf-8")
        return asyncio.run(asgi_request(self.app, method, target, headers, raw))

    def test_reads_approved_plan_and_combines_latest_task_state(self):
        response = self.request(
            "GET",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/execution?planVersion=1",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            set(response.json()),
            {"projectId", "planId", "planVersion", "editable", "tasks"},
        )
        task = response.json()["tasks"][0]
        self.assertEqual(task["sequenceNo"], 2)
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(task["blockerReason"], "Awaiting venue sign-off")
        serialized = response.body.decode("utf-8")
        for forbidden in ("organizationId", "workspaceId", "clientId", "actorId", "planVersionId"):
            self.assertNotIn(forbidden, serialized)

    def test_update_uses_server_scope_and_resolved_plan_version_id(self):
        response = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/execution-updates",
            {
                "expectedPlanVersion": 1,
                "taskId": TASK_ID,
                "expectedExecutionSequence": 2,
                "status": "completed",
                "actualStart": "2026-08-15",
                "actualFinish": "2026-08-15",
                "note": "Approved locally",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["update"]["sequenceNo"], 3)
        self.assertFalse(response.json()["replayed"])
        self.assertIn("planVersion=1", response.headers["location"])
        request, scope = self.execution.update_call
        self.assertEqual(request.plan_version_id, PLAN_VERSION_ID)
        self.assertEqual(request.expected_sequence, 2)
        self.assertEqual(
            scope,
            ExecutionScopeContext(
                ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
            ),
        )

    def test_update_rejects_client_scope_and_maps_conflict(self):
        rejected = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/execution-updates",
            {
                "expectedPlanVersion": 1,
                "taskId": TASK_ID,
                "expectedExecutionSequence": 2,
                "status": "completed",
                "actorId": ACTOR_ID,
            },
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIsNone(self.execution.update_call)

        self.execution.failure = ExecutionFailure(
            "EXECUTION_CONFLICT", "customer text and database secret"
        )
        conflict = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/execution-updates",
            {
                "expectedPlanVersion": 1,
                "taskId": TASK_ID,
                "expectedExecutionSequence": 2,
                "status": "blocked",
                "blockerReason": "Awaiting venue sign-off",
            },
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "EXECUTION_CONFLICT")
        self.assertNotIn("secret", conflict.body.decode("utf-8").lower())

    def test_update_rejects_noncanonical_actual_date(self):
        response = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/execution-updates",
            {
                "expectedPlanVersion": 1,
                "taskId": TASK_ID,
                "expectedExecutionSequence": 2,
                "status": "in_progress",
                "actualStart": "20260815",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "INVALID_INPUT")
        self.assertIsNone(self.execution.update_call)

    def test_unapproved_plan_fails_closed_and_authentication_is_required(self):
        self.schedule.approval = None
        unapproved = self.request(
            "GET",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/execution?planVersion=1",
        )
        self.assertEqual(unapproved.status_code, 422)
        self.assertEqual(unapproved.json()["code"], "PLAN_NOT_APPROVED")

        unauthorized = self.request(
            "GET",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/execution?planVersion=1",
            authenticated=False,
        )
        self.assertEqual(unauthorized.status_code, 401)

    def test_historical_approved_version_is_read_only(self):
        self.schedule.model = PlanReadModel(
            self.schedule.model.plan, self.schedule.model.version, (1, 2)
        )
        response = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/execution-updates",
            {
                "expectedPlanVersion": 1,
                "taskId": TASK_ID,
                "expectedExecutionSequence": 2,
                "status": "blocked",
                "blockerReason": "Awaiting venue sign-off",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "EXECUTION_CONFLICT")
        self.assertIsNone(self.execution.update_call)

    def test_csv_and_xlsx_exports_are_authenticated_downloads(self):
        csv_response = self.request(
            "GET",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/exports/execution.csv?planVersion=1",
        )
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("attachment", csv_response.headers["content-disposition"])
        self.assertIn("Publish launch brief", csv_response.body.decode("utf-8"))

        xlsx_response = self.request(
            "GET",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/exports/execution.xlsx?planVersion=1",
        )
        self.assertEqual(xlsx_response.status_code, 200)
        self.assertTrue(xlsx_response.body.startswith(b"PK"))
        self.assertEqual(xlsx_response.headers["x-content-type-options"], "nosniff")

        missing_version = self.request(
            "GET",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/exports/execution.csv",
        )
        self.assertEqual(missing_version.status_code, 400)


class FakeScheduleService:
    def __init__(self, model, approval):
        self.model = model
        self.approval = approval

    async def read_plan(self, project_id, plan_id, scope, *, plan_version=None):
        return self.model

    async def read_plan_approval(self, project_id, plan_id, scope, *, plan_version=None):
        return self.approval


class FakeExecutionService:
    def __init__(self):
        self.update_call = None
        self.failure = None
        self.state = ExecutionState(
            TASK_ID,
            2,
            "blocked",
            "Awaiting venue sign-off",
            "2026-08-15",
            None,
            "Local review",
            ACTOR_ID,
            NOW,
        )

    async def read_states(self, project_id, plan_version_id, scope):
        return {TASK_ID: self.state}

    async def update_task(self, request, scope):
        self.update_call = (request, scope)
        if self.failure is not None:
            raise self.failure
        state = ExecutionState(
            request.task_id,
            request.expected_sequence + 1,
            request.status,
            request.blocker_reason,
            request.actual_start,
            request.actual_finish,
            request.note,
            scope.actor_id,
            NOW,
        )
        return ExecutionUpdateResult(state, False)

    @staticmethod
    def export_csv(rows):
        return ExecutionService.export_csv(rows)

    @staticmethod
    def export_xlsx(rows):
        return ExecutionService.export_xlsx(rows)


def _plan_model():
    task = {
        "taskId": TASK_ID,
        "candidateId": TASK_UUID,
        "kind": "deliverable",
        "classification": "fact",
        "sourceText": "Publish launch brief",
        "title": "Publish launch brief",
        "sourceCitation": {},
        "reviewStatus": "approve",
        "durationWorkdays": 1,
        "predecessors": [],
        "ownerRole": "Campaign owner",
        "plannedStart": "2026-08-15",
        "plannedFinish": "2026-08-15",
        "hardDeadline": None,
        "approvedBufferWorkdays": 0,
        "isLocked": False,
        "status": "not_started",
    }
    plan = WbsPlan(
        PLAN_ID,
        ORGANIZATION_ID,
        WORKSPACE_ID,
        CLIENT_ID,
        PROJECT_ID,
        "00000000-0000-4000-8000-000000000009",
        "00000000-0000-4000-8000-000000000010",
        "a" * 64,
        "00000000-0000-4000-8000-000000000011",
        "00000000-0000-4000-8000-000000000012",
        1,
        ACTOR_ID,
        NOW,
    )
    version = WbsPlanVersion(
        PLAN_VERSION_ID,
        PLAN_ID,
        1,
        "draft",
        {"tasks": [task], "controls": []},
        "b" * 64,
        ACTOR_ID,
        NOW,
    )
    return PlanReadModel(plan, version, (1,))


def _approval():
    return PlanApproval(
        "00000000-0000-4000-8000-000000000013",
        PLAN_ID,
        PLAN_VERSION_ID,
        1,
        "00000000-0000-4000-8000-000000000014",
        "b" * 64,
        "c" * 64,
        "Ready for execution",
        ACTOR_ID,
        NOW,
    )


if __name__ == "__main__":
    unittest.main()
