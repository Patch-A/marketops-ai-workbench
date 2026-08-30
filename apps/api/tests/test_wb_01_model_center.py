from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from apps.api.marketops_import.service import ScopeContext
from apps.api.marketops_models import ModelCenterError, ModelProfileService, ModelScope


def uid() -> str:
    return str(uuid4())


def scope() -> ModelScope:
    return ModelScope(uid(), uid(), uid(), uid())


HTTP_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ("fastapi", "multipart")
)
if HTTP_DEPENDENCIES_AVAILABLE:
    from apps.api.marketops_import.http import create_app


class ModelProfileServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = ModelProfileService(Path(self.temp.name))
        self.scope = scope()

    async def asyncTearDown(self):
        self.temp.cleanup()

    def payload(self, **changes):
        value = {
            "provider": "DeepSeek",
            "displayName": "DeepSeek 本地兼容端点",
            "protocol": "openai-compatible",
            "endpoint": "http://127.0.0.1:4446/v1",
            "modelName": "deepseek-chat",
            "capabilities": ["chat", "structured_output"],
            "contextWindow": 128000,
            "region": "本机私有部署",
            "dataRetention": "由本机部署策略决定",
            "credentialRef": "env:DEEPSEEK_API_KEY",
            "status": "enabled",
        }
        value.update(changes)
        return value

    async def test_create_list_and_restart_persist_only_non_secret_metadata(self):
        profile = await self.service.create_profile(self.scope, self.payload())
        self.assertEqual(profile.health, "unverified")
        self.assertEqual(len(await self.service.list_profiles(self.scope)), 1)
        raw = (Path(self.temp.name) / "model-center" / "profiles.json").read_text(encoding="utf-8")
        self.assertIn("env:DEEPSEEK_API_KEY", raw)
        self.assertNotIn("apiKey", raw)
        self.assertNotIn("sk-", raw)
        restarted = ModelProfileService(Path(self.temp.name))
        loaded = await restarted.list_profiles(self.scope)
        self.assertEqual(loaded[0].profile_id, profile.profile_id)

    async def test_scope_isolation_and_endpoint_credential_rejections(self):
        await self.service.create_profile(self.scope, self.payload())
        other = scope()
        self.assertEqual(await self.service.list_profiles(other), ())
        with self.assertRaisesRegex(ModelCenterError, "HTTPS"):
            await self.service.create_profile(self.scope, self.payload(endpoint="http://example.com/v1"))
        with self.assertRaisesRegex(ModelCenterError, "query or fragment"):
            await self.service.create_profile(self.scope, self.payload(endpoint="https://example.com/v1?api_key=sk-secret"))
        with self.assertRaises(ModelCenterError):
            await self.service.create_profile(self.scope, self.payload(credentialRef="sk-secret"))

    async def test_optimistic_update_disable_and_match(self):
        first = await self.service.create_profile(self.scope, self.payload())
        second = await self.service.create_profile(
            self.scope,
            self.payload(displayName="备用模型", modelName="backup", capabilities=["chat", "structured_output"]),
        )
        updated = await self.service.update_profile(
            self.scope,
            first.profile_id,
            {"expectedVersion": 1, "status": "disabled", "displayName": first.display_name},
        )
        self.assertEqual(updated.status, "disabled")
        await self.service.update_profile(self.scope, second.profile_id, {"expectedVersion": 1, "status": "enabled"})
        with self.assertRaisesRegex(ModelCenterError, "changed"):
            await self.service.update_profile(self.scope, second.profile_id, {"expectedVersion": 1, "status": "disabled"})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-configured"}):
            match = await self.service.match_task(self.scope, "review")
        self.assertEqual(match["status"], "matched")
        self.assertEqual(match["preferred"]["profileId"], second.profile_id)

    async def test_unknown_task_is_rejected(self):
        with self.assertRaises(ModelCenterError) as raised:
            await self.service.match_task(self.scope, "automatic_external_send")
        self.assertEqual(raised.exception.code, "INVALID_INPUT")

    async def test_probe_marks_unconfigured_without_network_call(self):
        profile = await self.service.create_profile(self.scope, self.payload())
        with patch.dict(os.environ, {}, clear=True):
            checked = await self.service.probe_profile(self.scope, profile.profile_id)
        self.assertEqual(checked.health, "unconfigured")
        self.assertEqual(checked.last_error, "服务端凭据未配置")
        reloaded = (await self.service.list_profiles(self.scope))[0]
        self.assertEqual(reloaded.health, "unconfigured")


@unittest.skipUnless(
    HTTP_DEPENDENCIES_AVAILABLE,
    "FastAPI and python-multipart are required for model HTTP tests",
)
class ModelProfileHttpTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, method, path, body=None, token="token"):
        messages = []
        delivered = False
        raw = b"" if body is None else json.dumps(body).encode()
        headers = [(b"authorization", f"Bearer {token}".encode()), (b"content-type", b"application/json")]

        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message):
            messages.append(message)

        await app(
            {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": headers, "client": ("127.0.0.1", 1234), "server": ("testserver", 80)},
            receive, send,
        )
        start = next(item for item in messages if item["type"] == "http.response.start")
        data = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
        return start["status"], json.loads(data)

    async def test_authenticated_crud_and_match_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            current = scope()
            auth_scope = ScopeContext(current.organization_id, current.workspace_id, current.client_id, current.actor_id)
            service = ModelProfileService(Path(directory))
            app = create_app(model_profile_service=service, authenticator=lambda token: auth_scope if token == "token" else None)
            payload = {
                "provider": "Local",
                "displayName": "本机模型",
                "protocol": "openai-compatible",
                "endpoint": "http://127.0.0.1:3080/v1",
                "modelName": "deepseek-chat",
                "capabilities": ["chat", "structured_output"],
                "contextWindow": 32000,
                "region": "本机",
                "dataRetention": "本机策略",
                "credentialRef": "env:LOCAL_MODEL_KEY",
                "status": "enabled",
            }
            status, created = await self.request(app, "POST", "/v1/model-profiles", payload)
            self.assertEqual(status, 201)
            self.assertNotIn("apiKey", created["profile"])
            self.assertFalse(created["profile"]["credentialConfigured"])
            status, listing = await self.request(app, "GET", "/v1/model-profiles")
            self.assertEqual(status, 200)
            self.assertEqual(len(listing["profiles"]), 1)
            status, unavailable = await self.request(app, "POST", "/v1/model-task-matches", {"taskType": "review"})
            self.assertEqual(status, 200)
            self.assertEqual(unavailable["status"], "unavailable")
            with patch.dict(os.environ, {"LOCAL_MODEL_KEY": "test-configured"}):
                status, matched = await self.request(app, "POST", "/v1/model-task-matches", {"taskType": "review"})
            self.assertEqual(status, 200)
            self.assertEqual(matched["status"], "matched")
            with patch.dict(os.environ, {}, clear=True):
                status, probed = await self.request(app, "POST", f"/v1/model-profiles/{created['profile']['profileId']}/probe", {})
            self.assertEqual(status, 200)
            self.assertEqual(probed["profile"]["health"], "unconfigured")


if __name__ == "__main__":
    unittest.main()
