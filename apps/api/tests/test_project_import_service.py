from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT))

from marketops_import.service import (  # noqa: E402
    FilePayload,
    ImportClaim,
    ImportFailure,
    ImportRequest,
    MAX_FILE_SIZE_BYTES,
    ProjectImportService,
    ScopeContext,
    StoredObject,
)
from scripts import check_m1_01_openapi_contract as openapi_contract  # noqa: E402


STABLE_ERROR_CODES = {
    "INVALID_INPUT",
    "AUTHORIZATION_REQUIRED",
    "UNSUPPORTED_FORMAT",
    "INVALID_MEDIA_TYPE",
    "INVALID_DOCUMENT",
    "APPROVAL_REQUIRED",
    "PAYLOAD_TOO_LARGE",
    "OBJECT_WRITE_FAILED",
    "OBJECT_INTEGRITY_MISMATCH",
    "IDEMPOTENCY_CONFLICT",
    "INVALID_SERVER_ID",
    "INVALID_SERVER_CLOCK",
    "DATABASE_WRITE_FAILED",
}


class FakeObjectStore:
    def __init__(
        self,
        fail_on: str | None = None,
        corrupt_on: str | None = None,
        cancel_on: str | None = None,
        guard_enter_error: BaseException | None = None,
        guard_exit_error: BaseException | None = None,
    ):
        self.fail_on = fail_on
        self.corrupt_on = corrupt_on
        self.cancel_on = cancel_on
        self.guard_enter_error = guard_enter_error
        self.guard_exit_error = guard_exit_error
        self.calls: list[tuple[str, str]] = []
        self.objects: dict[str, bytes] = {}
        self.guard_depth = 0
        self.guard_events: list[str] = []

    @asynccontextmanager
    async def import_guard(self):
        if self.guard_enter_error is not None:
            raise self.guard_enter_error
        self.guard_depth += 1
        self.guard_events.append("enter")
        try:
            yield
        finally:
            self.guard_events.append("exit")
            self.guard_depth -= 1
            if self.guard_exit_error is not None:
                raise self.guard_exit_error

    @property
    def guard_active(self):
        return self.guard_depth > 0

    async def put_immutable(self, *, kind: str, storage_key: str, source_path: Path, size_bytes: int, sha256: str) -> StoredObject:
        self.calls.append((kind, storage_key))
        await asyncio.sleep(0)
        if kind == self.cancel_on:
            raise asyncio.CancelledError
        if kind == self.fail_on:
            raise OSError(f"failed to store {kind}")
        content = source_path.read_bytes()
        self.objects.setdefault(storage_key, content)
        stored_hash = "0" * 64 if kind == self.corrupt_on else hashlib.sha256(content).hexdigest()
        return StoredObject(storage_key=storage_key, size_bytes=size_bytes, sha256=stored_hash)


class FakeTransaction:
    def __init__(self, repository, scope):
        self.repository = repository
        self.scope = scope
        self.pending_project = None
        self.pending_audit = None

    def _find_by_idempotency_key(self, key):
        if self.repository.scope_leak:
            return next(
                (
                    record
                    for (_workspace_id, _client_id, stored_key), record in self.repository.imports.items()
                    if stored_key == key
                ),
                None,
            )
        return self.repository.imports.get(
            (self.scope.workspace_id, self.scope.client_id, key)
        )

    async def try_create_project_import(self, record):
        self.repository.claim_calls += 1
        if self.repository.cancel_on_claim:
            raise asyncio.CancelledError
        if self.repository.fail_database:
            raise RuntimeError("database unavailable")
        existing = self._find_by_idempotency_key(record.idempotency_key)
        if existing is not None:
            return ImportClaim(record=existing, created=False)
        self.pending_project = record
        if self.repository.invalid_created_claim == "record":
            return ImportClaim(
                record=replace(
                    record,
                    project_id="00000000-0000-4000-8000-000000000099",
                ),
                created=True,
            )
        if self.repository.invalid_created_claim == "created_type":
            return ImportClaim(record=record, created=1)  # type: ignore[arg-type]
        return ImportClaim(record=record, created=True)

    async def append_audit_event(self, event):
        if self.repository.cancel_on_audit:
            raise asyncio.CancelledError
        if self.repository.fail_audit:
            raise RuntimeError("audit unavailable")
        self.pending_audit = event

    def commit(self):
        record = self.pending_project
        if record is None and self.pending_audit is None:
            return
        if record is None or self.pending_audit is None:
            raise AssertionError("project and audit must commit together")
        self.repository.imports[
            (self.scope.workspace_id, self.scope.client_id, record.idempotency_key)
        ] = record
        self.repository.audit_events.append(self.pending_audit)
        if self.repository.guard_probe is not None and not self.repository.guard_probe():
            raise AssertionError("object-store guard ended before database commit")


