#!/usr/bin/env python3
"""Provision and attest the isolated PostgreSQL runtime used by M1-01 CI."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.marketops_import.migrations import run_migrations  # noqa: E402


MIGRATION = ROOT / "apps" / "api" / "migrations" / "0001_project_import.sql"
REVIEW_MIGRATION = ROOT / "apps" / "api" / "migrations" / "0002_extraction_review.sql"
IDEMPOTENCY_MIGRATION = (
    ROOT / "apps" / "api" / "migrations" / "0003_review_idempotency.sql"
)
SCHEDULE_MIGRATION = (
    ROOT / "apps" / "api" / "migrations" / "0004_wbs_schedule.sql"
)
APPROVAL_MIGRATION = (
    ROOT / "apps" / "api" / "migrations" / "0005_wbs_plan_approval.sql"
)
EXECUTION_MIGRATION = (
    ROOT / "apps" / "api" / "migrations" / "0006_task_execution.sql"
)
RETRIEVAL_MIGRATION = (
    ROOT / "apps" / "api" / "migrations" / "0007_source_retrieval.sql"
)
MIGRATOR_ROLE = "marketops_migrator"
APPLICATION_ROLE = "marketops_app"
DATABASE_NAME = "marketops_test"
POSTGRES_VERSION_NUM = 180004
POSTGRES_DIGEST = re.compile(r"postgres@sha256:[0-9a-f]{64}")
PINNED_POSTGRES_IMAGE = (
    "postgres@sha256:"
    "a02db8cac496f15b094798a38254f14d6e00741f709360e5e00bb6668ea31636"
)
RELATION_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
SEQUENCE_PRIVILEGES = ("SELECT", "UPDATE", "USAGE")
EXPECTED_RELATION_PRIVILEGES = frozenset(
    {
        ("organizations", "SELECT"),
        ("workspaces", "SELECT"),
        ("clients", "SELECT"),
        ("projects", "SELECT"),
        ("projects", "INSERT"),
        ("artifacts", "SELECT"),
        ("artifacts", "INSERT"),
        ("artifact_versions", "SELECT"),
        ("artifact_versions", "INSERT"),
        ("audit_events", "SELECT"),
        ("audit_events", "INSERT"),
        ("extraction_runs", "SELECT"),
        ("extraction_runs", "INSERT"),
        ("extraction_candidates", "SELECT"),
        ("extraction_candidates", "INSERT"),
        ("review_snapshots", "SELECT"),
        ("review_snapshots", "INSERT"),
        ("review_snapshot_items", "SELECT"),
        ("review_snapshot_items", "INSERT"),
        ("review_decisions", "SELECT"),
        ("review_decisions", "INSERT"),
        ("extraction_run_requests", "SELECT"),
        ("extraction_run_requests", "INSERT"),
        ("wbs_plans", "SELECT"),
        ("wbs_plans", "INSERT"),
        ("wbs_plan_versions", "SELECT"),
        ("wbs_plan_versions", "INSERT"),
        ("wbs_tasks", "SELECT"),
        ("wbs_tasks", "INSERT"),
        ("schedule_snapshots", "SELECT"),
        ("schedule_snapshots", "INSERT"),
        ("wbs_plan_approvals", "SELECT"),
        ("wbs_plan_approvals", "INSERT"),
        ("wbs_task_execution_updates", "SELECT"),
        ("wbs_task_execution_updates", "INSERT"),
        ("source_indexes", "SELECT"),
        ("source_indexes", "INSERT"),
        ("source_chunks", "SELECT"),
        ("source_chunks", "INSERT"),
        ("source_chunks", "DELETE"),
    }
)
EXPECTED_FUNCTION_EXECUTE = frozenset(
    {
        "current_actor_id()",
        "current_client_id()",
        "current_project_id()",
        "current_workspace_id()",
    }
)
EXPECTED_COLUMN_UPDATE_PRIVILEGES = frozenset(
    {
        ("projects", "created_by"),
        ("extraction_runs", "created_by"),
        ("wbs_plans", "created_by"),
        ("source_indexes", "status"),
        ("source_indexes", "withdrawn_by"),
        ("source_indexes", "withdrawn_at"),
    }
)


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required CI environment variable: {name}")
    return value


def validate_postgres_image(image_reference: str, repo_digest: str) -> None:
    if POSTGRES_DIGEST.fullmatch(image_reference) is None:
        raise RuntimeError("PostgreSQL image must be an immutable canonical digest")
    if image_reference != PINNED_POSTGRES_IMAGE:
        raise RuntimeError("PostgreSQL image differs from the reviewed service image digest")
    if repo_digest != image_reference:
        raise RuntimeError("service-container RepoDigest differs from the configured image")


async def quoted_literal(connection, value: str) -> str:
    return await connection.fetchval("SELECT pg_catalog.quote_literal($1)", value)


async def ensure_login_role(connection, name: str, password: str) -> None:
    if name not in {MIGRATOR_ROLE, APPLICATION_ROLE}:
        raise RuntimeError("refusing to provision an unexpected role")
    literal = await quoted_literal(connection, password)
    exists = await connection.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = $1)", name
    )
    if not exists:
        await connection.execute(
            f"CREATE ROLE {name} LOGIN PASSWORD {literal} "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
            "NOREPLICATION NOBYPASSRLS"
        )
    else:
        await connection.execute(
            f"ALTER ROLE {name} LOGIN PASSWORD {literal} "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
            "NOREPLICATION NOBYPASSRLS"
        )


def validate_server_log_safety(
    log_error_verbosity: str, log_min_error_statement: str
) -> dict[str, str]:
    if log_error_verbosity != "terse" or log_min_error_statement != "panic":
        raise RuntimeError("PostgreSQL CI logging could expose failure-row context")
    return {
        "errorVerbosity": log_error_verbosity,
        "minimumErrorStatement": log_min_error_statement,
    }


async def provision_roles_and_database(
    asyncpg, admin_dsn: str
) -> tuple[str, int, dict[str, str]]:
    connection = await asyncpg.connect(admin_dsn)
    try:
        version = await connection.fetchval("SHOW server_version")
        version_num = await connection.fetchval(
            "SELECT current_setting('server_version_num')::integer"
        )
        if version_num != POSTGRES_VERSION_NUM:
            raise RuntimeError(
                f"expected PostgreSQL server_version_num {POSTGRES_VERSION_NUM}, "
                f"got {version_num!r}"
            )
        log_safety = validate_server_log_safety(
            await connection.fetchval("SHOW log_error_verbosity"),
            await connection.fetchval("SHOW log_min_error_statement"),
        )
        await ensure_login_role(
            connection, MIGRATOR_ROLE, required_environment("MARKETOPS_TEST_MIGRATOR_PASSWORD")
        )
        await ensure_login_role(
            connection, APPLICATION_ROLE, required_environment("MARKETOPS_TEST_APP_PASSWORD")
        )
        await connection.execute(f"REVOKE {MIGRATOR_ROLE} FROM {APPLICATION_ROLE}")
        await connection.execute(f"ALTER DATABASE {DATABASE_NAME} OWNER TO {MIGRATOR_ROLE}")
        return version, version_num, log_safety
    finally:
        await connection.close()


async def migrate_and_grant(asyncpg, migrator_dsn: str) -> tuple[str, ...]:
    connection = await asyncpg.connect(migrator_dsn)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            migration_directory = Path(temporary)
            (migration_directory / MIGRATION.name).write_bytes(MIGRATION.read_bytes())
            first = await run_migrations(connection, migration_directory)
            if first != (MIGRATION.name,):
                raise RuntimeError(f"base migration contract failed: {first!r}")
            await connection.execute(
                f"""
            GRANT USAGE ON SCHEMA marketops TO {APPLICATION_ROLE};
            GRANT SELECT ON
                marketops.organizations,
                marketops.workspaces,
                marketops.clients
            TO {APPLICATION_ROLE};
            GRANT SELECT, INSERT ON marketops.projects
            TO {APPLICATION_ROLE};
            GRANT SELECT, INSERT ON
                marketops.artifacts,
                marketops.artifact_versions,
                marketops.audit_events
            TO {APPLICATION_ROLE};
            GRANT EXECUTE ON FUNCTION
                marketops.current_workspace_id(),
                marketops.current_client_id(),
                marketops.current_project_id(),
                marketops.current_actor_id()
            TO {APPLICATION_ROLE};
            """
            )
            (migration_directory / REVIEW_MIGRATION.name).write_bytes(
                REVIEW_MIGRATION.read_bytes()
            )
            second = await run_migrations(
                connection,
                migration_directory,
                allowed_schema_usage_roles=(APPLICATION_ROLE,),
            )
            if second != (REVIEW_MIGRATION.name,):
                raise RuntimeError(f"review migration upgrade contract failed: {second!r}")
            (migration_directory / IDEMPOTENCY_MIGRATION.name).write_bytes(
                IDEMPOTENCY_MIGRATION.read_bytes()
            )
            third = await run_migrations(
                connection,
                migration_directory,
                allowed_schema_usage_roles=(APPLICATION_ROLE,),
            )
            if third != (IDEMPOTENCY_MIGRATION.name,):
                raise RuntimeError(
                    f"review idempotency migration contract failed: {third!r}"
                )
            (migration_directory / SCHEDULE_MIGRATION.name).write_bytes(
                SCHEDULE_MIGRATION.read_bytes()
            )
            fourth = await run_migrations(
                connection,
                migration_directory,
                allowed_schema_usage_roles=(APPLICATION_ROLE,),
            )
            if fourth != (SCHEDULE_MIGRATION.name,):
                raise RuntimeError(
                    f"WBS schedule migration contract failed: {fourth!r}"
                )
            (migration_directory / APPROVAL_MIGRATION.name).write_bytes(
                APPROVAL_MIGRATION.read_bytes()
            )
            fifth = await run_migrations(
                connection,
                migration_directory,
                allowed_schema_usage_roles=(APPLICATION_ROLE,),
            )
            if fifth != (APPROVAL_MIGRATION.name,):
                raise RuntimeError(
                    f"WBS approval migration contract failed: {fifth!r}"
                )
            (migration_directory / EXECUTION_MIGRATION.name).write_bytes(
                EXECUTION_MIGRATION.read_bytes()
            )
            sixth = await run_migrations(
                connection,
                migration_directory,
                allowed_schema_usage_roles=(APPLICATION_ROLE,),
            )
            if sixth != (EXECUTION_MIGRATION.name,):
                raise RuntimeError(
                    f"task execution migration contract failed: {sixth!r}"
                )
            (migration_directory / RETRIEVAL_MIGRATION.name).write_bytes(
                RETRIEVAL_MIGRATION.read_bytes()
            )
            seventh = await run_migrations(
                connection,
                migration_directory,
                allowed_schema_usage_roles=(APPLICATION_ROLE,),
            )
            if seventh != (RETRIEVAL_MIGRATION.name,):
                raise RuntimeError(
                    f"source retrieval migration contract failed: {seventh!r}"
                )
            post_grant_replay = await run_migrations(
                connection,
                migration_directory,
                allowed_schema_usage_roles=(APPLICATION_ROLE,),
            )
            if post_grant_replay:
                raise RuntimeError(
                    f"post-grant migration replay unexpectedly applied {post_grant_replay!r}"
                )
        await connection.execute(
            f"""
            GRANT SELECT, INSERT ON
                marketops.extraction_runs,
                marketops.extraction_candidates,
                marketops.review_snapshots,
                marketops.review_snapshot_items,
                marketops.review_decisions,
                marketops.extraction_run_requests,
                marketops.wbs_plans,
                marketops.wbs_plan_versions,
                marketops.wbs_tasks,
                marketops.schedule_snapshots,
                marketops.wbs_plan_approvals,
                marketops.wbs_task_execution_updates
            TO {APPLICATION_ROLE};
            GRANT SELECT, INSERT ON marketops.source_indexes
            TO {APPLICATION_ROLE};
            GRANT UPDATE (status, withdrawn_by, withdrawn_at)
            ON marketops.source_indexes TO {APPLICATION_ROLE};
            GRANT SELECT, INSERT, DELETE ON marketops.source_chunks
            TO {APPLICATION_ROLE};
            GRANT UPDATE (created_by) ON marketops.projects TO {APPLICATION_ROLE};
            GRANT UPDATE (created_by) ON marketops.extraction_runs TO {APPLICATION_ROLE};
            GRANT UPDATE (created_by) ON marketops.wbs_plans TO {APPLICATION_ROLE};
            """
        )
        expected_migrations = {
            MIGRATION.name: hashlib.sha256(MIGRATION.read_bytes()).digest(),
            REVIEW_MIGRATION.name: hashlib.sha256(REVIEW_MIGRATION.read_bytes()).digest(),
            IDEMPOTENCY_MIGRATION.name: hashlib.sha256(
                IDEMPOTENCY_MIGRATION.read_bytes()
            ).digest(),
            SCHEDULE_MIGRATION.name: hashlib.sha256(
                SCHEDULE_MIGRATION.read_bytes()
            ).digest(),
            APPROVAL_MIGRATION.name: hashlib.sha256(
                APPROVAL_MIGRATION.read_bytes()
            ).digest(),
            EXECUTION_MIGRATION.name: hashlib.sha256(
                EXECUTION_MIGRATION.read_bytes()
            ).digest(),
            RETRIEVAL_MIGRATION.name: hashlib.sha256(
                RETRIEVAL_MIGRATION.read_bytes()
            ).digest(),
        }
        recorded = await connection.fetch(
            """
            SELECT migration_name, sha256
            FROM marketops.schema_migrations
            ORDER BY migration_name
            """
        )
        observed_migrations = {
            str(row["migration_name"]): bytes(row["sha256"]) for row in recorded
        }
        if observed_migrations != expected_migrations:
            raise RuntimeError("migration registry checksums do not match reviewed SQL")
        return (
            MIGRATION.name,
            REVIEW_MIGRATION.name,
            IDEMPOTENCY_MIGRATION.name,
            SCHEDULE_MIGRATION.name,
            APPROVAL_MIGRATION.name,
            EXECUTION_MIGRATION.name,
            RETRIEVAL_MIGRATION.name,
        )
    finally:
        await connection.close()


def _granted_pairs(rows, object_key: str) -> frozenset[tuple[str, str]]:
    return frozenset(
        (str(row[object_key]), str(row["privilege"]))
        for row in rows
        if row["granted"]
    )


def _reject_grant_options(rows, label: str, object_key: str) -> None:
    grantable = sorted(
        (str(row[object_key]), str(row["privilege"]))
        for row in rows
        if row["grantable"]
    )
    if grantable:
        raise RuntimeError(
            f"application {label} privileges include grant options: {grantable!r}"
        )


def _validate_application_attestation(
    role,
    memberships,
    schema_privileges,
    relation_privileges,
    column_privileges,
    sequence_privileges,
    function_privileges,
) -> dict[str, object]:
    expected_role = {
        "rolname": APPLICATION_ROLE,
        "rolcanlogin": True,
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolinherit": False,
        "rolreplication": False,
        "rolbypassrls": False,
    }
    observed_role = dict(role) if role is not None else {}
    if observed_role != expected_role:
        raise RuntimeError(f"application role attributes drifted: {observed_role!r}")

    observed_memberships = sorted(str(row["granted_role"]) for row in memberships)
    if observed_memberships:
        raise RuntimeError(
            "application role can SET ROLE through unexpected memberships: "
            f"{observed_memberships!r}"
        )

    observed_schema = {
        str(row["privilege"]): bool(row["granted"]) for row in schema_privileges
    }
    expected_schema = {"CREATE": False, "USAGE": True}
    if observed_schema != expected_schema:
        raise RuntimeError(
            f"application schema privileges drifted: {observed_schema!r}"
        )
    _reject_grant_options(schema_privileges, "schema", "privilege")

    observed_relations = _granted_pairs(relation_privileges, "object_name")
    if observed_relations != EXPECTED_RELATION_PRIVILEGES:
        missing = sorted(EXPECTED_RELATION_PRIVILEGES - observed_relations)
        unexpected = sorted(observed_relations - EXPECTED_RELATION_PRIVILEGES)
        raise RuntimeError(
            "application relation privileges drifted: "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
    _reject_grant_options(relation_privileges, "relation", "object_name")

    observed_columns = _granted_pairs(column_privileges, "object_name")
    unexpected_columns = sorted(
        pair for pair in observed_columns
        if pair[1] != "UPDATE" and pair not in EXPECTED_RELATION_PRIVILEGES
    )
    if unexpected_columns:
        raise RuntimeError(
            "application column privileges exceed the table privilege matrix: "
            f"{unexpected_columns!r}"
        )
    observed_column_updates = frozenset(
        (str(row["object_name"]), str(row["column_name"]))
        for row in column_privileges
        if row["granted"] and row["privilege"] == "UPDATE"
    )
    if observed_column_updates != EXPECTED_COLUMN_UPDATE_PRIVILEGES:
        raise RuntimeError(
            "application column UPDATE privileges drifted: "
            f"observed={sorted(observed_column_updates)!r}"
        )
    _reject_grant_options(column_privileges, "column", "object_name")

    observed_sequences = _granted_pairs(sequence_privileges, "object_name")
    if observed_sequences:
        raise RuntimeError(
            f"application sequence privileges drifted: {sorted(observed_sequences)!r}"
        )
    _reject_grant_options(sequence_privileges, "sequence", "object_name")

    observed_functions = frozenset(
        str(row["function_signature"])
        for row in function_privileges
        if row["granted"]
    )
    if observed_functions != EXPECTED_FUNCTION_EXECUTE:
        missing = sorted(EXPECTED_FUNCTION_EXECUTE - observed_functions)
        unexpected = sorted(observed_functions - EXPECTED_FUNCTION_EXECUTE)
        raise RuntimeError(
            "application function EXECUTE privileges drifted: "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
    _reject_grant_options(function_privileges, "function", "function_signature")

    relation_summary: dict[str, list[str]] = {}
    for object_name, privilege in sorted(observed_relations):
        relation_summary.setdefault(f"marketops.{object_name}", []).append(privilege)
    return {
        "attributes": observed_role,
        "memberOf": observed_memberships,
        "schemaPrivileges": ["USAGE"],
        "relationPrivileges": relation_summary,
        "functionExecute": sorted(observed_functions),
        "grantOptions": [],
        "sequencePrivileges": [],
        "columnUpdatePrivileges": [
            f"marketops.{table}.{column}"
            for table, column in sorted(observed_column_updates)
        ],
        "unexpectedColumnPrivileges": [],
    }


async def attest_application_role(asyncpg, app_dsn: str) -> dict[str, object]:
    connection = await asyncpg.connect(app_dsn)
    try:
        role = await connection.fetchrow(
            """
            SELECT
                rolname,
                rolcanlogin,
                rolsuper,
                rolcreatedb,
                rolcreaterole,
                rolinherit,
                rolreplication,
                rolbypassrls
            FROM pg_catalog.pg_roles
            WHERE rolname = current_user
            """
        )
        memberships = await connection.fetch(
            """
            SELECT granted_role.rolname AS granted_role
            FROM pg_catalog.pg_auth_members AS membership
            JOIN pg_catalog.pg_roles AS member_role
              ON member_role.oid = membership.member
            JOIN pg_catalog.pg_roles AS granted_role
              ON granted_role.oid = membership.roleid
            WHERE member_role.rolname = current_user
            ORDER BY granted_role.rolname
            """
        )
        schema_privileges = await connection.fetch(
            """
            SELECT privilege,
                   pg_catalog.has_schema_privilege(
                       current_user, 'marketops', privilege
                   ) AS granted,
                   pg_catalog.has_schema_privilege(
                       current_user, 'marketops', privilege || ' WITH GRANT OPTION'
                   ) AS grantable
            FROM (VALUES ('CREATE'), ('USAGE')) AS privileges(privilege)
            ORDER BY privilege
            """
        )
        relation_privileges = await connection.fetch(
            f"""
            SELECT relation.relname AS object_name,
                   privilege,
                   pg_catalog.has_table_privilege(
                       current_user, relation.oid, privilege
                   ) AS granted,
                   pg_catalog.has_table_privilege(
                       current_user,
                       relation.oid,
                       privilege || ' WITH GRANT OPTION'
                   ) AS grantable
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            CROSS JOIN (VALUES {", ".join(f"('{item}')" for item in RELATION_PRIVILEGES)})
                AS privileges(privilege)
            WHERE namespace.nspname = 'marketops'
              AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
            ORDER BY relation.relname, privilege
            """
        )
        column_privileges = await connection.fetch(
            f"""
            SELECT relation.relname AS object_name,
                   attribute.attname AS column_name,
                   privilege,
                   pg_catalog.has_column_privilege(
                       current_user, relation.oid, attribute.attnum, privilege
                   ) AS granted,
                   pg_catalog.has_column_privilege(
                       current_user,
                       relation.oid,
                       attribute.attnum,
                       privilege || ' WITH GRANT OPTION'
                   ) AS grantable
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_attribute AS attribute
              ON attribute.attrelid = relation.oid
             AND attribute.attnum > 0
             AND NOT attribute.attisdropped
            CROSS JOIN (VALUES {", ".join(f"('{item}')" for item in COLUMN_PRIVILEGES)})
                AS privileges(privilege)
            WHERE namespace.nspname = 'marketops'
              AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
            ORDER BY relation.relname, attribute.attnum, privilege
            """
        )
        sequence_privileges = await connection.fetch(
            f"""
            SELECT relation.relname AS object_name,
                   privilege,
                   pg_catalog.has_sequence_privilege(
                       current_user, relation.oid, privilege
                   ) AS granted,
                   pg_catalog.has_sequence_privilege(
                       current_user,
                       relation.oid,
                       privilege || ' WITH GRANT OPTION'
                   ) AS grantable
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            CROSS JOIN (VALUES {", ".join(f"('{item}')" for item in SEQUENCE_PRIVILEGES)})
                AS privileges(privilege)
            WHERE namespace.nspname = 'marketops'
              AND relation.relkind = 'S'
            ORDER BY relation.relname, privilege
            """
        )
        function_privileges = await connection.fetch(
            """
            SELECT routine.proname || '(' ||
                       pg_catalog.pg_get_function_identity_arguments(routine.oid) || ')'
                       AS function_signature,
                   'EXECUTE' AS privilege,
                   pg_catalog.has_function_privilege(
                       current_user, routine.oid, 'EXECUTE'
                   ) AS granted,
                   pg_catalog.has_function_privilege(
                       current_user, routine.oid, 'EXECUTE WITH GRANT OPTION'
                   ) AS grantable
            FROM pg_catalog.pg_proc AS routine
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = routine.pronamespace
            WHERE namespace.nspname = 'marketops'
            ORDER BY function_signature
            """
        )
        return _validate_application_attestation(
            role,
            memberships,
            schema_privileges,
            relation_privileges,
            column_privileges,
            sequence_privileges,
            function_privileges,
        )
    finally:
        await connection.close()


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def run(output: Path | None) -> None:
    try:
        import asyncpg
    except ImportError as error:
        raise RuntimeError("asyncpg is required for the PostgreSQL runtime gate") from error

    admin_dsn = required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    migrator_dsn = required_environment("MARKETOPS_TEST_MIGRATOR_DATABASE_URL")
    app_dsn = required_environment("MARKETOPS_TEST_DATABASE_URL")
    image_reference = required_environment("MARKETOPS_POSTGRES_IMAGE")
    repo_digest = required_environment("MARKETOPS_POSTGRES_REPO_DIGEST")
    validate_postgres_image(image_reference, repo_digest)
    version, version_num, log_safety = await provision_roles_and_database(
        asyncpg, admin_dsn
    )
    applied = await migrate_and_grant(asyncpg, migrator_dsn)
    role = await attest_application_role(asyncpg, app_dsn)
    evidence = {
        "schemaVersion": 2,
        "taskId": "M1-02",
        "workPackage": "WP2A-2-postgres-bootstrap",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "headSha": git_head(),
        "postgres": {
            "serverVersion": version,
            "serverVersionNum": version_num,
            "imageReference": image_reference,
            "repoDigest": repo_digest,
            "validatedRuntimePlatform": "linux/amd64",
            "safeFailureLogging": log_safety,
        },
        "migration": {
            "applied": list(applied),
            "reviewed": [
                {
                    "name": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in (
                    MIGRATION,
                    REVIEW_MIGRATION,
                    IDEMPOTENCY_MIGRATION,
                    SCHEDULE_MIGRATION,
                    APPROVAL_MIGRATION,
                    EXECUTION_MIGRATION,
                    RETRIEVAL_MIGRATION,
                )
            ],
            "replayApplied": [],
            "postGrantReplayApplied": [],
        },
        "applicationRole": role,
        "claimBoundary": (
            "This isolated CI record can establish version, migration, grant, and role "
            "behavior only; it does not establish adapter behavior, production readiness, "
            "recovery, demand, ROI, "
            "repeat use, payment, or M1-01 completion."
        ),
    }
    rendered = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
