from __future__ import annotations

import json
import importlib.util
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

HTTP_DEPENDENCIES_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "multipart"))
if HTTP_DEPENDENCIES_AVAILABLE:
    from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
from apps.api.marketops_import.service import ScopeContext
from apps.api.marketops_learning import KnowledgeEvidence, KnowledgeItem, LearningFailure
from apps.api.marketops_learning.postgres import (
    CapsuleReadModel,
    CapsuleSummary,
    KnowledgeReadModel,
    KnowledgeVersionView,
    PersistCapsuleResult,
)
if HTTP_DEPENDENCIES_AVAILABLE:
    from apps.api.tests.test_m1_02_review_http import asgi_request


IDS = {
    "organization": "00000000-0000-4000-8000-000000000301",
    "workspace": "00000000-0000-4000-8000-000000000302",
    "client": "00000000-0000-4000-8000-000000000303",
    "actor": "00000000-0000-4000-8000-000000000304",
    "project": "00000000-0000-4000-8000-000000000305",
    "capsule": "00000000-0000-4000-8000-000000000306",
    "knowledge": "00000000-0000-4000-8000-000000000307",
    "source": "00000000-0000-4000-8000-000000000308",
    "artifact": "00000000-0000-4000-8000-000000000309",
    "proposal_version": "00000000-0000-4000-8000-000000000310",
    "plan": "00000000-0000-4000-8000-000000000311",
    "plan_version": "00000000-0000-4000-8000-000000000312",
    "approval": "00000000-0000-4000-8000-000000000313",
    "schedule": "00000000-0000-4000-8000-000000000314",
}
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _knowledge() -> KnowledgeItem:
    return KnowledgeItem(
        IDS["knowledge"], 1, "observed_outcome", "candidate", "outcome_observation",
        "Outcome observation qualified leads: 42.", 1.0,
        (
            KnowledgeEvidence("artifact_version", IDS["source"], IDS["project"], "a" * 64),
            KnowledgeEvidence("capsule", IDS["capsule"], IDS["project"], "b" * 64),
        ),
        "c" * 64, "project", IDS["project"],
    )


def _capsule() -> CapsuleReadModel:
    item = _knowledge()
    return CapsuleReadModel(
        IDS["capsule"], 1, "ready", "d" * 64,
        {
            "brief": {
                "artifactId": IDS["artifact"], "versionId": IDS["proposal_version"],
                "version": 1, "sha256": "e" * 64,
            },
            "plan": {
                "planId": IDS["plan"], "versionId": IDS["plan_version"],
                "version": 2, "sha256": "f" * 64,
                "approval": {"id": IDS["approval"]},
                "schedule": {"id": IDS["schedule"], "scheduleSha256": "a" * 64},
            },
        },
        1, 0, 1, NOW, (item,)
    )


class FakeLearningService:
    def __init__(self):
        self.calls = []
        self.failure = None

    async def finalize_capsule(self, project_id, outcomes, retrospectives, scope):
        self.calls.append(("finalize", project_id, outcomes, retrospectives, scope))
        if self.failure:
            raise self.failure
        return PersistCapsuleResult(_capsule(), False)

    async def list_capsules(self, project_id, scope):
        self.calls.append(("list_capsules", project_id, scope))
        capsule = _capsule()
        return (
            CapsuleSummary(
                capsule.capsule_id, capsule.capsule_version, capsule.status,
                capsule.capsule_digest, capsule.outcome_count,
                capsule.retrospective_count, capsule.knowledge_count, capsule.created_at,
            ),
        )

    async def read_capsule(self, project_id, capsule_id, scope):
        self.calls.append(("read_capsule", project_id, capsule_id, scope))
        if self.failure:
            raise self.failure
        return _capsule()

    async def list_knowledge(self, project_id, scope):
        self.calls.append(("list_knowledge", project_id, scope))
        return (_knowledge(),)

    async def read_knowledge(self, project_id, knowledge_id, scope):
        self.calls.append(("read_knowledge", project_id, knowledge_id, scope))
        if self.failure:
            raise self.failure
        item = _knowledge()
        return KnowledgeReadModel(
            item,
            (KnowledgeVersionView(1, item.content, item.content_sha256, NOW),),
            item.evidence,
        )


