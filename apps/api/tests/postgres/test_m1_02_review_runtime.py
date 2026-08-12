from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation
from apps.api.marketops_review import (
    AsyncpgReviewRepository,
    CreateReviewRunRequest,
    ReviewFailure,
    ReviewRequest,
    ReviewScopeContext,
    ReviewService,
)


ADMIN_DSN = os.environ.get("MARKETOPS_TEST_ADMIN_DATABASE_URL", "")
APP_DSN = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "")
REQUIRE = os.environ.get("MARKETOPS_REQUIRE_TEST_PREREQUISITES") == "1"


class FailingDecisionRepository:
    def __init__(self, delegate):
        self.delegate = delegate

    @asynccontextmanager
    async def transaction(self, scope, project_id):
        async with self.delegate.transaction(scope, project_id) as transaction:
            yield FailingDecisionTransaction(transaction)


class FailingDecisionTransaction:
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def append_decision(self, decision):
        raise RuntimeError("synthetic decision failure")


class FailingCreateRepository:
    def __init__(self, delegate):
        self.delegate = delegate

    @asynccontextmanager
    async def transaction(self, scope, project_id):
        async with self.delegate.transaction(scope, project_id) as transaction:
            yield FailingCreateTransaction(transaction)


class FailingCreateTransaction:
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def append_audit_event(self, event):
        raise RuntimeError("synthetic create failure")


