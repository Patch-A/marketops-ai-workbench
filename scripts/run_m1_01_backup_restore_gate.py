#!/usr/bin/env python3
"""Exercise an application-consistent M1-01 logical backup and isolated restore."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.marketops_import.backup import (  # noqa: E402
    BUSINESS_TABLES,
    BackupBundleError,
    LEGACY_BUSINESS_TABLES,
    LEGACY_SCHEMA_VERSION,
    LEGACY_TASK_ID,
    MIGRATION_NAME,
    MIGRATION_SET,
    MIGRATION_SHA256,
    REVIEW_BUSINESS_TABLES,
    REVIEW_MIGRATION_SET,
    REVIEW_SCHEMA_VERSION,
    canonical_archive_path,
    create_backup_bundle,
    load_backup_bundle,
    restore_object_bundle,
    sha256_file,
)
from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation  # noqa: E402
from apps.api.marketops_review import (  # noqa: E402
    AsyncpgReviewRepository,
    CreateReviewRunRequest,
    ReviewRequest,
    ReviewScopeContext,
    ReviewService,
)
from apps.api.marketops_import.postgres import AsyncpgImportRepository  # noqa: E402
from apps.api.marketops_import.service import ProjectImportService, StoredObject  # noqa: E402
from apps.api.marketops_import.storage import LocalObjectStore  # noqa: E402
from apps.api.marketops_schedule import (  # noqa: E402
    AsyncpgScheduleRepository,
    CreatePlanRequest,
    ScheduleScopeContext,
    ScheduleService,
)
from scripts.run_m1_01_postgres_gate import (  # noqa: E402
    APPLICATION_ROLE,
    DATABASE_NAME,
    MIGRATOR_ROLE,
    POSTGRES_VERSION_NUM,
    attest_application_role,
    migrate_and_grant,
)
from scripts.run_m1_01_restart_recovery_gate import (  # noqa: E402
    TABLE_COUNTS,
    import_request,
    load_state,
    project_snapshot,
    required_environment,
    scope_from_state,
    validate_committed_snapshot,
    validate_container_id,
)


DATABASE_NAME_PATTERN = re.compile(r"marketops_restore_[0-9a-f]{16}")
NEGATIVE_BUNDLE_LABELS = ("tamperedDumpRejected", "tamperedObjectRejected")
TOC_TABLE_DATA = re.compile(
    r"^\d+;\s+\d+\s+\d+\s+TABLE DATA\s+marketops\s+([a-z_]+)\s+\S+\s*$"
)
def validate_database_name(value: str) -> str:
    if not isinstance(value, str) or DATABASE_NAME_PATTERN.fullmatch(value) is None:
        raise RuntimeError("restore database name is outside the isolated test namespace")
    return value


def replace_database_in_dsn(dsn: str, database_name: str) -> str:
    validate_database_name(database_name)
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.netloc:
        raise RuntimeError("database DSN is malformed")
    return urlunsplit((parsed.scheme, parsed.netloc, "/" + quote(database_name), parsed.query, ""))


def run_container_text(
    container_id: str,
    arguments: list[str],
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    container = validate_container_id(container_id)
    try:
        result = runner(
            ["docker", "exec", container, *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("PostgreSQL container tool could not be executed") from error
    if result.returncode != 0:
        raise RuntimeError("PostgreSQL container tool failed")
    return result.stdout


def postgres_tool_version(
    container_id: str,
    tool: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    if tool not in {"pg_dump", "pg_restore"}:
        raise RuntimeError("unexpected PostgreSQL tool")
    output = run_container_text(container_id, [tool, "--version"], runner=runner)
    match = re.fullmatch(
        rf"{tool} \(PostgreSQL\) ([^\s]+)(?:[ \t].*)?\r?\n?",
        output,
    )
    if match is None or match.group(1) != "18.4":
        raise RuntimeError(f"{tool} is not the reviewed PostgreSQL 18.4 client")
    return POSTGRES_VERSION_NUM


def create_database_dump(
    container_id: str,
    snapshot_id: str,
    output: Path,
    *,
    tables: tuple[str, ...] = BUSINESS_TABLES,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    if not isinstance(snapshot_id, str) or re.fullmatch(r"[0-9A-Fa-f:-]+", snapshot_id) is None:
        raise RuntimeError("exported PostgreSQL snapshot identifier is invalid")
    output.parent.mkdir(parents=True, exist_ok=True)
    if tables not in {BUSINESS_TABLES, REVIEW_BUSINESS_TABLES, LEGACY_BUSINESS_TABLES}:
        raise RuntimeError("database dump table allowlist is unsupported")
    command = [
        "docker",
        "exec",
        validate_container_id(container_id),
        "pg_dump",
        "-U",
        "postgres",
        "-d",
        DATABASE_NAME,
        "--format=custom",
        "--data-only",
        "--schema=marketops",
        "--exclude-table-data=marketops.schema_migrations",
        "--no-owner",
        "--no-privileges",
        f"--snapshot={snapshot_id}",
        *(f"--table=marketops.{table}" for table in tables),
    ]
    try:
        with output.open("xb") as target:
            result = runner(command, check=False, stdout=target, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as error:
        output.unlink(missing_ok=True)
        raise RuntimeError("PostgreSQL snapshot dump could not be created") from error
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise RuntimeError("PostgreSQL snapshot dump failed")


def read_dump_toc(
    container_id: str,
    dump_path: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    try:
        with dump_path.open("rb") as source:
            result = runner(
                ["docker", "exec", "-i", validate_container_id(container_id), "pg_restore", "--list"],
                check=False,
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("PostgreSQL dump table of contents could not be read") from error
    if result.returncode != 0:
        raise RuntimeError("PostgreSQL dump table of contents is unreadable")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("PostgreSQL dump table of contents is not UTF-8") from error


def validate_dump_toc(
    value: str, expected_tables: tuple[str, ...] = BUSINESS_TABLES
) -> tuple[str, ...]:
    if expected_tables not in {
        BUSINESS_TABLES,
        REVIEW_BUSINESS_TABLES,
        LEGACY_BUSINESS_TABLES,
    }:
        raise RuntimeError("database dump table allowlist is unsupported")
    tables: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        match = TOC_TABLE_DATA.fullmatch(line)
        if match is None:
            raise RuntimeError("database dump contains a non-allowlisted entry")
        tables.append(match.group(1))
    if len(tables) != len(set(tables)) or set(tables) != set(expected_tables):
        raise RuntimeError("database dump table-data allowlist is incomplete or duplicated")
    return tuple(tables)


def publish_after_toc_validation(
    container_id: str,
    dump_path: Path,
    publisher: Callable[[], Any],
    *,
    expected_tables: tuple[str, ...] = BUSINESS_TABLES,
    toc_reader: Callable[[str, Path], str] = read_dump_toc,
) -> tuple[Any, tuple[str, ...]]:
    tables = validate_dump_toc(toc_reader(container_id, dump_path), expected_tables)
    return publisher(), tables


def restore_database_dump(
    container_id: str,
    database_name: str,
    dump_path: Path,
    *,
    expect_failure: bool = False,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    command = [
        "docker",
        "exec",
        "-i",
        validate_container_id(container_id),
        "pg_restore",
        "-U",
        "postgres",
        "-d",
        validate_database_name(database_name),
        "--data-only",
        "--single-transaction",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
    ]
    try:
        with dump_path.open("rb") as source:
            result = runner(
                command,
                check=False,
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("isolated PostgreSQL restore could not be executed") from error
    if expect_failure:
        if result.returncode == 0:
            raise RuntimeError("negative PostgreSQL restore unexpectedly succeeded")
        return
    if result.returncode != 0:
        raise RuntimeError("isolated PostgreSQL restore failed")


def _row_set_hash(rows: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def migration_manifest_rows(rows: Any) -> list[dict[str, str]]:
    try:
        return [
            {"name": str(row["migration_name"]), "sha256": str(row["sha256"])}
            for row in rows
        ]
    except (KeyError, TypeError):
        raise RuntimeError("migration registry rows are malformed") from None


async def database_snapshot(
    connection: Any, tables: tuple[str, ...] = BUSINESS_TABLES
) -> dict[str, dict[str, Any]]:
    if tables not in {BUSINESS_TABLES, REVIEW_BUSINESS_TABLES, LEGACY_BUSINESS_TABLES}:
        raise RuntimeError("database snapshot table allowlist is unsupported")
    snapshot: dict[str, dict[str, Any]] = {}
    for table in tables:
        rows = await connection.fetch(
            f"""
            SELECT pg_catalog.to_jsonb(record)::text AS payload
            FROM marketops.{table} AS record
            ORDER BY pg_catalog.to_jsonb(record)::text
            """
        )
        payloads = [str(row["payload"]) for row in rows]
        snapshot[table] = {"count": len(payloads), "rowSetSha256": _row_set_hash(payloads)}
    return snapshot


async def referenced_objects(connection: Any) -> list[dict[str, Any]]:
    rows = await connection.fetch(
        """
        SELECT version.storage_key, artifact.kind, version.byte_size,
               pg_catalog.encode(version.sha256, 'hex') AS sha256
        FROM marketops.artifact_versions AS version
        JOIN marketops.artifacts AS artifact
          ON artifact.organization_id = version.organization_id
         AND artifact.workspace_id = version.workspace_id
         AND artifact.client_id = version.client_id
         AND artifact.project_id = version.project_id
         AND artifact.id = version.artifact_id
        ORDER BY version.storage_key
        """
    )
    return [
        {
            "storageKey": str(row["storage_key"]),
            "kind": str(row["kind"]),
            "sizeBytes": int(row["byte_size"]),
            "sha256": str(row["sha256"]),
        }
        for row in rows
    ]


def publish_legacy_bundle(
    destination: Path,
    *,
    database_dump: Path,
    object_root: Path,
    objects: list[dict[str, Any]],
    snapshot: dict[str, dict[str, Any]],
    dump_version: int,
    restore_version: int,
) -> Any:
    if destination.exists():
        raise RuntimeError("legacy backup destination already exists")
    destination.mkdir(parents=True)
    try:
        shutil.copyfile(database_dump, destination / "database.dump")
        manifest_objects = []
        for item in sorted(objects, key=lambda record: record["storageKey"]):
            archive_path = canonical_archive_path(item["storageKey"])
            source = object_root / Path(archive_path).relative_to("objects")
            target = destination / archive_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            manifest_objects.append({**item, "archivePath": archive_path})
        manifest = {
            "schemaVersion": LEGACY_SCHEMA_VERSION,
            "taskId": LEGACY_TASK_ID,
            "postgres": {
                "serverVersionNum": POSTGRES_VERSION_NUM,
                "dumpVersionNum": dump_version,
                "restoreVersionNum": restore_version,
            },
            "migration": {"name": MIGRATION_NAME, "sha256": MIGRATION_SHA256},
            "database": {
                "archivePath": "database.dump",
                "sha256": sha256_file(database_dump)[1],
                "tableData": list(LEGACY_BUSINESS_TABLES),
            },
            "snapshot": snapshot,
            "objects": manifest_objects,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return load_backup_bundle(destination)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def publish_review_v2_bundle(
    destination: Path,
    *,
    database_dump: Path,
    object_root: Path,
    objects: list[dict[str, Any]],
    snapshot: dict[str, dict[str, Any]],
    dump_version: int,
    restore_version: int,
) -> Any:
    if destination.exists():
        raise RuntimeError("review v2 backup destination already exists")
    destination.mkdir(parents=True)
    try:
        shutil.copyfile(database_dump, destination / "database.dump")
        manifest_objects = []
        for item in sorted(objects, key=lambda record: record["storageKey"]):
            archive_path = canonical_archive_path(item["storageKey"])
            source = object_root / Path(archive_path).relative_to("objects")
            target = destination / archive_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            manifest_objects.append({**item, "archivePath": archive_path})
        manifest = {
            "schemaVersion": REVIEW_SCHEMA_VERSION,
            "taskId": "M1-02",
            "postgres": {
                "serverVersionNum": POSTGRES_VERSION_NUM,
                "dumpVersionNum": dump_version,
                "restoreVersionNum": restore_version,
            },
            "migrations": list(REVIEW_MIGRATION_SET),
            "database": {
                "archivePath": "database.dump",
                "sha256": sha256_file(database_dump)[1],
                "tableData": list(REVIEW_BUSINESS_TABLES),
            },
            "snapshot": snapshot,
            "objects": manifest_objects,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return load_backup_bundle(destination)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


async def export_legacy_bundle(
    asyncpg: Any,
    *,
    admin_dsn: str,
    container_id: str,
    work_root: Path,
    bundle_root: Path,
    dump_version: int,
    restore_version: int,
) -> tuple[Any, tuple[str, ...]]:
    dump_path = work_root / "legacy-database.dump"
    connection = await asyncpg.connect(admin_dsn)
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            snapshot_id = await connection.fetchval(
                "SELECT pg_catalog.pg_export_snapshot()"
            )
            snapshot = await database_snapshot(connection, LEGACY_BUSINESS_TABLES)
            objects = await referenced_objects(connection)
            await asyncio.to_thread(
                create_database_dump,
                container_id,
                snapshot_id,
                dump_path,
                tables=LEGACY_BUSINESS_TABLES,
            )
    finally:
        await connection.close()
    if not objects:
        raise RuntimeError("legacy database snapshot references no immutable objects")

    def publish() -> Any:
        return publish_legacy_bundle(
            bundle_root,
            database_dump=dump_path,
            object_root=work_root / "objects",
            objects=objects,
            snapshot=snapshot,
            dump_version=dump_version,
            restore_version=restore_version,
        )

    return await asyncio.to_thread(
        publish_after_toc_validation,
        container_id,
        dump_path,
        publish,
        expected_tables=LEGACY_BUSINESS_TABLES,
    )


async def export_review_v2_bundle(
    asyncpg: Any,
    *,
    admin_dsn: str,
    container_id: str,
    work_root: Path,
    bundle_root: Path,
    dump_version: int,
    restore_version: int,
) -> tuple[Any, tuple[str, ...]]:
    dump_path = work_root / "review-v2-database.dump"
    connection = await asyncpg.connect(admin_dsn)
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            snapshot_id = await connection.fetchval(
                "SELECT pg_catalog.pg_export_snapshot()"
            )
            snapshot = await database_snapshot(connection, REVIEW_BUSINESS_TABLES)
            objects = await referenced_objects(connection)
            await asyncio.to_thread(
                create_database_dump,
                container_id,
                snapshot_id,
                dump_path,
                tables=REVIEW_BUSINESS_TABLES,
            )
    finally:
        await connection.close()
    if not objects:
        raise RuntimeError("review v2 database snapshot references no immutable objects")

    def publish() -> Any:
        return publish_review_v2_bundle(
            bundle_root,
            database_dump=dump_path,
            object_root=work_root / "objects",
            objects=objects,
            snapshot=snapshot,
            dump_version=dump_version,
            restore_version=restore_version,
        )

    return await asyncio.to_thread(
        publish_after_toc_validation,
        container_id,
        dump_path,
        publish,
        expected_tables=REVIEW_BUSINESS_TABLES,
    )


async def export_bundle(
    asyncpg: Any,
    *,
    admin_dsn: str,
    container_id: str,
    work_root: Path,
    bundle_root: Path,
    dump_version: int,
    restore_version: int,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    dump_path = work_root / "database.dump"
    connection = await asyncpg.connect(admin_dsn)
    try:
        version = await connection.fetchval("SELECT current_setting('server_version_num')::integer")
        if version != POSTGRES_VERSION_NUM:
            raise RuntimeError("source PostgreSQL server is not version 18.4")
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            snapshot_id = await connection.fetchval("SELECT pg_catalog.pg_export_snapshot()")
            source_migrations = await connection.fetch(
                """
                SELECT migration_name, pg_catalog.encode(sha256, 'hex') AS sha256
                FROM marketops.schema_migrations
                ORDER BY migration_name
                """
            )
            if migration_manifest_rows(source_migrations) != list(MIGRATION_SET):
                raise RuntimeError("source migration registry differs from reviewed migrations")
            snapshot = await database_snapshot(connection)
            objects = await referenced_objects(connection)
            await asyncio.to_thread(create_database_dump, container_id, snapshot_id, dump_path)
    finally:
        await connection.close()
    if not objects:
        raise RuntimeError("database snapshot references no immutable objects")
    def publish() -> Any:
        return create_backup_bundle(
            bundle_root,
            database_dump=dump_path,
            object_root=work_root / "objects",
            objects=objects,
            postgres={
                "serverVersionNum": POSTGRES_VERSION_NUM,
                "dumpVersionNum": dump_version,
                "restoreVersionNum": restore_version,
            },
            migrations=MIGRATION_SET,
            snapshot=snapshot,
        )

    bundle, toc_tables = await asyncio.to_thread(
        publish_after_toc_validation,
        container_id,
        dump_path,
        publish,
    )
    return bundle.manifest, toc_tables


async def create_restore_database(asyncpg: Any, admin_dsn: str, database_name: str) -> None:
    name = validate_database_name(database_name)
    connection = await asyncpg.connect(admin_dsn)
    try:
        exists = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname = $1)", name
        )
        if exists:
            raise RuntimeError("isolated restore database already exists")
        await connection.execute(f'CREATE DATABASE "{name}" OWNER {MIGRATOR_ROLE}')
    finally:
        await connection.close()


async def drop_restore_database(asyncpg: Any, admin_dsn: str, database_name: str) -> None:
    name = validate_database_name(database_name)
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await connection.close()


async def assert_empty_business_tables(asyncpg: Any, admin_dsn: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        for table in BUSINESS_TABLES:
            count = await connection.fetchval(f"SELECT count(*) FROM marketops.{table}")
            if count != 0:
                raise RuntimeError("isolated restore database is not empty")
    finally:
        await connection.close()


async def restored_security(asyncpg: Any, admin_dsn: str, app_dsn: str) -> dict[str, Any]:
    connection = await asyncpg.connect(admin_dsn)
    try:
        tables = await connection.fetch(
            """
            SELECT relation.relname,
                   pg_catalog.pg_get_userbyid(relation.relowner) AS owner,
                   relation.relrowsecurity,
                   relation.relforcerowsecurity
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'marketops'
              AND relation.relkind = 'r'
            ORDER BY relation.relname
            """
        )
        observed = {
            str(row["relname"]): {
                "owner": str(row["owner"]),
                "rls": bool(row["relrowsecurity"]),
                "forceRls": bool(row["relforcerowsecurity"]),
            }
            for row in tables
        }
        if set(observed) != {*BUSINESS_TABLES, "schema_migrations"}:
            raise RuntimeError("restored marketops table set drifted")
        for table in BUSINESS_TABLES:
            if observed[table] != {"owner": MIGRATOR_ROLE, "rls": True, "forceRls": True}:
                raise RuntimeError("restored table owner or RLS contract drifted")
        registry = observed["schema_migrations"]
        if registry != {"owner": MIGRATOR_ROLE, "rls": False, "forceRls": False}:
            raise RuntimeError("restored migration registry contract drifted")
        checksums = await connection.fetch(
            """
            SELECT migration_name, pg_catalog.encode(sha256, 'hex') AS sha256
            FROM marketops.schema_migrations
            ORDER BY migration_name
            """
        )
        if migration_manifest_rows(checksums) != list(MIGRATION_SET):
            raise RuntimeError("restored migration registry checksums drifted")
    finally:
        await connection.close()
    role = await attest_application_role(asyncpg, app_dsn)
    return {"ownersVerified": True, "forcedRlsTables": list(BUSINESS_TABLES), "applicationRole": role}


async def rls_visibility(asyncpg: Any, app_dsn: str, scope: Any, project_id: str) -> dict[str, bool]:
    connection = await asyncpg.connect(app_dsn)
    try:
        async def visible(*, workspace: str, client: str, project: str, actor: str) -> bool:
            async with connection.transaction():
                await connection.execute("SELECT pg_catalog.set_config('app.workspace_id', $1, true)", workspace)
                await connection.execute("SELECT pg_catalog.set_config('app.client_id', $1, true)", client)
                await connection.execute("SELECT pg_catalog.set_config('app.project_id', $1, true)", project)
                await connection.execute("SELECT pg_catalog.set_config('app.actor_id', $1, true)", actor)
                return await connection.fetchval("SELECT count(*) = 1 FROM marketops.projects WHERE id = $1", project_id)

        correct = await visible(
            workspace=scope.workspace_id,
            client=scope.client_id,
            project=project_id,
            actor=scope.actor_id,
        )
        missing_actor = await visible(
            workspace=scope.workspace_id,
            client=scope.client_id,
            project=project_id,
            actor="",
        )
        wrong_scope = await visible(
            workspace=str(uuid4()),
            client=str(uuid4()),
            project=str(uuid4()),
            actor=scope.actor_id,
        )
    finally:
        await connection.close()
    if not correct or missing_actor or wrong_scope:
        raise RuntimeError("restored RLS visibility contract failed")
    return {"correctScopeVisible": correct, "missingActorHidden": not missing_actor, "wrongScopeHidden": not wrong_scope}


async def verify_manifest_objects(bundle: Any, object_root: Path) -> None:
    store = LocalObjectStore(object_root)
    for item in bundle.manifest["objects"]:
        await store.verify_immutable(
            kind=item["kind"],
            stored=StoredObject(item["storageKey"], item["sizeBytes"], item["sha256"]),
        )


async def fresh_replay(app_dsn: str, restore_work_root: Path, state: Mapping[str, Any]) -> bool:
    scope = scope_from_state(dict(state))
    expected = state["committed"]
    repository = await AsyncpgImportRepository.create(app_dsn, min_size=1, max_size=1)
    try:
        service = ProjectImportService(
            object_store=LocalObjectStore(restore_work_root / "objects"),
            repository=repository,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        result = await service.import_project(
            import_request(restore_work_root, idempotency_key="wp5a-committed-restart"), scope
        )
    finally:
        await repository.close()
    observed = {
        "projectId": result.project_id,
        "sourceArtifactId": result.source_artifact_id,
        "sourceVersionId": result.source_version_id,
        "proposalArtifactId": result.proposal_artifact_id,
        "proposalVersionId": result.proposal_version_id,
        "manifestSha256": result.manifest_sha256,
    }
    if result.replayed is not True or observed != expected:
        raise RuntimeError("restored fresh-service replay differs from the source import")
    return True


async def fresh_review_replay(
    asyncpg: Any,
    app_dsn: str,
    scope: Any,
    committed: Mapping[str, Any],
    review_history: Mapping[str, Any],
) -> dict[str, Any]:
    pool = await asyncpg.create_pool(dsn=app_dsn, min_size=1, max_size=1)
    try:
        result = await ReviewService(
            repository=AsyncpgReviewRepository(pool),
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        ).replay_run_request(
            committed["projectId"],
            review_history["idempotencyKey"],
            committed["proposalVersionId"],
            review_history["proposalSha256"],
            ReviewScopeContext(
                scope.organization_id,
                scope.workspace_id,
                scope.client_id,
                scope.actor_id,
            ),
        )
    finally:
        await pool.close()
    if (
        result is None
        or result.replayed is not True
        or result.run.run_id != review_history["runId"]
        or result.snapshot.run_id != review_history["runId"]
        or result.snapshot.version != 1
    ):
        raise RuntimeError("restored review request did not replay its original version-1 run")
    return {
        "accepted": True,
        "runId": result.run.run_id,
        "reviewVersion": result.snapshot.version,
        "replayed": result.replayed,
    }


async def install_rejecting_restore_trigger(asyncpg: Any, admin_dsn: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(
            """
            CREATE FUNCTION marketops.reject_wp5b_restore()
            RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
            BEGIN
              RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'WP5B forced restore failure';
            END;
            $$;
            CREATE TRIGGER wp5b_reject_audit_insert
            BEFORE INSERT ON marketops.audit_events
            FOR EACH ROW EXECUTE FUNCTION marketops.reject_wp5b_restore();
            """
        )
    finally:
        await connection.close()


async def negative_bundle_checks(
    bundle_root: Path,
    work_root: Path,
    *,
    tree_remover: Callable[..., Any] = shutil.rmtree,
) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for label, relative in zip(
        NEGATIVE_BUNDLE_LABELS,
        (Path("database.dump"), None),
        strict=True,
    ):
        target = work_root / label
        shutil.copytree(bundle_root, target)
        if relative is None:
            manifest = load_backup_bundle(bundle_root).manifest
            relative = Path(manifest["objects"][0]["archivePath"])
        (target / relative).write_bytes(b"tampered")
        try:
            load_backup_bundle(target)
        except BackupBundleError:
            results[label] = True
        else:
            raise RuntimeError("tampered backup bundle was accepted")
        finally:
            active_error = sys.exc_info()[1]
            try:
                tree_remover(target, ignore_errors=False)
            except FileNotFoundError:
                pass
            except OSError:
                if active_error is not None:
                    active_error.add_note("tampered backup cleanup also failed")
                else:
                    raise RuntimeError("tampered backup cleanup failed") from None
    return results


async def seed_review_history(asyncpg: Any, admin_dsn: str, app_dsn: str, scope: Any, committed: Mapping[str, Any]) -> dict[str, Any]:
    connection = await asyncpg.connect(admin_dsn)
    try:
        source = await connection.fetchrow(
            """
            SELECT version.proposal_version,
                   pg_catalog.encode(version.sha256, 'hex') AS sha256
            FROM marketops.artifact_versions AS version
            WHERE version.id = $1
              AND version.project_id = $2
              AND version.approval_status = 'approved'
            """,
            committed["proposalVersionId"],
            committed["projectId"],
        )
    finally:
        await connection.close()
    if source is None:
        raise RuntimeError("approved proposal source for review recovery is missing")

    pool = await asyncpg.create_pool(dsn=app_dsn, min_size=1, max_size=2)
    try:
        review_scope = ReviewScopeContext(
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            scope.actor_id,
        )
        service = ReviewService(
            repository=AsyncpgReviewRepository(pool),
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        candidate_id = str(uuid4())
        candidate = Candidate(
            candidate_id=candidate_id,
            kind="deliverable",
            text="Publish the synthetic recovery brief.",
            classification="fact",
            confidence=0.9,
            source_citation=SourceCitation(
                source_version_id=committed["proposalVersionId"],
                source_sha256=str(source["sha256"]),
                location=SourceLocation.from_mapping(
                    {"kind": "line_range", "startLine": 1, "endLine": 1}
                ),
                section_path=("Recovery",),
                quote="Publish the synthetic recovery brief.",
            ),
        )
        created = await service.create_run(
            CreateReviewRunRequest(
                project_id=committed["projectId"],
                proposal_artifact_id=committed["proposalArtifactId"],
                proposal_version_id=committed["proposalVersionId"],
                proposal_version=int(source["proposal_version"]),
                proposal_sha256=str(source["sha256"]),
                candidates=(candidate,),
                idempotency_key="backup-review-request",
            ),
            review_scope,
        )
        reviewed = await service.review_candidate(
            committed["projectId"],
            created.run.run_id,
            ReviewRequest(1, candidate_id, "approve", "Synthetic recovery decision"),
            review_scope,
        )
        return {
            "runId": created.run.run_id,
            "candidateId": candidate_id,
            "latestReviewVersion": reviewed.snapshot.version,
            "idempotencyKey": "backup-review-request",
            "proposalSha256": str(source["sha256"]),
        }
    finally:
        await pool.close()


async def seed_schedule_history(
    asyncpg: Any,
    app_dsn: str,
    scope: Any,
    committed: Mapping[str, Any],
    review_history: Mapping[str, Any],
) -> dict[str, Any]:
    pool = await asyncpg.create_pool(dsn=app_dsn, min_size=1, max_size=2)
    try:
        review_scope = ReviewScopeContext(
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            scope.actor_id,
        )
        schedule_scope = ScheduleScopeContext(
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            scope.actor_id,
        )
        review_service = ReviewService(
            repository=AsyncpgReviewRepository(pool),
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        schedule_service = ScheduleService(
            repository=AsyncpgScheduleRepository(pool),
            review_service=review_service,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        created = await schedule_service.create_plan(
            CreatePlanRequest(
                committed["projectId"],
                review_history["runId"],
                review_history["latestReviewVersion"],
            ),
            schedule_scope,
        )
        scheduled = await schedule_service.calculate_plan_schedule(
            committed["projectId"],
            created.plan.plan.plan_id,
            created.plan.version.version,
            "2026-08-17",
            ("2026-08-20",),
            schedule_scope,
        )
        return {
            "planId": created.plan.plan.plan_id,
            "planVersion": created.plan.version.version,
            "scheduleSnapshotId": scheduled.snapshot.snapshot_id,
            "scheduleDigest": scheduled.snapshot.schedule_digest,
            "status": scheduled.snapshot.status,
        }
    finally:
        await pool.close()


async def verify_legacy_upgrade_restore(
    asyncpg: Any,
    *,
    bundle: Any,
    toc_tables: tuple[str, ...],
    container_id: str,
    database_name: str,
    admin_dsn: str,
    migrator_dsn: str,
    app_dsn: str,
    restore_work_root: Path,
    state: Mapping[str, Any],
    scope: Any,
    committed: Mapping[str, Any],
) -> dict[str, Any]:
    target_admin = replace_database_in_dsn(admin_dsn, database_name)
    target_migrator = replace_database_in_dsn(migrator_dsn, database_name)
    target_app = replace_database_in_dsn(app_dsn, database_name)
    await migrate_and_grant(asyncpg, target_migrator)
    await assert_empty_business_tables(asyncpg, target_admin)
    restore_object_bundle(bundle, restore_work_root / "objects")
    await asyncio.to_thread(
        restore_database_dump, container_id, database_name, bundle.database_dump
    )

    connection = await asyncpg.connect(target_admin)
    try:
        restored_snapshot = await database_snapshot(connection)
        restored_objects = await referenced_objects(connection)
    finally:
        await connection.close()
    for table in LEGACY_BUSINESS_TABLES:
        if restored_snapshot[table] != bundle.manifest["snapshot"][table]:
            raise RuntimeError("legacy restore row-set hashes differ from the v1 snapshot")
    empty_summary = {"count": 0, "rowSetSha256": _row_set_hash([])}
    review_tables = tuple(
        table for table in BUSINESS_TABLES if table not in LEGACY_BUSINESS_TABLES
    )
    if any(restored_snapshot[table] != empty_summary for table in review_tables):
        raise RuntimeError("legacy restore unexpectedly populated review tables")
    manifest_objects = [
        {key: item[key] for key in ("storageKey", "kind", "sizeBytes", "sha256")}
        for item in bundle.manifest["objects"]
    ]
    if restored_objects != manifest_objects:
        raise RuntimeError("legacy restored object references differ from the v1 manifest")
    await verify_manifest_objects(bundle, restore_work_root / "objects")

    restored_project, _ = await project_snapshot(
        asyncpg,
        target_admin,
        organization_id=scope.organization_id,
        project_id=committed["projectId"],
        expected_proposal_artifact_id=committed["proposalArtifactId"],
        expected_proposal_version_id=committed["proposalVersionId"],
    )
    validate_committed_snapshot(restored_project)
    security = await restored_security(asyncpg, target_admin, target_app)
    visibility = await rls_visibility(asyncpg, target_app, scope, committed["projectId"])
    replayed = await fresh_replay(target_app, restore_work_root, state)
    post_upgrade_review = await seed_review_history(
        asyncpg, target_admin, target_app, scope, committed
    )
    if post_upgrade_review["latestReviewVersion"] != 2:
        raise RuntimeError("legacy restore could not create a complete current review history")
    connection = await asyncpg.connect(target_admin)
    try:
        review_counts = dict(
            await connection.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM marketops.extraction_runs) AS extraction_runs,
                  (SELECT count(*) FROM marketops.extraction_candidates) AS extraction_candidates,
                  (SELECT count(*) FROM marketops.review_snapshots) AS review_snapshots,
                  (SELECT count(*) FROM marketops.review_snapshot_items) AS review_snapshot_items,
                  (SELECT count(*) FROM marketops.review_decisions) AS review_decisions
                  ,(SELECT count(*) FROM marketops.extraction_run_requests) AS extraction_run_requests
                """
            )
        )
    finally:
        await connection.close()
    expected_review_counts = {
        "extraction_runs": 1,
        "extraction_candidates": 1,
        "review_snapshots": 2,
        "review_snapshot_items": 2,
        "review_decisions": 1,
        "extraction_run_requests": 1,
    }
    if review_counts != expected_review_counts:
        raise RuntimeError("legacy restore produced incomplete current review rows")
    return {
        "accepted": True,
        "bundleSchemaVersion": LEGACY_SCHEMA_VERSION,
        "databaseTableData": list(toc_tables),
        "restoredIntoMigrationSet": list(MIGRATION_SET),
        "legacyRowSetHashesMatch": True,
        "reviewTablesInitiallyEmpty": list(review_tables),
        "objectReferencesMatch": True,
        "immutableObjectsVerified": True,
        "freshApplicationReplay": replayed,
        "postUpgradeReviewVersion": post_upgrade_review["latestReviewVersion"],
        "postUpgradeReviewRows": review_counts,
        "security": security,
        "visibility": visibility,
    }


