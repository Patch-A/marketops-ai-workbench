from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.marketops_import.migrations import run_migrations
from scripts.run_m1_01_postgres_gate import provision_roles_and_database


MIGRATIONS = ROOT / "apps" / "api" / "migrations"
APPLICATION_ROLE = "marketops_app"


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required test environment variable: {name}")
    return value


async def connect_with_retry(asyncpg, dsn: str):
    last_error: BaseException | None = None
    for attempt in range(3):
        try:
            return await asyncpg.connect(dsn)
        except (OSError, asyncpg.PostgresConnectionError) as error:
            last_error = error
            if attempt < 2:
                await asyncio.sleep(0.25 * (attempt + 1))
    raise RuntimeError("PostgreSQL connection remained unavailable") from last_error


async def provision() -> dict[str, object]:
    try:
        import asyncpg
    except ImportError as error:
        raise RuntimeError("asyncpg is required for the M2-01 PostgreSQL gate") from error

    version, version_num, log_safety = await provision_roles_and_database(
        asyncpg, required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    )
    connection = await asyncpg.connect(
        required_environment("MARKETOPS_TEST_MIGRATOR_DATABASE_URL")
    )
    try:
        app_has_schema_usage = await connection.fetchval(
            """
            SELECT CASE
                WHEN pg_catalog.to_regnamespace('marketops') IS NULL THEN false
                ELSE pg_catalog.has_schema_privilege($1, 'marketops', 'USAGE')
            END
            """,
            APPLICATION_ROLE,
        )
        allowed_roles = (APPLICATION_ROLE,) if app_has_schema_usage else ()
        applied = await run_migrations(
            connection,
            MIGRATIONS,
            allowed_schema_usage_roles=allowed_roles,
        )
        await connection.execute(
            f"""
            GRANT USAGE ON SCHEMA marketops TO {APPLICATION_ROLE};
            GRANT SELECT ON marketops.artifacts TO {APPLICATION_ROLE};
            GRANT SELECT ON marketops.artifact_versions TO {APPLICATION_ROLE};
            GRANT INSERT ON marketops.audit_events TO {APPLICATION_ROLE};
            GRANT SELECT, INSERT ON marketops.source_indexes TO {APPLICATION_ROLE};
            GRANT UPDATE (status, withdrawn_by, withdrawn_at)
                ON marketops.source_indexes TO {APPLICATION_ROLE};
            GRANT SELECT, INSERT, DELETE ON marketops.source_chunks
                TO {APPLICATION_ROLE};
            GRANT EXECUTE ON FUNCTION
                marketops.current_workspace_id(),
                marketops.current_client_id(),
                marketops.current_project_id(),
                marketops.current_actor_id()
            TO {APPLICATION_ROLE};
            """
        )
        replay = await run_migrations(
            connection, MIGRATIONS, allowed_schema_usage_roles=(APPLICATION_ROLE,)
        )
        if replay:
            raise RuntimeError(f"migration replay unexpectedly applied {replay!r}")
        recorded = await connection.fetch(
            "SELECT migration_name FROM marketops.schema_migrations ORDER BY migration_name"
        )
    finally:
        await connection.close()

    app = await connect_with_retry(
        asyncpg, required_environment("MARKETOPS_TEST_DATABASE_URL")
    )
    try:
        privileges = await app.fetchrow(
            """
            SELECT
                has_table_privilege(current_user, 'marketops.artifacts', 'SELECT') AS artifact_select,
                has_table_privilege(current_user, 'marketops.artifact_versions', 'SELECT') AS artifact_version_select,
                has_table_privilege(current_user, 'marketops.source_indexes', 'SELECT') AS index_select,
                has_table_privilege(current_user, 'marketops.source_indexes', 'INSERT') AS index_insert,
                has_table_privilege(current_user, 'marketops.source_indexes', 'UPDATE') AS index_update,
                has_column_privilege(current_user, 'marketops.source_indexes', 'status', 'UPDATE') AS status_update,
                has_column_privilege(current_user, 'marketops.source_indexes', 'withdrawn_by', 'UPDATE') AS withdrawn_by_update,
                has_column_privilege(current_user, 'marketops.source_indexes', 'withdrawn_at', 'UPDATE') AS withdrawn_at_update,
                has_table_privilege(current_user, 'marketops.source_indexes', 'DELETE') AS index_delete,
                has_table_privilege(current_user, 'marketops.source_chunks', 'SELECT') AS chunk_select,
                has_table_privilege(current_user, 'marketops.source_chunks', 'INSERT') AS chunk_insert,
                has_table_privilege(current_user, 'marketops.source_chunks', 'DELETE') AS chunk_delete,
                has_table_privilege(current_user, 'marketops.source_chunks', 'UPDATE') AS chunk_update,
                has_table_privilege(current_user, 'marketops.source_chunks', 'TRUNCATE') AS chunk_truncate
            """
        )
    finally:
        await app.close()
    expected = {
        "artifact_select": True,
        "artifact_version_select": True,
        "index_select": True,
        "index_insert": True,
        "index_update": False,
        "status_update": True,
        "withdrawn_by_update": True,
        "withdrawn_at_update": True,
        "index_delete": False,
        "chunk_select": True,
        "chunk_insert": True,
        "chunk_delete": True,
        "chunk_update": False,
        "chunk_truncate": False,
    }
    if dict(privileges) != expected:
        raise RuntimeError("M2-01 application privilege matrix drifted")
    return {
        "postgresVersion": version,
        "postgresVersionNum": version_num,
        "logSafety": log_safety,
        "appliedMigrations": list(applied),
        "recordedMigrations": [str(row["migration_name"]) for row in recorded],
        "privileges": expected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provision-only", action="store_true")
    args = parser.parse_args()
    evidence = asyncio.run(provision())
    if not args.provision_only:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "apps.api.tests.postgres.test_m2_01_retrieval_runtime",
                "-v",
            ],
            cwd=ROOT,
            check=False,
        )
        evidence["runtimeTestExitCode"] = completed.returncode
        if completed.returncode != 0:
            raise RuntimeError("M2-01 PostgreSQL runtime tests failed")
    print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