class FakeKnowledgeApprovalService:
    def __init__(self):
        self.calls = []
        decision = SimpleNamespace(
            version=1, action="approve", status="approved", effective_scope="project",
            content="Approved rehearsal control.", content_sha256="a" * 64, reason=None,
        )
        candidate = SimpleNamespace(knowledge_id=IDS["knowledge"], source_project_id=IDS["project"])
        self.history = SimpleNamespace(
            promotion_id=IDS["capsule"],
            state=SimpleNamespace(candidate=candidate, version=1, current=decision, decisions=(decision,)),
        )
        authorization = SimpleNamespace(
            source_project_id=IDS["project"], knowledge_id=IDS["knowledge"],
            promotion_version=1, effective_scope="client", content="Approved rehearsal control.",
            content_sha256="a" * 64, reason="Apply it.",
        )
        self.citation = SimpleNamespace(citation_id=IDS["source"], authorization=authorization, created_at=NOW)

    async def decide(self, scope, project_id, knowledge_id, request):
        self.calls.append(("decide", scope, project_id, knowledge_id, request))
        return SimpleNamespace(history=self.history, replayed=False)

    async def read_history(self, scope, project_id, knowledge_id):
        self.calls.append(("history", scope, project_id, knowledge_id))
        return self.history

    async def cite_approved_knowledge(self, scope, **kwargs):
        self.calls.append(("cite", scope, kwargs))
        return SimpleNamespace(citation=self.citation, replayed=False)

    async def list_effective_citations(self, scope, project_id):
        self.calls.append(("effective", scope, project_id))
        return (self.citation,)


