from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_import.postgres import AsyncpgImportRepository
from apps.api.marketops_import.service import (
    FilePayload,
    ImportRequest,
    ProjectImportService,
    ScopeContext,
)
from apps.api.marketops_import.storage import LocalObjectStore
from apps.api.marketops_review.postgres import AsyncpgReviewRepository
from apps.api.marketops_review.preparation import (
    ApprovedProposalPreparationService,
    AsyncpgApprovedProposalSourceReader,
    PreparationRequest,
)
from apps.api.marketops_review.service import ReviewScopeContext, ReviewService
DATABASE_URL = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "").strip()
ADMIN_DATABASE_URL = os.environ.get(
    "MARKETOPS_TEST_ADMIN_DATABASE_URL", ""
).strip()
ASYNCPG_AVAILABLE = importlib.util.find_spec("asyncpg") is not None
HTTP_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ("fastapi", "multipart")
)
RUNTIME_DEPENDENCIES_AVAILABLE = ASYNCPG_AVAILABLE and HTTP_DEPENDENCIES_AVAILABLE
REQUIRE_PREREQUISITES = os.environ.get("MARKETOPS_REQUIRE_TEST_PREREQUISITES") == "1"
if REQUIRE_PREREQUISITES and not (
    DATABASE_URL and ADMIN_DATABASE_URL and RUNTIME_DEPENDENCIES_AVAILABLE
):
    raise RuntimeError(
        "PostgreSQL test prerequisites are required: provide both test database URLs "
        "and install asyncpg, FastAPI, and python-multipart"
    )

if RUNTIME_DEPENDENCIES_AVAILABLE:
    import asyncpg

    from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
    from apps.api.tests.test_m1_02_review_http import asgi_request


def identifier() -> str:
    return str(uuid4())


