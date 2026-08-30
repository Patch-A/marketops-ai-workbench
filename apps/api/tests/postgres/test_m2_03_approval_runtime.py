from __future__ import annotations

import asyncio
import importlib.util
import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

RUNTIME_DEPENDENCIES_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("asyncpg", "fastapi", "multipart"))
if RUNTIME_DEPENDENCIES_AVAILABLE:
    from asyncpg import CheckViolationError, ForeignKeyViolationError

from apps.api.marketops_learning import LearningFailure
from apps.api.marketops_learning.approval import ApprovalDecisionRequest
from apps.api.marketops_learning.approval_postgres import AsyncpgKnowledgeApprovalRepository
from apps.api.tests.postgres.test_m2_02_learning_runtime import LearningPostgresRuntimeTests


ADMIN_DSN = os.environ.get("MARKETOPS_TEST_ADMIN_DATABASE_URL", "").strip()
APP_DSN = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(ADMIN_DSN and APP_DSN and RUNTIME_DEPENDENCIES_AVAILABLE, "PostgreSQL admin/application URLs and runtime dependencies are required")
class KnowledgeApprovalPostgresRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = LearningPostgresRuntimeTests("runTest")
        await self.fixture.asyncSetUp()
        outcomes, retrospectives = self.fixture.feedback()
        capsule = await self.fixture.service.finalize_capsule(
            self.fixture.project_id, outcomes, retrospectives, self.fixture.scope
        )
        self.source_project_id = self.fixture.project_id
        self.knowledge_id = capsule.capsule.knowledge_items[0].knowledge_id
        self.target_project_id = str(uuid4())
        self.target_artifact_id = str(uuid4())
        self.target_version_id = str(uuid4())
        self.additional_target_project_ids: list[str] = []
        self.additional_client_ids: list[str] = []
        self.additional_workspace_ids: list[str] = []
        await self._seed_target_project()
        self.repository = AsyncpgKnowledgeApprovalRepository(
            self.fixture.pool,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime(2026, 8, 16, 13, 0, tzinfo=timezone.utc),
        )

    async def asyncTearDown(self) -> None:
        if hasattr(self, "fixture"):
            await self.fixture.admin.execute("SET session_replication_role = replica")
            try:
                for table in ("knowledge_promotion_versions", "knowledge_promotion_roots"):
                    await self.fixture.admin.execute(
                        f"DELETE FROM marketops.{table} WHERE project_id = $1",
                        self.source_project_id,
                    )
                for table in (
                    "audit_events",
                    "knowledge_citations",
                    "artifact_versions",
                    "artifacts",
                    "projects",
                ):
                    for project_id in (self.target_project_id, *self.additional_target_project_ids):
                        await self.fixture.admin.execute(
                            f"DELETE FROM marketops.{table} WHERE project_id = $1"
                            if table not in {"projects"}
                            else "DELETE FROM marketops.projects WHERE id = $1",
                            project_id,
                        )
                for client_id in self.additional_client_ids:
                    await self.fixture.admin.execute(
                        "DELETE FROM marketops.clients WHERE id = $1", client_id
                    )
                for workspace_id in self.additional_workspace_ids:
                    await self.fixture.admin.execute(
                        "DELETE FROM marketops.workspaces WHERE id = $1", workspace_id
                    )
            finally:
                await self.fixture.admin.execute("SET session_replication_role = origin")
            await self.fixture.asyncTearDown()

    async def _seed_target_project(
        self,
        *,
        project_id: str | None = None,
        artifact_id: str | None = None,
        version_id: str | None = None,
        workspace_id: str | None = None,
        client_id: str | None = None,
        created_by: str | None = None,
    ) -> tuple[str, str, str]:
        fixture = self.fixture.fixture
        project_id = project_id or self.target_project_id
        artifact_id = artifact_id or self.target_artifact_id
        version_id = version_id or self.target_version_id
        workspace_id = workspace_id or fixture.workspace_id
        client_id = client_id or fixture.client_id
        created_by = created_by or fixture.actor_id
        async with fixture.admin.transaction():
            await fixture.admin.execute(
            """
            INSERT INTO marketops.projects (
                id, organization_id, workspace_id, client_id, name,
                import_request_id, import_manifest_sha256,
                approved_proposal_artifact_id, approved_proposal_version_id,
                approved_proposal_number, created_by
            ) VALUES ($1, $2, $3, $4, 'Knowledge target', $5, $6, $7, $8, 1, $9)
            """,
            project_id,
            fixture.organization_id,
            workspace_id,
            client_id,
            f"knowledge-target-{project_id}",
            bytes.fromhex("c" * 64),
            artifact_id,
            version_id,
            created_by,
            )
            await fixture.admin.execute(
                """
                INSERT INTO marketops.artifacts (
                    id, organization_id, workspace_id, client_id, project_id, kind, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'proposal', $6)
                """,
                artifact_id,
                fixture.organization_id,
                workspace_id,
                client_id,
                project_id,
                created_by,
            )
            await fixture.admin.execute(
                """
                INSERT INTO marketops.artifact_versions (
                    id, organization_id, workspace_id, client_id, project_id,
                    artifact_id, proposal_version, original_filename, media_type,
                    byte_size, storage_key, sha256, approval_status,
                    approved_by, approved_at, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, 1, 'target.md', 'text/markdown',
                    1, $7, $8, 'approved', $9, $10, $9
                )
                """,
                version_id,
                fixture.organization_id,
                workspace_id,
                client_id,
                project_id,
                artifact_id,
                f"knowledge-target/{version_id}",
                bytes.fromhex("d" * 64),
                created_by,
                datetime.now(timezone.utc),
            )
        return project_id, artifact_id, version_id

    async def _seed_foreign_target_project(
        self, *, workspace_id: str, client_id: str, created_by: str | None = None
    ) -> str:
        project_id = str(uuid4())
        self.additional_target_project_ids.append(project_id)
        fixture = self.fixture.fixture
        if workspace_id != fixture.workspace_id:
            self.additional_workspace_ids.append(workspace_id)
            await fixture.admin.execute(
                """
                INSERT INTO marketops.workspaces(id, organization_id, name, kind)
                VALUES ($1, $2, 'Foreign knowledge test workspace', 'team')
                """,
                workspace_id,
                fixture.organization_id,
            )
        if client_id != fixture.client_id:
            self.additional_client_ids.append(client_id)
            await fixture.admin.execute(
                """
                INSERT INTO marketops.clients(id, organization_id, workspace_id, name)
                VALUES ($1, $2, $3, 'Foreign knowledge test client')
                """,
                client_id,
                fixture.organization_id,
                workspace_id,
            )
        await self._seed_target_project(
            project_id=project_id,
            artifact_id=str(uuid4()),
            version_id=str(uuid4()),
            workspace_id=workspace_id,
            client_id=client_id,
            created_by=created_by,
        )
        return project_id

    async def test_explicit_same_client_elevation_cites_then_revocation_fails_closed(self) -> None:
        scope = self.fixture.scope
        approved = await self.repository.decide(
            scope,
            self.source_project_id,
            self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )
        self.assertEqual(approved.history.state.current.effective_scope, "project")
        elevated = await self.repository.decide(
            scope,
            self.source_project_id,
            self.knowledge_id,
            ApprovalDecisionRequest("elevate", 1, "client", reason="Same-client reuse."),
        )
        citation = await self.repository.cite_approved_knowledge(
            scope,
            source_project_id=self.source_project_id,
            source_knowledge_id=self.knowledge_id,
            target_project_id=self.target_project_id,
            reason="Use the approved rehearsal control.",
        )
        self.assertFalse(citation.replayed)
        self.assertEqual(citation.citation.authorization.promotion_version, 2)
        self.assertEqual(
            await self.fixture.admin.fetchval(
                "SELECT count(*) FROM marketops.knowledge_citations WHERE id = $1",
                citation.citation.citation_id,
            ),
            1,
        )
        self.assertEqual(
            [item.citation_id for item in await self.repository.list_effective_citations(scope, self.target_project_id)],
            [citation.citation.citation_id],
        )

        await self.repository.decide(
            scope,
            self.source_project_id,
            self.knowledge_id,
            ApprovalDecisionRequest("revoke", 2, reason="No longer approved."),
        )
        with self.assertRaisesRegex(LearningFailure, "not approved for citation"):
            await self.repository.cite_approved_knowledge(
                scope,
                source_project_id=self.source_project_id,
                source_knowledge_id=self.knowledge_id,
                target_project_id=self.target_project_id,
                reason="Attempt after revocation.",
            )
        self.assertEqual(
            await self.fixture.admin.fetchval(
                "SELECT count(*) FROM marketops.knowledge_citations WHERE project_id = $1",
                self.target_project_id,
            ),
            1,
        )
        self.assertEqual(
            await self.repository.list_effective_citations(scope, self.target_project_id),
            (),
        )

    async def test_project_scoped_approval_cannot_cite_to_a_sibling_project(self) -> None:
        scope = self.fixture.scope
        await self.repository.decide(
            scope,
            self.source_project_id,
            self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )

        with self.assertRaises(LearningFailure) as raised:
            await self.repository.cite_approved_knowledge(
                scope,
                source_project_id=self.source_project_id,
                source_knowledge_id=self.knowledge_id,
                target_project_id=self.target_project_id,
                reason="Attempt reuse before explicit client elevation.",
            )
        self.assertEqual(raised.exception.code, "KNOWLEDGE_SCOPE_DENIED")
        self.assertEqual(
            await self.repository.list_effective_citations(scope, self.target_project_id),
            (),
        )

    async def test_revision_hides_an_older_citation_from_effective_reads(self) -> None:
        scope = self.fixture.scope
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("elevate", 1, "client", reason="Same-client reuse."),
        )
        citation = await self.repository.cite_approved_knowledge(
            scope, source_project_id=self.source_project_id,
            source_knowledge_id=self.knowledge_id, target_project_id=self.target_project_id,
            reason="Apply the approved rehearsal control.",
        )
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest(
                "revise", 2, "client", "Use a revised rehearsal control.", "Updated wording."
            ),
        )
        self.assertEqual(
            await self.repository.list_effective_citations(scope, self.target_project_id),
            (),
        )
        self.assertEqual(
            await self.fixture.admin.fetchval(
                "SELECT count(*) FROM marketops.knowledge_citations WHERE id = $1",
                citation.citation.citation_id,
            ),
            1,
        )

    async def test_audit_failure_rolls_back_the_initial_promotion(self) -> None:
        function_name = f"reject_promotion_audit_{self.source_project_id.replace('-', '')}"
        await self.fixture.admin.execute(
            f"""
            CREATE FUNCTION marketops.{function_name}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.project_id = '{self.source_project_id}'::uuid
                   AND NEW.action = 'knowledge_promotion.decided' THEN
                    RAISE EXCEPTION 'forced promotion audit failure';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER {function_name} BEFORE INSERT ON marketops.audit_events
            FOR EACH ROW EXECUTE FUNCTION marketops.{function_name}();
            """
        )
        try:
            with self.assertRaisesRegex(LearningFailure, "could not be persisted"):
                await self.repository.decide(
                    self.fixture.scope, self.source_project_id, self.knowledge_id,
                    ApprovalDecisionRequest("approve", 0, "project"),
                )
        finally:
            await self.fixture.admin.execute(
                f"DROP TRIGGER IF EXISTS {function_name} ON marketops.audit_events; "
                f"DROP FUNCTION IF EXISTS marketops.{function_name}()"
            )
        self.assertEqual(
            await self.fixture.admin.fetchrow(
                """
                SELECT
                    (SELECT count(*) FROM marketops.knowledge_promotion_roots WHERE project_id = $1) AS roots,
                    (SELECT count(*) FROM marketops.knowledge_promotion_versions WHERE project_id = $1) AS versions,
                    (SELECT count(*) FROM marketops.audit_events WHERE project_id = $1 AND action = 'knowledge_promotion.decided') AS audits
                """,
                self.source_project_id,
            ),
            (0, 0, 0),
        )

    async def test_direct_sql_cannot_cite_another_actors_approval(self) -> None:
        scope = self.fixture.scope
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("elevate", 1, "client", reason="Same-client reuse."),
        )
        citation = await self.repository.cite_approved_knowledge(
            scope, source_project_id=self.source_project_id,
            source_knowledge_id=self.knowledge_id, target_project_id=self.target_project_id,
            reason="Apply the approved rehearsal control.",
        )
        authorization = citation.citation.authorization
        promotion_id = await self.fixture.admin.fetchval(
            "SELECT source_promotion_id FROM marketops.knowledge_citations WHERE id = $1",
            citation.citation.citation_id,
        )
        with self.assertRaises(CheckViolationError) as raised:
            async with self.fixture.admin.transaction():
                await self.fixture.admin.execute(
                    """
                    INSERT INTO marketops.knowledge_citations(
                        id, organization_id, workspace_id, client_id, project_id,
                        source_project_id, source_knowledge_id, source_promotion_id,
                        source_promotion_version, source_scope, content, content_sha256,
                        reason, citation_sha256, created_by, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                              decode($12, 'hex'), $13, decode($14, 'hex'), $15, $16)
                    """,
                    str(uuid4()), self.fixture.fixture.organization_id,
                    self.fixture.fixture.workspace_id, self.fixture.fixture.client_id,
                    self.target_project_id, self.source_project_id, self.knowledge_id,
                    promotion_id, authorization.promotion_version,
                    authorization.effective_scope, authorization.content,
                    authorization.content_sha256, authorization.reason,
                    "e" * 64, str(uuid4()), datetime.now(timezone.utc),
                )
        self.assertEqual(raised.exception.sqlstate, "23514")

    async def test_direct_sql_cannot_bridge_client_or_workspace(self) -> None:
        scope = self.fixture.scope
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )
        elevated = await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("elevate", 1, "client", reason="Same-client reuse."),
        )
        authorization = elevated.history.state.current
        assert authorization is not None
        promotion_id = await self.fixture.admin.fetchval(
            "SELECT id FROM marketops.knowledge_promotion_roots WHERE project_id = $1",
            self.source_project_id,
        )
        foreign_targets = (
            (
                "client",
                self.fixture.fixture.workspace_id,
                str(uuid4()),
            ),
            (
                "workspace",
                str(uuid4()),
                str(uuid4()),
            ),
        )
        for tenant_axis, workspace_id, client_id in foreign_targets:
            target_project_id = await self._seed_foreign_target_project(
                workspace_id=workspace_id, client_id=client_id
            )
            with self.subTest(tenant_axis=tenant_axis), self.assertRaises(ForeignKeyViolationError) as raised:
                async with self.fixture.admin.transaction():
                    await self.fixture.admin.execute(
                        """
                        INSERT INTO marketops.knowledge_citations(
                            id, organization_id, workspace_id, client_id, project_id,
                            source_project_id, source_knowledge_id, source_promotion_id,
                            source_promotion_version, source_scope, content, content_sha256,
                            reason, citation_sha256, created_by, created_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                                  decode($12, 'hex'), $13, decode($14, 'hex'), $15, $16)
                        """,
                        str(uuid4()), self.fixture.fixture.organization_id,
                        workspace_id, client_id, target_project_id,
                        self.source_project_id, self.knowledge_id, promotion_id,
                        authorization.version, authorization.effective_scope,
                        authorization.content, authorization.content_sha256,
                        "Attempt to bridge tenant scope.", uuid4().hex + uuid4().hex,
                        self.fixture.fixture.actor_id, datetime.now(timezone.utc),
                    )
            self.assertEqual(raised.exception.sqlstate, "23503")
            self.assertEqual(
                await self.fixture.admin.fetchval(
                    "SELECT count(*) FROM marketops.knowledge_citations WHERE project_id = $1",
                    target_project_id,
                ),
                0,
            )

    async def test_direct_sql_cannot_cite_into_another_actors_target_project(self) -> None:
        scope = self.fixture.scope
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )
        elevated = await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("elevate", 1, "client", reason="Same-client reuse."),
        )
        authorization = elevated.history.state.current
        assert authorization is not None
        target_project_id = await self._seed_foreign_target_project(
            workspace_id=self.fixture.fixture.workspace_id,
            client_id=self.fixture.fixture.client_id,
            created_by=str(uuid4()),
        )
        promotion_id = await self.fixture.admin.fetchval(
            "SELECT id FROM marketops.knowledge_promotion_roots WHERE project_id = $1",
            self.source_project_id,
        )
        with self.assertRaises(CheckViolationError) as raised:
            async with self.fixture.admin.transaction():
                await self.fixture.admin.execute(
                    """
                    INSERT INTO marketops.knowledge_citations(
                        id, organization_id, workspace_id, client_id, project_id,
                        source_project_id, source_knowledge_id, source_promotion_id,
                        source_promotion_version, source_scope, content, content_sha256,
                        reason, citation_sha256, created_by, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                              decode($12, 'hex'), $13, decode($14, 'hex'), $15, $16)
                    """,
                    str(uuid4()), self.fixture.fixture.organization_id,
                    self.fixture.fixture.workspace_id, self.fixture.fixture.client_id,
                    target_project_id, self.source_project_id, self.knowledge_id,
                    promotion_id, authorization.version, authorization.effective_scope,
                    authorization.content, authorization.content_sha256,
                    "Attempt to cite into another actor's project.", "f" * 64,
                    self.fixture.fixture.actor_id, datetime.now(timezone.utc),
                )
        self.assertEqual(raised.exception.sqlstate, "23514")
        self.assertEqual(
            await self.fixture.admin.fetchval(
                "SELECT count(*) FROM marketops.knowledge_citations WHERE project_id = $1",
                target_project_id,
            ),
            0,
        )

    async def test_real_replay_does_not_duplicate_decisions_or_citations(self) -> None:
        scope = self.fixture.scope
        first = await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )
        replay = await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("elevate", 1, "client", reason="Same-client reuse."),
        )
        citation = await self.repository.cite_approved_knowledge(
            scope, source_project_id=self.source_project_id,
            source_knowledge_id=self.knowledge_id, target_project_id=self.target_project_id,
            reason="Apply the approved rehearsal control.",
        )
        citation_replay = await self.repository.cite_approved_knowledge(
            scope, source_project_id=self.source_project_id,
            source_knowledge_id=self.knowledge_id, target_project_id=self.target_project_id,
            reason="Apply the approved rehearsal control.",
        )
        self.assertFalse(citation.replayed)
        self.assertTrue(citation_replay.replayed)
        self.assertEqual(
            await self.fixture.admin.fetchval(
                "SELECT count(*) FROM marketops.knowledge_promotion_versions WHERE project_id = $1",
                self.source_project_id,
            ),
            2,
        )
        self.assertEqual(
            await self.fixture.admin.fetchval(
                "SELECT count(*) FROM marketops.knowledge_citations WHERE project_id = $1",
                self.target_project_id,
            ),
            1,
        )

    async def test_concurrent_revoke_and_citation_never_leave_effective_reuse(self) -> None:
        scope = self.fixture.scope
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )
        await self.repository.decide(
            scope, self.source_project_id, self.knowledge_id,
            ApprovalDecisionRequest("elevate", 1, "client", reason="Same-client reuse."),
        )
        start = asyncio.Event()

        async def cite() -> str:
            await start.wait()
            try:
                await self.repository.cite_approved_knowledge(
                    scope, source_project_id=self.source_project_id,
                    source_knowledge_id=self.knowledge_id, target_project_id=self.target_project_id,
                    reason="Concurrent reuse request.",
                )
                return "cited"
            except LearningFailure as error:
                self.assertEqual(error.code, "KNOWLEDGE_NOT_ELIGIBLE")
                return "denied"

        async def revoke() -> None:
            await start.wait()
            await self.repository.decide(
                scope, self.source_project_id, self.knowledge_id,
                ApprovalDecisionRequest("revoke", 2, reason="Concurrent revocation."),
            )

        cite_task = asyncio.create_task(cite())
        revoke_task = asyncio.create_task(revoke())
        start.set()
        outcome, _ = await asyncio.gather(cite_task, revoke_task)
        self.assertIn(outcome, {"cited", "denied"})
        history = await self.repository.read_history(scope, self.source_project_id, self.knowledge_id)
        self.assertIsNotNone(history)
        assert history is not None and history.state.current is not None
        self.assertEqual(history.state.current.status, "revoked")
        self.assertEqual(
            await self.repository.list_effective_citations(scope, self.target_project_id),
            (),
        )


if __name__ == "__main__":
    unittest.main()
