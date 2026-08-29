from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
from apps.api.marketops_import.service import ScopeContext
from apps.api.marketops_obsidian import ObsidianReadOnlyService
from apps.api.tests.test_project_import_http import asgi_get, asgi_json


def uid() -> str:
    return str(uuid4())


class WB04ObsidianHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        (self.vault / "Project.md").write_text(
            "# Synthetic project note\nsecret-body-marker", encoding="utf-8"
        )
        self.other = self.root / "other-vault"
        self.other.mkdir()
        self.service = ObsidianReadOnlyService(
            self.root / "store", vault_root=self.vault
        )
        self.scope = ScopeContext(uid(), uid(), uid(), uid())
        self.headers = [("Authorization", "Bearer test-token")]
        self.app = self._app(self.scope, "test-token")

    def tearDown(self):
        self.temp.cleanup()

    def _app(self, scope: ScopeContext, token: str):
        return create_app(
            obsidian_service=self.service,
            authenticator=StaticBearerAuthenticator(token, scope),
            request_id_factory=lambda: "wb04-obsidian-request",
        )

    def test_authentication_connect_list_and_body_redaction(self):
        unauthorized = asyncio.run(
            asgi_get(self.app, "/v1/workbench/obsidian/connection", [])
        )
        self.assertEqual(unauthorized.status_code, 401)

        missing = asyncio.run(
            asgi_get(self.app, "/v1/workbench/obsidian/notes", self.headers)
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "CONNECTION_NOT_FOUND")

        created = asyncio.run(
            asgi_json(
                self.app,
                "POST",
                "/v1/workbench/obsidian/connection",
                json.dumps(
                    {"vaultPath": str(self.vault), "relativePaths": []}
                ).encode(),
                self.headers,
            )
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["cache-control"], "no-store")
        self.assertTrue(created.json()["connection"]["status"] == "connected")

        listed = asyncio.run(
            asgi_get(self.app, "/v1/workbench/obsidian/notes", self.headers)
        )
        self.assertEqual(listed.status_code, 200)
        payload = listed.json()
        self.assertTrue(payload["readOnly"])
        self.assertIn("syncedAt", payload)
        self.assertEqual(payload["notes"][0]["title"], "Synthetic project note")
        self.assertNotIn("secret-body-marker", listed.body.decode("utf-8"))

    def test_rejects_other_vault_and_isolates_authenticated_scope(self):
        rejected = asyncio.run(
            asgi_json(
                self.app,
                "POST",
                "/v1/workbench/obsidian/connection",
                json.dumps(
                    {"vaultPath": str(self.other), "relativePaths": []}
                ).encode(),
                self.headers,
            )
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()["code"], "VAULT_NOT_ALLOWED")
        self.assertNotIn(str(self.other), rejected.body.decode("utf-8"))

        connected = asyncio.run(
            asgi_json(
                self.app,
                "POST",
                "/v1/workbench/obsidian/connection",
                json.dumps(
                    {"vaultPath": str(self.vault), "relativePaths": []}
                ).encode(),
                self.headers,
            )
        )
        self.assertEqual(connected.status_code, 201)

        other_scope = ScopeContext(
            self.scope.organization_id,
            self.scope.workspace_id,
            uid(),
            uid(),
        )
        other_app = self._app(other_scope, "other-token")
        other_headers = [("Authorization", "Bearer other-token")]
        isolated = asyncio.run(
            asgi_get(other_app, "/v1/workbench/obsidian/notes", other_headers)
        )
        self.assertEqual(isolated.status_code, 404)

    def test_openapi_contract_contains_read_only_obsidian_routes(self):
        document = self.app.openapi()
        self.assertIn("/v1/workbench/obsidian/connection", document["paths"])
        self.assertIn("/v1/workbench/obsidian/notes", document["paths"])
        request_schema = document["components"]["schemas"][
            "ObsidianConnectionRequest"
        ]
        self.assertEqual(request_schema["properties"]["relativePaths"]["maxItems"], 200)
        self.assertEqual(
            document["components"]["schemas"]["ObsidianNoteListResult"]
            ["properties"]["readOnly"]["const"],
            True,
        )


if __name__ == "__main__":
    unittest.main()
