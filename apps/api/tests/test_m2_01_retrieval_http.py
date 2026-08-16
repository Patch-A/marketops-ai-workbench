from __future__ import annotations

import json
import unittest

from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
from apps.api.marketops_import.service import ScopeContext
from apps.api.marketops_retrieval import (
    IndexSourceResult,
    ParsedBlock,
    RetrievalFailure,
    SearchResult,
    SourceIdentity,
    WithdrawIndexResult,
    build_source_index,
    search_source_indexes,
    withdraw_source_index,
)
from apps.api.tests.test_m1_02_review_http import asgi_request


IDS = {
    "organization": "00000000-0000-4000-8000-000000000201",
    "workspace": "00000000-0000-4000-8000-000000000202",
    "client": "00000000-0000-4000-8000-000000000203",
    "actor": "00000000-0000-4000-8000-000000000204",
    "project": "00000000-0000-4000-8000-000000000205",
    "version": "00000000-0000-4000-8000-000000000206",
}


class FakeIndexingService:
    def __init__(self, result=None, failure=None):
        self.result = result
        self.failure = failure
        self.calls = []

    async def index_source(self, request, scope):
        self.calls.append((request, scope))
        if self.failure is not None:
            raise self.failure
        return self.result


class FakeRetrievalService:
    def __init__(self, index, search_result: SearchResult):
        self.index = index
        self.search_result = search_result
        self.calls = []

    async def read_index(self, **kwargs):
        self.calls.append(("read", kwargs))
        return self.index

    async def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return self.search_result

    async def withdraw(self, **kwargs):
        self.calls.append(("withdraw", kwargs))
        return WithdrawIndexResult(withdraw_source_index(self.index, kwargs["scope"]), False)


class RetrievalHttpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ScopeContext(
            IDS["organization"], IDS["workspace"], IDS["client"], IDS["actor"]
        )
        retrieval_scope = self._retrieval_scope()
        self.index = build_source_index(
            SourceIdentity(
                IDS["organization"],
                IDS["workspace"],
                IDS["client"],
                IDS["project"],
                IDS["version"],
                "a" * 64,
                "marketops-parser-v1",
            ),
            (ParsedBlock("legal review before media booking", {"line": 1}),),
            retrieval_scope,
        )
        self.search_result = search_source_indexes(
            (self.index,),
            project_id=IDS["project"],
            query="legal review",
            scope=retrieval_scope,
            current_source_hashes={IDS["version"]: "a" * 64},
        )

    def _retrieval_scope(self):
        from apps.api.marketops_retrieval import RetrievalScopeContext

        return RetrievalScopeContext(
            IDS["organization"], IDS["workspace"], IDS["client"], IDS["actor"]
        )

    def app(self, *, indexing=None, retrieval=None):
        return create_app(
            authenticator=StaticBearerAuthenticator("retrieval-token", self.scope),
            retrieval_indexing=indexing
            or FakeIndexingService(
                IndexSourceResult(self.index, "markdown", False)
            ),
            retrieval_service=retrieval
            or FakeRetrievalService(self.index, self.search_result),
            request_id_factory=lambda: "retrieval-request",
        )

    @staticmethod
    def headers():
        return [
            ("Authorization", "Bearer retrieval-token"),
            ("Content-Type", "application/json"),
        ]

    async def test_authenticated_index_search_read_and_withdrawal_contract(self):
        indexing = FakeIndexingService(
            IndexSourceResult(self.index, "markdown", False)
        )
        retrieval = FakeRetrievalService(self.index, self.search_result)
        app = self.app(indexing=indexing, retrieval=retrieval)

        created = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{IDS['project']}/source-indexes",
            self.headers(),
            json.dumps({"artifactVersionId": IDS["version"]}).encode(),
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["indexId"], self.index.index_id)
        self.assertEqual(created.json()["chunkCount"], 1)
        self.assertFalse(created.json()["replayed"])
        self.assertEqual(created.headers["cache-control"], "no-store")
        self.assertNotIn("storage", str(created.json()).lower())
        request, scope = indexing.calls[0]
        self.assertEqual(request.project_id, IDS["project"])
        self.assertEqual(request.artifact_version_id, IDS["version"])
        self.assertEqual(scope.actor_id, IDS["actor"])

        read = await asgi_request(
            app,
            "GET",
            f"/v1/projects/{IDS['project']}/source-indexes/{self.index.index_id}",
            [("Authorization", "Bearer retrieval-token")],
        )
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["status"], "ready")
        self.assertNotIn("replayed", read.json())

        searched = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{IDS['project']}/retrieval-searches",
            self.headers(),
            json.dumps({"query": "legal review", "limit": 5}).encode(),
        )
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(searched.json()["retrievalMode"], "lexical_ngram")
        self.assertEqual(searched.json()["resultCount"], 1)
        self.assertEqual(
            searched.json()["results"][0]["citation"]["freshness"], "fresh"
        )
        search_call = next(call for call in retrieval.calls if call[0] == "search")
        self.assertEqual(search_call[1]["query"], "legal review")
        self.assertEqual(search_call[1]["limit"], 5)

        withdrawn = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{IDS['project']}/source-indexes/{self.index.index_id}/withdrawal",
            self.headers(),
            b"{}",
        )
        self.assertEqual(withdrawn.status_code, 200)
        self.assertEqual(withdrawn.json()["status"], "withdrawn")
        self.assertEqual(withdrawn.json()["chunkCount"], 0)

    async def test_authentication_and_closed_json_bodies_fail_before_services(self):
        indexing = FakeIndexingService(
            IndexSourceResult(self.index, "markdown", False)
        )
        retrieval = FakeRetrievalService(self.index, self.search_result)
        app = self.app(indexing=indexing, retrieval=retrieval)
        missing_auth = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{IDS['project']}/retrieval-searches",
            [("Content-Type", "application/json")],
            b'{"query":"legal review"}',
        )
        unknown_index_field = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{IDS['project']}/source-indexes",
            self.headers(),
            json.dumps(
                {
                    "artifactVersionId": IDS["version"],
                    "parserVersion": "client-controlled",
                }
            ).encode(),
        )
        unknown_search_field = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{IDS['project']}/retrieval-searches",
            self.headers(),
            b'{"query":"legal review","workspaceId":"forbidden"}',
        )

        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(unknown_index_field.status_code, 400)
        self.assertEqual(unknown_search_field.status_code, 400)
        self.assertEqual(indexing.calls, [])
        self.assertEqual(retrieval.calls, [])

    async def test_retrieval_failures_are_sanitized(self):
        indexing = FakeIndexingService(
            failure=RetrievalFailure(
                "SOURCE_INTEGRITY_FAILED", "C:/customer/private-source.md"
            )
        )
        response = await asgi_request(
            self.app(indexing=indexing),
            "POST",
            f"/v1/projects/{IDS['project']}/source-indexes",
            self.headers(),
            json.dumps({"artifactVersionId": IDS["version"]}).encode(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "SOURCE_INTEGRITY_FAILED")
        self.assertNotIn("customer", str(response.json()).lower())
        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
