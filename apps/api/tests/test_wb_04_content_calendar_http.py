from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_calendar import CalendarItemService
from apps.api.marketops_content import ContentAssetService
from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
from apps.api.marketops_import.service import ScopeContext
from apps.api.tests.test_project_import_http import asgi_get, asgi_json

def uid() -> str:
    return str(uuid4())


class WB04ContentCalendarHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.scope = ScopeContext(uid(), uid(), uid(), uid())
        self.app = create_app(
            content_service=ContentAssetService(Path(self.temp.name)),
            calendar_service=CalendarItemService(Path(self.temp.name)),
            authenticator=StaticBearerAuthenticator("test-token", self.scope),
            request_id_factory=lambda: "wb04-request",
        )
        self.headers = [("Authorization", "Bearer test-token")]

    def tearDown(self):
        self.temp.cleanup()

    def test_authentication_and_server_roundtrip(self):
        unauthorized = asyncio.run(asgi_get(self.app, "/v1/workbench/content/briefs", []))
        self.assertEqual(unauthorized.status_code, 401)
        body = json.dumps({"topic": "主题", "channel": "知乎", "format": "长文", "audience": "负责人"}).encode()
        created = asyncio.run(asgi_json(self.app, "POST", "/v1/workbench/content/briefs", body, self.headers))
        self.assertEqual(created.status_code, 201)
        brief = created.json()["brief"]
        approved = asyncio.run(asgi_json(self.app, "POST", f"/v1/workbench/content/briefs/{brief['briefId']}/approve", json.dumps({"expectedVersion": 1}).encode(), self.headers))
        self.assertEqual(approved.status_code, 201)
        asset = asyncio.run(asgi_json(self.app, "POST", "/v1/workbench/content/assets", json.dumps({"briefId": brief["briefId"], "title": "封面", "channel": "图片资产", "format": "提示词任务", "assetType": "image", "prompt": "工业"}).encode(), self.headers))
        self.assertEqual(asset.status_code, 201)
        self.assertEqual(asset.json()["asset"]["status"], "needs_authorization")
        calendar = asyncio.run(asgi_json(self.app, "POST", "/v1/workbench/calendar/items", json.dumps({"title": "重点工作", "date": "2026-08-29", "source": "人工安排", "note": ""}).encode(), self.headers))
        self.assertEqual(calendar.status_code, 201)
        item = calendar.json()["item"]
        listed = asyncio.run(asgi_get(self.app, "/v1/workbench/calendar/items?period=all", self.headers))
        self.assertEqual(listed.json()["items"][0]["itemId"], item["itemId"])

    def test_openapi_contract_contains_wb04_routes(self):
        document = self.app.openapi()
        for path in ("/v1/workbench/content/briefs", "/v1/workbench/content/assets", "/v1/workbench/calendar/items"):
            self.assertIn(path, document["paths"])
        self.assertIn("ContentAsset", document["components"]["schemas"])
        self.assertIn("CalendarItem", document["components"]["schemas"])
