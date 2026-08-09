from __future__ import annotations

import sys
import unittest
import traceback
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from apps.api.marketops_import.postgres import (
    AsyncpgImportRepository,
    PostgresAuthorizationError,
    PostgresConstraintError,
    PostgresUnavailableError,
)
from apps.api.marketops_import.service import (
    AuditEvent,
    ProjectImportRecord,
    ScopeContext,
    StoredObject,
)


class DriverFailure(RuntimeError):
    def __init__(self, sqlstate, sensitive="secret-dsn password bytes"):
        self.sqlstate = sqlstate
        super().__init__(sensitive)


class FakeDatabaseTransaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        self.connection.transactions += 1
        return self

    async def __aexit__(self, error_type, error, traceback):
        if error_type is None:
            if self.connection.transaction_exit_failure is not None:
                raise self.connection.transaction_exit_failure
            self.connection.commits += 1
        else:
            self.connection.rollbacks += 1


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_):
        return None


class FakePool:
    def __init__(self, connection, *, close_failure=None):
        self.connection = connection
        self.closed = False
        self.close_failure = close_failure

    def acquire(self):
        return FakeAcquire(self.connection)

    async def close(self):
        if self.close_failure is not None:
            raise self.close_failure
        self.closed = True


class FakeConnection:
    def __init__(self, *, inserted=True, project=None, versions=None):
        self.inserted = inserted
        self.project = project
        self.versions = versions or []
        self.calls = []
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0
        self.failure = None
        self.transaction_exit_failure = None

    def transaction(self):
        return FakeDatabaseTransaction(self)

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if self.failure is not None:
            raise self.failure
        if "INSERT INTO marketops.projects" in sql:
            return {"id": args[0]} if self.inserted else None
        if "FROM marketops.projects" in sql:
            return self.project
        return {"set_config": "ok"}

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if self.failure is not None:
            raise self.failure
        return self.versions

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        if self.failure is not None:
            raise self.failure
        return "INSERT 0 1"


def identifier():
    return str(uuid4())


def record(scope):
    approved_at = datetime(2026, 8, 9, 1, 2, tzinfo=timezone.utc)
    return ProjectImportRecord(
        project_id=identifier(),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        client_id=scope.client_id,
        idempotency_key="request-001",
        manifest_sha256="a" * 64,
        project_name="Synthetic launch",
        status="planning",
        source_artifact_id=identifier(),
        source_version_id=identifier(),
        source=StoredObject("source-key", 10, "b" * 64),
        source_filename="brief.md",
        source_media_type="text/markdown",
        proposal_artifact_id=identifier(),
        proposal_version_id=identifier(),
        proposal=StoredObject("proposal-key", 20, "c" * 64),
        proposal_filename="proposal.md",
        proposal_media_type="text/markdown",
        approved_proposal_version=3,
        approval_status="approved",
        approved_by=scope.actor_id,
        approved_at=approved_at,
        created_by=scope.actor_id,
    )


def replay_rows(item):
    project = {
        "id": item.project_id,
        "organization_id": item.organization_id,
        "workspace_id": item.workspace_id,
        "client_id": item.client_id,
        "import_request_id": item.idempotency_key,
        "import_manifest_sha256": bytes.fromhex(item.manifest_sha256),
        "name": item.project_name,
        "status": item.status,
        "approved_proposal_artifact_id": item.proposal_artifact_id,
        "approved_proposal_version_id": item.proposal_version_id,
        "approved_proposal_number": item.approved_proposal_version,
        "created_by": item.created_by,
    }
    common = {
        "approved_at": None,
        "approved_by": None,
        "created_by": item.created_by,
    }
    versions = [
        {
            **common,
            "kind": "source",
            "artifact_id": item.source_artifact_id,
            "version_id": item.source_version_id,
            "proposal_version": None,
            "original_filename": item.source_filename,
            "media_type": item.source_media_type,
            "byte_size": item.source.size_bytes,
            "storage_key": item.source.storage_key,
            "sha256": bytes.fromhex(item.source.sha256),
            "approval_status": "not_applicable",
        },
        {
            **common,
            "kind": "proposal",
            "artifact_id": item.proposal_artifact_id,
            "version_id": item.proposal_version_id,
            "proposal_version": item.approved_proposal_version,
            "original_filename": item.proposal_filename,
            "media_type": item.proposal_media_type,
            "byte_size": item.proposal.size_bytes,
            "storage_key": item.proposal.storage_key,
            "sha256": bytes.fromhex(item.proposal.sha256),
            "approval_status": "approved",
            "approved_by": item.approved_by,
            "approved_at": item.approved_at,
        },
    ]
    return project, versions


class AsyncpgImportRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ScopeContext(identifier(), identifier(), identifier(), identifier())
        self.record = record(self.scope)

    async def test_atomic_claim_sets_local_scope_and_inserts_complete_record(self):
        connection = FakeConnection(inserted=True)
        repository = AsyncpgImportRepository(FakePool(connection))
        async with repository.transaction(self.scope) as transaction:
            claim = await transaction.try_create_project_import(self.record)
            await transaction.append_audit_event(
                AuditEvent(
                    identifier(), self.record.project_id, self.scope.actor_id,
                    "project.imported_with_approved_proposal",
                    self.record.manifest_sha256, self.record.source_version_id,
                    self.record.proposal_version_id, self.scope.actor_id,
                    self.record.approved_at,
                )
            )
        self.assertTrue(claim.created)
        self.assertEqual(connection.commits, 1)
        sql = "\n".join(call[1] for call in connection.calls)
        self.assertIn("set_config('app.workspace_id', $1, true)", sql)
        self.assertIn("ON CONFLICT (workspace_id, client_id, import_request_id) DO NOTHING", sql)
        self.assertEqual(sql.count("INSERT INTO marketops.artifacts"), 2)
        self.assertEqual(sql.count("INSERT INTO marketops.artifact_versions"), 2)
        self.assertIn("INSERT INTO marketops.audit_events", sql)
        for secret in (self.scope.workspace_id, self.scope.client_id, self.scope.actor_id):
            self.assertNotIn(secret, sql)

    async def test_conflict_maps_winning_project_after_switching_project_scope(self):
        project, versions = replay_rows(self.record)
        connection = FakeConnection(inserted=False, project=project, versions=versions)
        repository = AsyncpgImportRepository(FakePool(connection))
        async with repository.transaction(self.scope) as transaction:
            claim = await transaction.try_create_project_import(self.record)
        self.assertFalse(claim.created)
        self.assertEqual(claim.record, self.record)
        project_scope_calls = [
            call
            for call in connection.calls
            if call[1].strip().startswith("SELECT set_config('app.project_id'")
        ]
        self.assertEqual(
            [call[2][0] for call in project_scope_calls],
            [self.record.project_id, self.record.project_id],
        )
        self.assertNotIn("INSERT INTO marketops.artifacts", "\n".join(c[1] for c in connection.calls))

    async def test_server_scope_mismatch_fails_before_claim_and_rolls_back(self):
        connection = FakeConnection()
        repository = AsyncpgImportRepository(FakePool(connection))
        wrong = ProjectImportRecord(**{**self.record.__dict__, "client_id": identifier()})
        with self.assertRaises(PostgresAuthorizationError):
            async with repository.transaction(self.scope) as transaction:
                await transaction.try_create_project_import(wrong)
        self.assertEqual(connection.rollbacks, 1)
        self.assertNotIn("INSERT INTO marketops.projects", "\n".join(c[1] for c in connection.calls))

    async def test_driver_errors_are_stable_and_do_not_leak_details(self):
        cases = [
            ("08006", PostgresUnavailableError),
            ("42501", PostgresAuthorizationError),
            ("23514", PostgresConstraintError),
        ]
        for sqlstate, expected in cases:
            with self.subTest(sqlstate=sqlstate):
                connection = FakeConnection()
                connection.failure = DriverFailure(sqlstate)
                repository = AsyncpgImportRepository(FakePool(connection))
                with self.assertRaises(expected) as caught:
                    async with repository.transaction(self.scope):
                        pass
                self.assert_sanitized_exception(caught.exception)

    async def test_pool_creation_error_has_no_sensitive_exception_chain(self):
        async def create_pool(**_):
            raise DriverFailure("08006", "postgresql://user:secret@db/customer")

        fake_asyncpg = SimpleNamespace(create_pool=create_pool)
        with patch.dict(sys.modules, {"asyncpg": fake_asyncpg}):
            with self.assertRaises(PostgresUnavailableError) as caught:
                await AsyncpgImportRepository.create(
                    "postgresql://user:secret@db/customer"
                )
        self.assert_sanitized_exception(caught.exception, forbidden="user:secret")

    async def test_pool_close_error_has_no_sensitive_exception_chain(self):
        pool = FakePool(
            FakeConnection(),
            close_failure=DriverFailure(
                "08006", "postgresql://user:secret@db/customer"
            ),
        )
        repository = AsyncpgImportRepository(pool)
        with self.assertRaises(PostgresUnavailableError) as caught:
            await repository.close()
        self.assert_sanitized_exception(caught.exception, forbidden="user:secret")

    async def test_deferred_commit_error_has_no_sensitive_exception_chain(self):
        connection = FakeConnection()
        connection.transaction_exit_failure = DriverFailure(
            "23514", "constraint customer_payload secret-dsn"
        )
        repository = AsyncpgImportRepository(FakePool(connection))
        with self.assertRaises(PostgresConstraintError) as caught:
            async with repository.transaction(self.scope):
                pass
        self.assert_sanitized_exception(caught.exception)

    async def test_claim_driver_error_has_no_sensitive_exception_chain(self):
        connection = FakeConnection()
        repository = AsyncpgImportRepository(FakePool(connection))
        with self.assertRaises(PostgresUnavailableError) as caught:
            async with repository.transaction(self.scope) as transaction:
                connection.failure = DriverFailure("08006")
                await transaction.try_create_project_import(self.record)
        self.assertEqual(connection.rollbacks, 1)
        self.assert_sanitized_exception(caught.exception)

    async def test_pool_reuse_resets_all_scope_as_transaction_local(self):
        connection = FakeConnection()
        repository = AsyncpgImportRepository(FakePool(connection))
        other = ScopeContext(identifier(), identifier(), identifier(), identifier())
        async with repository.transaction(self.scope):
            pass
        async with repository.transaction(other):
            pass
        scope_calls = [call for call in connection.calls if "app.workspace_id" in call[1]]
        self.assertEqual(len(scope_calls), 2)
        self.assertEqual(scope_calls[0][2], (self.scope.workspace_id, self.scope.client_id, self.scope.actor_id))
        self.assertEqual(scope_calls[1][2], (other.workspace_id, other.client_id, other.actor_id))
        self.assertTrue(all("true" in call[1] for call in scope_calls))

    def assert_sanitized_exception(self, error, *, forbidden="secret-dsn"):
        self.assertNotIn(forbidden, str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = "".join(traceback.format_exception(error))
        self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
