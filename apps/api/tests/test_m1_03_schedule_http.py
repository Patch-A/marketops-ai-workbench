from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone


HTTP_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ("fastapi", "multipart")
)
if os.environ.get("MARKETOPS_REQUIRE_TEST_PREREQUISITES") == "1" and not HTTP_DEPENDENCIES_AVAILABLE:
    raise RuntimeError("HTTP test prerequisites are required")

if HTTP_DEPENDENCIES_AVAILABLE:
    from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
    from apps.api.marketops_import.service import ScopeContext
    from apps.api.marketops_schedule import (
        CreateApprovalResult,
        CreatePlanResult,
        CreateScheduleResult,
        PlanApproval,
        PlanReadModel,
        ScheduleScopeContext,
        ScheduleServiceFailure,
        ScheduleSnapshot,
        WbsPlan,
        WbsPlanVersion,
    )


ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"
CLIENT_ID = "00000000-0000-4000-8000-000000000003"
PROJECT_ID = "00000000-0000-4000-8000-000000000004"
ACTOR_ID = "00000000-0000-4000-8000-000000000005"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000006"
PROPOSAL_VERSION_ID = "00000000-0000-4000-8000-000000000007"
RUN_ID = "00000000-0000-4000-8000-000000000008"
REVIEW_SNAPSHOT_ID = "00000000-0000-4000-8000-000000000009"
PLAN_ID = "00000000-0000-4000-8000-000000000010"
PLAN_VERSION_ID = "00000000-0000-4000-8000-000000000011"
SCHEDULE_ID = "00000000-0000-4000-8000-000000000012"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000013"
APPROVAL_ID = "00000000-0000-4000-8000-000000000014"
TASK_ID = f"candidate:{CANDIDATE_ID}"
SOURCE_HASH = "a" * 64
PLAN_HASH = "b" * 64
SCHEDULE_HASH = "c" * 64
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


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


