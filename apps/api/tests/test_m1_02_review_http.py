from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace


HTTP_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ("fastapi", "multipart")
)
if os.environ.get("MARKETOPS_REQUIRE_TEST_PREREQUISITES") == "1" and not HTTP_DEPENDENCIES_AVAILABLE:
    raise RuntimeError("HTTP test prerequisites are required")

if HTTP_DEPENDENCIES_AVAILABLE:
    from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation
    from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
    from apps.api.marketops_import.service import ScopeContext
    from apps.api.marketops_review import (
        ApprovedProposalSource,
        CreateReviewRunResult,
        PreparationFailure,
        ReviewCandidateView,
        ReviewDecision,
        ReviewFailure,
        ReviewReadModel,
        ReviewResult,
        ReviewRun,
        ReviewRunSummary,
        ReviewScopeContext,
        ReviewSnapshot,
        ReviewSnapshotItem,
    )


ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"
CLIENT_ID = "00000000-0000-4000-8000-000000000003"
PROJECT_ID = "00000000-0000-4000-8000-000000000004"
ACTOR_ID = "00000000-0000-4000-8000-000000000005"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000006"
VERSION_ID = "00000000-0000-4000-8000-000000000007"
RUN_ID = "00000000-0000-4000-8000-000000000008"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000009"
SNAPSHOT_ID = "00000000-0000-4000-8000-000000000010"
DECISION_ID = "00000000-0000-4000-8000-000000000011"
SOURCE_HASH = "a" * 64
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


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
    response_body = b"".join(
        item.get("body", b"")
        for item in messages
        if item["type"] == "http.response.body"
    )
    return AsgiResponse(
        start["status"],
        {
            name.decode("latin-1"): value.decode("latin-1")
            for name, value in start["headers"]
        },
        response_body,
    )