@unittest.skipUnless(
    ADMIN_DSN and APP_DSN,
    "PostgreSQL admin/application test URLs are required",
)
class ReviewPostgresRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import asyncpg
        except ImportError as error:
            if REQUIRE:
                self.fail(f"asyncpg prerequisite is required: {error}")
            self.skipTest("asyncpg is unavailable")
        self.asyncpg = asyncpg
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
        self.pool = await asyncpg.create_pool(dsn=APP_DSN, min_size=1, max_size=4)
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
                    ("extraction_run_requests", "project_id", self.project_id),
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
        async with self.admin.transaction():
            await self.admin.execute(
                "INSERT INTO marketops.organizations (id, name) VALUES ($1, 'Review runtime')",
                self.organization_id,
            )
            await self.admin.execute(
                "INSERT INTO marketops.workspaces (id, organization_id, name, kind) VALUES ($1, $2, 'Review runtime', 'personal')",
                self.workspace_id,
                self.organization_id,
            )
            await self.admin.execute(
                "INSERT INTO marketops.clients (id, organization_id, workspace_id, name) VALUES ($1, $2, $3, 'Review runtime')",
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
                ) VALUES ($1, $2, $3, $4, 'Review runtime', $5, $6, $7, $8, 1, $9)
                """,
                self.project_id,
                self.organization_id,
                self.workspace_id,
                self.client_id,
                f"review-runtime-{self.project_id}",
                bytes.fromhex("b" * 64),
                self.artifact_id,
                self.version_id,
                self.actor_id,
            )
            await self.admin.execute(
                """
                INSERT INTO marketops.artifacts (
                    id, organization_id, workspace_id, client_id, project_id, kind, created_by
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
                f"review-runtime/{self.version_id}",
                bytes.fromhex(self.source_hash),
                self.actor_id,
                datetime.now(timezone.utc),
            )

    def candidate(self, *, kind="deliverable", text="Publish the runtime review brief."):
        candidate_id = str(uuid4())
        return Candidate(
            candidate_id=candidate_id,
            kind=kind,
            text=text,
            classification="fact",
            confidence=0.95,
            source_citation=SourceCitation(
                source_version_id=self.version_id,
                source_sha256=self.source_hash,
                location=SourceLocation.from_mapping(
                    {"kind": "line_range", "startLine": 1, "endLine": 1}
                ),
                section_path=("Runtime",),
                quote=text,
            ),
        )

    def service(self, repository=None):
        return ReviewService(
            repository=repository or AsyncpgReviewRepository(self.pool),
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )

    async def create_run(self, *, idempotency_key=None, repository=None):
        candidates = (
            self.candidate(),
            self.candidate(
                kind="milestone", text="Approve the runtime launch checkpoint."
            ),
        )
        created = await self.service(repository).create_run(
            CreateReviewRunRequest(
                self.project_id,
                self.artifact_id,
                self.version_id,
                1,
                self.source_hash,
                candidates,
                idempotency_key,
            ),
            self.scope,
        )
        return candidates, created

    async def _visible_counts(
        self, *, workspace_id, client_id, project_id, actor_id
    ):
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.workspace_id', $1, true)", workspace_id
                )
                await connection.execute(
                    "SELECT set_config('app.client_id', $1, true)", client_id
                )
                await connection.execute(
                    "SELECT set_config('app.project_id', $1, true)", project_id
                )
                await connection.execute(
                    "SELECT set_config('app.actor_id', $1, true)", actor_id
                )
                row = await connection.fetchrow(
                    """
                    SELECT
                      (SELECT count(*) FROM marketops.extraction_runs WHERE project_id = $1) AS extraction_runs,
                      (SELECT count(*) FROM marketops.extraction_candidates WHERE project_id = $1) AS extraction_candidates,
                      (SELECT count(*) FROM marketops.review_snapshots WHERE project_id = $1) AS review_snapshots,
                      (SELECT count(*) FROM marketops.review_snapshot_items WHERE project_id = $1) AS review_snapshot_items,
                      (SELECT count(*) FROM marketops.review_decisions WHERE project_id = $1) AS review_decisions
                      ,(SELECT count(*) FROM marketops.extraction_run_requests WHERE project_id = $1) AS extraction_run_requests
                      ,(SELECT count(*) FROM marketops.audit_events WHERE project_id = $1) AS audit_events
                    """,
                    self.project_id,
                )
                return dict(row)

    async def test_forced_rls_hides_cross_scope_and_append_only_rows(self):
        candidates, created = await self.create_run()
        await self.service().review_candidate(
            self.project_id,
            created.run.run_id,
            ReviewRequest(
                1, candidates[0].candidate_id, "approve", "RLS runtime evidence"
            ),
            self.scope,
        )
        expected = {
            "extraction_runs": 1,
            "extraction_candidates": 2,
            "review_snapshots": 2,
            "review_snapshot_items": 4,
            "review_decisions": 1,
            "extraction_run_requests": 0,
            "audit_events": 2,
        }
        self.assertEqual(
            await self._visible_counts(
                workspace_id=self.workspace_id,
                client_id=self.client_id,
                project_id=self.project_id,
                actor_id=self.actor_id,
            ),
            expected,
        )
        hidden = {table: 0 for table in expected}
        scope_cases = (
            (str(uuid4()), self.client_id, self.project_id, self.actor_id),
            (self.workspace_id, str(uuid4()), self.project_id, self.actor_id),
            (self.workspace_id, self.client_id, str(uuid4()), self.actor_id),
            (self.workspace_id, self.client_id, self.project_id, ""),
        )
        for workspace_id, client_id, project_id, actor_id in scope_cases:
            with self.subTest(
                workspace_id=workspace_id,
                client_id=client_id,
                project_id=project_id,
                actor_id=actor_id,
            ):
                self.assertEqual(
                    await self._visible_counts(
                        workspace_id=workspace_id,
                        client_id=client_id,
                        project_id=project_id,
                        actor_id=actor_id,
                    ),
                    hidden,
                )
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.workspace_id', $1, true)", self.workspace_id
                )
                await connection.execute(
                    "SELECT set_config('app.client_id', $1, true)", self.client_id
                )
                await connection.execute(
                    "SELECT set_config('app.project_id', $1, true)", self.project_id
                )
                await connection.execute(
                    "SELECT set_config('app.actor_id', $1, true)", self.actor_id
                )
                with self.assertRaises(self.asyncpg.InsufficientPrivilegeError):
                    await connection.execute(
                        "UPDATE marketops.extraction_runs SET candidate_count = 2 WHERE id = $1",
                        created.run.run_id,
                    )
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.workspace_id', $1, true)", self.workspace_id
                )
                await connection.execute(
                    "SELECT set_config('app.client_id', $1, true)", self.client_id
                )
                await connection.execute(
                    "SELECT set_config('app.project_id', $1, true)", self.project_id
                )
                await connection.execute(
                    "SELECT set_config('app.actor_id', $1, true)", self.actor_id
                )
                with self.assertRaisesRegex(
                    self.asyncpg.ObjectNotInPrerequisiteStateError, "append-only"
                ):
                    await connection.execute(
                        "UPDATE marketops.extraction_runs SET created_by = created_by WHERE id = $1",
                        created.run.run_id,
                    )

    async def test_concurrent_same_version_has_one_winner_and_no_partial_rows(self):
        candidates, created = await self.create_run()
        candidate = candidates[0]
        untouched = candidates[1]

        async def decide(action):
            try:
                result = await self.service().review_candidate(
                    self.project_id,
                    created.run.run_id,
                    ReviewRequest(1, candidate.candidate_id, action, f"{action} runtime"),
                    self.scope,
                )
                return result.snapshot.version
            except ReviewFailure as failure:
                return failure.code

        results = await asyncio.gather(decide("approve"), decide("reject"))
        self.assertCountEqual(results, [2, "REVIEW_CONFLICT"])
        latest_items = await self.admin.fetch(
            """
            SELECT candidate_id, status
            FROM marketops.review_snapshot_items
            WHERE snapshot_id = (
                SELECT id FROM marketops.review_snapshots
                WHERE run_id = $1
                ORDER BY review_version DESC
                LIMIT 1
            )
            """,
            created.run.run_id,
        )
        statuses = {str(row["candidate_id"]): row["status"] for row in latest_items}
        self.assertIn(statuses[candidate.candidate_id], {"approve", "reject"})
        self.assertEqual(statuses[untouched.candidate_id], "pending")
        counts = await self.admin.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM marketops.review_snapshots WHERE run_id = $1) AS snapshots,
              (SELECT count(*) FROM marketops.review_snapshot_items WHERE run_id = $1) AS items,
              (SELECT count(*) FROM marketops.review_decisions WHERE run_id = $1) AS decisions,
              (SELECT count(*) FROM marketops.audit_events WHERE target_id = $1 OR event_data->>'runId' = $2) AS audits
            """,
            created.run.run_id,
            created.run.run_id,
        )
        self.assertEqual(dict(counts), {"snapshots": 2, "items": 4, "decisions": 1, "audits": 2})

    async def test_failure_after_snapshot_insert_rolls_back_snapshot_items_and_audit(self):
        candidates, created = await self.create_run()
        candidate = candidates[0]
        failing = FailingDecisionRepository(AsyncpgReviewRepository(self.pool))
        with self.assertRaises(ReviewFailure) as raised:
            await self.service(failing).review_candidate(
                self.project_id,
                created.run.run_id,
                ReviewRequest(1, candidate.candidate_id, "approve", "Rollback runtime"),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")
        counts = await self.admin.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM marketops.review_snapshots WHERE run_id = $1) AS snapshots,
              (SELECT count(*) FROM marketops.review_snapshot_items WHERE run_id = $1) AS items,
              (SELECT count(*) FROM marketops.review_decisions WHERE run_id = $1) AS decisions,
              (SELECT count(*) FROM marketops.audit_events WHERE target_id = $1 OR event_data->>'runId' = $2) AS audits
            """,
            created.run.run_id,
            created.run.run_id,
        )
        self.assertEqual(dict(counts), {"snapshots": 1, "items": 2, "decisions": 0, "audits": 1})

    async def test_persistent_idempotency_replays_one_run_and_conflicts_on_source(self):
        candidates, created = await self.create_run(idempotency_key="review-runtime-replay")
        replay = await self.service().create_run(
            CreateReviewRunRequest(
                self.project_id,
                self.artifact_id,
                self.version_id,
                1,
                self.source_hash,
                candidates,
                "review-runtime-replay",
            ),
            self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.run.run_id, created.run.run_id)

        request_count, run_count = await self.admin.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM marketops.extraction_run_requests WHERE project_id = $1),
              (SELECT count(*) FROM marketops.extraction_runs WHERE project_id = $1)
            """,
            self.project_id,
        )
        self.assertEqual((request_count, run_count), (1, 1))

        with self.assertRaises(ReviewFailure) as raised:
            await self.service().replay_run_request(
                self.project_id,
                "review-runtime-replay",
                self.version_id,
                "b" * 64,
                self.scope,
            )
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_CONFLICT")

    async def test_concurrent_same_key_has_one_request_and_one_run(self):
        candidates = (self.candidate(),)

        async def create():
            return await self.service().create_run(
                CreateReviewRunRequest(
                    self.project_id,
                    self.artifact_id,
                    self.version_id,
                    1,
                    self.source_hash,
                    candidates,
                    "review-runtime-concurrent",
                ),
                self.scope,
            )

        results = await asyncio.gather(create(), create())
        self.assertEqual(sum(result.replayed for result in results), 1)
        self.assertEqual(len({result.run.run_id for result in results}), 1)
        counts = await self.admin.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM marketops.extraction_run_requests WHERE project_id = $1) AS requests,
              (SELECT count(*) FROM marketops.extraction_runs WHERE project_id = $1) AS runs
            """,
            self.project_id,
        )
        self.assertEqual(dict(counts), {"requests": 1, "runs": 1})

    async def test_failed_create_leaves_no_request_placeholder(self):
        failing = FailingCreateRepository(AsyncpgReviewRepository(self.pool))
        with self.assertRaises(ReviewFailure):
            await self.create_run(
                idempotency_key="review-runtime-rollback",
                repository=failing,
            )
        counts = await self.admin.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM marketops.extraction_run_requests WHERE project_id = $1) AS requests,
              (SELECT count(*) FROM marketops.extraction_runs WHERE project_id = $1) AS runs
            """,
            self.project_id,
        )
        self.assertEqual(dict(counts), {"requests": 0, "runs": 0})

    async def test_other_actor_cannot_read_request_run_or_append_decision(self):
        candidates, created = await self.create_run(idempotency_key="review-runtime-actor")
        other_actor = str(uuid4())
        hidden = await self._visible_counts(
            workspace_id=self.workspace_id,
            client_id=self.client_id,
            project_id=self.project_id,
            actor_id=other_actor,
        )
        self.assertEqual(hidden, {table: 0 for table in hidden})
        other_scope = ReviewScopeContext(
            self.organization_id, self.workspace_id, self.client_id, other_actor
        )
        with self.assertRaises(ReviewFailure) as raised:
            await self.service().review_candidate(
                self.project_id,
                created.run.run_id,
                ReviewRequest(1, candidates[0].candidate_id, "approve", "wrong actor"),
                other_scope,
            )
        self.assertEqual(raised.exception.code, "REVIEW_NOT_FOUND")

    async def test_integrity_trigger_rejects_request_source_mismatch(self):
        candidates, created = await self.create_run()
        with self.assertRaisesRegex(
            self.asyncpg.CheckViolationError,
            "extraction run request differs from its review run",
        ):
            async with self.admin.transaction():
                await self.admin.execute(
                    """
                    INSERT INTO marketops.extraction_run_requests (
                        id, organization_id, workspace_id, client_id, project_id,
                        idempotency_key, expected_proposal_version_id,
                        expected_proposal_sha256, run_id, created_by, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, now())
                    """,
                    str(uuid4()),
                    self.organization_id,
                    self.workspace_id,
                    self.client_id,
                    self.project_id,
                    "review-runtime-mismatch",
                    self.version_id,
                    bytes.fromhex("b" * 64),
                    created.run.run_id,
                    self.actor_id,
                )


if __name__ == "__main__":
    unittest.main()
