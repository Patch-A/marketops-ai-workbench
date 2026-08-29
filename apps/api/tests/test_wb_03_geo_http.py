from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_geo import GeoSnapshotService
from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
from apps.api.marketops_import.service import ScopeContext
from apps.api.tests.test_project_import_http import asgi_get, asgi_json


def uid() -> str:
    return str(uuid4())


class GeoHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.scope = ScopeContext(uid(), uid(), uid(), uid())
        self.app = create_app(
            geo_service=GeoSnapshotService(Path(self.temp.name)),
            authenticator=StaticBearerAuthenticator("test-token", self.scope),
            request_id_factory=lambda: "geo-request",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_authenticated_query_set_snapshot_and_gap_task(self):
        headers = [("Authorization", "Bearer test-token")]
        body = json.dumps({"product": "连接器", "market": "印度", "language": "中文", "queries": ["供应商怎么选"]}).encode()
        created = asyncio.run(asgi_json(self.app, "POST", "/v1/workbench/geo/query-sets", body, headers))
        self.assertEqual(created.status_code, 201)
        query_set = created.json()["querySet"]
        snapshot_body = json.dumps({"platform": "ChatGPT", "queryId": query_set["queries"][0]["queryId"], "visibility": "unclear", "observation": "无法判断", "observedAt": "2026-08-29", "citation": None}).encode()
        snapshot = asyncio.run(asgi_json(self.app, "POST", f"/v1/workbench/geo/query-sets/{query_set['querySetId']}/snapshots", snapshot_body, headers))
        self.assertEqual(snapshot.status_code, 201)
        self.assertEqual(snapshot.json()["task"]["status"], "needs_review")
        listed = asyncio.run(asgi_get(self.app, f"/v1/workbench/geo/query-sets/{query_set['querySetId']}/snapshots", headers))
        self.assertEqual(len(listed.json()["snapshots"]), 1)

    def test_unauthenticated_route_is_rejected(self):
        response = asyncio.run(asgi_get(self.app, "/v1/workbench/geo/query-sets", []))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTHORIZATION_REQUIRED")

    def test_runtime_openapi_contains_geo_contract(self):
        document = self.app.openapi()
        self.assertIn("/v1/workbench/geo/query-sets", document["paths"])
        self.assertIn("/v1/workbench/geo/query-sets/{querySetId}/snapshots", document["paths"])
        self.assertEqual(document["paths"]["/v1/workbench/geo/query-sets"]["post"]["operationId"], "createGeoQuerySet")
        self.assertEqual(document["paths"]["/v1/workbench/geo/query-sets/{querySetId}/snapshots"]["post"]["operationId"], "createGeoSnapshot")
        schemas = document["components"]["schemas"]
        self.assertIn("GeoQuerySetCreateRequest", schemas)
        self.assertIn("GeoSnapshotCreateResult", schemas)
        self.assertIn("GEO_NOT_FOUND", schemas["ImportError"]["properties"]["code"]["enum"])
        self.assertIn("GEO_STORE_FAILED", schemas["ImportError"]["properties"]["code"]["enum"])
