from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit
from uuid import uuid4


HTTP_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("fastapi", "multipart")
)
REQUIRE_PREREQUISITES = os.environ.get("MARKETOPS_REQUIRE_TEST_PREREQUISITES") == "1"
if REQUIRE_PREREQUISITES and not HTTP_DEPENDENCIES_AVAILABLE:
    raise RuntimeError(
        "HTTP test prerequisites are required: install FastAPI and python-multipart"
    )

if HTTP_DEPENDENCIES_AVAILABLE:
    from starlette import formparsers
    from starlette.datastructures import Headers

    from apps.api.marketops_import import http
    from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
    from apps.api.marketops_import.postgres import (
        ApprovedProposalView,
        ProjectDetail,
        ProjectFileView,
        ProjectSummary,
    )
    from apps.api.marketops_import.service import (
        ImportFailure,
        ImportResult,
        ScopeContext,
    )


def identifier():
    return str(uuid4())


@unittest.skipUnless(
    HTTP_DEPENDENCIES_AVAILABLE,
    "FastAPI and python-multipart are required for HTTP adapter tests",
)
class ProjectImportHttpTests(unittest.TestCase):
    def setUp(self):
        self.scope = ScopeContext(identifier(), identifier(), identifier(), identifier())
        self.service = FakeService()
        self.app = create_app(
            import_service=self.service,
            authenticator=StaticBearerAuthenticator("test-token", self.scope),
            request_id_factory=lambda: "request-id",
        )

    def request(self, *, files=None, headers=None):
        request_headers = [
            ("Authorization", "Bearer test-token"),
            ("Idempotency-Key", "request-001"),
        ]
        request_headers.extend(headers or [])
        body, content_type = encode_multipart(files or valid_parts())
        request_headers.append(("Content-Type", content_type))
        return asyncio.run(asgi_post(self.app, request_headers, body))

    def test_success_uses_server_scope_and_cleans_random_temp_files(self):
        response = self.request()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["projectId"], self.service.result.project_id)
        self.assertEqual(
            response.headers["location"],
            f"/v1/projects/{self.service.result.project_id}",
        )
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.service.scope, self.scope)
        self.assertEqual(self.service.source_bytes, b"# source\n")
        self.assertEqual(self.service.proposal_bytes, b"# approved proposal\n")
        self.assertEqual(self.service.request.source.filename, "../../brief.md")
        self.assertFalse(self.service.request.source.source_path.exists())
        self.assertFalse(self.service.request.proposal.source_path.exists())

    def test_runtime_openapi_is_the_reviewed_frozen_contract(self):
        contract_path = (
            Path(__file__).resolve().parents[1]
            / "openapi"
            / "project-import.openapi.yaml"
        )
        frozen = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertEqual(self.app.openapi(), frozen)
        operation = self.app.openapi()["paths"]["/v1/project-imports"]["post"]
        self.assertEqual(
            operation["security"],
            [{"deploymentActor": []}, {"browserDeploymentActor": []}],
        )
        self.assertIn("/v1/projects", self.app.openapi()["paths"])
        self.assertIn("/v1/projects/{projectId}", self.app.openapi()["paths"])
        self.assertEqual(
            set(operation["responses"]),
            {"201", "400", "401", "403", "409", "413", "415", "422", "500"},
        )

        first = self.app.openapi()
        first["paths"].clear()
        first["info"]["title"] = "mutated caller copy"
        second = self.app.openapi()
        self.assertEqual(second, frozen)
        self.assertIsNot(first, second)

    def test_custom_authenticator_scope_is_canonicalized_into_a_new_value(self):
        raw_scope = ScopeContext(
            *(f"{{{str(uuid4()).upper()}}}" for _ in range(4))
        )
        canonical_scope = ScopeContext(
            *(str(uuid4_value).lower() for uuid4_value in map(http.UUID, raw_scope.__dict__.values()))
        )

        def authenticate(token):
            self.assertEqual(token, "custom-token")
            return raw_scope

        app = create_app(
            import_service=self.service,
            authenticator=authenticate,
            request_id_factory=lambda: "request-id",
        )
        body, content_type = encode_multipart(valid_parts())
        response = asyncio.run(
            asgi_post(
                app,
                [
                    ("Authorization", "Bearer custom-token"),
                    ("Idempotency-Key", "request-001"),
                    ("Content-Type", content_type),
                ],
                body,
            )
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.service.scope, canonical_scope)
        self.assertIsNot(self.service.scope, raw_scope)

    def test_missing_or_invalid_bearer_token_returns_frozen_401(self):
        for authorization in (None, "Bearer wrong"):
            request_headers = [("Idempotency-Key", "request-001")]
            if authorization:
                request_headers.append(("Authorization", authorization))
            body, content_type = encode_multipart(valid_parts())
            request_headers.append(("Content-Type", content_type))
            response = asyncio.run(asgi_post(self.app, request_headers, body))
            with self.subTest(authorization=authorization):
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")
                self.assertEqual(response.json()["code"], "AUTHORIZATION_REQUIRED")
        self.assertEqual(self.service.calls, 0)

    def test_invalid_basic_credentials_return_basic_challenge(self):
        body, content_type = encode_multipart(valid_parts())
        response = asyncio.run(
            asgi_post(
                self.app,
                [
                    ("Authorization", "Basic abc"),
                    ("Idempotency-Key", "request-001"),
                    ("Content-Type", content_type),
                ],
                body,
            )
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.headers["www-authenticate"],
            'Basic realm="MarketOps", charset="UTF-8"',
        )

    def test_valid_basic_credentials_can_import_with_server_scope(self):
        app = create_app(
            import_service=self.service,
            authenticator=StaticBearerAuthenticator(
                "test-token", self.scope, basic_username="operator"
            ),
            request_id_factory=lambda: "request-id",
        )
        body, content_type = encode_multipart(valid_parts())
        encoded = base64.b64encode(b"operator:test-token").decode("ascii")
        response = asyncio.run(
            asgi_post(
                app,
                [
                    ("Authorization", f"Basic {encoded}"),
                    ("Idempotency-Key", "request-001"),
                    ("Content-Type", content_type),
                ],
                body,
            )
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.service.scope, self.scope)

    def test_authenticated_actor_with_invalid_server_scope_returns_403(self):
        invalid = ScopeContext("bad", identifier(), identifier(), identifier())
        app = create_app(
            import_service=self.service,
            authenticator=StaticBearerAuthenticator("test-token", invalid),
            request_id_factory=lambda: "request-id",
        )
        body, content_type = encode_multipart(valid_parts())
        response = asyncio.run(
            asgi_post(
                app,
                [
                    ("Authorization", "Bearer test-token"),
                    ("Idempotency-Key", "request-001"),
                    ("Content-Type", content_type),
                ],
                body,
            )
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("www-authenticate", response.headers)
        self.assertEqual(response.json()["code"], "AUTHORIZATION_REQUIRED")

    def test_duplicate_extra_missing_and_wrong_part_types_are_rejected(self):
        cases = {
            "duplicate": valid_parts() + [("projectName", (None, "duplicate"))],
            "extra": valid_parts() + [("workspaceId", (None, identifier()))],
            "missing": [part for part in valid_parts() if part[0] != "proposalFile"],
            "file-instead-of-field": [
                part if part[0] != "projectName" else ("projectName", ("name.txt", b"x", "text/plain"))
                for part in valid_parts()
            ],
        }
        for name, parts in cases.items():
            with self.subTest(name=name):
                response = self.request(files=parts)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "INVALID_INPUT")
        self.assertEqual(self.service.calls, 0)
        body, content_type = encode_multipart(valid_parts())
        response = asyncio.run(
            asgi_post(
                self.app,
                [
                    ("Authorization", "Bearer test-token"),
                    ("Idempotency-Key", "request-001"),
                    ("Idempotency-Key", "request-002"),
                    ("Content-Type", content_type),
                ],
                body,
            )
        )
        self.assertEqual(response.status_code, 400)

    def test_oversized_file_returns_413_without_calling_service(self):
        parts = valid_parts(source=b"x" * (25 * 1024 * 1024 + 1))
        response = self.request(files=parts)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "PAYLOAD_TOO_LARGE")
        self.assertEqual(self.service.calls, 0)

    def test_oversized_file_is_rejected_before_the_remaining_body_is_consumed(self):
        parts = valid_parts(
            source=b"x" * (25 * 1024 * 1024 + 1),
            proposal=b"y" * (2 * 1024 * 1024),
        )
        body, content_type = encode_multipart(parts)
        response, consumed = asyncio.run(
            asgi_post_chunked(
                self.app,
                [
                    ("Authorization", "Bearer test-token"),
                    ("Idempotency-Key", "request-001"),
                    ("Content-Type", content_type),
                ],
                body,
            )
        )
        self.assertEqual(response.status_code, 413)
        self.assertLess(consumed, len(body))
        self.assertEqual(self.service.calls, 0)

    def test_total_multipart_stream_has_a_hard_limit(self):
        body, content_type = encode_multipart(valid_parts(source=b"x" * 2048))
        with patch.object(http, "_MAX_MULTIPART_REQUEST_SIZE_BYTES", 1024):
            response, consumed = asyncio.run(
                asgi_post_chunked(
                    self.app,
                    [
                        ("Authorization", "Bearer test-token"),
                        ("Idempotency-Key", "request-001"),
                        ("Content-Type", content_type),
                    ],
                    body,
                    chunk_size=256,
                )
            )
        self.assertEqual(response.status_code, 413)
        self.assertLess(consumed, len(body))
        self.assertEqual(self.service.calls, 0)

    def test_domain_failures_use_frozen_statuses_and_sanitized_envelope(self):
        cases = {
            "INVALID_INPUT": 400,
            "IDEMPOTENCY_CONFLICT": 409,
            "PAYLOAD_TOO_LARGE": 413,
            "UNSUPPORTED_FORMAT": 415,
            "INVALID_MEDIA_TYPE": 415,
            "INVALID_DOCUMENT": 422,
            "APPROVAL_REQUIRED": 422,
            "OBJECT_WRITE_FAILED": 500,
            "OBJECT_INTEGRITY_MISMATCH": 500,
            "INVALID_SERVER_ID": 500,
            "INVALID_SERVER_CLOCK": 500,
            "DATABASE_WRITE_FAILED": 500,
        }
        for code, status in cases.items():
            with self.subTest(code=code):
                self.service.failure = ImportFailure(
                    code, "secret-dsn source bytes temporary-path", retryable=True
                )
                response = self.request()
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["code"], code)
                self.assertNotIn("secret-dsn", response.text)
                self.assertEqual(set(response.json()), {"code", "message", "retryable", "requestId"})

    def test_non_multipart_and_duplicate_idempotency_headers_fail_as_400(self):
        response = asyncio.run(
            asgi_post(
                self.app,
                [
                    ("Authorization", "Bearer test-token"),
                    ("Idempotency-Key", "request-001"),
                    ("Content-Type", "application/octet-stream"),
                ],
                b"not multipart",
            )
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_content_type_headers_fail_as_400(self):
        body, content_type = encode_multipart(valid_parts())
        for second_value in (content_type, "application/octet-stream"):
            with self.subTest(second_value=second_value):
                response = asyncio.run(
                    asgi_post(
                        self.app,
                        [
                            ("Authorization", "Bearer test-token"),
                            ("Idempotency-Key", "request-001"),
                            ("Content-Type", content_type),
                            ("Content-Type", second_value),
                        ],
                        body,
                    )
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "INVALID_INPUT")
        self.assertEqual(self.service.calls, 0)


@unittest.skipUnless(
    HTTP_DEPENDENCIES_AVAILABLE,
    "FastAPI and python-multipart are required for HTTP adapter tests",
)
class ProjectReadHttpTests(unittest.TestCase):
    def setUp(self):
        self.scope = ScopeContext(identifier(), identifier(), identifier(), identifier())
        self.reader = FakeProjectReader()
        self.authenticator = StaticBearerAuthenticator(
            "test-token", self.scope, basic_username="operator"
        )
        self.app = create_app(
            project_reader=self.reader,
            authenticator=self.authenticator,
            request_id_factory=lambda: "request-id",
        )

    def get(self, path, *, authorization="Bearer test-token"):
        headers = [] if authorization is None else [("Authorization", authorization)]
        return asyncio.run(asgi_get(self.app, path, headers))

    def test_list_is_bounded_scoped_no_store_and_does_not_expose_secrets(self):
        response = self.get("/v1/projects?limit=7")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.reader.list_scope, self.scope)
        self.assertEqual(self.reader.limit, 7)
        self.assertEqual(response.json()["projects"][0]["projectId"], self.reader.project_id)
        for forbidden in ("storageKey", "idempotency", "credential", "actorId"):
            self.assertNotIn(forbidden, response.text)

    def test_list_rejects_unknown_duplicate_and_out_of_range_query(self):
        for query in ("limit=0", "limit=101", "limit=x", "limit=2&limit=3", "scope=x"):
            with self.subTest(query=query):
                response = self.get(f"/v1/projects?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "INVALID_INPUT")
        self.assertEqual(self.reader.list_calls, 0)

    def test_detail_returns_only_display_metadata_and_canonical_id(self):
        response = self.get(f"/v1/projects/{{{self.reader.project_id.upper()}}}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.reader.get_scope, self.scope)
        self.assertEqual(self.reader.requested_project_id, self.reader.project_id)
        body = response.json()
        self.assertEqual(body["approvedProposal"]["approvalStatus"], "approved")
        self.assertEqual(body["approvedProposal"]["sha256"], "ab" * 32)
        self.assertEqual(body["sourceFile"]["filename"], "brief.md")
        self.assertNotIn("sha256", body["sourceFile"])
        for forbidden in ("storageKey", "idempotency", "approvedBy"):
            self.assertNotIn(forbidden, response.text)

    def test_invalid_absent_and_foreign_ids_are_indistinguishable(self):
        invalid = self.get("/v1/projects/not-a-uuid")
        self.reader.detail = None
        absent = self.get(f"/v1/projects/{identifier()}")
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(invalid.body, absent.body)
        self.assertEqual(invalid.json()["code"], "PROJECT_NOT_FOUND")

    def test_basic_authentication_uses_server_scope_for_api(self):
        encoded = base64.b64encode(b"operator:test-token").decode("ascii")
        response = self.get("/v1/projects", authorization=f"Basic {encoded}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.reader.list_scope, self.scope)

    def test_read_failure_is_sanitized_and_retryable(self):
        self.reader.failure = RuntimeError("postgresql://user:secret@customer")
        response = self.get("/v1/projects")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "DATABASE_WRITE_FAILED")
        self.assertTrue(response.json()["retryable"])
        self.assertNotIn("secret", response.text)

    def test_static_whitelist_is_basic_protected_and_never_contains_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in (
                "index.html",
                "app.js",
                "project-import.js",
                "review-workbench.js",
                "styles.css",
            ):
                (root / filename).write_text(f"asset:{filename}", encoding="utf-8")
            app = create_app(
                project_reader=self.reader,
                authenticator=self.authenticator,
                request_id_factory=lambda: "request-id",
                static_root=root,
            )
            challenged = asyncio.run(asgi_get(app, "/", []))
            self.assertEqual(challenged.status_code, 401)
            self.assertEqual(
                challenged.headers["www-authenticate"],
                'Basic realm="MarketOps", charset="UTF-8"',
            )
            encoded = base64.b64encode(b"operator:test-token").decode("ascii")
            loaded = asyncio.run(
                asgi_get(app, "/app.js", [("Authorization", f"Basic {encoded}")])
            )
            self.assertEqual(loaded.status_code, 200)
            self.assertEqual(loaded.headers["cache-control"], "no-store")
            self.assertEqual(loaded.headers["x-content-type-options"], "nosniff")
            self.assertEqual(loaded.headers["referrer-policy"], "no-referrer")
            self.assertEqual(
                loaded.headers["content-security-policy"],
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
            self.assertEqual(loaded.text, "asset:app.js")
            self.assertNotIn("test-token", loaded.text)

            review_module = asyncio.run(
                asgi_get(
                    app,
                    "/review-workbench.js",
                    [("Authorization", f"Basic {encoded}")],
                )
            )
            self.assertEqual(review_module.status_code, 200)
            self.assertEqual(review_module.headers["cache-control"], "no-store")
            self.assertIn("javascript", review_module.headers["content-type"])
            self.assertEqual(review_module.text, "asset:review-workbench.js")

            unknown = asyncio.run(
                asgi_get(app, "/.git/config", [("Authorization", f"Basic {encoded}")])
            )
            self.assertEqual(unknown.status_code, 404)


@unittest.skipUnless(
    HTTP_DEPENDENCIES_AVAILABLE,
    "FastAPI and python-multipart are required for HTTP adapter tests",
)
class UploadCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_cancellation_removes_partial_temp_file(self):
        upload = CancellingUpload()
        captured = []
        original = http.tempfile.NamedTemporaryFile

        def tracked(*args, **kwargs):
            handle = original(*args, **kwargs)
            captured.append(Path(handle.name))
            return handle

        with patch.object(http.tempfile, "NamedTemporaryFile", side_effect=tracked):
            with self.assertRaises(asyncio.CancelledError):
                await http._stream_upload(upload)
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0].exists())

    async def test_multipart_cancellation_closes_parser_spool_files(self):
        body, content_type = encode_multipart(
            valid_parts(source=b"x" * (2 * 1024 * 1024))
        )
        captured = []
        original = formparsers.SpooledTemporaryFile

        def tracked(*args, **kwargs):
            handle = original(*args, **kwargs)
            captured.append(handle)
            return handle

        async def stream():
            yield body[: 1536 * 1024]
            raise asyncio.CancelledError

        parser = http._FileSizeLimitedMultipartParser(
            Headers({"Content-Type": content_type}),
            stream(),
            max_files=2,
            max_fields=3,
            max_part_size=4096,
        )
        with patch.object(formparsers, "SpooledTemporaryFile", side_effect=tracked):
            with self.assertRaises(asyncio.CancelledError):
                await parser.parse()
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].closed)


