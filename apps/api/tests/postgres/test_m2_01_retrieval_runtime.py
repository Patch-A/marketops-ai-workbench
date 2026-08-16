from __future__ import annotations

import os
import json
import asyncio
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

try:
    import asyncpg
except ImportError:
    asyncpg = None

from apps.api.marketops_retrieval import (
    AsyncpgArtifactVersionSourceReader,
    AsyncpgRetrievalRepository,
    IndexSourceRequest,
    ParsedBlock,
    RetrievalFailure,
    RetrievalApplicationService,
    RetrievalScopeContext,
    SourceIdentity,
    SourceIndexingService,
    build_source_index,
)
from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
from apps.api.marketops_import.service import ScopeContext
from apps.api.marketops_import.storage import LocalObjectStore
from apps.api.tests.test_m1_02_review_http import asgi_request


ADMIN_DSN = os.environ.get("MARKETOPS_TEST_ADMIN_DATABASE_URL", "").strip()
APP_DSN = os.environ.get("MARKETOPS_TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(
    asyncpg is not None and ADMIN_DSN and APP_DSN,
    "PostgreSQL admin/application test URLs are required",
)
class RetrievalPostgresRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.object_store = LocalObjectStore(Path(self.temporary.name) / "objects")
        self.admin = await asyncpg.connect(ADMIN_DSN)
        self.pool = await asyncpg.create_pool(APP_DSN, min_size=1, max_size=4)
        self.ids = {name: str(uuid4()) for name in (
            "organization", "workspace", "client", "actor", "project",
            "artifact", "version", "foreign_workspace", "foreign_client",
            "foreign_project", "foreign_artifact", "foreign_version",
        )}
        self.scope = RetrievalScopeContext(
            self.ids["organization"], self.ids["workspace"], self.ids["client"],
            self.ids["actor"],
        )
        self.source_payload = b"# Legal\nlegal review before media booking\n"
        self.foreign_payload = b"# Legal\nlegal review before media booking foreign\n"
        self.source_sha = hashlib.sha256(self.source_payload).hexdigest()
        self.foreign_sha = hashlib.sha256(self.foreign_payload).hexdigest()
        await self._seed()
        self.repository = AsyncpgRetrievalRepository(
            self.pool, id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )

    async def asyncTearDown(self) -> None:
        await self.pool.close()
        await self.admin.close()

    async def _seed(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.admin.transaction():
            await self.admin.execute(
                "INSERT INTO marketops.organizations (id, name) VALUES ($1, 'synthetic')",
                self.ids["organization"],
            )
            for workspace in (self.ids["workspace"], self.ids["foreign_workspace"]):
                await self.admin.execute(
                    "INSERT INTO marketops.workspaces (id, organization_id, name, kind) "
                    "VALUES ($1, $2, 'synthetic', 'personal')",
                    workspace, self.ids["organization"],
                )
            pairs = (
                (self.ids["client"], self.ids["workspace"]),
                (self.ids["foreign_client"], self.ids["foreign_workspace"]),
            )
            for client, workspace in pairs:
                await self.admin.execute(
                    "INSERT INTO marketops.clients (id, organization_id, workspace_id, name) "
                    "VALUES ($1, $2, $3, 'synthetic')",
                    client, self.ids["organization"], workspace,
                )
            projects = (
                (self.ids["project"], self.ids["workspace"], self.ids["client"],
                 self.ids["artifact"], self.ids["version"], self.source_sha),
                (self.ids["foreign_project"], self.ids["foreign_workspace"],
                 self.ids["foreign_client"], self.ids["foreign_artifact"],
                 self.ids["foreign_version"], self.foreign_sha),
            )
            for project, workspace, client, artifact, version, source_sha in projects:
                payload = (
                    self.foreign_payload
                    if version == self.ids["foreign_version"]
                    else self.source_payload
                )
                storage_key = (
                    f"workspaces/{workspace}/clients/{client}/imports/"
                    f"{'c' * 64}/{'d' * 64}/proposal/{source_sha}"
                )
                source_path = Path(self.temporary.name) / f"{version}.md"
                source_path.write_bytes(payload)
                await self.object_store.put_immutable(
                    kind="proposal",
                    storage_key=storage_key,
                    source_path=source_path,
                    size_bytes=len(payload),
                    sha256=source_sha,
                )
                await self.admin.execute(
                    """
                    INSERT INTO marketops.projects (
                        id, organization_id, workspace_id, client_id, name, import_request_id,
                        import_manifest_sha256, approved_proposal_artifact_id,
                        approved_proposal_version_id, approved_proposal_number, created_by
                    ) VALUES ($1, $2, $3, $4, 'synthetic', $5, decode($6, 'hex'),
                              $7, $8, 1, $9)
                    """,
                    project, self.ids["organization"], workspace, client, str(uuid4()),
                    "c" * 64, artifact, version, self.ids["actor"],
                )
                await self.admin.execute(
                    """
                    INSERT INTO marketops.artifacts (
                        id, organization_id, workspace_id, client_id, project_id, kind,
                        created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'proposal', $6)
                    """,
                    artifact, self.ids["organization"], workspace, client, project,
                    self.ids["actor"],
                )
                await self.admin.execute(
                    """
                    INSERT INTO marketops.artifact_versions (
                        id, organization_id, workspace_id, client_id, project_id,
                        artifact_id, proposal_version, original_filename, media_type,
                        byte_size, storage_key, sha256, approval_status, approved_by,
                        approved_at, created_by, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, 1, 'synthetic.md',
                              'text/markdown', $7, $8, decode($9, 'hex'), 'approved',
                              $10, $11, $10, $11)
                    """,
                    version, self.ids["organization"], workspace, client, project,
                    artifact, len(payload), storage_key, source_sha,
                    self.ids["actor"], now,
                )
            await self.admin.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def _index(self, *, foreign: bool = False):
        workspace = self.ids["foreign_workspace"] if foreign else self.ids["workspace"]
        client = self.ids["foreign_client"] if foreign else self.ids["client"]
        project = self.ids["foreign_project"] if foreign else self.ids["project"]
        version = self.ids["foreign_version"] if foreign else self.ids["version"]
        source_sha = self.foreign_sha if foreign else self.source_sha
        source = SourceIdentity(
            self.ids["organization"], workspace, client, project, version,
            source_sha, "marketops-parser-v1",
        )
        scope = RetrievalScopeContext(
            self.ids["organization"], workspace, client, self.ids["actor"]
        )
        return build_source_index(
            source,
            (ParsedBlock("legal review before media booking", {"line": 1}),),
            scope,
        )

    async def test_persist_replay_scope_isolation_and_minimum_privileges(self):
        index = self._index()
        foreign_index = self._index(foreign=True)
        foreign_scope = RetrievalScopeContext(
            self.ids["organization"], self.ids["foreign_workspace"],
            self.ids["foreign_client"], self.ids["actor"],
        )
        first, replay = await asyncio.gather(
            self.repository.persist_index(index, self.scope),
            self.repository.persist_index(index, self.scope),
        )
        await self.repository.persist_index(foreign_index, foreign_scope)
        self.assertEqual(sorted((first.replayed, replay.replayed)), [False, True])
        self.assertEqual(first.index, index)
        self.assertEqual(replay.index, index)
        ready = await self.repository.list_ready_indexes(self.scope, self.ids["project"])
        self.assertEqual(ready, (index,))

        self.assertEqual(
            await self.repository.list_ready_indexes(
                foreign_scope, self.ids["foreign_project"]
            ),
            (foreign_index,),
        )
        async with self.pool.acquire() as connection:
            privileges = await connection.fetchrow(
                """
                SELECT
                    has_table_privilege(current_user, 'marketops.artifacts', 'SELECT') AS artifact_select,
                    has_table_privilege(current_user, 'marketops.artifact_versions', 'SELECT') AS artifact_version_select,
                    has_table_privilege(current_user, 'marketops.source_indexes', 'SELECT') AS index_select,
                    has_table_privilege(current_user, 'marketops.source_indexes', 'INSERT') AS index_insert,
                    has_table_privilege(current_user, 'marketops.source_indexes', 'UPDATE') AS index_update,
                    has_column_privilege(current_user, 'marketops.source_indexes', 'status', 'UPDATE') AS status_update,
                    has_table_privilege(current_user, 'marketops.source_chunks', 'DELETE') AS chunk_delete,
                    has_table_privilege(current_user, 'marketops.source_chunks', 'TRUNCATE') AS chunk_truncate
                """
            )
        self.assertEqual(dict(privileges), {
            "artifact_select": True, "artifact_version_select": True,
            "index_select": True, "index_insert": True, "index_update": False,
            "status_update": True, "chunk_delete": True, "chunk_truncate": False,
        })

    async def test_selected_object_is_verified_parsed_and_persisted_server_side(self):
        service = SourceIndexingService(
            source_reader=AsyncpgArtifactVersionSourceReader(self.pool),
            object_store=self.object_store,
            repository=self.repository,
        )
        result = await service.index_source(
            IndexSourceRequest(self.ids["project"], self.ids["version"]),
            self.scope,
        )
        replay = await service.index_source(
            IndexSourceRequest(self.ids["project"], self.ids["version"]),
            self.scope,
        )

        self.assertFalse(result.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(result.document_format, "markdown")
        self.assertEqual(result.index, replay.index)
        self.assertEqual(
            [chunk.text for chunk in result.index.chunks],
            ["Legal", "legal review before media booking"],
        )
        with self.assertRaises(RetrievalFailure) as raised:
            await service.index_source(
                IndexSourceRequest(
                    self.ids["project"], self.ids["foreign_version"]
                ),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "SOURCE_NOT_FOUND")

    async def test_authenticated_http_index_search_read_and_withdrawal(self):
        indexing = SourceIndexingService(
            source_reader=AsyncpgArtifactVersionSourceReader(self.pool),
            object_store=self.object_store,
            repository=self.repository,
        )
        app = create_app(
            authenticator=StaticBearerAuthenticator(
                "runtime-retrieval-token",
                ScopeContext(
                    self.ids["organization"],
                    self.ids["workspace"],
                    self.ids["client"],
                    self.ids["actor"],
                ),
            ),
            retrieval_indexing=indexing,
            retrieval_service=RetrievalApplicationService(self.repository),
            request_id_factory=lambda: "runtime-retrieval-request",
        )
        headers = [
            ("Authorization", "Bearer runtime-retrieval-token"),
            ("Content-Type", "application/json"),
        ]
        created = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{self.ids['project']}/source-indexes",
            headers,
            json.dumps({"artifactVersionId": self.ids["version"]}).encode(),
        )
        self.assertEqual(created.status_code, 201)
        index_id = created.json()["indexId"]

        searched = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{self.ids['project']}/retrieval-searches",
            headers,
            b'{"query":"legal review","limit":5}',
        )
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(searched.json()["resultCount"], 1)
        self.assertEqual(
            searched.json()["results"][0]["citation"]["indexId"], index_id
        )

        read = await asgi_request(
            app,
            "GET",
            f"/v1/projects/{self.ids['project']}/source-indexes/{index_id}",
            [("Authorization", "Bearer runtime-retrieval-token")],
        )
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["status"], "ready")

        withdrawn = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{self.ids['project']}/source-indexes/{index_id}/withdrawal",
            headers,
            b"{}",
        )
        self.assertEqual(withdrawn.status_code, 200)
        self.assertEqual(withdrawn.json()["status"], "withdrawn")

        empty = await asgi_request(
            app,
            "POST",
            f"/v1/projects/{self.ids['project']}/retrieval-searches",
            headers,
            b'{"query":"legal review"}',
        )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["resultCount"], 0)

    async def test_withdrawal_is_atomic_audited_and_hides_derived_text(self):
        index = self._index()
        await self.repository.persist_index(index, self.scope)
        withdrawn = await self.repository.withdraw_index(
            index.index_id, self.ids["project"], self.scope
        )
        replay = await self.repository.withdraw_index(
            index.index_id, self.ids["project"], self.scope
        )
        self.assertFalse(withdrawn.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(withdrawn.index.chunks, ())
        self.assertEqual(
            await self.repository.list_ready_indexes(self.scope, self.ids["project"]), ()
        )
        chunk_count = await self.admin.fetchval(
            "SELECT count(*) FROM marketops.source_chunks WHERE index_id = $1",
            index.index_id,
        )
        audits = await self.admin.fetch(
            "SELECT action, event_data FROM marketops.audit_events "
            "WHERE target_id = $1 ORDER BY created_at, action",
            index.index_id,
        )
        self.assertEqual(chunk_count, 0)
        self.assertEqual([row["action"] for row in audits], [
            "source_index.created", "source_index.withdrawn",
        ])
        event_data = [
            json.loads(row["event_data"])
            if isinstance(row["event_data"], str)
            else dict(row["event_data"])
            for row in audits
        ]
        rendered = str(event_data)
        self.assertNotIn("legal review", rendered)

    async def test_audit_failure_rolls_back_withdrawal_and_chunk_deletion(self):
        index = self._index()
        await self.repository.persist_index(index, self.scope)
        await self.admin.execute(
            """
            CREATE OR REPLACE FUNCTION marketops.fail_m2_withdrawal_audit()
            RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
            BEGIN
                IF NEW.action = 'source_index.withdrawn' THEN
                    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'forced audit failure';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER fail_m2_withdrawal_audit
            BEFORE INSERT ON marketops.audit_events
            FOR EACH ROW EXECUTE FUNCTION marketops.fail_m2_withdrawal_audit();
            """
        )
        try:
            with self.assertRaises(RetrievalFailure) as raised:
                await self.repository.withdraw_index(
                    index.index_id, self.ids["project"], self.scope
                )
            self.assertEqual(raised.exception.code, "RETRIEVAL_WRITE_FAILED")
            self.assertNotIn("forced audit failure", str(raised.exception))
        finally:
            await self.admin.execute(
                "DROP TRIGGER fail_m2_withdrawal_audit ON marketops.audit_events; "
                "DROP FUNCTION marketops.fail_m2_withdrawal_audit()"
            )
        state = await self.admin.fetchrow(
            "SELECT status, withdrawn_by, withdrawn_at FROM marketops.source_indexes "
            "WHERE id = $1",
            index.index_id,
        )
        chunk_count = await self.admin.fetchval(
            "SELECT count(*) FROM marketops.source_chunks WHERE index_id = $1",
            index.index_id,
        )
        withdrawal_audits = await self.admin.fetchval(
            "SELECT count(*) FROM marketops.audit_events "
            "WHERE target_id = $1 AND action = 'source_index.withdrawn'",
            index.index_id,
        )
        self.assertEqual(dict(state), {
            "status": "ready", "withdrawn_by": None, "withdrawn_at": None,
        })
        self.assertEqual(chunk_count, len(index.chunks))
        self.assertEqual(withdrawal_audits, 0)


if __name__ == "__main__":
    unittest.main()