@unittest.skipUnless(
    HTTP_DEPENDENCIES_AVAILABLE,
    "FastAPI and python-multipart are required for review HTTP tests",
)
class ReviewHttpTests(unittest.TestCase):
    def setUp(self):
        self.scope = ScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
        )
        self.review_scope = ReviewScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
        )
        self.run = ReviewRun(
            RUN_ID,
            ORGANIZATION_ID,
            WORKSPACE_ID,
            CLIENT_ID,
            PROJECT_ID,
            ARTIFACT_ID,
            VERSION_ID,
            3,
            SOURCE_HASH,
            1,
            ACTOR_ID,
            NOW,
        )
        self.snapshot = ReviewSnapshot(
            SNAPSHOT_ID,
            RUN_ID,
            1,
            (ReviewSnapshotItem(CANDIDATE_ID, "pending"),),
            ACTOR_ID,
            NOW,
        )
        self.created = CreateReviewRunResult(self.run, self.snapshot, False)
        self.candidate = Candidate(
            CANDIDATE_ID,
            "deliverable",
            "Publish the reviewed brief.",
            "fact",
            0.9,
            SourceCitation(
                VERSION_ID,
                SOURCE_HASH,
                SourceLocation.from_mapping(
                    {"kind": "line_range", "startLine": 2, "endLine": 2}
                ),
                ("Deliverables",),
                "Publish the reviewed brief.",
            ),
        )
        self.review = FakeReviewService(self.created, self.candidate)
        self.preparation = FakePreparation(self.created)
        self.app = create_app(
            authenticator=StaticBearerAuthenticator("test-token", self.scope),
            review_preparation=self.preparation,
            review_service=self.review,
            request_id_factory=lambda: "request-id",
        )

    def request(self, method, target, body=None, headers=None, raw=None):
        request_headers = [("Authorization", "Bearer test-token")]
        payload = raw
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
        if payload is not None:
            request_headers.append(("Content-Type", "application/json"))
        request_headers.extend(headers or [])
        return asyncio.run(
            asgi_request(
                self.app,
                method,
                target,
                request_headers,
                payload or b"",
            )
        )

    def create_body(self):
        return {
            "expectedProposalVersionId": VERSION_ID,
            "expectedProposalSha256": SOURCE_HASH,
        }

    def test_create_uses_server_scope_and_persistent_replay_precedes_preparation(self):
        first = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/extraction-runs",
            self.create_body(),
            [("Idempotency-Key", "review-request-001")],
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.headers["cache-control"], "no-store")
        self.assertEqual(
            first.headers["location"],
            f"/v1/projects/{PROJECT_ID}/extraction-runs/{RUN_ID}",
        )
        self.assertFalse(first.json()["replayed"])
        self.assertEqual(self.preparation.scope, self.review_scope)
        self.assertEqual(self.preparation.request.idempotency_key, "review-request-001")

        self.review.replay = CreateReviewRunResult(self.run, self.snapshot, True)
        second = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/extraction-runs",
            self.create_body(),
            [("Idempotency-Key", "review-request-001")],
        )
        self.assertEqual(second.status_code, 201)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(self.preparation.calls, 1)

    def test_create_rejects_client_facts_duplicate_json_and_oversize(self):
        cases = (
            (json.dumps({**self.create_body(), "candidates": []}).encode(), 400),
            (b'{"expectedProposalVersionId":"' + VERSION_ID.encode() + b'","expectedProposalVersionId":"' + VERSION_ID.encode() + b'","expectedProposalSha256":"' + SOURCE_HASH.encode() + b'"}', 400),
            (json.dumps(self.create_body()).encode() + b" " * 17000, 413),
        )
        for raw, status in cases:
            with self.subTest(status=status):
                response = self.request(
                    "POST",
                    f"/v1/projects/{PROJECT_ID}/extraction-runs",
                    headers=[("Idempotency-Key", "review-request-002")],
                    raw=raw,
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.preparation.calls, 0)

    def test_list_detail_and_decision_return_complete_no_store_models(self):
        listed = self.request(
            "GET", f"/v1/projects/{PROJECT_ID}/extraction-runs?limit=10"
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["runs"][0]["runId"], RUN_ID)

        detail = self.request(
            "GET",
            f"/v1/projects/{PROJECT_ID}/extraction-runs/{RUN_ID}?reviewVersion=1",
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["selectedReviewVersion"], 1)
        self.assertEqual(detail.json()["candidates"][0]["sourceCitation"]["quote"], "Publish the reviewed brief.")

        decided = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/extraction-runs/{RUN_ID}/decisions",
            {
                "expectedReviewVersion": 1,
                "candidateId": CANDIDATE_ID,
                "action": "approve",
                "reason": "Verified",
            },
        )
        self.assertEqual(decided.status_code, 201)
        self.assertEqual(decided.json()["reviewVersion"], 2)
        self.assertIn("reviewVersion=2", decided.headers["location"])
        self.assertTrue(
            all(
                response.headers["cache-control"] == "no-store"
                for response in (listed, detail, decided)
            )
        )

    def test_stable_failures_are_sanitized_and_cancellation_propagates(self):
        self.review.failure = ReviewFailure("REVIEW_CONFLICT", "secret reason")
        response = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/extraction-runs/{RUN_ID}/decisions",
            {
                "expectedReviewVersion": 1,
                "candidateId": CANDIDATE_ID,
                "action": "approve",
                "reason": "customer secret",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["retryable"])
        self.assertNotIn("secret", response.body.decode().lower())

        self.review.failure = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.request(
                "GET", f"/v1/projects/{PROJECT_ID}/extraction-runs"
            )

    def test_review_routes_return_declared_403_for_invalid_server_scope(self):
        invalid_scope = ScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, "not-a-canonical-uuid"
        )
        app = create_app(
            authenticator=StaticBearerAuthenticator("invalid-scope-token", invalid_scope),
            review_preparation=self.preparation,
            review_service=self.review,
            request_id_factory=lambda: "request-id",
        )
        cases = (
            (
                "POST",
                f"/v1/projects/{PROJECT_ID}/extraction-runs",
                [
                    ("Authorization", "Bearer invalid-scope-token"),
                    ("Content-Type", "application/json"),
                    ("Idempotency-Key", "invalid-scope-review"),
                ],
                json.dumps(self.create_body()).encode(),
            ),
            (
                "GET",
                f"/v1/projects/{PROJECT_ID}/extraction-runs",
                [("Authorization", "Bearer invalid-scope-token")],
                b"",
            ),
            (
                "GET",
                f"/v1/projects/{PROJECT_ID}/extraction-runs/{RUN_ID}",
                [("Authorization", "Bearer invalid-scope-token")],
                b"",
            ),
            (
                "POST",
                f"/v1/projects/{PROJECT_ID}/extraction-runs/{RUN_ID}/decisions",
                [
                    ("Authorization", "Bearer invalid-scope-token"),
                    ("Content-Type", "application/json"),
                ],
                json.dumps(
                    {
                        "expectedReviewVersion": 1,
                        "candidateId": CANDIDATE_ID,
                        "action": "approve",
                        "reason": "Must not reach the service",
                    }
                ).encode(),
            ),
        )
        for method, target, headers, body in cases:
            with self.subTest(method=method, target=target):
                response = asyncio.run(
                    asgi_request(app, method, target, headers, body)
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "AUTHORIZATION_REQUIRED")
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_decision_server_allocation_failure_is_declared_sanitized_500(self):
        self.review.failure = ReviewFailure(
            "INVALID_SERVER_CLOCK", "customer text and database secret"
        )
        response = self.request(
            "POST",
            f"/v1/projects/{PROJECT_ID}/extraction-runs/{RUN_ID}/decisions",
            {
                "expectedReviewVersion": 1,
                "candidateId": CANDIDATE_ID,
                "action": "approve",
                "reason": "customer decision text",
            },
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "INVALID_SERVER_CLOCK")
        self.assertFalse(response.json()["retryable"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("customer", response.body.decode().lower())
        self.assertNotIn("secret", response.body.decode().lower())

    def test_query_integers_use_canonical_decimal_form(self):
        for target in (
            f"/v1/projects/{PROJECT_ID}/extraction-runs?limit=01",
            f"/v1/projects/{PROJECT_ID}/extraction-runs/{RUN_ID}?reviewVersion=01",
        ):
            with self.subTest(target=target):
                response = self.request("GET", target)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_openapi_contains_closed_review_contract(self):
        paths = self.app.openapi()["paths"]
        self.assertIn("/v1/projects/{projectId}/extraction-runs", paths)
        self.assertIn("/v1/projects/{projectId}/extraction-runs/{runId}", paths)
        self.assertIn(
            "/v1/projects/{projectId}/extraction-runs/{runId}/decisions",
            paths,
        )
        create = paths["/v1/projects/{projectId}/extraction-runs"]["post"]
        schema = create["requestBody"]["content"]["application/json"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]),
            {"expectedProposalVersionId", "expectedProposalSha256"},
        )


