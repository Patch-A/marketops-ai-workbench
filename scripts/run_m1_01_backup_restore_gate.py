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
    MIGRATION_SHA256,
    create_backup_bundle,
    load_backup_bundle,
    restore_object_bundle,
)
from apps.api.marketops_import.postgres import AsyncpgImportRepository  # noqa: E402
from apps.api.marketops_import.service import ProjectImportService, StoredObject  # noqa: E402
from apps.api.marketops_import.storage import LocalObjectStore  # noqa: E402
from scripts.run_m1_01_postgres_gate import (  # noqa: E402
    APPLICATION_ROLE,
    DATABASE_NAME,
    MIGRATION,
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
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    if not isinstance(snapshot_id, str) or re.fullmatch(r"[0-9A-Fa-f:-]+", snapshot_id) is None:
        raise RuntimeError("exported PostgreSQL snapshot identifier is invalid")
    output.parent.mkdir(parents=True, exist_ok=True)
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


def validate_dump_toc(value: str) -> tuple[str, ...]:
    tables: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        match = TOC_TABLE_DATA.fullmatch(line)
        if match is None:
            raise RuntimeError("database dump contains a non-allowlisted entry")
        tables.append(match.group(1))
    if len(tables) != len(set(tables)) or set(tables) != set(BUSINESS_TABLES):
        raise RuntimeError("database dump table-data allowlist is incomplete or duplicated")
    return tuple(tables)


def publish_after_toc_validation(
    container_id: str,
    dump_path: Path,
    publisher: Callable[[], Any],
    *,
    toc_reader: Callable[[str, Path], str] = read_dump_toc,
) -> tuple[Any, tuple[str, ...]]:
    tables = validate_dump_toc(toc_reader(container_id, dump_path))
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


async def database_snapshot(connection: Any) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for table in BUSINESS_TABLES:
        rows = await connection.fetch(
            f"""
            SELECT pg_catalog.to_jsonb(record)::text AS payload
            FROM marketops.{table} AS record
            ORDER BY record.id
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
            source_migration = await connection.fetchval(
                "SELECT pg_catalog.encode(sha256, 'hex') FROM marketops.schema_migrations WHERE migration_name = $1",
                MIGRATION.name,
            )
            if source_migration != MIGRATION_SHA256:
                raise RuntimeError("source migration registry differs from the reviewed migration")
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
            migration={"name": MIGRATION.name, "sha256": MIGRATION_SHA256},
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
        checksum = await connection.fetchval(
            "SELECT pg_catalog.encode(sha256, 'hex') FROM marketops.schema_migrations WHERE migration_name = $1",
            MIGRATION.name,
        )
        if checksum != MIGRATION_SHA256:
            raise RuntimeError("restored migration registry checksum drifted")
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


async def negative_bundle_checks(bundle_root: Path, work_root: Path) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for label, relative in (
        ("tamperedDumpRejected", Path("database.dump")),
        ("tamperedObjectRejected", None),
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
            shutil.rmtree(target, ignore_errors=True)
    return results


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
    restore_database = "marketops_restore_" + uuid4().hex[:16]
    negative_database = "marketops_restore_" + uuid4().hex[:16]
    created_databases: list[str] = []

    try:
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
            "schemaVersion": 1,
            "taskId": "M1-01",
            "workPackage": "WP5B-backup-restore",
            "postgresVersionNum": POSTGRES_VERSION_NUM,
            "migrationSha256": MIGRATION_SHA256,
            "databaseTableData": list(toc_tables),
            "snapshot": manifest["snapshot"],
            "objectCount": len(manifest["objects"]),
            "databaseDumpSha256": manifest["database"]["sha256"],
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
            },
            "negativeChecks": negative,
            "claimBoundary": (
                "This synthetic CI experiment establishes application-level logical backup and "
                "isolated restore for the reviewed PostgreSQL 18.4 migration and current immutable "
                "local-object adapter. It does not establish physical crash consistency, WAL/PITR, "
                "authenticity, encryption, off-site retention, production cutover, RPO/RTO, "
                "cross-host or cross-version recovery, demand, ROI, repeat use, payment, or M1-01 completion."
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
        for path in (bundle_root, restore_work_root):
            try:
                shutil.rmtree(path, ignore_errors=False)
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_failed = True
        try:
            (work_root / "database.dump").unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
        if cleanup_failed:
            if active_error is not None:
                active_error.add_note("WP5B isolated cleanup also failed")
            else:
                raise RuntimeError("WP5B isolated cleanup failed")


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