class FakeRepository:
    def __init__(
        self,
        fail_database=False,
        fail_audit=False,
        scope_leak=False,
        cancel_on_claim=False,
        cancel_on_audit=False,
        fail_on_commit=False,
        cancel_on_commit=False,
        invalid_created_claim=None,
        guard_probe=None,
    ):
        self.fail_database = fail_database
        self.fail_audit = fail_audit
        self.scope_leak = scope_leak
        self.cancel_on_claim = cancel_on_claim
        self.cancel_on_audit = cancel_on_audit
        self.fail_on_commit = fail_on_commit
        self.cancel_on_commit = cancel_on_commit
        self.invalid_created_claim = invalid_created_claim
        self.guard_probe = guard_probe
        self.transaction_count = 0
        self.claim_calls = 0
        self.rollbacks = 0
        self.scopes = []
        self.imports = {}
        self.audit_events = []
        self.claim_lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self, scope):
        self.transaction_count += 1
        self.scopes.append(scope)
        transaction = FakeTransaction(self, scope)
        async with self.claim_lock:
            try:
                yield transaction
                if self.cancel_on_commit:
                    raise asyncio.CancelledError
                if self.fail_on_commit:
                    raise RuntimeError("commit failed")
                transaction.commit()
            except BaseException:
                self.rollbacks += 1
                raise


class SequentialIds:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return str(UUID(int=self.value, version=4))


class ProjectImportServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.temp_path = Path(self.temp_directory.name)
        self.scope = ScopeContext(
            organization_id="00000000-0000-4000-8000-000000000001",
            workspace_id="00000000-0000-4000-8000-000000000002",
            client_id="00000000-0000-4000-8000-000000000003",
            actor_id="00000000-0000-4000-8000-000000000004",
        )

    def payload(self, filename, media_type, content):
        path = self.temp_path / filename
        path.write_bytes(content)
        return FilePayload(filename, media_type, path)

    def valid_request(self, **changes):
        values = {
            "idempotency_key": "request-001",
            "project_name": "Synthetic AI Event",
            "proposal_version": 3,
            "approval_confirmed": True,
            "source": changes.pop(
                "source",
                self.payload("brief-default.md", "text/markdown", b"# Brief\nSynthetic only."),
            ),
            "proposal": changes.pop(
                "proposal",
                self.payload(
                    "proposal-default.md",
                    "text/markdown",
                    b"# Approved proposal\nVersion 3.",
                ),
            ),
        }
        values.update(changes)
        return ImportRequest(**values)

    def build_service(self, store=None, repository=None):
        return ProjectImportService(
            object_store=store or FakeObjectStore(),
            repository=repository or FakeRepository(),
            id_factory=SequentialIds(),
            clock=lambda: datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc),
        )

    async def test_valid_import_persists_files_before_atomic_project_and_audit(self):
        store = FakeObjectStore()
        repository = FakeRepository()
        result = await self.build_service(store, repository).import_project(
            self.valid_request(), self.scope
        )

        self.assertFalse(result.replayed)
        self.assertTrue(result.source_artifact_id)
        self.assertTrue(result.proposal_artifact_id)
        self.assertEqual([kind for kind, _ in store.calls], ["source", "proposal"])
        self.assertEqual(repository.transaction_count, 1)
        self.assertEqual(repository.rollbacks, 0)
        self.assertEqual(len(repository.imports), 1)
        self.assertEqual(len(repository.audit_events), 1)
        record = next(iter(repository.imports.values()))
        self.assertEqual(record.status, "planning")
        self.assertEqual(record.approved_proposal_version, 3)
        self.assertEqual(record.approved_by, self.scope.actor_id)
        self.assertEqual(record.approved_at, datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(repository.audit_events[0].actor_id, self.scope.actor_id)

    async def test_import_guard_covers_object_writes_and_database_commit(self):
        store = FakeObjectStore()
        repository = FakeRepository(guard_probe=lambda: store.guard_active)

        await self.build_service(store, repository).import_project(
            self.valid_request(), self.scope
        )

        self.assertEqual(store.guard_events, ["enter", "exit"])
        self.assertFalse(store.guard_active)

    async def test_import_guard_timeout_and_error_are_stable_retryable_object_failures(self):
        for label, guard_error in {
            "timeout": TimeoutError("secret lock path"),
            "runtime": RuntimeError("secret lock path"),
        }.items():
            with self.subTest(label=label):
                store = FakeObjectStore(guard_enter_error=guard_error)
                repository = FakeRepository()
                with self.assertRaises(ImportFailure) as caught:
                    await self.build_service(store, repository).import_project(
                        self.valid_request(), self.scope
                    )
                self.assertEqual(caught.exception.code, "OBJECT_WRITE_FAILED")
                self.assertTrue(caught.exception.retryable)
                self.assertNotIn("secret", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(store.calls, [])
                self.assertEqual(repository.transaction_count, 0)

    async def test_import_guard_exit_error_is_stable_and_releases_after_commit(self):
        store = FakeObjectStore(
            guard_exit_error=RuntimeError("secret cleanup failure")
        )
        repository = FakeRepository()

        with self.assertRaises(ImportFailure) as caught:
            await self.build_service(store, repository).import_project(
                self.valid_request(), self.scope
            )

        self.assertEqual(caught.exception.code, "OBJECT_WRITE_FAILED")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("secret", str(caught.exception))
        self.assertFalse(store.guard_active)
        self.assertEqual(len(repository.imports), 1)
        self.assertEqual(len(repository.audit_events), 1)

    async def test_import_guard_domain_failure_is_preserved(self):
        original = ImportFailure(
            "OBJECT_WRITE_FAILED", "stable domain failure", retryable=True
        )
        store = FakeObjectStore(guard_enter_error=original)
        with self.assertRaises(ImportFailure) as caught:
            await self.build_service(store=store).import_project(
                self.valid_request(), self.scope
            )
        self.assertIs(caught.exception, original)

    async def test_import_guard_cancellation_propagates_and_releases(self):
        entering = FakeObjectStore(guard_enter_error=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.build_service(store=entering).import_project(
                self.valid_request(), self.scope
            )
        self.assertFalse(entering.guard_active)
        self.assertEqual(entering.calls, [])

        exiting = FakeObjectStore(guard_exit_error=asyncio.CancelledError())
        repository = FakeRepository()
        with self.assertRaises(asyncio.CancelledError):
            await self.build_service(exiting, repository).import_project(
                self.valid_request(), self.scope
            )
        self.assertFalse(exiting.guard_active)
        self.assertEqual(len(repository.imports), 1)

    async def test_same_idempotency_key_and_manifest_replays_existing_project(self):
        repository = FakeRepository()
        service = self.build_service(repository=repository)
        first = await service.import_project(self.valid_request(), self.scope)
        second = await service.import_project(self.valid_request(), self.scope)

        self.assertEqual(second.project_id, first.project_id)
        self.assertTrue(second.replayed)
        self.assertEqual(len(repository.imports), 1)
        self.assertEqual(len(repository.audit_events), 1)
        self.assertEqual(repository.claim_calls, 2)

    async def test_concurrent_same_manifest_uses_one_atomic_claim_and_replays_winner(self):
        repository = FakeRepository()
        store = FakeObjectStore()
        service = self.build_service(store=store, repository=repository)

        first, second = await asyncio.gather(
            service.import_project(self.valid_request(), self.scope),
            service.import_project(self.valid_request(), self.scope),
        )

        self.assertEqual(first.project_id, second.project_id)
        self.assertEqual(sorted((first.replayed, second.replayed)), [False, True])
        self.assertEqual(repository.claim_calls, 2)
        self.assertEqual(len(repository.imports), 1)
        self.assertEqual(len(repository.audit_events), 1)

    async def test_concurrent_different_manifest_has_one_winner_and_one_conflict(self):
        repository = FakeRepository()
        service = self.build_service(repository=repository)

        outcomes = await asyncio.gather(
            service.import_project(self.valid_request(), self.scope),
            service.import_project(
                self.valid_request(project_name="Different project"), self.scope
            ),
            return_exceptions=True,
        )

        results = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, ImportFailure)]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].replayed)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(repository.claim_calls, 2)
        self.assertEqual(len(repository.imports), 1)
        self.assertEqual(len(repository.audit_events), 1)

    async def test_idempotency_key_is_normalized_consistently_for_lookup_and_write(self):
        repository = FakeRepository()
        service = self.build_service(repository=repository)
        first = await service.import_project(
            self.valid_request(idempotency_key="  request-003  "), self.scope
        )
        second = await service.import_project(
            self.valid_request(idempotency_key="  request-003  "), self.scope
        )
        self.assertEqual(second.project_id, first.project_id)
        self.assertTrue(second.replayed)
        self.assertEqual(len(repository.imports), 1)

    async def test_same_idempotency_key_with_different_manifest_conflicts(self):
        repository = FakeRepository()
        service = self.build_service(repository=repository)
        await service.import_project(self.valid_request(), self.scope)

        with self.assertRaisesRegex(ImportFailure, "IDEMPOTENCY_CONFLICT"):
            await service.import_project(
                self.valid_request(project_name="Different project"), self.scope
            )
        self.assertEqual(len(repository.imports), 1)

    async def test_repository_scope_leak_cannot_replay_another_clients_project(self):
        repository = FakeRepository(scope_leak=True)
        service = self.build_service(repository=repository)
        await service.import_project(
            self.valid_request(idempotency_key="request-004"), self.scope
        )
        mismatched_scopes = {
            "organization": ScopeContext(
                "00000000-0000-4000-8000-000000000091",
                self.scope.workspace_id,
                self.scope.client_id,
                self.scope.actor_id,
            ),
            "workspace": ScopeContext(
                self.scope.organization_id,
                "00000000-0000-4000-8000-000000000092",
                self.scope.client_id,
                self.scope.actor_id,
            ),
            "client": ScopeContext(
                self.scope.organization_id,
                self.scope.workspace_id,
                "00000000-0000-4000-8000-000000000093",
                self.scope.actor_id,
            ),
        }
        for dimension, mismatched_scope in mismatched_scopes.items():
            with self.subTest(dimension=dimension), self.assertRaisesRegex(
                ImportFailure, "IDEMPOTENCY_CONFLICT"
            ):
                await service.import_project(
                    self.valid_request(idempotency_key="request-004"),
                    mismatched_scope,
                )

    async def test_same_key_and_manifest_have_disjoint_storage_keys_across_tenant_scopes(self):
        for dimension, different_scope in {
            "workspace": ScopeContext(
                self.scope.organization_id,
                "00000000-0000-4000-8000-000000000094",
                self.scope.client_id,
                self.scope.actor_id,
            ),
            "client": ScopeContext(
                self.scope.organization_id,
                self.scope.workspace_id,
                "00000000-0000-4000-8000-000000000095",
                self.scope.actor_id,
            ),
        }.items():
            with self.subTest(dimension=dimension):
                store = FakeObjectStore()
                service = self.build_service(store=store)
                await service.import_project(self.valid_request(), self.scope)
                await service.import_project(self.valid_request(), different_scope)
                first_keys = {key for _, key in store.calls[:2]}
                second_keys = {key for _, key in store.calls[2:]}
                self.assertTrue(first_keys.isdisjoint(second_keys))

    async def test_same_manifest_with_different_keys_uses_distinct_storage_keys(self):
        store = FakeObjectStore()
        service = self.build_service(store=store)
        await service.import_project(
            self.valid_request(idempotency_key="request-005"), self.scope
        )
        await service.import_project(
            self.valid_request(idempotency_key="request-006"), self.scope
        )
        first_keys = {key for _, key in store.calls[:2]}
        second_keys = {key for _, key in store.calls[2:]}
        self.assertTrue(first_keys.isdisjoint(second_keys))

    async def test_unsupported_source_format_fails_before_storage_or_database(self):
        store = FakeObjectStore()
        repository = FakeRepository()
        request = self.valid_request(source=self.payload("brief.pdf", "application/pdf", b"%PDF"))

        with self.assertRaisesRegex(ImportFailure, "UNSUPPORTED_FORMAT"):
            await self.build_service(store, repository).import_project(
                request, self.scope
            )
        self.assertEqual(store.calls, [])
        self.assertEqual(repository.transaction_count, 0)

    async def test_invalid_proposal_version_fails_before_storage(self):
        store = FakeObjectStore()
        with self.assertRaisesRegex(ImportFailure, "INVALID_INPUT"):
            await self.build_service(store=store).import_project(
                self.valid_request(proposal_version=0), self.scope
            )
        self.assertEqual(store.calls, [])

    async def test_explicit_approval_is_required_before_storage(self):
        store = FakeObjectStore()
        with self.assertRaisesRegex(ImportFailure, "APPROVAL_REQUIRED"):
            await self.build_service(store=store).import_project(
                self.valid_request(approval_confirmed=False), self.scope
            )
        self.assertEqual(store.calls, [])

    async def test_invalid_docx_package_is_rejected_before_storage(self):
        store = FakeObjectStore()
        request = self.valid_request(
            proposal=self.payload(
                "proposal.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                b"not a docx package",
            )
        )
        with self.assertRaisesRegex(ImportFailure, "INVALID_DOCUMENT"):
            await self.build_service(store=store).import_project(request, self.scope)
        self.assertEqual(store.calls, [])

    async def test_verified_basic_docx_and_csv_are_accepted(self):
        docx = REPOSITORY_ROOT / "validation" / "fixtures" / "document-parser-spike-001" / "ai-event-brief.docx"
        source = FilePayload(
            "ai-event-brief.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            docx,
        )
        result = await self.build_service().import_project(
            self.valid_request(source=source), self.scope
        )
        self.assertTrue(result.source_version_id)

        csv_source = self.payload("schedule.csv", "text/csv", b"task_id,name\nT-1,Brief\n")
        csv_result = await self.build_service().import_project(
            self.valid_request(idempotency_key="request-002", source=csv_source), self.scope
        )
        self.assertTrue(csv_result.source_version_id)

    async def test_empty_media_type_is_rejected_before_storage(self):
        store = FakeObjectStore()
        request = self.valid_request(source=self.payload("brief.md", "", b"# Brief"))
        with self.assertRaisesRegex(ImportFailure, "INVALID_MEDIA_TYPE"):
            await self.build_service(store=store).import_project(request, self.scope)
        self.assertEqual(store.calls, [])

    async def test_invalid_utf8_after_valid_markdown_or_csv_prefix_is_rejected(self):
        for filename, media_type, content in (
            ("brief.md", "text/markdown", b"# Valid prefix\n\xff"),
            ("brief.csv", "text/csv", b"id,name\n1,Valid\n\xff"),
        ):
            with self.subTest(filename=filename):
                store = FakeObjectStore()
                request = self.valid_request(
                    source=self.payload(filename, media_type, content)
                )
                with self.assertRaisesRegex(ImportFailure, "INVALID_DOCUMENT"):
                    await self.build_service(store=store).import_project(
                        request, self.scope
                    )
                self.assertEqual(store.calls, [])

    async def test_file_size_limit_is_enforced_before_storage(self):
        oversized = self.temp_path / "oversized.md"
        with oversized.open("wb") as output:
            output.seek(MAX_FILE_SIZE_BYTES)
            output.write(b"x")
        store = FakeObjectStore()
        request = self.valid_request(source=FilePayload("oversized.md", "text/markdown", oversized))
        with self.assertRaisesRegex(ImportFailure, "PAYLOAD_TOO_LARGE"):
            await self.build_service(store=store).import_project(request, self.scope)
        self.assertEqual(store.calls, [])

    async def test_object_failure_prevents_database_transaction(self):
        store = FakeObjectStore(fail_on="proposal")
        repository = FakeRepository()

        with self.assertRaisesRegex(ImportFailure, "OBJECT_WRITE_FAILED"):
            await self.build_service(store, repository).import_project(
                self.valid_request(), self.scope
            )
        self.assertEqual(repository.transaction_count, 0)

    async def test_object_store_cancellation_propagates_without_database_access(self):
        store = FakeObjectStore(cancel_on="source")
        repository = FakeRepository()

        with self.assertRaises(asyncio.CancelledError):
            await self.build_service(store, repository).import_project(
                self.valid_request(), self.scope
            )

        self.assertEqual(repository.transaction_count, 0)
        self.assertFalse(store.guard_active)

    async def test_stored_hash_mismatch_prevents_database_transaction(self):
        store = FakeObjectStore(corrupt_on="source")
        repository = FakeRepository()

        with self.assertRaisesRegex(ImportFailure, "OBJECT_INTEGRITY_MISMATCH"):
            await self.build_service(store, repository).import_project(
                self.valid_request(), self.scope
            )
        self.assertEqual(repository.transaction_count, 0)

    async def test_database_failure_rolls_back_project_and_audit_after_durable_files(self):
        store = FakeObjectStore()
        repository = FakeRepository(fail_database=True)

        with self.assertRaisesRegex(ImportFailure, "DATABASE_WRITE_FAILED"):
            await self.build_service(store, repository).import_project(
                self.valid_request(), self.scope
            )
        self.assertEqual(len(store.objects), 2)
        self.assertEqual(repository.imports, {})
        self.assertEqual(repository.audit_events, [])
        self.assertEqual(repository.rollbacks, 1)

    async def test_invalid_created_claim_record_is_a_database_failure(self):
        repository = FakeRepository(invalid_created_claim="record")

        with self.assertRaisesRegex(ImportFailure, "DATABASE_WRITE_FAILED"):
            await self.build_service(repository=repository).import_project(
                self.valid_request(), self.scope
            )

        self.assertEqual(repository.imports, {})
        self.assertEqual(repository.audit_events, [])
        self.assertEqual(repository.rollbacks, 1)

    async def test_invalid_created_claim_marker_is_a_database_failure(self):
        repository = FakeRepository(invalid_created_claim="created_type")

        with self.assertRaisesRegex(ImportFailure, "DATABASE_WRITE_FAILED"):
            await self.build_service(repository=repository).import_project(
                self.valid_request(), self.scope
            )

        self.assertEqual(repository.imports, {})
        self.assertEqual(repository.audit_events, [])
        self.assertEqual(repository.rollbacks, 1)

    async def test_audit_failure_rolls_back_pending_project(self):
        repository = FakeRepository(fail_audit=True)
        with self.assertRaisesRegex(ImportFailure, "DATABASE_WRITE_FAILED"):
            await self.build_service(repository=repository).import_project(
                self.valid_request(), self.scope
            )
        self.assertEqual(repository.imports, {})
        self.assertEqual(repository.audit_events, [])
        self.assertEqual(repository.rollbacks, 1)

    async def test_database_claim_cancellation_propagates_and_rolls_back(self):
        repository = FakeRepository(cancel_on_claim=True)

        with self.assertRaises(asyncio.CancelledError):
            await self.build_service(repository=repository).import_project(
                self.valid_request(), self.scope
            )

        self.assertEqual(repository.imports, {})
        self.assertEqual(repository.audit_events, [])
        self.assertEqual(repository.rollbacks, 1)

    async def test_audit_cancellation_propagates_and_rolls_back_claim(self):
        repository = FakeRepository(cancel_on_audit=True)

        with self.assertRaises(asyncio.CancelledError):
            await self.build_service(repository=repository).import_project(
                self.valid_request(), self.scope
            )

        self.assertEqual(repository.imports, {})
        self.assertEqual(repository.audit_events, [])
        self.assertEqual(repository.rollbacks, 1)

    async def test_commit_failure_is_a_database_failure_and_rolls_back(self):
        repository = FakeRepository(fail_on_commit=True)

        with self.assertRaisesRegex(ImportFailure, "DATABASE_WRITE_FAILED"):
            await self.build_service(repository=repository).import_project(
                self.valid_request(), self.scope
            )

        self.assertEqual(repository.imports, {})
        self.assertEqual(repository.audit_events, [])
        self.assertEqual(repository.rollbacks, 1)

    async def test_commit_cancellation_propagates_and_rolls_back(self):
        store = FakeObjectStore()
        repository = FakeRepository(cancel_on_commit=True)

        with self.assertRaises(asyncio.CancelledError):
            await self.build_service(store=store, repository=repository).import_project(
                self.valid_request(), self.scope
            )

        self.assertEqual(repository.imports, {})
        self.assertEqual(repository.audit_events, [])
        self.assertEqual(repository.rollbacks, 1)
        self.assertFalse(store.guard_active)

    async def test_openapi_length_limits_are_enforced_by_core_service(self):
        with self.assertRaisesRegex(ImportFailure, "INVALID_INPUT"):
            await self.build_service().import_project(
                self.valid_request(idempotency_key="short"), self.scope
            )
        with self.assertRaisesRegex(ImportFailure, "INVALID_INPUT"):
            await self.build_service().import_project(
                self.valid_request(project_name="x" * 301), self.scope
            )

    async def test_request_cannot_supply_authorization_scope(self):
        request_fields = {field.name for field in fields(ImportRequest)}
        self.assertTrue({"workspace_id", "client_id", "actor_id", "organization_id"}.isdisjoint(request_fields))

    async def test_invalid_server_scope_fails_before_storage(self):
        store = FakeObjectStore()
        invalid_scope = ScopeContext("org", "../workspace", "client", "actor")
        with self.assertRaisesRegex(ImportFailure, "AUTHORIZATION_REQUIRED"):
            await self.build_service(store=store).import_project(
                self.valid_request(), invalid_scope
            )
        self.assertEqual(store.calls, [])

    async def test_id_factory_must_return_uuid_values(self):
        store = FakeObjectStore()
        repository = FakeRepository()
        generated = SequentialIds()

        def invalid_last_required_id():
            generated.value += 1
            if generated.value == 6:
                return "id-6"
            return str(UUID(int=generated.value, version=4))

        service = ProjectImportService(
            object_store=store,
            repository=repository,
            id_factory=invalid_last_required_id,
            clock=lambda: datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(ImportFailure, "INVALID_SERVER_ID"):
            await service.import_project(self.valid_request(), self.scope)
        self.assertEqual(store.calls, [])
        self.assertEqual(repository.transaction_count, 0)

    async def test_invalid_clock_fails_before_storage_or_database(self):
        invalid_clocks = {
            "naive": lambda: datetime(2026, 8, 9, 4, 0),
            "wrong_type": lambda: "2026-08-09T04:00:00Z",
            "raises": lambda: (_ for _ in ()).throw(RuntimeError("clock unavailable")),
        }
        for case, clock in invalid_clocks.items():
            with self.subTest(case=case):
                store = FakeObjectStore()
                repository = FakeRepository()
                service = ProjectImportService(
                    object_store=store,
                    repository=repository,
                    id_factory=SequentialIds(),
                    clock=clock,
                )
                with self.assertRaisesRegex(ImportFailure, "INVALID_SERVER_CLOCK"):
                    await service.import_project(self.valid_request(), self.scope)
                self.assertEqual(store.calls, [])
                self.assertEqual(repository.transaction_count, 0)


class OpenApiContractCheckerTests(unittest.TestCase):
    def test_every_stable_error_code_is_required_by_the_checker(self):
        contract_path = (
            REPOSITORY_ROOT
            / "apps"
            / "api"
            / "openapi"
            / "project-import.openapi.yaml"
        )
        document = json.loads(contract_path.read_text(encoding="utf-8"))
        for code in sorted(STABLE_ERROR_CODES):
            with self.subTest(code=code):
                mutated = copy.deepcopy(document)
                error_codes = mutated["components"]["schemas"]["ImportError"][
                    "properties"
                ]["code"]["enum"]
                error_codes.remove(code)
                self.assertIn("stable error code enum", openapi_contract.validate(mutated))


if __name__ == "__main__":
    unittest.main()
