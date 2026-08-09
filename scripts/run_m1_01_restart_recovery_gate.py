#!/usr/bin/env python3
"""Exercise M1-01 restart persistence and uncommitted connection loss in CI."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.marketops_import.postgres import (  # noqa: E402
    AsyncpgImportRepository,
    PostgresAdapterError,
)
from apps.api.marketops_import.service import (  # noqa: E402
    AuditEvent,
    FilePayload,
    ImportRequest,
    ProjectImportRecord,
    ProjectImportService,
    ScopeContext,
    StoredObject,
)
from apps.api.marketops_import.storage import LocalObjectStore  # noqa: E402


CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}")
LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")
TABLE_COUNTS = {
    "projects": 1,
    "artifacts": 2,
    "artifactVersions": 2,
    "auditEvents": 1,
}
SOURCE_CONTENT = "# WP5A synthetic source\n"
PROPOSAL_CONTENT = "# WP5A approved proposal\n"


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required CI environment variable: {name}")
    return value


def validate_container_id(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if CONTAINER_ID.fullmatch(normalized) is None:
        raise RuntimeError("PostgreSQL service container ID is invalid")
    return normalized


def restart_postgres_container(
    container_id: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    validated = validate_container_id(container_id)
    try:
        runner(
            ["docker", "restart", validated],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("PostgreSQL service-container restart failed") from error


def validate_committed_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("counts") != TABLE_COUNTS:
        raise RuntimeError("committed import row counts did not survive restart")
    if snapshot.get("approvedProposalSelected") is not True:
        raise RuntimeError("approved proposal selection did not survive restart")
    if snapshot.get("artifactKinds") != ["proposal", "source"]:
        raise RuntimeError("committed artifact versions are incomplete after restart")


def validate_uncommitted_snapshot(snapshot: dict[str, Any]) -> None:
    expected = {name: 0 for name in TABLE_COUNTS}
    if snapshot.get("counts") != expected:
        raise RuntimeError("connection loss left partial uncommitted rows")


def identifier() -> str:
    return str(uuid4())


def canonical_uuid(value: Any, label: str) -> str:
    try:
        parsed = UUID(str(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError(f"restart state has an invalid {label}") from error
    if str(parsed) != value:
        raise RuntimeError(f"restart state has a non-canonical {label}")
    return value


def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        scope = state["scope"]
        committed = state["committed"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("restart state is missing or malformed") from error
    if state.get("schemaVersion") != 1 or state.get("taskId") != "M1-01":
        raise RuntimeError("restart state has an unsupported contract")
    for name in ("organizationId", "workspaceId", "clientId", "actorId"):
        canonical_uuid(scope.get(name), name)
    for name in (
        "projectId",
        "sourceArtifactId",
        "sourceVersionId",
        "proposalArtifactId",
        "proposalVersionId",
    ):
        canonical_uuid(committed.get(name), name)
    if LOWER_SHA256.fullmatch(str(committed.get("manifestSha256", ""))) is None:
        raise RuntimeError("restart state has an invalid manifest hash")
    return state


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def scope_from_state(state: dict[str, Any]) -> ScopeContext:
    scope = state["scope"]
    return ScopeContext(
        scope["organizationId"],
        scope["workspaceId"],
        scope["clientId"],
        scope["actorId"],
    )


def fixture_paths(work_root: Path) -> tuple[Path, Path]:
    fixture_root = work_root / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    source = fixture_root / "source.md"
    proposal = fixture_root / "proposal.md"
    expected = ((source, SOURCE_CONTENT), (proposal, PROPOSAL_CONTENT))
    for path, content in expected:
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise RuntimeError("restart fixture content changed between phases")
        path.write_text(content, encoding="utf-8")
    return source, proposal


def import_request(work_root: Path, *, idempotency_key: str) -> ImportRequest:
    source, proposal = fixture_paths(work_root)
    return ImportRequest(
        idempotency_key=idempotency_key,
        project_name="WP5A committed synthetic import",
        proposal_version=1,
        approval_confirmed=True,
        source=FilePayload("source.md", "text/markdown", source),
        proposal=FilePayload("proposal.md", "text/markdown", proposal),
    )


async def wait_for_database(asyncpg: Any, dsn: str, *, timeout_seconds: float = 30) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    attempts = 0
    while loop.time() < deadline:
        attempts += 1
        connection = None
        try:
            connection = await asyncpg.connect(dsn=dsn, timeout=2)
            if await connection.fetchval("SELECT 1") == 1:
                return attempts
        except Exception:
            await asyncio.sleep(0.5)
        finally:
            if connection is not None:
                try:
                    await connection.close()
                except Exception:
                    pass
    raise RuntimeError("PostgreSQL did not become ready after service restart")


async def seed_scope(asyncpg: Any, admin_dsn: str, scope: ScopeContext) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        bypass = await connection.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if not bypass:
            raise RuntimeError("restart gate requires a test-only RLS-bypass admin role")
        await connection.execute(
            "INSERT INTO marketops.organizations (id, name) VALUES ($1, 'WP5A synthetic org')",
            scope.organization_id,
        )
        await connection.execute(
            """
            INSERT INTO marketops.workspaces (id, organization_id, name, kind)
            VALUES ($1, $2, 'WP5A synthetic workspace', 'personal')
            """,
            scope.workspace_id,
            scope.organization_id,
        )
        await connection.execute(
            """
            INSERT INTO marketops.clients (id, organization_id, workspace_id, name)
            VALUES ($1, $2, $3, 'WP5A synthetic client')
            """,
            scope.client_id,
            scope.organization_id,
            scope.workspace_id,
        )
    finally:
        await connection.close()


async def cleanup_scope(asyncpg: Any, admin_dsn: str, organization_id: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute("SET session_replication_role = replica")
        try:
            for table in (
                "audit_events",
                "artifact_versions",
                "artifacts",
                "projects",
                "clients",
                "workspaces",
            ):
                await connection.execute(
                    f"DELETE FROM marketops.{table} WHERE organization_id = $1",
                    organization_id,
                )
            await connection.execute(
                "DELETE FROM marketops.organizations WHERE id = $1", organization_id
            )
        finally:
            await connection.execute("SET session_replication_role = origin")
    finally:
        await connection.close()


async def project_snapshot(
    asyncpg: Any,
    admin_dsn: str,
    *,
    organization_id: str,
    project_id: str,
    expected_proposal_artifact_id: str,
    expected_proposal_version_id: str,
) -> tuple[dict[str, Any], list[Any]]:
    connection = await asyncpg.connect(admin_dsn)
    try:
        table_names = {
            "projects": "projects",
            "artifacts": "artifacts",
            "artifactVersions": "artifact_versions",
            "auditEvents": "audit_events",
        }
        counts = {}
        for label, table in table_names.items():
            project_column = "id" if table == "projects" else "project_id"
            counts[label] = await connection.fetchval(
                f"""
                SELECT count(*) FROM marketops.{table}
                WHERE organization_id = $1 AND {project_column} = $2
                """,
                organization_id,
                project_id,
            )
        project = await connection.fetchrow(
            """
            SELECT approved_proposal_artifact_id, approved_proposal_version_id,
                   approved_proposal_number
            FROM marketops.projects
            WHERE organization_id = $1 AND id = $2
            """,
            organization_id,
            project_id,
        )
        versions = await connection.fetch(
            """
            SELECT artifact.kind, version.id, version.artifact_id, version.byte_size,
                   version.storage_key, version.sha256
            FROM marketops.artifacts AS artifact
            JOIN marketops.artifact_versions AS version
              ON version.organization_id = artifact.organization_id
             AND version.workspace_id = artifact.workspace_id
             AND version.client_id = artifact.client_id
             AND version.project_id = artifact.project_id
             AND version.artifact_id = artifact.id
            WHERE artifact.organization_id = $1 AND artifact.project_id = $2
            ORDER BY artifact.kind
            """,
            organization_id,
            project_id,
        )
    finally:
        await connection.close()

    approved = project is not None and (
        str(project["approved_proposal_artifact_id"]) == expected_proposal_artifact_id
        and str(project["approved_proposal_version_id"]) == expected_proposal_version_id
        and project["approved_proposal_number"] == 1
    )
    return (
        {
            "counts": counts,
            "approvedProposalSelected": approved,
            "artifactKinds": [str(row["kind"]) for row in versions],
        },
        versions,
    )


async def run_service_import(
    app_dsn: str,
    work_root: Path,
    scope: ScopeContext,
    *,
    idempotency_key: str,
):
    repository = await AsyncpgImportRepository.create(app_dsn, min_size=1, max_size=2)
    try:
        service = ProjectImportService(
            object_store=LocalObjectStore(work_root / "objects"),
            repository=repository,
            id_factory=identifier,
            clock=lambda: datetime.now(timezone.utc),
        )
        return await service.import_project(
            import_request(work_root, idempotency_key=idempotency_key), scope
        )
    finally:
        await repository.close()


async def prepare(work_root: Path, state_path: Path) -> None:
    try:
        import asyncpg
    except ImportError as error:
        raise RuntimeError("asyncpg is required for the restart recovery gate") from error
    admin_dsn = required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    app_dsn = required_environment("MARKETOPS_TEST_DATABASE_URL")
    scope = ScopeContext(identifier(), identifier(), identifier(), identifier())
    await seed_scope(asyncpg, admin_dsn, scope)
    result = await run_service_import(
        app_dsn, work_root, scope, idempotency_key="wp5a-committed-restart"
    )
    if result.replayed:
        raise RuntimeError("restart prepare unexpectedly replayed an existing import")
    write_json(
        state_path,
        {
            "schemaVersion": 1,
            "taskId": "M1-01",
            "scope": {
                "organizationId": scope.organization_id,
                "workspaceId": scope.workspace_id,
                "clientId": scope.client_id,
                "actorId": scope.actor_id,
            },
            "committed": {
                "projectId": result.project_id,
                "sourceArtifactId": result.source_artifact_id,
                "sourceVersionId": result.source_version_id,
                "proposalArtifactId": result.proposal_artifact_id,
                "proposalVersionId": result.proposal_version_id,
                "manifestSha256": result.manifest_sha256,
            },
        },
    )


async def restart() -> None:
    try:
        import asyncpg
    except ImportError as error:
        raise RuntimeError("asyncpg is required for the restart recovery gate") from error
    container_id = required_environment("MARKETOPS_TEST_POSTGRES_CONTAINER_ID")
    admin_dsn = required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    await asyncio.to_thread(restart_postgres_container, container_id)
    attempts = await wait_for_database(asyncpg, admin_dsn)
    print(json.dumps({"postgresRestarted": True, "readinessAttempts": attempts}))


async def verify(work_root: Path, state_path: Path, output: Path) -> None:
    try:
        import asyncpg
    except ImportError as error:
        raise RuntimeError("asyncpg is required for the restart recovery gate") from error
    state = load_state(state_path)
    scope = scope_from_state(state)
    committed = state["committed"]
    admin_dsn = required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    app_dsn = required_environment("MARKETOPS_TEST_DATABASE_URL")
    snapshot, versions = await project_snapshot(
        asyncpg,
        admin_dsn,
        organization_id=scope.organization_id,
        project_id=committed["projectId"],
        expected_proposal_artifact_id=committed["proposalArtifactId"],
        expected_proposal_version_id=committed["proposalVersionId"],
    )
    validate_committed_snapshot(snapshot)
    store = LocalObjectStore(work_root / "objects")
    for row in versions:
        await store.verify_immutable(
            kind=str(row["kind"]),
            stored=StoredObject(
                storage_key=str(row["storage_key"]),
                size_bytes=int(row["byte_size"]),
                sha256=bytes(row["sha256"]).hex(),
            ),
        )

    replay = await run_service_import(
        app_dsn, work_root, scope, idempotency_key="wp5a-committed-restart"
    )
    observed = {
        "projectId": replay.project_id,
        "sourceArtifactId": replay.source_artifact_id,
        "sourceVersionId": replay.source_version_id,
        "proposalArtifactId": replay.proposal_artifact_id,
        "proposalVersionId": replay.proposal_version_id,
        "manifestSha256": replay.manifest_sha256,
    }
    if replay.replayed is not True or observed != committed:
        raise RuntimeError("fresh service replay did not recover the committed import")

    write_json(
        output,
        {
            "schemaVersion": 1,
            "taskId": "M1-01",
            "workPackage": "WP5A-restart-recovery",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "committedRestart": {
                **snapshot,
                "freshProcessReplay": True,
                "immutableObjectsVerified": ["proposal", "source"],
            },
            "claimBoundary": (
                "This isolated CI experiment establishes committed database and runner-local "
                "object retention across a normal restart of the same service container, with "
                "recovery through a fresh Python process, pool, and service instance. It does "
                "not establish power-loss durability, durable host volumes, backup/restore, "
                "node replacement, production RPO/RTO, browser recovery, demand, ROI, repeat "
                "use, payment, or M1-01 completion."
            ),
        },
    )


def storage_key(
    scope: ScopeContext,
    *,
    idempotency_key: str,
    manifest_sha256: str,
    kind: str,
    sha256: str,
) -> str:
    request_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return (
        f"workspaces/{scope.workspace_id}/clients/{scope.client_id}/imports/"
        f"{request_hash}/{manifest_sha256}/{kind}/{sha256}"
    )


async def make_connection_loss_record(
    store: LocalObjectStore,
    work_root: Path,
    scope: ScopeContext,
) -> ProjectImportRecord:
    idempotency_key = "wp5a-uncommitted-connection-loss"
    manifest = "d" * 64
    source_path, proposal_path = fixture_paths(work_root)

    async def retain(kind: str, path: Path) -> StoredObject:
        content = path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        return await store.put_immutable(
            kind=kind,
            storage_key=storage_key(
                scope,
                idempotency_key=idempotency_key,
                manifest_sha256=manifest,
                kind=kind,
                sha256=sha256,
            ),
            source_path=path,
            size_bytes=len(content),
            sha256=sha256,
        )

    source = await retain("source", source_path)
    proposal = await retain("proposal", proposal_path)
    approved_at = datetime.now(timezone.utc)
    return ProjectImportRecord(
        project_id=identifier(),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        client_id=scope.client_id,
        idempotency_key=idempotency_key,
        manifest_sha256=manifest,
        project_name="WP5A uncommitted synthetic import",
        status="planning",
        source_artifact_id=identifier(),
        source_version_id=identifier(),
        source=source,
        source_filename="source.md",
        source_media_type="text/markdown",
        proposal_artifact_id=identifier(),
        proposal_version_id=identifier(),
        proposal=proposal,
        proposal_filename="proposal.md",
        proposal_media_type="text/markdown",
        approved_proposal_version=1,
        approval_status="approved",
        approved_by=scope.actor_id,
        approved_at=approved_at,
        created_by=scope.actor_id,
    )


async def append_record(transaction: Any, scope: ScopeContext, record: ProjectImportRecord) -> None:
    claim = await transaction.try_create_project_import(record)
    if not claim.created:
        raise RuntimeError("connection-loss test import unexpectedly replayed")
    await transaction.append_audit_event(
        AuditEvent(
            event_id=identifier(),
            project_id=record.project_id,
            actor_id=scope.actor_id,
            action="project.imported_with_approved_proposal",
            manifest_sha256=record.manifest_sha256,
            source_version_id=record.source_version_id,
            proposal_version_id=record.proposal_version_id,
            approved_by=scope.actor_id,
            approved_at=record.approved_at,
        )
    )


async def terminate_backend(asyncpg: Any, admin_dsn: str, application_name: str) -> int:
    connection = await asyncpg.connect(admin_dsn)
    try:
        rows = await connection.fetch(
            """
            SELECT pid, backend_xid IS NOT NULL AS has_xid
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND usename = 'marketops_app'
              AND application_name = $1
            """,
            application_name,
        )
        if len(rows) != 1 or rows[0]["has_xid"] is not True:
            raise RuntimeError("connection-loss backend is not uniquely in a transaction")
        pid = int(rows[0]["pid"])
        if await connection.fetchval("SELECT pg_terminate_backend($1)", pid) is not True:
            raise RuntimeError("PostgreSQL refused to terminate the test backend")
        for _ in range(40):
            exists = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid = $1)", pid
            )
            if not exists:
                return pid
            await asyncio.sleep(0.25)
        raise RuntimeError("terminated test backend remained visible")
    finally:
        await connection.close()


async def connection_loss(
    work_root: Path,
    state_path: Path,
    output: Path,
) -> None:
    try:
        import asyncpg
    except ImportError as error:
        raise RuntimeError("asyncpg is required for the restart recovery gate") from error
    state = load_state(state_path)
    scope = scope_from_state(state)
    admin_dsn = required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    app_dsn = required_environment("MARKETOPS_TEST_DATABASE_URL")
    record = await make_connection_loss_record(
        LocalObjectStore(work_root / "objects"), work_root, scope
    )
    application_name = "marketops-wp5a-" + uuid4().hex
    repository = await AsyncpgImportRepository.create(
        app_dsn,
        min_size=1,
        max_size=1,
        server_settings={"application_name": application_name},
    )
    ready = asyncio.Event()
    release = asyncio.Event()

    async def hold_transaction() -> PostgresAdapterError | None:
        try:
            async with repository.transaction(scope) as transaction:
                await append_record(transaction, scope, record)
                ready.set()
                await release.wait()
        except PostgresAdapterError as error:
            return error
        return None

    task = asyncio.create_task(hold_transaction())
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
        await terminate_backend(asyncpg, admin_dsn, application_name)
        release.set()
        adapter_error = await asyncio.wait_for(task, timeout=10)
        if adapter_error is None or adapter_error.retryable is not True:
            raise RuntimeError("connection loss did not produce a retryable adapter failure")
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        try:
            await asyncio.wait_for(repository.close(), timeout=10)
        except (asyncio.TimeoutError, PostgresAdapterError):
            pass

    rolled_back, _ = await project_snapshot(
        asyncpg,
        admin_dsn,
        organization_id=scope.organization_id,
        project_id=record.project_id,
        expected_proposal_artifact_id=record.proposal_artifact_id,
        expected_proposal_version_id=record.proposal_version_id,
    )
    validate_uncommitted_snapshot(rolled_back)

    retry_repository = await AsyncpgImportRepository.create(app_dsn, min_size=1, max_size=1)
    try:
        async with retry_repository.transaction(scope) as transaction:
            await append_record(transaction, scope, record)
    finally:
        await retry_repository.close()
    retried, _ = await project_snapshot(
        asyncpg,
        admin_dsn,
        organization_id=scope.organization_id,
        project_id=record.project_id,
        expected_proposal_artifact_id=record.proposal_artifact_id,
        expected_proposal_version_id=record.proposal_version_id,
    )
    validate_committed_snapshot(retried)

    try:
        evidence = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("committed restart evidence is missing or malformed") from error
    evidence["uncommittedConnectionLoss"] = {
        "beforeRetry": rolled_back,
        "afterRetry": retried,
        "adapterErrorCode": adapter_error.code,
        "adapterFailureRetryable": adapter_error.retryable,
        "databaseAggregateRolledBack": True,
        "objectsRetainedBeforeDatabaseTransaction": True,
    }
    write_json(output, evidence)
    await cleanup_scope(asyncpg, admin_dsn, scope.organization_id)
    print(output.read_text(encoding="utf-8"), end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--work-root", type=Path, required=True)
    prepare_parser.add_argument("--state", type=Path, required=True)
    subparsers.add_parser("restart")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--work-root", type=Path, required=True)
    verify_parser.add_argument("--state", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path, required=True)
    loss_parser = subparsers.add_parser("connection-loss")
    loss_parser.add_argument("--work-root", type=Path, required=True)
    loss_parser.add_argument("--state", type=Path, required=True)
    loss_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "prepare":
        asyncio.run(prepare(args.work_root, args.state))
    elif args.command == "restart":
        asyncio.run(restart())
    elif args.command == "verify":
        asyncio.run(verify(args.work_root, args.state, args.output))
    else:
        asyncio.run(connection_loss(args.work_root, args.state, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
