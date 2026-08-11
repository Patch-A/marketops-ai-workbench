from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation
from apps.api.marketops_review.postgres import AsyncpgReviewRepository
from apps.api.marketops_review.service import (
    CreateReviewRunRequest,
    ReviewFailure,
    ReviewRequest,
    ReviewScopeContext,
    ReviewService,
)


ADMIN_DSN = os.environ.get("MARKETOPS_TEST_ADMIN_DATABASE_URL", "")
APP_DSN = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "")
REQUIRE = os.environ.get("MARKETOPS_REQUIRE_TEST_PREREQUISITES") == "1"


@unittest.skipUnless(
    ADMIN_DSN and APP_DSN,
    "PostgreSQL admin/application test URLs are required",
)
class ReviewReadPostgresRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import asyncpg
        except ImportError as error:
            if REQUIRE:
                self.fail(f"asyncpg prerequisite is required: {error}")
            self.skipTest("asyncpg is unavailable")
        self.organization_id = str(uuid4())
        self.workspace_id = str(uuid4())
        self.client_id = str(uuid4())
        self.project_id = str(uuid4())
        self.actor_id = str(uuid4())
        self.artifact_id = str(uuid4())
        self.version_id = str(uuid4())
        self.source_hash = "a" * 64
        self.scope = ReviewScopeContext(
            self.organization_id,
            self.workspace_id,
            self.client_id,
            self.actor_id,
        )
        self.admin = await asyncpg.connect(ADMIN_DSN)
        self.pool = await asyncpg.create_pool(dsn=APP_DSN, min_size=1, max_size=2)
        await self._seed_project()

    async def asyncTearDown(self):
        if hasattr(self, "pool"):
            await self.pool.close()
        if hasattr(self, "admin"):
            try:
                await self.admin.execute("SET session_replication_role = replica")
                for table, column, value in (
                    ("audit_events", "project_id", self.project_id),
                    ("review_decisions", "project_id", self.project_id),
                    ("review_snapshot_items", "project_id", self.project_id),
                    ("review_snapshots", "project_id", self.project_id),
                    ("extraction_candidates", "project_id", self.project_id),
                    ("extraction_runs", "project_id", self.project_id),
                    ("artifact_versions", "project_id", self.project_id),
                    ("artifacts", "project_id", self.project_id),
                    ("projects", "id", self.project_id),
                    ("clients", "id", self.client_id),
                    ("workspaces", "id", self.workspace_id),
                    ("organizations", "id", self.organization_id),
                ):
                    await self.admin.execute(
                        f"DELETE FROM marketops.{table} WHERE {column} = $1", value
                    )
            finally:
                await self.admin.execute("SET session_replication_role = origin")
                await self.admin.close()

    async def _seed_project(self):
        now = datetime.now(timezone.utc)
        async with self.admin.transaction():
            await self.admin.execute(
                "INSERT INTO marketops.organizations (id, name) VALUES ($1, 'Review read runtime')",
                self.organization_id,
            )
            await self.admin.execute(
                "INSERT INTO marketops.workspaces (id, organization_id, name, kind) VALUES ($1, $2, 'Review read runtime', 'personal')",
                self.workspace_id,
                self.organization_id,
            )
            await self.admin.execute(
                "INSERT INTO marketops.clients (id, organization_id, workspace_id, name) VALUES ($1, $2, $3, 'Review read runtime')",
                self.client_id,
                self.organization_id,
                self.workspace_id,
            )
            await self.admin.execute(
                """
                INSERT INTO marketops.projects (
                    id, organization_id, workspace_id, client_id, name,
                    import_request_id, import_manifest_sha256,
                    approved_proposal_artifact_id, approved_proposal_version_id,
                    approved_proposal_number, created_by
                ) VALUES ($1, $2, $3, $4, 'Review read runtime', $5, $6, $7, $8, 1, $9)
                """,
                self.project_id,
                self.organization_id,
                self.workspace_id,
                self.client_id,
                f"review-read-runtime-{self.project_id}",
                bytes.fromhex("b" * 64),
                self.artifact_id,
                self.version_id,
                self.actor_id,
            )
            await self.admin.execute(
                """
                INSERT INTO marketops.artifacts (
                    id, organization_id, workspace_id, client_id, project_id,
                    kind, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'proposal', $6)
                """,
                self.artifact_id,
                self.organization_id,
                self.workspace_id,
                self.client_id,
                self.project_id,
                self.actor_id,
            )
            await self.admin.execute(
                """
                INSERT INTO marketops.artifact_versions (
                    id, organization_id, workspace_id, client_id, project_id,
                    artifact_id, proposal_version, original_filename, media_type,
                    byte_size, storage_key, sha256, approval_status,
                    approved_by, approved_at, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, 1, 'proposal.md', 'text/markdown',
                    1, $7, $8, 'approved', $9, $10, $9
                )
                """,
                self.version_id,
                self.organization_id,
                self.workspace_id,
                self.client_id,
                self.project_id,
                self.artifact_id,
                f"review-read-runtime/{self.version_id}",
                bytes.fromhex(self.source_hash),
                self.actor_id,
                now,
            )

    def candidate(self, kind, text, line):
        return Candidate(
            candidate_id=str(uuid4()),
            kind=kind,
            text=text,
            classification="fact",
            confidence=0.95,
            source_citation=SourceCitation(
                source_version_id=self.version_id,
                source_sha256=self.source_hash,
                location=SourceLocation.from_mapping(
                    {"kind": "line_range", "startLine": line, "endLine": line}
                ),
                section_path=("Runtime",),
                quote=text,
            ),
        )

    def service(self):
        return ReviewService(
            repository=AsyncpgReviewRepository(self.pool),
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )

    async def _counts(self):
        return tuple(
            await self.admin.fetchrow(
                """
                SELECT
                    (SELECT count(*) FROM marketops.review_snapshots WHERE project_id = $1),
                    (SELECT count(*) FROM marketops.review_snapshot_items WHERE project_id = $1),
                    (SELECT count(*) FROM marketops.review_decisions WHERE project_id = $1),
                    (SELECT count(*) FROM marketops.audit_events WHERE project_id = $1)
                """,
                self.project_id,
            )
        )

    async def test_latest_and_as_of_reads_preserve_decision_history_without_writes(self):
        first = self.candidate("deliverable", "Revise the runtime launch brief.", 1)
        second = self.candidate("milestone", "Approve the runtime media plan.", 2)
        service = self.service()
        created = await service.create_run(
            CreateReviewRunRequest(
                self.project_id,
                self.artifact_id,
                self.version_id,
                1,
                self.source_hash,
                (first, second),
            ),
            self.scope,
        )
        await service.review_candidate(
            self.project_id,
            created.run.run_id,
            ReviewRequest(
                1,
                first.candidate_id,
                "modify",
                "Clarify the launch promise",
                "Keep the approved qualifier",
                "Revised runtime brief",
            ),
            self.scope,
        )
        await service.review_candidate(
            self.project_id,
            created.run.run_id,
            ReviewRequest(2, second.candidate_id, "approve", "Media plan is ready"),
            self.scope,
        )
        before = await self._counts()

        summaries = await service.list_runs(self.project_id, self.scope)
        latest = await service.read_review(
            self.project_id, created.run.run_id, self.scope
        )
        historical = await service.read_review(
            self.project_id, created.run.run_id, self.scope, review_version=2
        )
        after = await self._counts()

        self.assertEqual(summaries[0].latest_review_version, 3)
        self.assertEqual(latest.selected_decision.review_version, 3)
        self.assertEqual(latest.candidates[0].last_decision.review_version, 2)
        self.assertEqual(
            latest.candidates[0].last_decision.reason,
            "Clarify the launch promise",
        )
        self.assertEqual(latest.candidates[1].last_decision.review_version, 3)
        self.assertEqual(historical.snapshot.version, 2)
        self.assertEqual(historical.selected_decision.review_version, 2)
        self.assertIsNone(historical.candidates[1].last_decision)
        self.assertEqual(before, after)

        invisible_cases = (
            ReviewScopeContext(
                self.organization_id,
                self.workspace_id,
                self.client_id,
                str(uuid4()),
            ),
            ReviewScopeContext(
                self.organization_id,
                str(uuid4()),
                self.client_id,
                self.actor_id,
            ),
            ReviewScopeContext(
                self.organization_id,
                self.workspace_id,
                str(uuid4()),
                self.actor_id,
            ),
            ReviewScopeContext(
                str(uuid4()),
                self.workspace_id,
                self.client_id,
                self.actor_id,
            ),
        )
        for invisible_scope in invisible_cases:
            with self.subTest(scope=invisible_scope):
                self.assertEqual(
                    await service.list_runs(self.project_id, invisible_scope), ()
                )
                with self.assertRaises(ReviewFailure) as hidden:
                    await service.read_review(
                        self.project_id, created.run.run_id, invisible_scope
                    )
                self.assertEqual(hidden.exception.code, "REVIEW_NOT_FOUND")

        missing_project = str(uuid4())
        self.assertEqual(await service.list_runs(missing_project, self.scope), ())
        with self.assertRaises(ReviewFailure) as hidden:
            await service.read_review(
                missing_project, created.run.run_id, self.scope
            )
        self.assertEqual(hidden.exception.code, "REVIEW_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
