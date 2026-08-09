from __future__ import annotations

import asyncio
import importlib.util
import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from apps.api.marketops_import.postgres import AsyncpgImportRepository
from apps.api.marketops_import.service import (
    AuditEvent,
    ProjectImportRecord,
    ScopeContext,
    StoredObject,
)


DATABASE_URL = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "").strip()
ADMIN_DATABASE_URL = os.environ.get("MARKETOPS_TEST_ADMIN_DATABASE_URL", "").strip()
ASYNCPG_AVAILABLE = importlib.util.find_spec("asyncpg") is not None
REQUIRE_PREREQUISITES = os.environ.get("MARKETOPS_REQUIRE_TEST_PREREQUISITES") == "1"
if REQUIRE_PREREQUISITES and not (
    DATABASE_URL and ADMIN_DATABASE_URL and ASYNCPG_AVAILABLE
):
    raise RuntimeError(
        "PostgreSQL test prerequisites are required: provide both test database URLs "
        "and install asyncpg"
    )

if ASYNCPG_AVAILABLE:
    import asyncpg


def identifier():
    return str(uuid4())


@unittest.skipUnless(
    DATABASE_URL and ASYNCPG_AVAILABLE,
    "MARKETOPS_TEST_DATABASE_URL and asyncpg are required for PostgreSQL tests",
)
class PostgreSQLRoleAndRlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=2)

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_application_role_cannot_bypass_rls(self):
        async with self.pool.acquire() as connection:
            role = await connection.fetchrow(
                """
                SELECT rolname, rolsuper, rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
            self.assertIsNotNone(role)
            self.assertFalse(role["rolsuper"])
            self.assertFalse(role["rolbypassrls"])
            forced = await connection.fetchval(
                """
                SELECT bool_and(relrowsecurity AND relforcerowsecurity)
                FROM pg_class
                WHERE oid IN (
                    'marketops.projects'::regclass,
                    'marketops.artifacts'::regclass,
                    'marketops.artifact_versions'::regclass,
                    'marketops.audit_events'::regclass
                )
                """
            )
            self.assertTrue(forced)

    async def test_missing_scope_is_invisible_and_local_scope_does_not_leak(self):
        async with self.pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval("SELECT count(*) FROM marketops.clients"), 0
            )
            async with connection.transaction():
                await connection.fetchval(
                    "SELECT set_config('app.workspace_id', $1, true)", identifier()
                )
                await connection.fetchval(
                    "SELECT set_config('app.client_id', $1, true)", identifier()
                )
                await connection.fetchval(
                    "SELECT set_config('app.actor_id', $1, true)", identifier()
                )
                self.assertEqual(
                    await connection.fetchval("SELECT count(*) FROM marketops.clients"), 0
                )
        async with self.pool.acquire() as reused:
            settings = await reused.fetchrow(
                """
                SELECT
                    nullif(current_setting('app.workspace_id', true), '') AS workspace_id,
                    nullif(current_setting('app.client_id', true), '') AS client_id,
                    nullif(current_setting('app.actor_id', true), '') AS actor_id
                """
            )
            self.assertTrue(all(settings[name] is None for name in settings.keys()))


@unittest.skipUnless(
    DATABASE_URL and ADMIN_DATABASE_URL and ASYNCPG_AVAILABLE,
    "both PostgreSQL test URLs and asyncpg are required for seeded RLS/concurrency tests",
)
class PostgreSQLSeededRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admin = await asyncpg.connect(ADMIN_DATABASE_URL)
        bypass = await self.admin.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if not bypass:
            self.fail("MARKETOPS_TEST_ADMIN_DATABASE_URL must use a test-only RLS-bypass role")
        self.organization_id = identifier()
        self.workspace_id = identifier()
        self.client_id = identifier()
        self.other_client_id = identifier()
        self.actor_id = identifier()
        await self.admin.execute(
            "INSERT INTO marketops.organizations (id, name) VALUES ($1, 'WP4 synthetic org')",
            self.organization_id,
        )
        await self.admin.execute(
            """
            INSERT INTO marketops.workspaces (id, organization_id, name, kind)
            VALUES ($1, $2, 'WP4 synthetic workspace', 'personal')
            """,
            self.workspace_id,
            self.organization_id,
        )
        await self.admin.executemany(
            """
            INSERT INTO marketops.clients (id, organization_id, workspace_id, name)
            VALUES ($1, $2, $3, $4)
            """,
            [
                (self.client_id, self.organization_id, self.workspace_id, "visible client"),
                (self.other_client_id, self.organization_id, self.workspace_id, "hidden client"),
            ],
        )
        self.pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)

    async def asyncTearDown(self):
        await self.pool.close()
        await self.admin.execute("SET session_replication_role = replica")
        try:
            for table in (
                "audit_events",
                "artifact_versions",
                "artifacts",
                "projects",
                "clients",
                "workspaces",
                "organizations",
            ):
                await self.admin.execute(
                    f"DELETE FROM marketops.{table} WHERE organization_id = $1"
                    if table != "organizations"
                    else "DELETE FROM marketops.organizations WHERE id = $1",
                    self.organization_id,
                )
        finally:
            await self.admin.execute("SET session_replication_role = origin")
            await self.admin.close()

    async def test_rls_exposes_only_server_selected_client(self):
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.fetchval(
                    "SELECT set_config('app.workspace_id', $1, true)", self.workspace_id
                )
                await connection.fetchval(
                    "SELECT set_config('app.client_id', $1, true)", self.client_id
                )
                await connection.fetchval(
                    "SELECT set_config('app.actor_id', $1, true)", self.actor_id
                )
                clients = await connection.fetch("SELECT id FROM marketops.clients")
                self.assertEqual([str(row["id"]) for row in clients], [self.client_id])

    async def test_concurrent_idempotency_claim_has_one_winner_and_one_replay(self):
        scope = ScopeContext(
            self.organization_id, self.workspace_id, self.client_id, self.actor_id
        )
        repository = AsyncpgImportRepository(self.pool)
        first = make_record(scope, "runtime-concurrency-" + identifier())
        second = make_record(
            scope, first.idempotency_key, manifest_sha256=first.manifest_sha256
        )

        async def claim(candidate):
            async with repository.transaction(scope) as transaction:
                result = await transaction.try_create_project_import(candidate)
                if result.created:
                    await transaction.append_audit_event(
                        AuditEvent(
                            event_id=identifier(),
                            project_id=result.record.project_id,
                            actor_id=scope.actor_id,
                            action="project.imported_with_approved_proposal",
                            manifest_sha256=result.record.manifest_sha256,
                            source_version_id=result.record.source_version_id,
                            proposal_version_id=result.record.proposal_version_id,
                            approved_by=scope.actor_id,
                            approved_at=result.record.approved_at,
                        )
                    )
                return result

        claims = await asyncio.gather(claim(first), claim(second))
        self.assertEqual(sorted(result.created for result in claims), [False, True])
        self.assertEqual(claims[0].record.project_id, claims[1].record.project_id)
        self.assertEqual(
            await self.admin.fetchval(
                """
                SELECT count(*) FROM marketops.audit_events
                WHERE organization_id = $1 AND action = 'project.imported_with_approved_proposal'
                """,
                self.organization_id,
            ),
            1,
        )


def make_record(scope, idempotency_key, *, manifest_sha256="a" * 64):
    approved_at = datetime.now(timezone.utc)
    project_id = identifier()
    return ProjectImportRecord(
        project_id=project_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        client_id=scope.client_id,
        idempotency_key=idempotency_key,
        manifest_sha256=manifest_sha256,
        project_name="WP4 synthetic concurrency import",
        status="planning",
        source_artifact_id=identifier(),
        source_version_id=identifier(),
        source=StoredObject(
            f"synthetic/{project_id}/source", 1, "b" * 64
        ),
        source_filename="source.md",
        source_media_type="text/markdown",
        proposal_artifact_id=identifier(),
        proposal_version_id=identifier(),
        proposal=StoredObject(
            f"synthetic/{project_id}/proposal", 1, "c" * 64
        ),
        proposal_filename="proposal.md",
        proposal_media_type="text/markdown",
        approved_proposal_version=1,
        approval_status="approved",
        approved_by=scope.actor_id,
        approved_at=approved_at,
        created_by=scope.actor_id,
    )


if __name__ == "__main__":
    unittest.main()