@unittest.skipUnless(HTTP_DEPENDENCIES_AVAILABLE, "FastAPI and python-multipart are required for learning HTTP tests")
@unittest.skipUnless(HTTP_DEPENDENCIES_AVAILABLE, "FastAPI and python-multipart are required for learning HTTP tests")
class LearningHttpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ScopeContext(
            IDS["organization"], IDS["workspace"], IDS["client"], IDS["actor"]
        )
        self.service = FakeLearningService()
        self.approval = FakeKnowledgeApprovalService()
        self.app = create_app(
            authenticator=StaticBearerAuthenticator("learning-token", self.scope),
            learning_service=self.service,
            knowledge_approval_service=self.approval,
            request_id_factory=lambda: "learning-request",
        )

    @staticmethod
    def headers():
        return [
            ("Authorization", "Bearer learning-token"),
            ("Content-Type", "application/json"),
        ]

    async def request(self, method, target, body=None, *, authenticated=True):
        headers = self.headers() if authenticated else [("Content-Type", "application/json")]
        return await asgi_request(
            self.app, method, target, headers,
            b"" if body is None else json.dumps(body).encode("utf-8"),
        )

    async def test_finalization_uses_only_feedback_and_server_scope(self):
        response = await self.request(
            "POST", f"/v1/projects/{IDS['project']}/capsules",
            {
                "outcomes": [{
                    "metric": "qualified leads", "actualValue": "42", "unit": "count",
                    "source": {"sourceType": "artifact_version", "sourceId": IDS["source"]},
                }],
                "retrospectives": [],
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["location"], f"/v1/projects/{IDS['project']}/capsules/{IDS['capsule']}")
        self.assertFalse(response.json()["replayed"])
        _, project_id, outcomes, retrospectives, scope = self.service.calls[0]
        self.assertEqual(project_id, IDS["project"])
        self.assertEqual(retrospectives, ())
        self.assertEqual(outcomes[0].source.project_id, IDS["project"])
        self.assertEqual(outcomes[0].source.binding_sha256, "0" * 64)
        self.assertEqual(scope.organization_id, IDS["organization"])
        binding = response.json()["capsule"]["binding"]
        self.assertEqual(binding["proposal"]["versionId"], IDS["proposal_version"])
        self.assertEqual(binding["plan"]["digest"], "f" * 64)
        self.assertEqual(binding["approval"]["approvalId"], IDS["approval"])
        self.assertEqual(binding["schedule"]["snapshotId"], IDS["schedule"])
        self.assertNotIn("organizationId", response.body.decode("utf-8"))

    async def test_closed_body_and_authentication_fail_before_service(self):
        forbidden = await self.request(
            "POST", f"/v1/projects/{IDS['project']}/capsules",
            {
                "outcomes": [], "retrospectives": [], "candidateScope": "workspace",
            },
        )
        unauthenticated = await self.request(
            "POST", f"/v1/projects/{IDS['project']}/capsules",
            {"outcomes": [], "retrospectives": []}, authenticated=False,
        )
        self.assertEqual(forbidden.status_code, 400)
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(self.service.calls, [])

    async def test_read_only_history_and_sanitized_failure_contract(self):
        listed = await self.request("GET", f"/v1/projects/{IDS['project']}/capsules")
        capsule = await self.request(
            "GET", f"/v1/projects/{IDS['project']}/capsules/{IDS['capsule']}"
        )
        knowledge = await self.request("GET", f"/v1/projects/{IDS['project']}/knowledge")
        detail = await self.request(
            "GET", f"/v1/projects/{IDS['project']}/knowledge/{IDS['knowledge']}"
        )
        self.assertEqual(listed.json()["capsules"][0]["capsuleVersion"], 1)
        self.assertEqual(capsule.json()["capsule"]["knowledge"][0]["status"], "candidate")
        self.assertEqual(capsule.json()["capsule"]["binding"]["proposal"]["sha256"], "e" * 64)
        self.assertEqual(knowledge.json()["knowledge"][0]["scope"], "project")
        self.assertEqual(detail.json()["versions"][0]["version"], 1)
        self.assertEqual(detail.json()["evidence"][0]["sourceId"], IDS["source"])

        self.service.failure = LearningFailure("LEARNING_READ_FAILED", "C:/private/customer")
        failed = await self.request(
            "GET", f"/v1/projects/{IDS['project']}/knowledge/{IDS['knowledge']}"
        )
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["code"], "LEARNING_READ_FAILED")
        self.assertNotIn("customer", failed.body.decode("utf-8").lower())

    async def test_approval_and_citation_routes_are_authenticated_and_body_closed(self):
        decision = await self.request(
            "POST", f"/v1/projects/{IDS['project']}/knowledge/{IDS['knowledge']}/approval-decisions",
            {"action": "approve", "expectedVersion": 0, "effectiveScope": "project"},
        )
        history = await self.request(
            "GET", f"/v1/projects/{IDS['project']}/knowledge/{IDS['knowledge']}/approval-decisions",
        )
        citation = await self.request(
            "POST", f"/v1/projects/{IDS['capsule']}/knowledge-sources/{IDS['project']}/items/{IDS['knowledge']}/citations",
            {"reason": "Apply it."},
        )
        effective = await self.request(
            "GET", f"/v1/projects/{IDS['capsule']}/knowledge-citations"
        )
        rejected = await self.request(
            "POST", f"/v1/projects/{IDS['project']}/knowledge/{IDS['knowledge']}/approval-decisions",
            {"action": "approve", "expectedVersion": 0, "effectiveScope": "project", "clientId": IDS["client"]},
        )
        self.assertEqual(decision.status_code, 201)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(citation.status_code, 201)
        self.assertEqual(effective.status_code, 200)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.approval.calls[0][3], IDS["knowledge"])
        self.assertEqual(self.approval.calls[0][4].effective_scope, "project")
        self.assertEqual(self.approval.calls[2][2]["source_project_id"], IDS["project"])
        self.assertEqual(self.approval.calls[3][0], "effective")
        self.assertNotIn("clientId", decision.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