async def verify_review_v2_upgrade_restore(
    asyncpg: Any,
    *,
    bundle: Any,
    toc_tables: tuple[str, ...],
    container_id: str,
    database_name: str,
    admin_dsn: str,
    migrator_dsn: str,
    app_dsn: str,
    restore_work_root: Path,
    scope: Any,
    committed: Mapping[str, Any],
) -> dict[str, Any]:
    target_admin = replace_database_in_dsn(admin_dsn, database_name)
    target_migrator = replace_database_in_dsn(migrator_dsn, database_name)
    target_app = replace_database_in_dsn(app_dsn, database_name)
    await migrate_and_grant(asyncpg, target_migrator)
    await assert_empty_business_tables(asyncpg, target_admin)
    restore_object_bundle(bundle, restore_work_root / "objects")
    await asyncio.to_thread(
        restore_database_dump, container_id, database_name, bundle.database_dump
    )
    connection = await asyncpg.connect(target_admin)
    try:
        restored_snapshot = await database_snapshot(connection)
    finally:
        await connection.close()
    for table in REVIEW_BUSINESS_TABLES:
        if restored_snapshot[table] != bundle.manifest["snapshot"][table]:
            raise RuntimeError("review v2 restore row-set hashes differ from its snapshot")
    empty_request = {"count": 0, "rowSetSha256": _row_set_hash([])}
    if restored_snapshot["extraction_run_requests"] != empty_request:
        raise RuntimeError("review v2 restore unexpectedly populated idempotency requests")
    post_upgrade_review = await seed_review_history(
        asyncpg, target_admin, target_app, scope, committed
    )
    if post_upgrade_review["latestReviewVersion"] != 2:
        raise RuntimeError("review v2 restore could not create a current review request")
    request_count = await asyncpg.connect(target_admin)
    try:
        restored_requests = await request_count.fetchval(
            "SELECT count(*) FROM marketops.extraction_run_requests"
        )
    finally:
        await request_count.close()
    if restored_requests != 1:
        raise RuntimeError("review v2 upgrade did not admit persistent idempotency")
    return {
        "accepted": True,
        "bundleSchemaVersion": REVIEW_SCHEMA_VERSION,
        "databaseTableData": list(toc_tables),
        "restoredIntoMigrationSet": list(MIGRATION_SET),
        "legacyReviewRowsPreserved": True,
        "idempotencyRequestsInitiallyEmpty": True,
        "postUpgradeIdempotencyRequests": restored_requests,
    }