@unittest.skipUnless(
    HTTP_DEPENDENCIES_AVAILABLE,
    "FastAPI and python-multipart are required for runtime settings tests",
)
class RuntimeSettingsTests(unittest.TestCase):
    def test_scope_uuid_environment_values_are_canonicalized(self):
        from apps.api.main import RuntimeSettings

        identifiers = [identifier() for _ in range(4)]
        environment = {
            "MARKETOPS_DATABASE_URL": "postgresql://example.invalid/test",
            "MARKETOPS_OBJECT_ROOT": "objects",
            "MARKETOPS_DEPLOYMENT_TOKEN": "token",
            "MARKETOPS_DEPLOYMENT_USERNAME": "operator",
            "MARKETOPS_ORGANIZATION_ID": "{" + identifiers[0].upper() + "}",
            "MARKETOPS_WORKSPACE_ID": identifiers[1].upper(),
            "MARKETOPS_CLIENT_ID": "{" + identifiers[2] + "}",
            "MARKETOPS_ACTOR_ID": identifiers[3].upper(),
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = RuntimeSettings.from_environment()
        self.assertEqual(
            settings.scope,
            ScopeContext(*identifiers),
        )
        self.assertEqual(settings.basic_username, "operator")


@unittest.skipUnless(
    HTTP_DEPENDENCIES_AVAILABLE,
    "FastAPI and python-multipart are required for runtime lifespan tests",
)
class RuntimeLifespanTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        from apps.api.main import RuntimeSettings

        return RuntimeSettings(
            database_url="postgresql://example.invalid/test",
            object_root=Path("objects"),
            bearer_token="token",
            basic_username="operator",
            scope=ScopeContext(*(identifier() for _ in range(4))),
        )

    async def test_one_shared_pool_is_closed_once(self):
        from apps.api import main

        pool = SimpleNamespace()
        owner = SimpleNamespace(pool=pool, close=AsyncMock())
        application = SimpleNamespace(state=SimpleNamespace())
        with (
            patch.object(main.RuntimeSettings, "from_environment", return_value=self.settings()),
            patch.object(main.AsyncpgImportRepository, "create", AsyncMock(return_value=owner)),
            patch.object(main, "AsyncpgReviewRepository") as review_repository,
            patch.object(main, "AsyncpgApprovedProposalSourceReader") as source_reader,
        ):
            async with main._lifespan(application):
                self.assertIs(application.state.project_reader, owner)
                review_repository.assert_called_once_with(pool)
                source_reader.assert_called_once_with(pool)
        owner.close.assert_awaited_once_with()

    async def test_pool_creation_failure_or_cancellation_does_not_close_unknown_resource(self):
        from apps.api import main

        for failure in (RuntimeError("secret dsn"), asyncio.CancelledError()):
            with self.subTest(type=type(failure).__name__):
                create = AsyncMock(side_effect=failure)
                with (
                    patch.object(main.RuntimeSettings, "from_environment", return_value=self.settings()),
                    patch.object(main.AsyncpgImportRepository, "create", create),
                ):
                    with self.assertRaises(type(failure)):
                        async with main._lifespan(SimpleNamespace(state=SimpleNamespace())):
                            pass

    async def test_setup_failure_and_cancellation_close_created_pool_once(self):
        from apps.api import main

        for failure in (RuntimeError("setup failed"), asyncio.CancelledError()):
            with self.subTest(type=type(failure).__name__):
                owner = SimpleNamespace(pool=SimpleNamespace(), close=AsyncMock())
                with (
                    patch.object(main.RuntimeSettings, "from_environment", return_value=self.settings()),
                    patch.object(main.AsyncpgImportRepository, "create", AsyncMock(return_value=owner)),
                    patch.object(main, "AsyncpgReviewRepository", side_effect=failure),
                ):
                    with self.assertRaises(type(failure)):
                        async with main._lifespan(SimpleNamespace(state=SimpleNamespace())):
                            pass
                owner.close.assert_awaited_once_with()


if HTTP_DEPENDENCIES_AVAILABLE:
    class FakeService:
        def __init__(self):
            self.calls = 0
            self.failure = None
            self.request = None
            self.scope = None
            self.result = ImportResult(
                identifier(), identifier(), identifier(), identifier(), identifier(), "a" * 64, False
            )

        async def import_project(self, request, scope):
            self.calls += 1
            self.request = request
            self.scope = scope
            self.source_bytes = request.source.source_path.read_bytes()
            self.proposal_bytes = request.proposal.source_path.read_bytes()
            if self.failure:
                raise self.failure
            return self.result


    class FakeProjectReader:
        def __init__(self):
            self.project_id = identifier()
            self.created_at = datetime(2026, 8, 10, 1, 2, tzinfo=timezone.utc)
            self.approved_at = datetime(2026, 8, 10, 1, 1, tzinfo=timezone.utc)
            self.list_scope = None
            self.get_scope = None
            self.limit = None
            self.requested_project_id = None
            self.list_calls = 0
            self.failure = None
            self.summary = ProjectSummary(
                self.project_id, "Synthetic launch", "planning", 3, self.created_at
            )
            self.detail = ProjectDetail(
                self.project_id,
                "Synthetic launch",
                "planning",
                self.created_at,
                ProjectFileView(identifier(), identifier(), "brief.md", "text/markdown", 10),
                ApprovedProposalView(
                    identifier(), identifier(), "proposal.md", "text/markdown", 20,
                    "ab" * 32, 3, "approved", self.approved_at,
                ),
            )

        async def list_projects(self, scope, *, limit):
            self.list_calls += 1
            self.list_scope = scope
            self.limit = limit
            if self.failure:
                raise self.failure
            return (self.summary,)

        async def get_project(self, scope, project_id):
            self.get_scope = scope
            self.requested_project_id = project_id
            if self.failure:
                raise self.failure
            return self.detail


    class CancellingUpload:
        def __init__(self):
            self.calls = 0

        async def read(self, _):
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise asyncio.CancelledError


def valid_parts(*, source=b"# source\n", proposal=b"# approved proposal\n"):
    return [
        ("projectName", (None, "Synthetic launch")),
        ("proposalVersion", (None, "3")),
        ("approvalConfirmed", (None, "true")),
        ("sourceFile", ("../../brief.md", source, "text/markdown")),
        ("proposalFile", ("proposal.md", proposal, "text/markdown")),
    ]


def encode_multipart(parts):
    boundary = "marketops-wp4-boundary"
    chunks = []
    for name, value in parts:
        filename = value[0]
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        chunks.append((disposition + "\r\n").encode("utf-8"))
        if filename is not None:
            chunks.append(f"Content-Type: {value[2]}\r\n".encode("ascii"))
        chunks.append(b"\r\n")
        content = value[1]
        chunks.append(content if isinstance(content, bytes) else content.encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


@dataclass
class AsgiResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def json(self):
        return json.loads(self.body)

    @property
    def text(self):
        return self.body.decode("utf-8")


async def asgi_post(app, headers, body):
    messages = []
    delivered = False

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
            "method": "POST",
            "scheme": "http",
            "path": "/v1/project-imports",
            "raw_path": b"/v1/project-imports",
            "query_string": b"",
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
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        name.decode("latin-1"): value.decode("latin-1")
        for name, value in start["headers"]
    }
    return AsgiResponse(start["status"], response_headers, response_body)


async def asgi_get(app, target, headers):
    messages = []
    delivered = False
    parsed = urlsplit(target)

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": parsed.path,
            "raw_path": parsed.path.encode("ascii"),
            "query_string": parsed.query.encode("ascii"),
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
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        name.decode("latin-1"): value.decode("latin-1")
        for name, value in start["headers"]
    }
    return AsgiResponse(start["status"], response_headers, response_body)


async def asgi_post_chunked(app, headers, body, *, chunk_size=1024 * 1024):
    messages = []
    offset = 0

    async def receive():
        nonlocal offset
        if offset >= len(body):
            return {"type": "http.disconnect"}
        end = min(offset + chunk_size, len(body))
        chunk = body[offset:end]
        offset = end
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": offset < len(body),
        }

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/project-imports",
            "raw_path": b"/v1/project-imports",
            "query_string": b"",
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
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        name.decode("latin-1"): value.decode("latin-1")
        for name, value in start["headers"]
    }
    return AsgiResponse(start["status"], response_headers, response_body), offset


if __name__ == "__main__":
    unittest.main()
