from __future__ import annotations

import base64
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_content import ContentAssetService, ContentError, ContentScope


class FakeModels:
    async def match_task(self, scope, task_type):
        return {"status": "matched", "preferred": {"profileId": str(uuid4()), "endpoint": "http://127.0.0.1:9", "modelName": "fake-image", "credentialRef": "env:FAKE_IMAGE_KEY"}}


class ImageEndpointHandler(BaseHTTPRequestHandler):
    last_auth = None
    last_payload = None

    def do_POST(self):
        import json
        length = int(self.headers.get("Content-Length", "0"))
        type(self).last_auth = self.headers.get("Authorization")
        type(self).last_payload = json.loads(self.rfile.read(length))
        body = json.dumps({"data": [{"b64_json": base64.b64encode(b"fake-png").decode()}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args):
        return


class WB04ImageGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = ContentAssetService(Path(self.temp.name))
        self.scope = ContentScope(*(str(uuid4()) for _ in range(4)))
        brief = await self.service.create_brief(self.scope, {"topic": "topic", "channel": "channel", "format": "format", "audience": "audience"})
        brief = await self.service.approve_brief(self.scope, brief["briefId"], {"expectedVersion": 1})
        self.asset = await self.service.create_asset(self.scope, {"briefId": brief["briefId"], "title": "image", "channel": "web", "format": "png", "assetType": "image", "prompt": "factory"})

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_unconfigured_model_fails_closed_and_records_failure(self):
        os.environ.pop("FAKE_IMAGE_KEY", None)
        with self.assertRaisesRegex(ContentError, "凭据未配置"):
            await self.service.generate_image(self.scope, self.asset["assetId"], FakeModels())
        current = (await self.service.list_assets(self.scope))[0]
        self.assertEqual(current["status"], "failed")
        self.assertEqual(current["errorCode"], "MODEL_UNAVAILABLE")

    async def test_b64_response_is_persisted_with_hash(self):
        os.environ["FAKE_IMAGE_KEY"] = "secret"
        original = self.service._request_generation
        self.service._request_generation = staticmethod(lambda endpoint, model, token, prompt: {"bytes": base64.b64decode("iVBORw0KGgo="), "mimeType": "image/png"})
        try:
            result = await self.service.generate_image(self.scope, self.asset["assetId"], FakeModels())
        finally:
            self.service._request_generation = original
            os.environ.pop("FAKE_IMAGE_KEY", None)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["resultSha256"]), 64)
        item, raw = await self.service.get_asset_result(self.scope, self.asset["assetId"])
        self.assertEqual(item["assetId"], self.asset["assetId"])
        self.assertEqual(raw, b"\x89PNG\r\n\x1a\n")

    async def test_local_openai_compatible_endpoint_receives_server_token(self):
        server = HTTPServer(("127.0.0.1", 0), ImageEndpointHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            os.environ["FAKE_IMAGE_KEY"] = "server-only-token"
            profile = {"profileId": str(uuid4()), "endpoint": f"http://127.0.0.1:{server.server_port}", "modelName": "fake-image", "credentialRef": "env:FAKE_IMAGE_KEY"}
            class LocalModels:
                async def match_task(self, scope, task_type): return {"status": "matched", "preferred": profile}
            result = await self.service.generate_image(self.scope, self.asset["assetId"], LocalModels())
            self.assertEqual(result["status"], "ready")
            self.assertEqual(ImageEndpointHandler.last_auth, "Bearer server-only-token")
            self.assertEqual(ImageEndpointHandler.last_payload["model"], "fake-image")
            self.assertEqual(ImageEndpointHandler.last_payload["prompt"], "factory")
        finally:
            os.environ.pop("FAKE_IMAGE_KEY", None); server.shutdown(); thread.join(timeout=2); server.server_close()

    async def test_cancel_uses_optimistic_version(self):
        queued = await self.service.update_asset(self.scope, self.asset["assetId"], {"expectedVersion": 1, "status": "queued"})
        cancelled = await self.service.cancel_image(self.scope, self.asset["assetId"], queued["version"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaisesRegex(ContentError, "only queued"):
            await self.service.cancel_image(self.scope, self.asset["assetId"], queued["version"])
