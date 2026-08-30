#!/usr/bin/env python3
"""Exercise dry-run-first, race-safe M1-01 orphan cleanup on Linux CI."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.marketops_import.cleanup import (  # noqa: E402
    CleanupFailure,
    CompleteReferenceReader,
    OrphanCleaner,
    ReferenceSnapshot,
    cleanup_plan_sha256,
    serialize_cleanup_plan,
)
from apps.api.marketops_import.postgres import AsyncpgImportRepository  # noqa: E402
from apps.api.marketops_import.service import (  # noqa: E402
    ImportFailure,
    ProjectImportService,
    ScopeContext,
    StoredObject,
)
from apps.api.marketops_import.storage import LocalObjectStore  # noqa: E402
from scripts.run_m1_01_restart_recovery_gate import (  # noqa: E402
    append_record,
    fixture_paths,
    identifier,
    import_request,
    load_state,
    make_connection_loss_record,
    project_snapshot,
    required_environment,
    scope_from_state,
    validate_uncommitted_snapshot,
    write_json,
)


OBSERVATION_INTERVAL = timedelta(microseconds=1)
PLAN_LIFETIME = timedelta(minutes=5)
PROCESS_TIMEOUT_SECONDS = 20.0
RACE_IDEMPOTENCY_KEY = "wp5c-concurrent-import"
POST_PLAN_IDEMPOTENCY_KEY = "wp5c-plan-after-new"
EVIDENCE_KEYS = {
    "schemaVersion",
    "taskId",
    "workPackage",
    "generatedAt",
    "dryRun",
    "failureMatrix",
    "crossProcessRace",
    "integrity",
    "claimBoundary",
}
DRY_RUN_EVIDENCE_KEYS = {
    "payloadUnchanged",
    "candidateCount",
    "canonicalPlanHashBound",
}
FAILURE_MATRIX_EVIDENCE_KEYS = {
    "wrongExpectedHash",
    "tamperedPayload",
    "candidateIdentityDrift",
    "symbolicLinkLayout",
    "specialFileLayout",
    "concurrentCleanup",
    "referenceReaderUnavailable",
    "deleteFailureRecovery",
    "cancellation",
}
RACE_EVIDENCE_KEYS = {
    "actualFlockContentionObserved",
    "newlyReferencedRetainedCount",
    "permanentOrphanDeletedCount",
    "planAfterNewRetainedCount",
}
INTEGRITY_EVIDENCE_KEYS = {
    "databaseReferenceCount",
    "allReferencedObjectsSizeAndSha256Verified",
    "failedTransactionRolledBack",
}


class _AlwaysFailRepository:
    @asynccontextmanager
    async def transaction(self, _scope: ScopeContext):
        raise RuntimeError("intentional gate transaction failure")
        yield  # pragma: no cover


class _RecordingObjectStore(LocalObjectStore):
    def __init__(self, root: Path | str):
        super().__init__(root)
        self.stored: list[StoredObject] = []

    async def put_immutable(self, **kwargs: Any) -> StoredObject:
        stored = await super().put_immutable(**kwargs)
        self.stored.append(stored)
        return stored


class _PausingObjectStore(LocalObjectStore):
    def __init__(self, root: Path | str, ready: Path, release: Path):
        super().__init__(root)
        self.ready = ready
        self.release = release

    async def put_immutable(self, **kwargs: Any) -> StoredObject:
        stored = await super().put_immutable(**kwargs)
        if kwargs.get("kind") == "proposal":
            self.ready.write_text("ready\n", encoding="ascii")
            await _wait_for_path(self.release, timeout=PROCESS_TIMEOUT_SECONDS)
        return stored


class _AdvancingEmptyReferenceReader(CompleteReferenceReader):
    def __init__(self):
        self._observed_at = datetime.now(timezone.utc)

    async def snapshot_references(self) -> ReferenceSnapshot:
        self._observed_at += timedelta(seconds=1)
        return ReferenceSnapshot(self._observed_at, (), complete=True)


class _UnavailableReferenceReader(CompleteReferenceReader):
    async def snapshot_references(self) -> ReferenceSnapshot:
        raise RuntimeError("intentional test-only reference outage")


class _BlockingReferenceReader(CompleteReferenceReader):
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def snapshot_references(self) -> ReferenceSnapshot:
        self.entered.set()
        await self.release.wait()
        return ReferenceSnapshot(datetime.now(timezone.utc), (), complete=True)


class PostgresCompleteReferenceReader(CompleteReferenceReader):
    """Test-only RLS-bypass reader for the current workspace/client scope."""

    def __init__(self, admin_dsn: str, workspace_id: str, client_id: str):
        self._admin_dsn = admin_dsn
        self._workspace_id = workspace_id
        self._client_id = client_id

    async def snapshot_references(self) -> ReferenceSnapshot:
        try:
            import asyncpg
        except ImportError as error:  # pragma: no cover - runtime gate only
            raise RuntimeError("asyncpg is required for the orphan cleanup gate") from error
        connection = await asyncpg.connect(self._admin_dsn)
        try:
            bypass = await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            if bypass is not True:
                raise RuntimeError("cleanup gate requires a test-only complete reference reader")
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                observed_at = await connection.fetchval("SELECT clock_timestamp()")
                rows = await connection.fetch(
                    """
                    SELECT storage_key, byte_size,
                           pg_catalog.encode(sha256, 'hex') AS sha256
                    FROM marketops.artifact_versions
                    WHERE workspace_id = $1 AND client_id = $2
                    ORDER BY storage_key
                    """,
                    self._workspace_id,
                    self._client_id,
                )
        finally:
            await connection.close()
        return ReferenceSnapshot(
            observed_at=observed_at,
            references=tuple(
                StoredObject(
                    storage_key=str(row["storage_key"]),
                    size_bytes=int(row["byte_size"]),
                    sha256=str(row["sha256"]),
                )
                for row in rows
            ),
            complete=True,
        )


def physical_key(stored: StoredObject) -> str:
    return hashlib.sha256(stored.storage_key.encode("ascii")).hexdigest()


def managed_payload_inventory(root: Path) -> dict[str, tuple[int, str]]:
    """Return size/hash only for canonical payload paths, ignoring lock metadata."""
    inventory: dict[str, tuple[int, str]] = {}
    if not root.exists():
        return inventory
    for shard in sorted(root.iterdir(), key=lambda item: item.name):
        if len(shard.name) != 2 or any(ch not in "0123456789abcdef" for ch in shard.name):
            continue
        if not shard.is_dir() or shard.is_symlink():
            continue
        for item in sorted(shard.iterdir(), key=lambda entry: entry.name):
            key = shard.name + item.name
            if len(key) != 64 or any(ch not in "0123456789abcdef" for ch in key):
                continue
            if not item.is_file() or item.is_symlink():
                continue
            content = item.read_bytes()
            inventory[key] = (len(content), hashlib.sha256(content).hexdigest())
    return inventory


def validate_non_sensitive_evidence(evidence: dict[str, Any]) -> None:
    if set(evidence) != EVIDENCE_KEYS:
        raise RuntimeError("cleanup evidence fields do not match the public contract")
    nested_contracts = {
        "dryRun": DRY_RUN_EVIDENCE_KEYS,
        "failureMatrix": FAILURE_MATRIX_EVIDENCE_KEYS,
        "crossProcessRace": RACE_EVIDENCE_KEYS,
        "integrity": INTEGRITY_EVIDENCE_KEYS,
    }
    for field, expected in nested_contracts.items():
        if not isinstance(evidence[field], dict) or set(evidence[field]) != expected:
            raise RuntimeError("cleanup evidence nested fields do not match the public contract")

    def strings(value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield str(key)
                yield from strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from strings(item)

    for value in strings(evidence):
        lowered = value.lower()
        path_like = (
            value.startswith(("/", "\\\\"))
            or (len(value) >= 3 and value[0].isalpha() and value[1] == ":" and value[2] in "\\/")
        )
        if (
            "postgresql://" in lowered
            or "postgres://" in lowered
            or "marketops-ci-admin" in lowered
            or "marketops-ci-app" in lowered
            or path_like
        ):
            raise RuntimeError("cleanup evidence contains a sensitive value or absolute path")


async def _wait_for_path(path: Path, *, timeout: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if path.is_file():
            return
        await asyncio.sleep(0.05)
    raise RuntimeError("cleanup gate child-process handshake timed out")


def _write_atomic_marker(path: Path) -> None:
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    temporary.write_text("observed\n", encoding="ascii")
    os.replace(temporary, path)


async def _wait_process(process: subprocess.Popen[str], *, label: str) -> None:
    try:
        return_code = await asyncio.wait_for(
            asyncio.to_thread(process.wait), timeout=PROCESS_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as error:
        process.kill()
        await asyncio.to_thread(process.wait)
        raise RuntimeError(f"{label} child process timed out") from error
    if return_code != 0:
        raise RuntimeError(f"{label} child process failed")


def _child_command(work_root: Path, output: Path, mode: str) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--work-root",
        str(work_root),
        "--output",
        str(output),
        "--child-mode",
        mode,
    ]


async def _expect_cleanup_code(
    awaitable: Any,
    expected: str,
    *,
    retryable: bool | None = None,
) -> str:
    try:
        await awaitable
    except CleanupFailure as error:
        if (
            error.code != expected
            or error.recovery_state != "unchanged"
            or (retryable is not None and error.retryable is not retryable)
        ):
            raise RuntimeError("cleanup failure did not match its fail-closed contract") from error
        return error.code
    raise RuntimeError("cleanup negative path unexpectedly succeeded")


async def _make_failed_transaction_orphans(
    asyncpg: Any,
    app_dsn: str,
    store: LocalObjectStore,
    work_root: Path,
    scope: ScopeContext,
) -> tuple[StoredObject, StoredObject]:
    repository = await AsyncpgImportRepository.create(app_dsn, min_size=1, max_size=1)
    record = None
    try:
        try:
            async with store.import_guard():
                record = await make_connection_loss_record(
                    store, work_root / "wp5c-failed-transaction", scope
                )
                async with repository.transaction(scope) as transaction:
                    await append_record(transaction, scope, record)
                    raise ImportFailure(
                        "EXPECTED_TEST_ROLLBACK",
                        "the WP5C gate intentionally rolls back this synthetic import",
                    )
        except ImportFailure as error:
            if error.code != "EXPECTED_TEST_ROLLBACK":
                raise RuntimeError(
                    "failed-transaction orphan setup returned an unexpected failure"
                ) from error
        else:
            raise RuntimeError("failed-transaction orphan setup unexpectedly committed")
    finally:
        await repository.close()
    if record is None:
        raise RuntimeError("failed-transaction orphan setup did not retain objects")
    rolled_back, _ = await project_snapshot(
        asyncpg,
        required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL"),
        organization_id=scope.organization_id,
        project_id=record.project_id,
        expected_proposal_artifact_id=record.proposal_artifact_id,
        expected_proposal_version_id=record.proposal_version_id,
    )
    validate_uncommitted_snapshot(rolled_back)
    return record.source, record.proposal


async def _make_service_orphans(
    object_root: Path,
    fixture_root: Path,
    scope: ScopeContext,
    *,
    idempotency_key: str,
) -> tuple[StoredObject, StoredObject]:
    fixture_paths(fixture_root)
    store = _RecordingObjectStore(object_root)
    service = ProjectImportService(
        object_store=store,
        repository=_AlwaysFailRepository(),
        id_factory=identifier,
        clock=lambda: datetime.now(timezone.utc),
    )
    try:
        await service.import_project(
            import_request(fixture_root, idempotency_key=idempotency_key), scope
        )
    except ImportFailure as error:
        if error.code != "DATABASE_WRITE_FAILED" or error.retryable is not True:
            raise RuntimeError("service orphan setup returned an unexpected failure") from error
    else:
        raise RuntimeError("service orphan setup unexpectedly committed")
    if len(store.stored) != 2:
        raise RuntimeError("service orphan setup did not retain both immutable objects")
    return tuple(store.stored)  # type: ignore[return-value]


async def _identity_drift_check(
    cleaner: OrphanCleaner,
    store: LocalObjectStore,
    object_root: Path,
    fixture_root: Path,
    scope: ScopeContext,
) -> str:
    drift_objects = await _make_service_orphans(
        object_root,
        fixture_root,
        scope,
        idempotency_key="wp5c-identity-drift",
    )
    plan = await cleaner.dry_run()
    payload = serialize_cleanup_plan(plan)
    drift_path = store._destination(
        PurePosixPath(drift_objects[0].storage_key).parts[-2],
        drift_objects[0].storage_key,
        drift_objects[0].sha256,
    )
    metadata = drift_path.stat()
    os.utime(drift_path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000))
    code = await _expect_cleanup_code(
        cleaner.apply(payload, cleanup_plan_sha256(payload)), "CLEANUP_OBJECT_CHANGED"
    )
    for stored in drift_objects:
        path = store._destination(
            PurePosixPath(stored.storage_key).parts[-2], stored.storage_key, stored.sha256
        )
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
    return code


async def _linux_failure_matrix(
    work_root: Path,
    scope: ScopeContext,
) -> dict[str, str]:
    failure_root = work_root / "wp5c-linux-failure-root"
    if failure_root.exists():
        raise RuntimeError("cleanup failure-matrix root already exists")
    store = LocalObjectStore(failure_root)
    reader = _AdvancingEmptyReferenceReader()
    cleaner = OrphanCleaner(
        store,
        reader,
        observation_interval=OBSERVATION_INTERVAL,
        plan_lifetime=PLAN_LIFETIME,
        lock_timeout=1.0,
    )
    failure_root.mkdir(parents=True)
    baseline = managed_payload_inventory(failure_root)

    external = work_root / "wp5c-external-sentinel"
    external.write_bytes(b"outside cleanup root\n")
    external_digest = hashlib.sha256(external.read_bytes()).hexdigest()
    unsafe_link = failure_root / "unexpected-link"
    unsafe_link.symlink_to(external)
    symlink_code = await _expect_cleanup_code(
        cleaner.dry_run(), "UNSAFE_OBJECT_LAYOUT"
    )
    if (
        hashlib.sha256(external.read_bytes()).hexdigest() != external_digest
        or managed_payload_inventory(failure_root) != baseline
    ):
        raise RuntimeError("symbolic-link rejection changed payload or external target")
    unsafe_link.unlink()

    fifo = failure_root / "unexpected-fifo"
    os.mkfifo(fifo, mode=0o600)
    special_code = await _expect_cleanup_code(
        cleaner.dry_run(), "UNSAFE_OBJECT_LAYOUT"
    )
    if managed_payload_inventory(failure_root) != baseline:
        raise RuntimeError("special-file rejection changed object payloads")
    fifo.unlink()

    unavailable = OrphanCleaner(
        store,
        _UnavailableReferenceReader(),
        observation_interval=OBSERVATION_INTERVAL,
        plan_lifetime=PLAN_LIFETIME,
        lock_timeout=1.0,
    )
    unavailable_code = await _expect_cleanup_code(
        unavailable.dry_run(), "REFERENCE_SNAPSHOT_FAILED", retryable=True
    )
    if managed_payload_inventory(failure_root) != baseline:
        raise RuntimeError("reference outage changed object payloads")

    busy_cleaner = OrphanCleaner(
        store,
        reader,
        observation_interval=OBSERVATION_INTERVAL,
        plan_lifetime=PLAN_LIFETIME,
        lock_timeout=0.05,
    )
    async with store.cleanup_guard(timeout_seconds=1.0):
        busy_code = await _expect_cleanup_code(
            busy_cleaner.dry_run(), "CLEANUP_LOCK_BUSY", retryable=True
        )
    if managed_payload_inventory(failure_root) != baseline:
        raise RuntimeError("concurrent-cleanup rejection changed object payloads")

    cancel_root = work_root / "wp5c-cancellation-root"
    cancel_objects = await _make_service_orphans(
        cancel_root,
        work_root / "wp5c-cancellation-fixtures",
        scope,
        idempotency_key="wp5c-cancellation",
    )
    cancel_store = LocalObjectStore(cancel_root)
    cancel_plan_reader = _AdvancingEmptyReferenceReader()
    cancel_plan_cleaner = OrphanCleaner(
        cancel_store,
        cancel_plan_reader,
        observation_interval=OBSERVATION_INTERVAL,
        plan_lifetime=PLAN_LIFETIME,
        lock_timeout=1.0,
    )
    cancel_plan = await cancel_plan_cleaner.dry_run()
    cancel_payload = serialize_cleanup_plan(cancel_plan)
    cancel_before = managed_payload_inventory(cancel_root)
    blocking_reader = _BlockingReferenceReader()
    cancelling_cleaner = OrphanCleaner(
        cancel_store,
        blocking_reader,
        observation_interval=OBSERVATION_INTERVAL,
        plan_lifetime=PLAN_LIFETIME,
        lock_timeout=1.0,
    )
    cancellation = asyncio.create_task(
        cancelling_cleaner.apply(cancel_payload, cleanup_plan_sha256(cancel_payload))
    )
    await asyncio.wait_for(blocking_reader.entered.wait(), timeout=5.0)
    cancellation.cancel()
    cancelled = await asyncio.gather(cancellation, return_exceptions=True)
    if len(cancelled) != 1 or not isinstance(cancelled[0], asyncio.CancelledError):
        raise RuntimeError("cleanup cancellation did not propagate")
    if managed_payload_inventory(cancel_root) != cancel_before:
        raise RuntimeError("cleanup cancellation changed planned object payloads")
    async with cancel_store.cleanup_guard(timeout_seconds=1.0):
        pass
    if {physical_key(item) for item in cancel_objects} != set(cancel_before):
        raise RuntimeError("cleanup cancellation fixture is incomplete")

    delete_root = work_root / "wp5c-delete-failure-root"
    delete_objects = await _make_service_orphans(
        delete_root,
        work_root / "wp5c-delete-failure-fixtures",
        scope,
        idempotency_key="wp5c-delete-failure",
    )
    delete_store = LocalObjectStore(delete_root)
    delete_reader = _AdvancingEmptyReferenceReader()
    delete_cleaner = OrphanCleaner(
        delete_store,
        delete_reader,
        observation_interval=OBSERVATION_INTERVAL,
        plan_lifetime=PLAN_LIFETIME,
        lock_timeout=1.0,
    )
    delete_plan = await delete_cleaner.dry_run()
    delete_payload = serialize_cleanup_plan(delete_plan)
    delete_before = managed_payload_inventory(delete_root)
    real_unlink = Path.unlink
    deleted_targets = 0

    def fail_second_quarantine_delete(path: Path, *args: Any, **kwargs: Any):
        nonlocal deleted_targets
        if ".cleanup-quarantine" in path.parts and len(path.name) == 64:
            deleted_targets += 1
            if deleted_targets == 2:
                raise OSError("intentional WP5C quarantine delete failure")
        return real_unlink(path, *args, **kwargs)

    with mock.patch.object(Path, "unlink", new=fail_second_quarantine_delete):
        try:
            await delete_cleaner.apply(
                delete_payload, cleanup_plan_sha256(delete_payload)
            )
        except CleanupFailure as error:
            if (
                error.code != "CLEANUP_APPLY_FAILED"
                or error.recovery_state != "moved=2,deleted=1,restored=1"
            ):
                raise RuntimeError("delete failure recovery state is incorrect") from error
            delete_code = error.code + ":partial-recovery-recorded"
        else:
            raise RuntimeError("injected quarantine delete failure unexpectedly succeeded")
    delete_after = managed_payload_inventory(delete_root)
    planned_delete_keys = {physical_key(item) for item in delete_objects}
    if (
        len(delete_before) != 2
        or len(delete_after) != 1
        or not set(delete_after).issubset(planned_delete_keys)
        or hashlib.sha256(external.read_bytes()).hexdigest() != external_digest
    ):
        raise RuntimeError("delete failure affected an unplanned object or external target")

    return {
        "symbolicLinkLayout": symlink_code,
        "specialFileLayout": special_code,
        "concurrentCleanup": busy_code,
        "referenceReaderUnavailable": unavailable_code,
        "deleteFailureRecovery": delete_code,
        "cancellation": "propagated-no-mutation-lock-released",
    }


async def _run_importer_child(work_root: Path, output: Path) -> None:
    state = load_state(work_root / "state.json")
    scope = scope_from_state(state)
    ready = work_root / "wp5c-ipc" / "importer.ready"
    release = work_root / "wp5c-ipc" / "importer.release"
    app_dsn = required_environment("MARKETOPS_TEST_DATABASE_URL")
    repository = await AsyncpgImportRepository.create(app_dsn, min_size=1, max_size=1)
    try:
        service = ProjectImportService(
            object_store=_PausingObjectStore(work_root / "objects", ready, release),
            repository=repository,
            id_factory=identifier,
            clock=lambda: datetime.now(timezone.utc),
        )
        result = await service.import_project(
            import_request(work_root, idempotency_key=RACE_IDEMPOTENCY_KEY), scope
        )
    finally:
        await repository.close()
    write_json(output, {"committed": not result.replayed})


async def _run_apply_child(work_root: Path, output: Path) -> None:
    ipc = work_root / "wp5c-ipc"
    payload = (ipc / "plan.json").read_bytes()
    expected_sha256 = (ipc / "plan.sha256").read_text(encoding="ascii").strip()
    cleaner = OrphanCleaner(
        LocalObjectStore(work_root / "objects"),
        PostgresCompleteReferenceReader(
            required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL"),
            load_state(work_root / "state.json")["scope"]["workspaceId"],
            load_state(work_root / "state.json")["scope"]["clientId"],
        ),
        observation_interval=OBSERVATION_INTERVAL,
        plan_lifetime=PLAN_LIFETIME,
        lock_timeout=10.0,
    )
    import fcntl

    blocked_marker = ipc / "cleanup.blocked"
    real_flock = fcntl.flock
    contention_observed = False

    def observed_flock(descriptor: int, operation: int):
        nonlocal contention_observed
        try:
            return real_flock(descriptor, operation)
        except BlockingIOError:
            if not contention_observed:
                contention_observed = True
                _write_atomic_marker(blocked_marker)
            raise

    with mock.patch("fcntl.flock", new=observed_flock):
        result = await cleaner.apply(payload, expected_sha256)
    if not contention_observed:
        raise RuntimeError("cleanup apply acquired the lock without observed contention")
    write_json(
        output,
        {
            "deletedCount": len(result.deleted_physical_keys),
            "retainedReferencedCount": len(result.retained_referenced_physical_keys),
            "deleted": list(result.deleted_physical_keys),
            "retained": list(result.retained_referenced_physical_keys),
        },
    )


async def run(work_root: Path, output: Path, state_path: Path | None = None) -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("orphan cleanup runtime gate requires Linux")
    try:
        import asyncpg
    except ImportError as error:
        raise RuntimeError("asyncpg is required for the orphan cleanup gate") from error
    state_path = state_path or work_root / "state.json"
    if state_path.resolve() != (work_root / "state.json").resolve():
        shutil.copyfile(state_path, work_root / "state.json")
    state = load_state(work_root / "state.json")
    scope = scope_from_state(state)
    object_root = work_root / "objects"
    store = LocalObjectStore(object_root)
    admin_dsn = required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    app_dsn = required_environment("MARKETOPS_TEST_DATABASE_URL")
    reader = PostgresCompleteReferenceReader(
        admin_dsn, state["scope"]["workspaceId"], state["scope"]["clientId"]
    )
    cleaner = OrphanCleaner(
        store,
        reader,
        observation_interval=OBSERVATION_INTERVAL,
        plan_lifetime=PLAN_LIFETIME,
        lock_timeout=10.0,
    )

    failed_orphans = await _make_failed_transaction_orphans(
        asyncpg, app_dsn, store, work_root, scope
    )
    race_orphans = await _make_service_orphans(
        object_root,
        work_root,
        scope,
        idempotency_key=RACE_IDEMPOTENCY_KEY,
    )
    identity_code = await _identity_drift_check(
        cleaner, store, object_root, work_root, scope
    )
    linux_failures = await _linux_failure_matrix(work_root, scope)

    before_dry_run = managed_payload_inventory(object_root)
    plan = await cleaner.dry_run()
    plan_payload = serialize_cleanup_plan(plan)
    plan_sha256 = cleanup_plan_sha256(plan_payload)
    after_dry_run = managed_payload_inventory(object_root)
    if before_dry_run != after_dry_run:
        raise RuntimeError("dry-run changed immutable object payloads")
    planned = {candidate.physical_key for candidate in plan.candidates}
    expected_candidates = {physical_key(item) for item in failed_orphans + race_orphans}
    if planned != expected_candidates:
        raise RuntimeError("dry-run candidate set did not match the isolated gate fixture")

    wrong_hash_code = await _expect_cleanup_code(
        cleaner.apply(plan_payload, "0" * 64), "CLEANUP_PLAN_TAMPERED"
    )
    tampered = plan_payload[:-1] + (b" " if plan_payload[-1:] != b" " else b"\n")
    tampered_code = await _expect_cleanup_code(
        cleaner.apply(tampered, plan_sha256), "CLEANUP_PLAN_TAMPERED"
    )
    if managed_payload_inventory(object_root) != before_dry_run:
        raise RuntimeError("rejected plans changed immutable object payloads")

    post_plan_orphans = await _make_service_orphans(
        object_root,
        work_root,
        scope,
        idempotency_key=POST_PLAN_IDEMPOTENCY_KEY,
    )
    post_plan_keys = {physical_key(item) for item in post_plan_orphans}

    ipc = work_root / "wp5c-ipc"
    ipc.mkdir(parents=True, exist_ok=True)
    plan_path = ipc / "plan.json"
    plan_path.write_bytes(plan_payload)
    (ipc / "plan.sha256").write_text(plan_sha256 + "\n", encoding="ascii")
    importer_output = ipc / "importer.json"
    apply_output = ipc / "apply.json"
    ready = ipc / "importer.ready"
    release = ipc / "importer.release"
    blocked = ipc / "cleanup.blocked"
    for path in (importer_output, apply_output, ready, release, blocked):
        path.unlink(missing_ok=True)

    log_sink = subprocess.DEVNULL
    importer = subprocess.Popen(
        _child_command(work_root, importer_output, "importer"),
        stdout=log_sink,
        stderr=log_sink,
        text=True,
    )
    await _wait_for_path(ready, timeout=PROCESS_TIMEOUT_SECONDS)
    applier = subprocess.Popen(
        _child_command(work_root, apply_output, "apply"),
        stdout=log_sink,
        stderr=log_sink,
        text=True,
    )
    await _wait_for_path(blocked, timeout=PROCESS_TIMEOUT_SECONDS)
    if applier.poll() is not None:
        importer.kill()
        await asyncio.to_thread(importer.wait)
        raise RuntimeError("cleanup apply exited after reporting lock contention")
    release.write_text("release\n", encoding="ascii")
    await _wait_process(importer, label="importer")
    await _wait_process(applier, label="cleanup apply")

    importer_result = json.loads(importer_output.read_text(encoding="utf-8"))
    apply_result = json.loads(apply_output.read_text(encoding="utf-8"))
    if importer_result != {"committed": True}:
        raise RuntimeError("concurrent importer did not commit exactly once")
    failed_keys = {physical_key(item) for item in failed_orphans}
    race_keys = {physical_key(item) for item in race_orphans}
    if set(apply_result.get("deleted", ())) != failed_keys:
        raise RuntimeError("cleanup did not delete exactly the permanent orphan group")
    if set(apply_result.get("retained", ())) != race_keys:
        raise RuntimeError("cleanup did not retain exactly the newly referenced race group")
    final_inventory = managed_payload_inventory(object_root)
    if failed_keys.intersection(final_inventory):
        raise RuntimeError("permanent failed-transaction orphans survived apply")
    if not (race_keys | post_plan_keys).issubset(final_inventory):
        raise RuntimeError("race or plan-after-new objects were incorrectly deleted")

    final_snapshot = await reader.snapshot_references()
    if len(final_snapshot.references) != 4:
        raise RuntimeError("final database reference set is not the isolated four-object fixture")
    for stored in final_snapshot.references:
        kind = PurePosixPath(stored.storage_key).parts[-2]
        await store.verify_immutable(kind=kind, stored=stored)

    evidence = {
        "schemaVersion": 1,
        "taskId": "M1-01",
        "workPackage": "WP5C-orphan-cleanup",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dryRun": {
            "payloadUnchanged": True,
            "candidateCount": len(plan.candidates),
            "canonicalPlanHashBound": True,
        },
        "failureMatrix": {
            "wrongExpectedHash": wrong_hash_code,
            "tamperedPayload": tampered_code,
            "candidateIdentityDrift": identity_code,
            **linux_failures,
        },
        "crossProcessRace": {
            "actualFlockContentionObserved": True,
            "newlyReferencedRetainedCount": len(race_keys),
            "permanentOrphanDeletedCount": len(failed_keys),
            "planAfterNewRetainedCount": len(post_plan_keys),
        },
        "integrity": {
            "databaseReferenceCount": len(final_snapshot.references),
            "allReferencedObjectsSizeAndSha256Verified": True,
            "failedTransactionRolledBack": True,
        },
        "claimBoundary": (
            "This isolated Linux CI experiment establishes dry-run-first cleanup for the "
            "tested local adapter, cooperative flock importers, a same-filesystem quarantine, "
            "and a privileged complete-reference reader. It does not establish Windows or "
            "network-filesystem safety, a production least-privilege maintenance role, "
            "crash-recoverable quarantine, legal retention, storage quotas, production "
            "reliability, M1-01 completion, demand, ROI, repeat use, or payment."
        ),
    }
    validate_non_sensitive_evidence(evidence)
    write_json(output, evidence)
    print(output.read_text(encoding="utf-8"), end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument(
        "--child-mode", choices=("importer", "apply"), help=argparse.SUPPRESS
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.child_mode == "importer":
        asyncio.run(_run_importer_child(args.work_root, args.output))
    elif args.child_mode == "apply":
        asyncio.run(_run_apply_child(args.work_root, args.output))
    else:
        asyncio.run(run(args.work_root, args.output, args.state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