@unittest.skipUnless(
    DATABASE_URL and ADMIN_DATABASE_URL and RUNTIME_DEPENDENCIES_AVAILABLE,
    "PostgreSQL admin/application URLs and runtime dependencies are required",
)
class ReviewPreparationPostgresRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.object_store = LocalObjectStore(
            Path(self.temporary.name) / "objects"
        )
        self.source_path = Path(self.temporary.name) / "source.md"
        self.proposal_path = Path(self.temporary.name) / "approved.md"
        self.source_path.write_bytes(b"# Source\nSynthetic runtime context.\n")
        self.proposal_bytes = (
            b"# Deliverables\n- Registration page\n\n"
            b"# Constraints\n- Capacity is limited to 360\n"
        )
        self.proposal_path.write_bytes(self.proposal_bytes)

        self.organization_id = identifier()
        self.workspace_id = identifier()
        self.client_id = identifier()
        self.actor_id = identifier()
        self.admin = await asyncpg.connect(ADMIN_DATABASE_URL)
        bypass = await self.admin.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if not bypass:
            self.fail(
                "MARKETOPS_TEST_ADMIN_DATABASE_URL must use a test-only RLS-bypass role"
            )
        await self._seed_scope()
        self.pool = await asyncpg.create_pool(
            dsn=DATABASE_URL, min_size=1, max_size=4
        )

    async def asyncTearDown(self):
        if hasattr(self, "pool"):
            await self.pool.close()
        if hasattr(self, "admin"):
            await self.admin.execute("SET session_replication_role = replica")
            try:
                for table in (
                    "audit_events",
                    "review_decisions",
                    "review_snapshot_items",
                    "review_snapshots",
                    "extraction_candidates",
                    "extraction_run_requests",
                    "extraction_runs",
                    "artifact_versions",
                    "artifacts",
                    "projects",
                    "clients",
                    "workspaces",
                    "organizations",
                ):
                    if table == "organizations":
                        sql = "DELETE FROM marketops.organizations WHERE id = $1"
                    else:
                        sql = (
                            f"DELETE FROM marketops.{table} "
                            "WHERE organization_id = $1"
                        )
                    await self.admin.execute(sql, self.organization_id)
            finally:
                await self.admin.execute("SET session_replication_role = origin")
                await self.admin.close()

    async def _seed_scope(self) -> None:
        async with self.admin.transaction():
            await self.admin.execute(
                "INSERT INTO marketops.organizations (id, name) "
                "VALUES ($1, 'Preparation runtime')",
                self.organization_id,
            )
            await self.admin.execute(
                """
                INSERT INTO marketops.workspaces (
                    id, organization_id, name, kind
                ) VALUES ($1, $2, 'Preparation runtime', 'personal')
                """,
                self.workspace_id,
                self.organization_id,
            )
            await self.admin.execute(
                """
                INSERT INTO marketops.clients (
                    id, organization_id, workspace_id, name
                ) VALUES ($1, $2, $3, 'Preparation runtime')
                """,
                self.client_id,
                self.organization_id,
                self.workspace_id,
            )

    async def _import_approved_proposal(self):
        scope = ScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        service = ProjectImportService(
            object_store=self.object_store,
            repository=AsyncpgImportRepository(self.pool),
            id_factory=identifier,
            clock=lambda: datetime.now(timezone.utc),
        )
        result = await service.import_project(
            ImportRequest(
                idempotency_key=f"preparation-runtime-{identifier()}",
                project_name="Preparation runtime project",
                proposal_version=1,
                approval_confirmed=True,
                source=FilePayload(
                    "source.md", "text/markdown", self.source_path
                ),
                proposal=FilePayload(
                    "approved.md", "text/markdown", self.proposal_path
                ),
            ),
            scope,
        )
        return result

    async def test_import_to_verified_preparation_persists_source_bound_review(self):
        imported = await self._import_approved_proposal()
        review_scope = ReviewScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        source_reader = AsyncpgApprovedProposalSourceReader(self.pool)
        service = ApprovedProposalPreparationService(
            source_reader=source_reader,
            object_store=self.object_store,
            review_service=ReviewService(
                repository=AsyncpgReviewRepository(self.pool),
                id_factory=identifier,
                clock=lambda: datetime.now(timezone.utc),
            ),
        )
        proposal_hash = hashlib.sha256(self.proposal_bytes).hexdigest()
        prepared = await service.prepare_and_create_run(
            PreparationRequest(
                project_id=imported.project_id,
                expected_proposal_version_id=imported.proposal_version_id,
                expected_proposal_sha256=proposal_hash,
            ),
            review_scope,
        )

        self.assertEqual(prepared.source.project_id, imported.project_id)
        self.assertEqual(
            prepared.source.artifact_id, imported.proposal_artifact_id
        )
        self.assertEqual(prepared.source.version_id, imported.proposal_version_id)
        self.assertEqual(prepared.source.sha256, proposal_hash)
        self.assertEqual(len(prepared.candidates), 2)
        self.assertTrue(
            all(
                candidate.source_citation.source_version_id
                == imported.proposal_version_id
                and candidate.source_citation.source_sha256 == proposal_hash
                for candidate in prepared.candidates
            )
        )

        run_id = prepared.review_result.run.run_id
        run = await self.admin.fetchrow(
            """
            SELECT
                proposal_artifact_id,
                proposal_version_id,
                proposal_version,
                encode(proposal_sha256, 'hex') AS proposal_sha256,
                candidate_count,
                created_by
            FROM marketops.extraction_runs
            WHERE id = $1 AND project_id = $2
            """,
            run_id,
            imported.project_id,
        )
        self.assertIsNotNone(run)
        self.assertEqual(str(run["proposal_artifact_id"]), imported.proposal_artifact_id)
        self.assertEqual(str(run["proposal_version_id"]), imported.proposal_version_id)
        self.assertEqual(run["proposal_version"], 1)
        self.assertEqual(run["proposal_sha256"], proposal_hash)
        self.assertEqual(run["candidate_count"], 2)
        self.assertEqual(str(run["created_by"]), self.actor_id)

        facts = await self.admin.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM marketops.extraction_candidates
               WHERE run_id = $1) AS candidate_count,
              (SELECT count(*) FROM marketops.extraction_candidates
               WHERE run_id = $1
                 AND source_version_id = $2
                 AND source_sha256 = $3
                 AND review_status = 'pending') AS bound_pending_candidates,
              (SELECT count(*) FROM marketops.review_snapshots
               WHERE run_id = $1 AND review_version = 1) AS snapshot_count,
              (SELECT count(*) FROM marketops.review_snapshot_items
               WHERE run_id = $1 AND status = 'pending') AS pending_items
            """,
            run_id,
            imported.proposal_version_id,
            bytes.fromhex(proposal_hash),
        )
        self.assertEqual(
            dict(facts),
            {
                "candidate_count": 2,
                "bound_pending_candidates": 2,
                "snapshot_count": 1,
                "pending_items": 2,
            },
        )

        invisible_cases = (
            (
                ReviewScopeContext(
                    self.organization_id,
                    self.workspace_id,
                    self.client_id,
                    identifier(),
                ),
                imported.project_id,
            ),
            (
                ReviewScopeContext(
                    self.organization_id,
                    identifier(),
                    self.client_id,
                    self.actor_id,
                ),
                imported.project_id,
            ),
            (
                ReviewScopeContext(
                    self.organization_id,
                    self.workspace_id,
                    identifier(),
                    self.actor_id,
                ),
                imported.project_id,
            ),
            (review_scope, identifier()),
        )
        for scope, project_id in invisible_cases:
            with self.subTest(scope=scope, project_id=project_id):
                self.assertIsNone(
                    await source_reader.read_approved_proposal(scope, project_id)
                )

    async def test_top_level_semantic_table_round_trips_empty_section_path(self):
        self.proposal_bytes = (
            b"| Deliverable | Owner |\n"
            b"| --- | --- |\n"
            b"| Registration page | Marketing |\n"
        )
        self.proposal_path.write_bytes(self.proposal_bytes)
        imported = await self._import_approved_proposal()
        review_scope = ReviewScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        review_service = ReviewService(
            repository=AsyncpgReviewRepository(self.pool),
            id_factory=identifier,
            clock=lambda: datetime.now(timezone.utc),
        )
        prepared = await ApprovedProposalPreparationService(
            source_reader=AsyncpgApprovedProposalSourceReader(self.pool),
            object_store=self.object_store,
            review_service=review_service,
        ).prepare_and_create_run(
            PreparationRequest(
                project_id=imported.project_id,
                expected_proposal_version_id=imported.proposal_version_id,
                expected_proposal_sha256=hashlib.sha256(
                    self.proposal_bytes
                ).hexdigest(),
            ),
            review_scope,
        )

        read_model = await review_service.read_review(
            imported.project_id,
            prepared.review_result.run.run_id,
            review_scope,
        )

        self.assertEqual(len(read_model.candidates), 1)
        self.assertEqual(
            read_model.candidates[0].candidate.source_citation.section_path,
            (),
        )

    async def test_http_create_list_detail_and_decision_use_postgres_fact_source(self):
        imported = await self._import_approved_proposal()
        import_scope = ScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        review_scope = ReviewScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        review_service = ReviewService(
            repository=AsyncpgReviewRepository(self.pool),
            id_factory=identifier,
            clock=lambda: datetime.now(timezone.utc),
        )
        preparation = ApprovedProposalPreparationService(
            source_reader=AsyncpgApprovedProposalSourceReader(self.pool),
            object_store=self.object_store,
            review_service=review_service,
        )
        app = create_app(
            authenticator=StaticBearerAuthenticator("runtime-token", import_scope),
            review_preparation=preparation,
            review_service=review_service,
            request_id_factory=lambda: "runtime-request",
        )
        headers = [
            ("Authorization", "Bearer runtime-token"),
            ("Content-Type", "application/json"),
        ]
        proposal_hash = hashlib.sha256(self.proposal_bytes).hexdigest()
        created = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{imported.project_id}/extraction-runs",
            [*headers, ("Idempotency-Key", "runtime-http-review")],
            json.dumps(
                {
                    "expectedProposalVersionId": imported.proposal_version_id,
                    "expectedProposalSha256": proposal_hash,
                }
            ).encode(),
        )
        self.assertEqual(created.status_code, 201)
        run_id = created.json()["runId"]
        self.assertFalse(created.json()["replayed"])

        listed = await asgi_request(
            app,
            "GET",
            f"/v1/projects/{imported.project_id}/extraction-runs",
            [("Authorization", "Bearer runtime-token")],
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["runs"][0]["runId"], run_id)

        detail = await asgi_request(
            app,
            "GET",
            f"/v1/projects/{imported.project_id}/extraction-runs/{run_id}",
            [("Authorization", "Bearer runtime-token")],
        )
        self.assertEqual(detail.status_code, 200)
        candidate_id = detail.json()["candidates"][0]["candidateId"]
        self.assertEqual(
            detail.json()["candidates"][0]["sourceCitation"]["sourceSha256"],
            proposal_hash,
        )

        decided = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{imported.project_id}/extraction-runs/{run_id}/decisions",
            headers,
            json.dumps(
                {
                    "expectedReviewVersion": 1,
                    "candidateId": candidate_id,
                    "action": "approve",
                    "reason": "PostgreSQL HTTP runtime evidence",
                }
            ).encode(),
        )
        self.assertEqual(decided.status_code, 201)
        self.assertEqual(decided.json()["reviewVersion"], 2)
        self.assertTrue(
            all(
                response.headers["cache-control"] == "no-store"
                for response in (created, listed, detail, decided)
            )
        )


if __name__ == "__main__":
    unittest.main()