@unittest.skipUnless(
    HTTP_DEPENDENCIES_AVAILABLE,
    "FastAPI and python-multipart are required for schedule HTTP tests",
)
class M103ScheduleHttpTests(unittest.TestCase):
    def setUp(self):
        self.scope = ScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
        )
        self.model = self._model()
        self.service = FakeScheduleService(
            self.model, self._model(version=2), self._schedule(), self._approval()
        )
        self.app = create_app(
            authenticator=StaticBearerAuthenticator("test-token", self.scope),
            schedule_service=self.service,
            request_id_factory=lambda: "request-id",
        )

    def _citation(self):
        return {
            "sourceVersionId": PROPOSAL_VERSION_ID,
            "sourceSha256": SOURCE_HASH,
            "location": {"kind": "line_range", "startLine": 1, "endLine": 1},
            "sectionPath": ["Plan"],
            "quote": "Publish the launch brief.",
        }

    def _model(self, version=1):
        task = {
            "taskId": TASK_ID,
            "candidateId": CANDIDATE_ID,
            "kind": "deliverable",
            "classification": "fact",
            "sourceText": "Publish the launch brief.",
            "text": "Publish the launch brief.",
            "title": "Publish the launch brief.",
            "sourceCitation": self._citation(),
            "reviewStatus": "approve",
            "reviewDecision": {},
            "durationWorkdays": version,
            "predecessors": [],
            "ownerRole": "Campaign owner",
            "plannedStart": None,
            "plannedFinish": None,
            "hardDeadline": None,
            "approvedBufferWorkdays": 0,
            "isLocked": False,
            "status": "not_started",
        }
        payload = {
            "schemaVersion": 1,
            "planVersion": version,
            "status": "draft",
            "projectId": PROJECT_ID,
            "proposal": {"versionId": PROPOSAL_VERSION_ID, "sha256": SOURCE_HASH},
            "sourceReviewRunId": RUN_ID,
            "sourceReviewSnapshotId": REVIEW_SNAPSHOT_ID,
            "sourceReviewVersion": 3,
            "tasks": [task],
            "controls": [],
        }
        plan = WbsPlan(
            PLAN_ID,
            ORGANIZATION_ID,
            WORKSPACE_ID,
            CLIENT_ID,
            PROJECT_ID,
            ARTIFACT_ID,
            PROPOSAL_VERSION_ID,
            SOURCE_HASH,
            RUN_ID,
            REVIEW_SNAPSHOT_ID,
            3,
            ACTOR_ID,
            NOW,
        )
        plan_version = WbsPlanVersion(
            PLAN_VERSION_ID, PLAN_ID, version, "draft", payload, PLAN_HASH, ACTOR_ID, NOW
        )
        return PlanReadModel(plan, plan_version, tuple(range(1, version + 1)))

    def _schedule(self):
        payload = {
            "topologicalOrder": [TASK_ID],
            "tasks": [
                {
                    "taskId": TASK_ID,
                    "candidateId": CANDIDATE_ID,
                    "title": "Publish the launch brief.",
                    "sourceCitation": self._citation(),
                    "predecessors": [],
                    "durationWorkdays": 1,
                    "approvedBufferWorkdays": 0,
                    "isLocked": False,
                    "calculatedStart": "2026-08-17",
                    "calculatedFinish": "2026-08-17",
                    "requiredStart": "2026-08-17",
                    "hardDeadline": None,
                }
            ],
            "conflicts": [],
            "deadlineMisses": [],
            "sourceDateDrift": [],
        }
        return ScheduleSnapshot(
            SCHEDULE_ID,
            PLAN_ID,
            PLAN_VERSION_ID,
            1,
            "2026-08-17",
            ("2026-08-20",),
            "ready",
            PLAN_HASH,
            SCHEDULE_HASH,
            payload,
            ACTOR_ID,
            NOW,
        )

    def _approval(self):
        return PlanApproval(
            APPROVAL_ID,
            PLAN_ID,
            PLAN_VERSION_ID,
            1,
            SCHEDULE_ID,
            PLAN_HASH,
            SCHEDULE_HASH,
            "Ready for execution",
            ACTOR_ID,
            NOW,
        )

    def request(self, method, target, body=None, *, raw=None, headers=None):
        request_headers = [("Authorization", "Bearer test-token")]
        payload = raw
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
        if payload is not None:
            request_headers.append(("Content-Type", "application/json"))
        request_headers.extend(headers or [])
        return asyncio.run(
            asgi_request(
                self.app, method, target, request_headers, payload or b""
            )
        )

    def test_create_read_revise_and_schedule_use_server_scope(self):
        created = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans",
            {"reviewRunId": RUN_ID, "reviewVersion": 3},
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["cache-control"], "no-store")
        self.assertEqual(created.json()["plan"]["planId"], PLAN_ID)
        self.assertFalse(created.json()["replayed"])
        self.assertIn("planVersion=1", created.headers["location"])

        read = self.request(
            "GET", f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}?planVersion=1"
        )
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["tasks"][0]["taskId"], TASK_ID)

        revised = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/revisions",
            {
                "expectedPlanVersion": 1,
                "taskUpdates": [
                    {"taskId": TASK_ID, "changes": {"durationWorkdays": 2}}
                ],
            },
        )
        self.assertEqual(revised.status_code, 201)
        self.assertEqual(revised.json()["selectedPlanVersion"], 2)

        scheduled = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/schedule-snapshots",
            {
                "expectedPlanVersion": 1,
                "projectStart": "2026-08-17",
                "holidays": ["2026-08-20"],
            },
        )
        self.assertEqual(scheduled.status_code, 201)
        self.assertEqual(scheduled.json()["snapshot"]["scheduleDigest"], SCHEDULE_HASH)
        self.assertTrue(scheduled.json()["replayed"])
        self.assertEqual(
            self.service.scope,
            ScheduleScopeContext(
                ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
            ),
        )

    def test_approval_create_and_read_exclude_actor_and_scope(self):
        created = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/approvals",
            {
                "expectedPlanVersion": 1,
                "scheduleSnapshotId": SCHEDULE_ID,
                "reason": "Ready for execution",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["cache-control"], "no-store")
        self.assertIn("planVersion=1", created.headers["location"])
        self.assertTrue(created.json()["replayed"])
        approval = created.json()["approval"]
        self.assertEqual(approval["approvalId"], APPROVAL_ID)
        self.assertEqual(approval["scheduleSnapshotId"], SCHEDULE_ID)
        serialized = json.dumps(approval)
        for forbidden in (
            "organizationId",
            "workspaceId",
            "clientId",
            "actorId",
            "approvedBy",
            "credential",
        ):
            self.assertNotIn(forbidden, serialized)

        read = self.request(
            "GET",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/approvals?planVersion=1",
        )
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["approval"]["approvalId"], APPROVAL_ID)

    def test_requests_fail_closed_for_client_facts_duplicates_and_size(self):
        cases = (
            (
                f"/v1/projects/{PROJECT_ID}/wbs-plans",
                {"reviewRunId": RUN_ID, "reviewVersion": 3, "actorId": ACTOR_ID},
                None,
                400,
            ),
            (
                f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/revisions",
                {"expectedPlanVersion": 1, "taskUpdates": []},
                None,
                400,
            ),
            (
                f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/revisions",
                {
                    "expectedPlanVersion": 1,
                    "taskUpdates": [
                        {"taskId": TASK_ID, "changes": {"title": "x"}}
                    ]
                    * 1001,
                },
                None,
                400,
            ),
            (
                f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/schedule-snapshots",
                {
                    "expectedPlanVersion": 1,
                    "projectStart": "2026-08-17",
                    "holidays": ["2026-08-20", "2026-08-20"],
                },
                None,
                400,
            ),
            (
                f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/approvals",
                {
                    "expectedPlanVersion": 1,
                    "scheduleSnapshotId": SCHEDULE_ID,
                    "reason": "Ready",
                    "planDigest": PLAN_HASH,
                },
                None,
                400,
            ),
            (
                f"/v1/projects/{PROJECT_ID}/wbs-plans",
                None,
                b'{"reviewRunId":"' + RUN_ID.encode() + b'","reviewRunId":"' + RUN_ID.encode() + b'","reviewVersion":3}',
                400,
            ),
            (
                f"/v1/projects/{PROJECT_ID}/wbs-plans",
                None,
                b'{"reviewRunId":"' + RUN_ID.encode() + b'","reviewVersion":NaN}',
                400,
            ),
            (
                f"/v1/projects/{PROJECT_ID}/wbs-plans",
                None,
                json.dumps({"reviewRunId": RUN_ID, "reviewVersion": 3}).encode() + b" " * (129 * 1024),
                413,
            ),
        )
        for target, body, raw, expected in cases:
            with self.subTest(target=target, expected=expected):
                response = self.request("POST", target, body, raw=raw)
                self.assertEqual(response.status_code, expected)
                self.assertEqual(response.headers["cache-control"], "no-store")
                if expected == 413:
                    self.assertNotIn("uploaded file", response.json()["message"])
        self.assertEqual(self.service.calls, [])

    def test_plan_query_requires_canonical_positive_integer(self):
        for query in ("planVersion=01", "planVersion=0", "planVersion=1&planVersion=2", "other=1"):
            response = self.request(
                "GET", f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}?{query}"
            )
            self.assertEqual(response.status_code, 400)

    def test_service_failures_are_mapped_and_sanitized(self):
        self.service.failure = ScheduleServiceFailure(
            "PLAN_CONFLICT", "customer text and database secret"
        )
        conflict = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/revisions",
            {
                "expectedPlanVersion": 1,
                "taskUpdates": [{"taskId": TASK_ID, "changes": {"title": "Updated"}}],
            },
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "PLAN_CONFLICT")
        self.assertNotIn("secret", conflict.body.decode().lower())

        self.service.failure = ScheduleServiceFailure(
            "invalid_duration", "customer duration"
        )
        invalid = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/schedule-snapshots",
            {"expectedPlanVersion": 1, "projectStart": "2026-08-17", "holidays": []},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["code"], "WBS_VALIDATION_FAILED")
        self.assertNotIn("customer", invalid.body.decode().lower())

        self.service.failure = ScheduleServiceFailure(
            "SCHEDULE_NOT_READY", "customer conflict details"
        )
        blocked = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}/approvals",
            {
                "expectedPlanVersion": 1,
                "scheduleSnapshotId": SCHEDULE_ID,
                "reason": "Ready",
            },
        )
        self.assertEqual(blocked.status_code, 422)
        self.assertEqual(blocked.json()["code"], "SCHEDULE_NOT_READY")
        self.assertNotIn("customer", blocked.body.decode().lower())

        unavailable_app = create_app(
            authenticator=StaticBearerAuthenticator("test-token", self.scope),
            request_id_factory=lambda: "request-id",
        )
        unavailable = asyncio.run(
            asgi_request(
                unavailable_app,
                "POST",
                f"/v1/projects/{PROJECT_ID}/wbs-plans",
                [
                    ("Authorization", "Bearer test-token"),
                    ("Content-Type", "application/json"),
                ],
                json.dumps({"reviewRunId": RUN_ID, "reviewVersion": 3}).encode(),
            )
        )
        self.assertEqual(unavailable.status_code, 503)
        self.assertNotIn("review repository", unavailable.json()["message"])

    def test_cancellation_propagates(self):
        self.service.failure = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.request(
                "GET", f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}"
            )

    def test_schedule_routes_require_authentication(self):
        response = asyncio.run(
            asgi_request(
                self.app,
                "GET",
                f"/v1/projects/{PROJECT_ID}/wbs-plans/{PLAN_ID}",
                [],
            )
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_openapi_contains_closed_schedule_contract(self):
        document = self.app.openapi()
        paths = document["paths"]
        expected = {
            "/v1/projects/{projectId}/wbs-plans",
            "/v1/projects/{projectId}/wbs-plans/{planId}",
            "/v1/projects/{projectId}/wbs-plans/{planId}/revisions",
            "/v1/projects/{projectId}/wbs-plans/{planId}/schedule-snapshots",
            "/v1/projects/{projectId}/wbs-plans/{planId}/approvals",
        }
        self.assertTrue(expected.issubset(paths))
        create_schema = paths["/v1/projects/{projectId}/wbs-plans"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertFalse(create_schema["additionalProperties"])
        self.assertEqual(
            set(create_schema["properties"]), {"reviewRunId", "reviewVersion"}
        )
        revision = document["components"]["schemas"]["WbsRevisionRequest"]
        self.assertFalse(revision["additionalProperties"])
        request_contracts = json.dumps(
            [
                create_schema,
                revision,
                document["components"]["schemas"]["WbsTaskChanges"],
                document["components"]["schemas"]["ScheduleRequest"],
                document["components"]["schemas"]["PlanApprovalRequest"],
            ]
        )
        for forbidden in ("organizationId", "workspaceId", "clientId", "actorId"):
            self.assertNotIn(forbidden, request_contracts)


if HTTP_DEPENDENCIES_AVAILABLE:
    class FakeScheduleService:
        def __init__(self, model, revised, snapshot, approval):
            self.model = model
            self.revised = revised
            self.snapshot = snapshot
            self.approval = approval
            self.calls = []
            self.scope = None
            self.failure = None

        def maybe_fail(self):
            if self.failure is not None:
                raise self.failure

        async def create_plan(self, request, scope):
            self.maybe_fail()
            self.scope = scope
            self.calls.append(("create", request))
            return CreatePlanResult(self.model, False)

        async def read_plan(self, project_id, plan_id, scope, *, plan_version=None):
            self.maybe_fail()
            self.scope = scope
            self.calls.append(("read", project_id, plan_id, plan_version))
            return self.model

        async def revise_plan(self, project_id, plan_id, expected, updates, scope):
            self.maybe_fail()
            self.scope = scope
            self.calls.append(("revise", project_id, plan_id, expected, updates))
            return self.revised

        async def calculate_plan_schedule(
            self, project_id, plan_id, expected, project_start, holidays, scope
        ):
            self.maybe_fail()
            self.scope = scope
            self.calls.append(
                ("schedule", project_id, plan_id, expected, project_start, holidays)
            )
            return CreateScheduleResult(self.snapshot, True)

        async def approve_plan(
            self, project_id, plan_id, expected, snapshot_id, reason, scope
        ):
            self.maybe_fail()
            self.scope = scope
            self.calls.append(
                ("approve", project_id, plan_id, expected, snapshot_id, reason)
            )
            return CreateApprovalResult(self.approval, True)

        async def read_plan_approval(
            self, project_id, plan_id, scope, *, plan_version=None
        ):
            self.maybe_fail()
            self.scope = scope
            self.calls.append(("read-approval", project_id, plan_id, plan_version))
            return self.approval


if __name__ == "__main__":
    unittest.main()
