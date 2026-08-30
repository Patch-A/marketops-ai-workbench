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


async def migrate_and_grant_m2(asyncpg, migrator_dsn: str) -> tuple[str, ...]:
    """Apply the current M2 migration chain and grant the app role."""
    connection = await asyncpg.connect(migrator_dsn)
    try:
        app_has_schema_usage = await connection.fetchval(
            "SELECT has_schema_privilege($1, 'marketops', 'USAGE')",
            APPLICATION_ROLE,
        ) if await connection.fetchval(
            "SELECT to_regnamespace('marketops') IS NOT NULL"
        ) else False
        applied = await run_migrations(
            connection,
            MIGRATIONS,
            allowed_schema_usage_roles=(APPLICATION_ROLE,) if app_has_schema_usage else (),
        )
        await connection.execute(
            f"""
            GRANT USAGE ON SCHEMA marketops TO {APPLICATION_ROLE};
            GRANT SELECT ON
                marketops.organizations, marketops.workspaces, marketops.clients,
                marketops.projects, marketops.artifacts, marketops.artifact_versions,
                marketops.audit_events,
                marketops.extraction_runs, marketops.extraction_candidates,
                marketops.review_snapshots, marketops.review_snapshot_items,
                marketops.review_decisions, marketops.extraction_run_requests,
                marketops.wbs_plans, marketops.wbs_plan_versions, marketops.wbs_tasks,
                marketops.wbs_plan_approvals, marketops.schedule_snapshots,
                marketops.wbs_task_execution_updates, marketops.source_indexes,
                marketops.source_chunks, marketops.project_capsules,
                marketops.project_outcomes, marketops.project_retrospectives,
                marketops.knowledge_items, marketops.knowledge_item_versions,
                marketops.knowledge_item_evidence,
                marketops.knowledge_promotion_roots,
                marketops.knowledge_promotion_versions,
                marketops.knowledge_citations
            TO {APPLICATION_ROLE};
            GRANT INSERT ON
                marketops.projects, marketops.artifacts, marketops.artifact_versions,
                marketops.audit_events, marketops.extraction_runs,
                marketops.extraction_candidates, marketops.review_snapshots,
                marketops.review_snapshot_items, marketops.review_decisions,
                marketops.extraction_run_requests, marketops.wbs_plans,
                marketops.wbs_plan_versions, marketops.wbs_tasks,
                marketops.schedule_snapshots, marketops.wbs_plan_approvals,
                marketops.wbs_task_execution_updates, marketops.source_indexes,
                marketops.source_chunks, marketops.project_capsules,
                marketops.project_outcomes, marketops.project_retrospectives,
                marketops.knowledge_items, marketops.knowledge_item_versions,
                marketops.knowledge_item_evidence,
                marketops.knowledge_promotion_roots,
                marketops.knowledge_promotion_versions,
                marketops.knowledge_citations
            TO {APPLICATION_ROLE};
            GRANT DELETE ON marketops.source_chunks TO {APPLICATION_ROLE};
            GRANT UPDATE (status, withdrawn_by, withdrawn_at)
                ON marketops.source_indexes TO {APPLICATION_ROLE};
            GRANT UPDATE (created_by) ON
                marketops.projects, marketops.extraction_runs, marketops.wbs_plans
            TO {APPLICATION_ROLE};
            GRANT EXECUTE ON FUNCTION
                marketops.current_workspace_id(), marketops.current_client_id(),
                marketops.current_project_id(), marketops.current_actor_id()
            TO {APPLICATION_ROLE};
            """
        )
        replay = await run_migrations(
            connection, MIGRATIONS, allowed_schema_usage_roles=(APPLICATION_ROLE,)
        )
        if replay:
            raise RuntimeError(f"migration replay unexpectedly applied {replay!r}")
        return tuple(applied)
    finally:
        await connection.close()


