#!/usr/bin/env python3
"""Static and mutation checks for the M1-01 PostgreSQL import contract.

This checker intentionally validates a narrowly frozen migration. It does not
replace executing the migration and adversarial tests against PostgreSQL 18.4.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "apps" / "api" / "migrations" / "0001_project_import.sql"


@dataclass(frozen=True)
class Guard:
    name: str
    test: Callable[[str], bool]
    mutation_pattern: str


def normalized(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.lower()).strip()


def contains(pattern: str) -> Callable[[str], bool]:
    regex = re.compile(pattern, re.IGNORECASE | re.DOTALL)
    return lambda sql: regex.search(sql) is not None


TABLES = (
    "organizations",
    "workspaces",
    "clients",
    "projects",
    "artifacts",
    "artifact_versions",
    "audit_events",
)

TENANT_TABLES = (
    "organizations",
    "workspaces",
    "clients",
    "projects",
    "artifacts",
    "artifact_versions",
    "audit_events",
)


def has_all_tables(sql: str) -> bool:
    return all(
        re.search(rf"create\s+table\s+marketops\.{table}\b", sql, re.I)
        for table in TABLES
    )


def has_uuid_and_timestamptz(sql: str) -> bool:
    for table in TABLES:
        match = re.search(
            rf"create\s+table\s+marketops\.{table}\s*\((.*?)\);", sql, re.I | re.S
        )
        if not match:
            return False
        body = match.group(1)
        if not re.search(r"\bid\s+uuid\s+(?:primary\s+key\s+)?not\s+null|\bid\s+uuid\s+primary\s+key", body, re.I):
            return False
        if not re.search(r"\bcreated_at\s+timestamptz\s+not\s+null", body, re.I):
            return False
    return True


def has_direct_scope_columns(sql: str) -> bool:
    required = {
        "workspaces": ("organization_id",),
        "clients": ("organization_id", "workspace_id"),
        "projects": ("organization_id", "workspace_id", "client_id"),
        "artifacts": ("organization_id", "workspace_id", "client_id", "project_id"),
        "artifact_versions": (
            "organization_id",
            "workspace_id",
            "client_id",
            "project_id",
            "artifact_id",
        ),
        "audit_events": (
            "organization_id",
            "workspace_id",
            "client_id",
            "project_id",
            "actor_id",
        ),
    }
    for table, columns in required.items():
        match = re.search(
            rf"create\s+table\s+marketops\.{table}\s*\((.*?)\);", sql, re.I | re.S
        )
        if not match or not all(re.search(rf"\b{column}\b", match.group(1), re.I) for column in columns):
            return False
    return True


def has_scoped_foreign_keys(sql: str) -> bool:
    compact = normalized(sql)
    required = (
        "foreign key (organization_id, workspace_id) references marketops.workspaces (organization_id, id)",
        "foreign key (organization_id, workspace_id, client_id) references marketops.clients (organization_id, workspace_id, id)",
        "foreign key (organization_id, workspace_id, client_id, project_id) references marketops.projects (organization_id, workspace_id, client_id, id)",
        "foreign key (organization_id, workspace_id, client_id, project_id, artifact_id) references marketops.artifacts (organization_id, workspace_id, client_id, project_id, id)",
    )
    return all(fragment in compact for fragment in required)


def has_sha256_checks(sql: str) -> bool:
    required = (
        r"constraint\s+artifact_versions_sha256_32\s+check\s*\(\s*octet_length\s*\(\s*sha256\s*\)\s*=\s*32\s*\)",
        r"constraint\s+projects_manifest_sha256_32\s+check\s*\(\s*octet_length\s*\(\s*import_manifest_sha256\s*\)\s*=\s*32\s*\)",
    )
    return all(re.search(pattern, sql, re.I | re.S) for pattern in required)


def has_explicit_approval_metadata(sql: str) -> bool:
    version = re.search(
        r"create\s+table\s+marketops\.artifact_versions\s*\((.*?)\);",
        sql,
        re.I | re.S,
    )
    if not version:
        return False
    body = normalized(version.group(1))
    required = (
        "approval_status text not null",
        "approved_by uuid",
        "approved_at timestamptz",
        "constraint artifact_versions_approval_consistent check",
        "approval_status = 'approved' and proposal_version is not null and approved_by is not null and approved_at is not null",
    )
    return all(fragment in body for fragment in required)


def has_planning_project_status(sql: str) -> bool:
    projects = re.search(
        r"create\s+table\s+marketops\.projects\s*\((.*?)\);", sql, re.I | re.S
    )
    if not projects:
        return False
    body = normalized(projects.group(1))
    return (
        "status text not null default 'planning'" in body
        and "status in ('planning', 'active', 'archived')" in body
    )


def has_immutable_triggers(sql: str, trigger_name: str, table: str) -> bool:
    function_raises = re.search(
        r"create\s+function\s+marketops\.reject_immutable_row_change\s*\(\s*\).*?raise\s+exception",
        sql,
        re.I | re.S,
    )
    trigger_calls_function = re.search(
        rf"create\s+trigger\s+{trigger_name}\s+before\s+update\s+or\s+delete\s+on\s+marketops\.{table}\s+for\s+each\s+row\s+execute\s+function\s+marketops\.reject_immutable_row_change\s*\(\s*\)",
        sql,
        re.I | re.S,
    )
    return function_raises is not None and trigger_calls_function is not None


def has_deferred_approved_proposal_fk(sql: str) -> bool:
    expected = """constraint projects_approved_proposal_version_fk foreign key (
        organization_id, workspace_id, client_id, id,
        approved_proposal_artifact_id, approved_proposal_version_id
    ) references marketops.artifact_versions (
        organization_id, workspace_id, client_id, project_id, artifact_id, id
    ) on delete restrict deferrable initially deferred;"""
    return normalized(expected) in normalized(sql)


def has_final_proposal_validation(sql: str) -> bool:
    match = re.search(
        r"create\s+function\s+marketops\.check_final_approved_proposal\s*\(\s*\)(.*?)\$\$\s*;",
        sql,
        re.I | re.S,
    )
    if not match:
        return False
    body = normalized(match.group(1))
    required = (
        "from marketops.projects as project",
        "where project.id = new.id",
        "join marketops.artifact_versions as version",
        "join marketops.artifacts as artifact",
        "artifact.kind = 'proposal'",
        "version.proposal_version = project.approved_proposal_number",
        "version.approval_status = 'approved'",
        "version.approved_by = project.created_by",
        "version.approved_at is not null",
    )
    return all(fragment in body for fragment in required)


def has_retained_version_boundary(sql: str) -> bool:
    return has_deferred_approved_proposal_fk(sql) and not re.search(
        r"references\s+marketops\.(?:artifacts|artifact_versions)\b.*?on\s+delete\s+cascade",
        sql,
        re.I | re.S,
    )


def has_rls_for_all_tables(sql: str) -> bool:
    for table in TENANT_TABLES:
        if not re.search(
            rf"alter\s+table\s+marketops\.{table}\s+enable\s+row\s+level\s+security", sql, re.I
        ):
            return False
        if not re.search(
            rf"alter\s+table\s+marketops\.{table}\s+force\s+row\s+level\s+security", sql, re.I
        ):
            return False
        if not re.search(rf"create\s+policy\s+\w+\s+on\s+marketops\.{table}\b", sql, re.I):
            return False
    return True


def has_scoped_rls_predicates(sql: str) -> bool:
    compact = normalized(sql)
    required = (
        "create policy workspaces_scope on marketops.workspaces using (id = marketops.current_workspace_id()) with check (id = marketops.current_workspace_id())",
        "create policy clients_scope on marketops.clients using ( workspace_id = marketops.current_workspace_id() and id = marketops.current_client_id() ) with check ( workspace_id = marketops.current_workspace_id() and id = marketops.current_client_id() )",
        "create policy projects_scope_select on marketops.projects for select using ( workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() )",
        "create policy projects_scope_insert on marketops.projects for insert with check ( workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() and id = marketops.current_project_id() and created_by = marketops.current_actor_id() )",
    )
    return all(fragment in compact for fragment in required)


def has_project_write_policies(sql: str) -> bool:
    compact = normalized(sql)
    required = (
        "create policy projects_scope_update on marketops.projects for update using ( workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() and id = marketops.current_project_id() ) with check ( workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() and id = marketops.current_project_id() )",
        "create policy projects_scope_delete on marketops.projects for delete using ( workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() and id = marketops.current_project_id() )",
    )
    return all(fragment in compact for fragment in required)


def has_artifact_policy(sql: str, table: str, actor_column: str) -> bool:
    compact = normalized(sql)
    expected = (
        f"create policy {table}_scope on marketops.{table} using ( "
        "workspace_id = marketops.current_workspace_id() "
        "and client_id = marketops.current_client_id() "
        "and project_id = marketops.current_project_id() ) with check ( "
        "workspace_id = marketops.current_workspace_id() "
        "and client_id = marketops.current_client_id() "
        "and project_id = marketops.current_project_id() "
        f"and {actor_column} = marketops.current_actor_id() )"
    )
    return expected in compact


def has_fail_closed_scope_helpers(sql: str) -> bool:
    for scope in ("workspace_id", "client_id", "project_id", "actor_id"):
        if not re.search(
            rf"create\s+(?:or\s+replace\s+)?function\s+marketops\.current_{scope}\s*\(\s*\).*?return\s+nullif\s*\(\s*current_setting\s*\(\s*'app\.{scope}'\s*,\s*true\s*\)\s*,\s*''\s*\)\s*::\s*uuid",
            sql,
            re.I | re.S,
        ):
            return False
    return True


GUARDS = (
    Guard("all core tables", has_all_tables, r"create\s+table\s+marketops\.organizations\b"),
    Guard("UUID and timestamptz types", has_uuid_and_timestamptz, r"\bcreated_at\s+timestamptz\s+not\s+null"),
    Guard("direct scope columns", has_direct_scope_columns, r"\bactor_id\s+uuid\s+not\s+null"),
    Guard("scoped composite foreign keys", has_scoped_foreign_keys, r"foreign\s+key\s*\(\s*organization_id\s*,\s*workspace_id\s*,\s*client_id\s*,\s*project_id\s*,\s*artifact_id\s*\)"),
    Guard("32-byte SHA-256", has_sha256_checks, r"constraint\s+artifact_versions_sha256_32\s+check\s*\(\s*octet_length\s*\(\s*sha256\s*\)\s*=\s*32\s*\)"),
    Guard("explicit approval metadata", has_explicit_approval_metadata, r"constraint\s+artifact_versions_approval_consistent\s+check"),
    Guard("planning project status", has_planning_project_status, r"default\s+'planning'"),
    Guard("idempotency manifest uniqueness", contains(r"import_manifest_sha256\s+bytea\s+not\s+null.*?unique\s*\(\s*workspace_id\s*,\s*client_id\s*,\s*import_request_id\s*\)"), r"unique\s*\(\s*workspace_id\s*,\s*client_id\s*,\s*import_request_id\s*\)"),
    Guard("immutable artifact identity trigger", lambda sql: has_immutable_triggers(sql, "artifacts_identity_immutable", "artifacts"), r"create\s+trigger\s+artifacts_identity_immutable"),
    Guard("immutable artifact versions trigger", lambda sql: has_immutable_triggers(sql, "artifact_versions_immutable", "artifact_versions"), r"create\s+trigger\s+artifact_versions_immutable"),
    Guard("append-only audit trigger", lambda sql: has_immutable_triggers(sql, "audit_events_append_only", "audit_events"), r"create\s+trigger\s+audit_events_append_only"),
    Guard("deferred approved proposal foreign key", has_deferred_approved_proposal_fk, r"deferrable\s+initially\s+deferred(?=\s*;)"),
    Guard("deferred approved proposal constraint trigger", contains(r"create\s+constraint\s+trigger\s+projects_approved_proposal_check.*?deferrable\s+initially\s+deferred.*?for\s+each\s+row"), r"create\s+constraint\s+trigger\s+projects_approved_proposal_check"),
    Guard("approved proposal final-row validation", has_final_proposal_validation, r"artifact\.kind\s*=\s*'proposal'"),
    Guard("RLS enable, force, and policies", has_rls_for_all_tables, r"alter\s+table\s+marketops\.artifact_versions\s+force\s+row\s+level\s+security"),
    Guard("scoped RLS predicates", has_scoped_rls_predicates, r"create\s+policy\s+projects_scope_select\s+on\s+marketops\.projects.*?;"),
    Guard("project update and delete RLS predicates", has_project_write_policies, r"create\s+policy\s+projects_scope_update\s+on\s+marketops\.projects.*?;"),
    Guard("artifact write RLS predicate", lambda sql: has_artifact_policy(sql, "artifacts", "created_by"), r"create\s+policy\s+artifacts_scope\s+on\s+marketops\.artifacts.*?;"),
    Guard("artifact version write RLS predicate", lambda sql: has_artifact_policy(sql, "artifact_versions", "created_by"), r"create\s+policy\s+artifact_versions_scope\s+on\s+marketops\.artifact_versions.*?;"),
    Guard("audit write RLS predicate", lambda sql: has_artifact_policy(sql, "audit_events", "actor_id"), r"create\s+policy\s+audit_events_scope\s+on\s+marketops\.audit_events.*?;"),
    Guard("fail-closed transaction scope helpers", has_fail_closed_scope_helpers, r"current_setting\s*\(\s*'app\.actor_id'\s*,\s*true\s*\)"),
    Guard("PUBLIC privileges revoked", contains(r"revoke\s+all\s+on\s+schema\s+marketops\s+from\s+public.*?revoke\s+all\s+on\s+all\s+tables\s+in\s+schema\s+marketops\s+from\s+public.*?revoke\s+(?:all|execute)\s+on\s+all\s+functions\s+in\s+schema\s+marketops\s+from\s+public"), r"revoke\s+all\s+on\s+all\s+tables\s+in\s+schema\s+marketops\s+from\s+public"),
    Guard("retained versions use RESTRICT without CASCADE", has_retained_version_boundary, r"on\s+delete\s+restrict(?=\s+deferrable\s+initially\s+deferred)"),
)


def validate(sql: str) -> list[str]:
    return [guard.name for guard in GUARDS if not guard.test(sql)]


def mutation_failures(sql: str) -> list[str]:
    failures: list[str] = []
    for guard in GUARDS:
        mutated, replacements = re.subn(
            guard.mutation_pattern,
            "/* mutation removed required guard */",
            sql,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if replacements != 1:
            failures.append(f"{guard.name}: mutation pattern did not match")
            continue
        if guard.name not in validate(mutated):
            failures.append(f"{guard.name}: checker accepted weakened migration")
    return failures


def main() -> int:
    if not MIGRATION.is_file():
        print(f"FAIL: migration not found: {MIGRATION.relative_to(ROOT)}", file=sys.stderr)
        return 1

    sql = MIGRATION.read_text(encoding="utf-8")
    failures = validate(sql)
    failures.extend(mutation_failures(sql))
    if failures:
        print("FAIL: PostgreSQL contract checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"PASS: M1-01 PostgreSQL contract ({len(GUARDS)} guards and mutations)")
    print("LIMITATION: static checks only; PostgreSQL 18.4 integration remains required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