async def run(work_root: Path, state_path: Path, output: Path) -> None:
    try:
        import asyncpg
    except ImportError as error:
        raise RuntimeError("asyncpg is required for the backup/restore gate") from error

    state = load_state(state_path)
    scope = scope_from_state(state)
    committed = state["committed"]
    admin_dsn = required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    migrator_dsn = required_environment("MARKETOPS_TEST_MIGRATOR_DATABASE_URL")
    app_dsn = required_environment("MARKETOPS_TEST_DATABASE_URL")
    container_id = validate_container_id(required_environment("MARKETOPS_TEST_POSTGRES_CONTAINER_ID"))
    dump_version = await asyncio.to_thread(postgres_tool_version, container_id, "pg_dump")
    restore_version = await asyncio.to_thread(postgres_tool_version, container_id, "pg_restore")
    bundle_root = work_root / "bundle"
    restore_work_root = work_root / "isolated-restore"
    legacy_bundle_root = work_root / "legacy-v1-bundle"
    legacy_restore_work_root = work_root / "legacy-v1-restore"
    review_v2_bundle_root = work_root / "review-v2-bundle"
    review_v2_restore_work_root = work_root / "review-v2-restore"
    restore_database = "marketops_restore_" + uuid4().hex[:16]
    legacy_restore_database = "marketops_restore_" + uuid4().hex[:16]
    review_v2_restore_database = "marketops_restore_" + uuid4().hex[:16]
    negative_database = "marketops_restore_" + uuid4().hex[:16]
    created_databases: list[str] = []

    try:
        legacy_bundle, legacy_toc_tables = await export_legacy_bundle(
            asyncpg,
            admin_dsn=admin_dsn,
            container_id=container_id,
            work_root=work_root,
            bundle_root=legacy_bundle_root,
            dump_version=dump_version,
            restore_version=restore_version,
        )
        review_history = await seed_review_history(asyncpg, admin_dsn, app_dsn, scope, committed)
        review_v2_bundle, review_v2_toc_tables = await export_review_v2_bundle(
            asyncpg,
            admin_dsn=admin_dsn,
            container_id=container_id,
            work_root=work_root,
            bundle_root=review_v2_bundle_root,
            dump_version=dump_version,
            restore_version=restore_version,
        )
        schedule_history = await seed_schedule_history(
            asyncpg, app_dsn, scope, committed, review_history
        )
        manifest, toc_tables = await export_bundle(
            asyncpg,
            admin_dsn=admin_dsn,
            container_id=container_id,
            work_root=work_root,
            bundle_root=bundle_root,
            dump_version=dump_version,
            restore_version=restore_version,
        )
        bundle = load_backup_bundle(bundle_root)
        negative = await negative_bundle_checks(bundle_root, work_root)

        await create_restore_database(asyncpg, admin_dsn, legacy_restore_database)
        created_databases.append(legacy_restore_database)
        legacy_upgrade = await verify_legacy_upgrade_restore(
            asyncpg,
            bundle=legacy_bundle,
            toc_tables=legacy_toc_tables,
            container_id=container_id,
            database_name=legacy_restore_database,
            admin_dsn=admin_dsn,
            migrator_dsn=migrator_dsn,
            app_dsn=app_dsn,
            restore_work_root=legacy_restore_work_root,
            state=state,
            scope=scope,
            committed=committed,
        )

        await create_restore_database(asyncpg, admin_dsn, review_v2_restore_database)
        created_databases.append(review_v2_restore_database)
        review_v2_upgrade = await verify_review_v2_upgrade_restore(
            asyncpg,
            bundle=review_v2_bundle,
            toc_tables=review_v2_toc_tables,
            container_id=container_id,
            database_name=review_v2_restore_database,
            admin_dsn=admin_dsn,
            migrator_dsn=migrator_dsn,
            app_dsn=app_dsn,
            restore_work_root=review_v2_restore_work_root,
            scope=scope,
            committed=committed,
        )

        await create_restore_database(asyncpg, admin_dsn, restore_database)
        created_databases.append(restore_database)
        target_admin = replace_database_in_dsn(admin_dsn, restore_database)
        target_migrator = replace_database_in_dsn(migrator_dsn, restore_database)
        target_app = replace_database_in_dsn(app_dsn, restore_database)
        await migrate_and_grant(asyncpg, target_migrator)
        await assert_empty_business_tables(asyncpg, target_admin)
        restore_object_bundle(bundle, restore_work_root / "objects")
        await asyncio.to_thread(
            restore_database_dump, container_id, restore_database, bundle.database_dump
        )

        target_connection = await asyncpg.connect(target_admin)
        try:
            restored_snapshot = await database_snapshot(target_connection)
            restored_objects = await referenced_objects(target_connection)
        finally:
            await target_connection.close()
        if restored_snapshot != manifest["snapshot"]:
            raise RuntimeError("restored database row-set hashes differ from the exported snapshot")
        manifest_objects = [
            {key: item[key] for key in ("storageKey", "kind", "sizeBytes", "sha256")}
            for item in manifest["objects"]
        ]
        if restored_objects != manifest_objects:
            raise RuntimeError("restored database object references differ from the manifest")
        await verify_manifest_objects(bundle, restore_work_root / "objects")

        restored_project, _ = await project_snapshot(
            asyncpg,
            target_admin,
            organization_id=scope.organization_id,
            project_id=committed["projectId"],
            expected_proposal_artifact_id=committed["proposalArtifactId"],
            expected_proposal_version_id=committed["proposalVersionId"],
        )
        validate_committed_snapshot(restored_project)
        security = await restored_security(asyncpg, target_admin, target_app)
        visibility = await rls_visibility(asyncpg, target_app, scope, committed["projectId"])
        replayed = await fresh_replay(target_app, restore_work_root, state)
        review_replayed = await fresh_review_replay(
            asyncpg, target_app, scope, committed, review_history
        )

        source_project_after, _ = await project_snapshot(
            asyncpg,
            admin_dsn,
            organization_id=scope.organization_id,
            project_id=committed["projectId"],
            expected_proposal_artifact_id=committed["proposalArtifactId"],
            expected_proposal_version_id=committed["proposalVersionId"],
        )
        validate_committed_snapshot(source_project_after)
        await verify_manifest_objects(bundle, work_root / "objects")

        await create_restore_database(asyncpg, admin_dsn, negative_database)
        created_databases.append(negative_database)
        negative_admin = replace_database_in_dsn(admin_dsn, negative_database)
        negative_migrator = replace_database_in_dsn(migrator_dsn, negative_database)
        await migrate_and_grant(asyncpg, negative_migrator)
        await install_rejecting_restore_trigger(asyncpg, negative_admin)
        await asyncio.to_thread(
            restore_database_dump,
            container_id,
            negative_database,
            bundle.database_dump,
            expect_failure=True,
        )
        await assert_empty_business_tables(asyncpg, negative_admin)
        negative["singleTransactionRollbackVerified"] = True

        evidence = {
            "schemaVersion": 2,
            "taskId": "M1-02",
            "workPackage": "WP2B-1-review-backup-restore",
            "postgresVersionNum": POSTGRES_VERSION_NUM,
            "migrations": list(MIGRATION_SET),
            "databaseTableData": list(toc_tables),
            "snapshot": manifest["snapshot"],
            "objectCount": len(manifest["objects"]),
            "databaseDumpSha256": manifest["database"]["sha256"],
            "legacyV1UpgradeRestore": legacy_upgrade,
            "legacyV2UpgradeRestore": review_v2_upgrade,
            "isolatedRestore": {
                "accepted": True,
                "projectAggregate": TABLE_COUNTS,
                "approvedProposalSelected": True,
                "objectReferencesMatch": True,
                "immutableObjectsVerified": True,
                "freshApplicationReplay": replayed,
                "sourceCommittedAggregateUnchanged": True,
                "sourceObjectsUnchanged": True,
                "security": security,
                "visibility": visibility,
                "reviewHistory": review_history,
                "scheduleHistory": schedule_history,
                "freshReviewReplay": review_replayed,
                "persistentIdempotencyRequestRestored": (
                    manifest["snapshot"]["extraction_run_requests"]["count"] == 1
                    and restored_snapshot["extraction_run_requests"]
                    == manifest["snapshot"]["extraction_run_requests"]
                ),
            },
            "negativeChecks": negative,
            "claimBoundary": (
                "This synthetic CI experiment establishes application-level logical backup and "
                "isolated restore for the reviewed PostgreSQL 18.4 migrations, non-empty review history, "
                "a seven-table v1 bundle and a twelve-table v2 review bundle each upgraded into the current "
                "four-migration schema, a non-empty v3 persistent review idempotency request replayed through "
                "a fresh ReviewService, non-empty WBS and schedule rows restored by row-set hash, and the current immutable "
                "local-object adapter. It does not establish physical crash consistency, WAL/PITR, "
                "authenticity, encryption, off-site retention, production cutover, RPO/RTO, "
                "cross-host or PostgreSQL-version recovery, demand, ROI, repeat use, payment, or M1-02 completion."
            ),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(output.read_text(encoding="utf-8"), end="")
    finally:
        active_error = sys.exc_info()[1]
        cleanup_failed = False
        for database in reversed(created_databases):
            try:
                await drop_restore_database(asyncpg, admin_dsn, database)
            except Exception:
                cleanup_failed = True
        for path in (
            bundle_root,
            restore_work_root,
            legacy_bundle_root,
            legacy_restore_work_root,
            review_v2_bundle_root,
            review_v2_restore_work_root,
            *(work_root / label for label in NEGATIVE_BUNDLE_LABELS),
        ):
            try:
                shutil.rmtree(path, ignore_errors=False)
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_failed = True
        try:
            (work_root / "database.dump").unlink(missing_ok=True)
            (work_root / "legacy-database.dump").unlink(missing_ok=True)
            (work_root / "review-v2-database.dump").unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
        if cleanup_failed:
            if active_error is not None:
                active_error.add_note("WP2B-1 backup/restore cleanup also failed")
            else:
                raise RuntimeError("WP2B-1 backup/restore cleanup failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.work_root, arguments.state, arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