async def provision() -> dict[str, object]:
    try:
        import asyncpg
    except ImportError as error:
        raise RuntimeError("asyncpg is required for the M2-02 PostgreSQL gate") from error

    version, version_num, log_safety = await provision_roles_and_database(
        asyncpg, required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    )
    connection = await asyncpg.connect(
        required_environment("MARKETOPS_TEST_MIGRATOR_DATABASE_URL")
    )
    try:
        app_has_schema_usage = await connection.fetchval(
            "SELECT has_schema_privilege($1, 'marketops', 'USAGE')",
            APPLICATION_ROLE,
        ) if await connection.fetchval(
            "SELECT to_regnamespace('marketops') IS NOT NULL"
        ) else False
        applied = await run_migrations(
            connection,
            MIGRATIONS,
            allowed_schema_usage_roles=(APPLICATION_ROLE,) if app_has_schema_usage else (),
        )
        await connection.execute(
            f"""
            GRANT USAGE ON SCHEMA marketops TO {APPLICATION_ROLE};
            GRANT SELECT ON
                marketops.organizations,
                marketops.workspaces,
                marketops.clients,
                marketops.projects,
                marketops.audit_events,
                marketops.artifacts,
                marketops.artifact_versions,
                marketops.extraction_runs,
                marketops.extraction_candidates,
                marketops.review_snapshots,
                marketops.review_snapshot_items,
                marketops.review_decisions,
                marketops.extraction_run_requests,
                marketops.wbs_plans,
                marketops.wbs_plan_versions,
                marketops.wbs_tasks,
                marketops.wbs_plan_approvals,
                marketops.schedule_snapshots,
                marketops.wbs_task_execution_updates,
                marketops.source_indexes,
                marketops.source_chunks,
                marketops.project_capsules,
                marketops.project_outcomes,
                marketops.project_retrospectives,
                marketops.knowledge_items,
                marketops.knowledge_item_versions,
                marketops.knowledge_item_evidence,
                marketops.knowledge_promotion_roots,
                marketops.knowledge_promotion_versions,
                marketops.knowledge_citations
            TO {APPLICATION_ROLE};
            GRANT INSERT ON
                marketops.projects,
                marketops.audit_events,
                marketops.artifacts,
                marketops.artifact_versions,
                marketops.audit_events,
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
                marketops.wbs_task_execution_updates,
                marketops.source_indexes,
                marketops.source_chunks,
                marketops.project_capsules,
                marketops.project_outcomes,
                marketops.project_retrospectives,
                marketops.knowledge_items,
                marketops.knowledge_item_versions,
                marketops.knowledge_item_evidence,
                marketops.knowledge_promotion_roots,
                marketops.knowledge_promotion_versions,
                marketops.knowledge_citations
            TO {APPLICATION_ROLE};
            GRANT UPDATE (created_by) ON
                marketops.projects,
                marketops.extraction_runs,
                marketops.wbs_plans
            TO {APPLICATION_ROLE};
            GRANT DELETE ON marketops.source_chunks TO {APPLICATION_ROLE};
            GRANT UPDATE (status, withdrawn_by, withdrawn_at)
                ON marketops.source_indexes TO {APPLICATION_ROLE};
            GRANT EXECUTE ON FUNCTION
                marketops.current_workspace_id(),
                marketops.current_client_id(),
                marketops.current_project_id(),
                marketops.current_actor_id()
            TO {APPLICATION_ROLE};
            """
        )
        recorded = await connection.fetch(
            "SELECT migration_name FROM marketops.schema_migrations ORDER BY migration_name"
        )
    finally:
        await connection.close()

    app = await asyncpg.connect(required_environment("MARKETOPS_TEST_DATABASE_URL"))
    try:
        privileges = await app.fetchrow(
            """
            SELECT
                has_table_privilege(current_user, 'marketops.audit_events', 'SELECT') AS audit_select,
                has_table_privilege(current_user, 'marketops.project_capsules', 'SELECT') AS capsule_select,
                has_table_privilege(current_user, 'marketops.project_capsules', 'INSERT') AS capsule_insert,
                has_table_privilege(current_user, 'marketops.project_capsules', 'UPDATE') AS capsule_update,
                has_table_privilege(current_user, 'marketops.project_outcomes', 'INSERT') AS outcome_insert,
                has_table_privilege(current_user, 'marketops.project_retrospectives', 'INSERT') AS retrospective_insert,
                has_table_privilege(current_user, 'marketops.knowledge_items', 'INSERT') AS knowledge_insert,
                has_table_privilege(current_user, 'marketops.knowledge_item_versions', 'INSERT') AS knowledge_version_insert,
                has_table_privilege(current_user, 'marketops.knowledge_item_evidence', 'INSERT') AS evidence_insert,
                has_table_privilege(current_user, 'marketops.knowledge_items', 'DELETE') AS knowledge_delete,
                has_table_privilege(current_user, 'marketops.knowledge_promotion_roots', 'SELECT') AS promotion_root_select,
                has_table_privilege(current_user, 'marketops.knowledge_promotion_roots', 'INSERT') AS promotion_root_insert,
                has_table_privilege(current_user, 'marketops.knowledge_promotion_versions', 'SELECT') AS promotion_version_select,
                has_table_privilege(current_user, 'marketops.knowledge_promotion_versions', 'INSERT') AS promotion_version_insert,
                has_table_privilege(current_user, 'marketops.knowledge_citations', 'SELECT') AS citation_select,
                has_table_privilege(current_user, 'marketops.knowledge_citations', 'INSERT') AS citation_insert,
                has_table_privilege(current_user, 'marketops.knowledge_citations', 'UPDATE') AS citation_update,
                has_table_privilege(current_user, 'marketops.knowledge_citations', 'DELETE') AS citation_delete
            """
        )
    finally:
        await app.close()
    expected = {
        "audit_select": True,
        "capsule_select": True,
        "capsule_insert": True,
        "capsule_update": False,
        "outcome_insert": True,
        "retrospective_insert": True,
        "knowledge_insert": True,
        "knowledge_version_insert": True,
        "evidence_insert": True,
        "knowledge_delete": False,
        "promotion_root_select": True,
        "promotion_root_insert": True,
        "promotion_version_select": True,
        "promotion_version_insert": True,
        "citation_select": True,
        "citation_insert": True,
        "citation_update": False,
        "citation_delete": False,
    }
    if dict(privileges) != expected:
        raise RuntimeError("M2 application privilege matrix drifted")
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
                "apps.api.tests.postgres.test_m2_02_learning_runtime",
                "-v",
            ],
            cwd=ROOT,
            check=False,
        )
        evidence["runtimeTestExitCode"] = completed.returncode
        if completed.returncode != 0:
            raise RuntimeError("M2-02 PostgreSQL runtime tests failed")
    print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