if HTTP_DEPENDENCIES_AVAILABLE:
    class FakePreparation:
        def __init__(self, created):
            self.created = created
            self.calls = 0
            self.request = None
            self.scope = None
            self.failure = None

        async def prepare_and_create_run(self, request, scope):
            self.calls += 1
            self.request = request
            self.scope = scope
            if self.failure is not None:
                raise self.failure
            source = ApprovedProposalSource(
                ORGANIZATION_ID,
                WORKSPACE_ID,
                CLIENT_ID,
                PROJECT_ID,
                ARTIFACT_ID,
                VERSION_ID,
                3,
                "proposal.md",
                "text/markdown",
                10,
                "opaque",
                SOURCE_HASH,
            )
            return SimpleNamespace(source=source, review_result=self.created)


    class FakeReviewService:
        def __init__(self, created, candidate):
            self.created = created
            self.candidate = candidate
            self.replay = None
            self.failure = None

        def maybe_fail(self):
            if self.failure is not None:
                raise self.failure

        async def replay_run_request(self, *args):
            self.maybe_fail()
            return self.replay

        async def list_runs(self, project_id, scope, *, limit):
            self.maybe_fail()
            return (ReviewRunSummary(self.created.run, 1),)

        async def read_review(self, project_id, run_id, scope, *, review_version):
            self.maybe_fail()
            return ReviewReadModel(
                self.created.run,
                self.created.snapshot,
                (ReviewCandidateView(1, self.candidate, "pending", None),),
                (1,),
                None,
            )

        async def review_candidate(self, project_id, run_id, request, scope):
            self.maybe_fail()
            decision = ReviewDecision(
                DECISION_ID,
                RUN_ID,
                2,
                CANDIDATE_ID,
                request.action,
                request.reason,
                request.comment,
                request.replacement_text,
                ACTOR_ID,
                NOW,
            )
            return ReviewResult(
                RUN_ID,
                ReviewSnapshot(
                    SNAPSHOT_ID,
                    RUN_ID,
                    2,
                    (ReviewSnapshotItem(CANDIDATE_ID, request.action),),
                    ACTOR_ID,
                    NOW,
                ),
                decision,
            )


if __name__ == "__main__":
    unittest.main()
