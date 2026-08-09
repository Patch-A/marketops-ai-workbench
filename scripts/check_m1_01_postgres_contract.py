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
RUNNER = ROOT / "apps" / "api" / "marketops_import" / "migrations.py"
sys.path.insert(0, str(ROOT / "apps" / "api"))

from marketops_import.migrations import (  # noqa: E402
    InvalidMigrationSetError,
    discover_migrations,
    validate_no_transaction_control,
)


@dataclass(frozen=True)
class Guard:
    name: str
    test: Callable[[str], bool]
    mutation_pattern: str
    mutation_replacement: str = "/* mutation removed required guard */"


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


def has_no_migration_transaction_control(sql: str) -> bool:
    try:
        validate_no_transaction_control(sql, MIGRATION.name)
    except InvalidMigrationSetError:
        return False
    return True


def has_exact_registry_acl_predicates(source: str) -> bool:
    compact = normalized(source)
    required = (
        "where acl.grantee <> 0 "
        "and acl.grantee <> namespace.nspowner "
        ") as non_owner_schema_privileges",
        "where privileged_attribute.attrelid = registry.oid "
        "and privileged_attribute.attnum > 0 "
        "and not privileged_attribute.attisdropped "
        "and acl.grantee <> registry.relowner "
        ") as non_owner_column_privilege_count",
    )
    return all(fragment in compact for fragment in required)


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
        "create policy workspaces_scope on marketops.workspaces using ( marketops.current_actor_id() is not null and id = marketops.current_workspace_id() ) with check ( marketops.current_actor_id() is not null and id = marketops.current_workspace_id() )",
        "create policy clients_scope on marketops.clients using ( marketops.current_actor_id() is not null and workspace_id = marketops.current_workspace_id() and id = marketops.current_client_id() ) with check ( marketops.current_actor_id() is not null and workspace_id = marketops.current_workspace_id() and id = marketops.current_client_id() )",
        "create policy projects_scope_select on marketops.projects for select using ( marketops.current_actor_id() is not null and workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() )",
        "create policy projects_scope_insert on marketops.projects for insert with check ( marketops.current_actor_id() is not null and workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() and id = marketops.current_project_id() and created_by = marketops.current_actor_id() )",
    )
    return all(fragment in compact for fragment in required)


def has_project_write_policies(sql: str) -> bool:
    compact = normalized(sql)
    required = (
        "create policy projects_scope_update on marketops.projects for update using ( marketops.current_actor_id() is not null and workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() and id = marketops.current_project_id() ) with check ( marketops.current_actor_id() is not null and workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() and id = marketops.current_project_id() )",
        "create policy projects_scope_delete on marketops.projects for delete using ( marketops.current_actor_id() is not null and workspace_id = marketops.current_workspace_id() and client_id = marketops.current_client_id() and id = marketops.current_project_id() )",
    )
    return all(fragment in compact for fragment in required)


def has_artifact_policy(sql: str, table: str, actor_column: str) -> bool:
    compact = normalized(sql)
    expected = (
        f"create policy {table}_scope on marketops.{table} using ( "
        "marketops.current_actor_id() is not null "
        "and workspace_id = marketops.current_workspace_id() "
        "and client_id = marketops.current_client_id() "
        "and project_id = marketops.current_project_id() ) with check ( "
        "marketops.current_actor_id() is not null "
        "and workspace_id = marketops.current_workspace_id() "
        "and client_id = marketops.current_client_id() "
        "and project_id = marketops.current_project_id() "
        f"and {actor_column} = marketops.current_actor_id() )"
    )
    return expected in compact


def all_policies_require_actor(sql: str) -> bool:
    policies = re.findall(
        r"create\s+policy\s+\w+\s+on\s+marketops\.\w+.*?;",
        sql,
        re.I | re.S,
    )
    if len(policies) != 10:
        return False
    for policy in policies:
        compact = normalized(policy)
        clause_count = len(re.findall(r"\busing\s*\(|\bwith\s+check\s*\(", compact))
        actor_count = compact.count("marketops.current_actor_id() is not null")
        if clause_count == 0 or actor_count != clause_count:
            return False
    return True


def preserves_project_creator(sql: str) -> bool:
    function = re.search(
        r"create\s+function\s+marketops\.reject_project_creator_change\s*\(\s*\)"
        r".*?new\.created_by\s+is\s+distinct\s+from\s+old\.created_by"
        r".*?raise\s+exception.*?\$\$\s*;",
        sql,
        re.I | re.S,
    )
    trigger = re.search(
        r"create\s+trigger\s+projects_created_by_immutable\s+"
        r"before\s+update\s+of\s+created_by\s+on\s+marketops\.projects\s+"
        r"for\s+each\s+row\s+execute\s+function\s+"
        r"marketops\.reject_project_creator_change\s*\(\s*\)",
        sql,
        re.I | re.S,
    )
    return function is not None and trigger is not None


def has_intended_read_boundaries(sql: str) -> bool:
    compact = normalized(sql)
    project_policy = re.search(
        r"create policy projects_scope_select on marketops\.projects.*?;",
        compact,
        re.I | re.S,
    )
    if project_policy is None:
        return False
    project_text = project_policy.group(0)
    if "current_client_id()" not in project_text or "current_project_id()" in project_text:
        return False
    return all(
        re.search(
            rf"create policy {table}_scope on marketops\.{table} "
            rf"using \( .*?project_id = marketops\.current_project_id\(\).*?\)",
            compact,
            re.I | re.S,
        )
        for table in ("artifacts", "artifact_versions", "audit_events")
    )


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
    Guard(
        "runner-only transaction ownership",
        has_no_migration_transaction_control,
        r"\A(?=CREATE\s+SCHEMA)",
        "BEGIN;\n",
    ),
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
    Guard("actor required by every application policy", all_policies_require_actor, r"marketops\.current_actor_id\(\)\s+is\s+not\s+null"),
    Guard("project creator is immutable", preserves_project_creator, r"create\s+trigger\s+projects_created_by_immutable"),
    Guard("client and project read boundaries", has_intended_read_boundaries, r"client_id\s*=\s*marketops\.current_client_id\(\)(?=\s*\))"),
    Guard("artifact write RLS predicate", lambda sql: has_artifact_policy(sql, "artifacts", "created_by"), r"create\s+policy\s+artifacts_scope\s+on\s+marketops\.artifacts.*?;"),
    Guard("artifact version write RLS predicate", lambda sql: has_artifact_policy(sql, "artifact_versions", "created_by"), r"create\s+policy\s+artifact_versions_scope\s+on\s+marketops\.artifact_versions.*?;"),
    Guard("audit write RLS predicate", lambda sql: has_artifact_policy(sql, "audit_events", "actor_id"), r"create\s+policy\s+audit_events_scope\s+on\s+marketops\.audit_events.*?;"),
    Guard("fail-closed transaction scope helpers", has_fail_closed_scope_helpers, r"current_setting\s*\(\s*'app\.actor_id'\s*,\s*true\s*\)"),
    Guard("PUBLIC privileges revoked", contains(r"revoke\s+all\s+on\s+schema\s+marketops\s+from\s+public.*?revoke\s+all\s+on\s+all\s+tables\s+in\s+schema\s+marketops\s+from\s+public.*?revoke\s+(?:all|execute)\s+on\s+all\s+functions\s+in\s+schema\s+marketops\s+from\s+public"), r"revoke\s+all\s+on\s+all\s+tables\s+in\s+schema\s+marketops\s+from\s+public"),
    Guard("retained versions use RESTRICT without CASCADE", has_retained_version_boundary, r"on\s+delete\s+restrict(?=\s+deferrable\s+initially\s+deferred)"),
)


RUNNER_GUARDS = (
    Guard(
        "runner uses one outer transaction",
        contains(r"async\s+with\s+connection\.transaction\s*\(\s*\)"),
        r"async\s+with\s+connection\.transaction\s*\(\s*\)",
    ),
    Guard(
        "transaction-scoped advisory lock",
        contains(r"select\s+pg_advisory_xact_lock\s*\(\s*\$1\s*\)"),
        r"pg_advisory_xact_lock",
    ),
    Guard(
        "raw-byte SHA-256",
        lambda source: "path.read_bytes()" in source and "hashlib.sha256(raw_sql)" in source,
        r"path\.read_bytes\(\)",
    ),
    Guard(
        "ordered contiguous migration files",
        lambda source: "sorted(directory.glob(\"*.sql\")" in source
        and "expected_number" in source,
        r"sorted\(directory\.glob\(\"\*\.sql\"\)",
    ),
    Guard(
        "transaction-control preflight before database transaction",
        lambda source: (
            source.find("validate_no_transaction_control(sql_text, path.name)")
            < source.find("async with connection.transaction():")
            and source.find("validate_no_transaction_control(sql_text, path.name)")
            >= 0
        ),
        r"validate_no_transaction_control\(sql_text,\s*path\.name\)",
    ),
    Guard(
        "PostgreSQL-aware lexical regions",
        lambda source: all(
            marker in source
            for marker in (
                'if character == "-" and following == "-":',
                'if character == "/" and following == "*":',
                "depth += 1",
                "if character in (\"'\", '\"'):",
                "delimiter_match = re.match",
                "body_end = sql.find(delimiter, body_start)",
                "atomic_depth",
                "create_routine_statement",
                'word == "atomic"',
            )
        ),
        r"delimiter_match\s*=\s*re\.match",
    ),
    Guard(
        "immutable registry triggers",
        lambda source: all(
            marker in normalized(source)
            for marker in (
                "before update or delete on marketops.schema_migrations",
                "before truncate on marketops.schema_migrations",
                "revoke all on table marketops.schema_migrations from public",
            )
        ),
        r"before\s+update\s+or\s+delete\s+on\s+marketops\.schema_migrations",
    ),
    Guard(
        "registry pg_catalog attestation",
        lambda source: all(
            marker in normalized(source)
            for marker in (
                "from pg_catalog.pg_namespace as namespace",
                "join pg_catalog.pg_class as registry",
                "from pg_catalog.pg_attribute as attribute",
                "from pg_catalog.pg_constraint as constraint_record",
                "from pg_catalog.pg_trigger as trigger_record",
                "from pg_catalog.aclexplode(",
                "registry.relkind::text as relkind",
                "registry.relpersistence::text as relpersistence",
                "registry.relhasrules as has_rules",
                "registry.relhassubclass as has_subclass",
                "registry.relrowsecurity as rls_enabled",
                "registry.relforcerowsecurity as force_rls_enabled",
                "from pg_catalog.pg_inherits as inheritance_record",
                "trigger_record.tgqual is null",
                "trigger_record.tgattr = ''::pg_catalog.int2vector",
            )
        ),
        r"from\s+pg_catalog\.pg_attribute\s+as\s+attribute",
    ),
    Guard(
        "registry row trigger has no UPDATE OF restriction",
        contains(r"trigger_record\.tgattr\s*=\s*''::pg_catalog\.int2vector"),
        r"trigger_record\.tgattr\s*=\s*''::pg_catalog\.int2vector",
    ),
    Guard(
        "registry schema and column ACL attestation",
        lambda source: all(
            marker in normalized(source)
            for marker in (
                "as non_owner_schema_privileges",
                "privileged_attribute.attacl",
                "cross join lateral pg_catalog.aclexplode(",
                "as non_owner_column_privilege_count",
            )
        ),
        r"privileged_attribute\.attacl",
    ),
    Guard(
        "registry ACL predicates do not hide dangerous privileges",
        has_exact_registry_acl_predicates,
        r"acl\.grantee\s*<>\s*namespace\.nspowner",
    ),
    Guard(
        "exact registry ownership shape triggers and privileges",
        lambda source: all(
            marker in source
            for marker in (
                '"schema_owner": current_user',
                '"table_owner": current_user',
                '"function_owner": current_user',
                '"relkind": "r"',
                '"relpersistence": "p"',
                '"has_rules": False',
                '"has_subclass": False',
                '"rls_enabled": False',
                '"force_rls_enabled": False',
                '"inheritance_edge_count": 0',
                '"trigger_count": 2',
                '"row_trigger_count": 1',
                '"truncate_trigger_count": 1',
                '"public_schema_privilege_count": 0',
                '"non_owner_schema_privileges"',
                '"public_table_privilege_count": 0',
                '"non_owner_table_privilege_count": 0',
                '"non_owner_column_privilege_count": 0',
                '"public_function_privilege_count": 0',
                '"non_owner_function_privilege_count": 0',
                "EXPECTED_REGISTRY_COLUMNS",
                "EXPECTED_REGISTRY_CONSTRAINTS",
                "DEFAULT_ALLOWED_SCHEMA_USAGE_ROLES",
                'f"{role}:USAGE:not-grantable"',
                'role == "public"',
                "role == current_user",
                "len(set(roles)) != len(roles)",
            )
        ),
        r'"schema_owner"\s*:\s*current_user',
    ),
    Guard(
        "registry attested before history read",
        lambda source: (
            0
            <= source.find("attestation = await connection.fetch(REGISTRY_ATTESTATION_SQL)")
            < source.find("rows = await connection.fetch(READ_REGISTRY_SQL)")
            and 0
            <= source.find("validate_registry_contract(\n            attestation,")
            < source.find("rows = await connection.fetch(READ_REGISTRY_SQL)")
            and "allowed_schema_usage_roles=" in source
        ),
        r"validate_registry_contract\(\s*attestation,",
    ),
    Guard(
        "exact-prefix history validation",
        lambda source: "applied_name != expected.name" in source
        and "len(rows) > len(migrations)" in source,
        r"applied_name\s*!=\s*expected\.name",
    ),
    Guard(
        "intact migration execution",
        lambda source: "await connection.execute(migration.sql_text())" in source
        and not re.search(r"\.split\s*\(\s*[\"']\s*;", source),
        r"await\s+connection\.execute\(migration\.sql_text\(\)\)",
    ),
    Guard(
        "migration and registry record share transaction",
        contains(
            r"for\s+migration\s+in\s+migrations\[applied_count:\]\s*:"
            r".*?await\s+connection\.execute\(migration\.sql_text\(\)\)"
            r".*?await\s+connection\.execute\(\s*insert_registry_sql"
        ),
        r"await\s+connection\.execute\(\s*insert_registry_sql",
    ),
    Guard(
        "registry insert affects exactly one row",
        lambda source: (
            "insert_result = await connection.execute(" in source
            and 'if insert_result != "INSERT 0 1"' in source
            and "raise RegistryContractError(" in source
        ),
        r'if\s+insert_result\s*!=\s*"INSERT 0 1"',
    ),
)


def validate_guards(content: str, guards: tuple[Guard, ...]) -> list[str]:
    return [guard.name for guard in guards if not guard.test(content)]


def validate(sql: str) -> list[str]:
    return validate_guards(sql, GUARDS)


def mutation_failures_for(content: str, guards: tuple[Guard, ...]) -> list[str]:
    failures: list[str] = []
    for guard in guards:
        mutated, replacements = re.subn(
            guard.mutation_pattern,
            guard.mutation_replacement,
            content,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if replacements != 1:
            failures.append(f"{guard.name}: mutation pattern did not match")
            continue
        if guard.name not in validate_guards(mutated, guards):
            failures.append(f"{guard.name}: checker accepted weakened migration")
    return failures


def mutation_failures(sql: str) -> list[str]:
    return mutation_failures_for(sql, GUARDS)


def transaction_control_mutation_failures(sql: str) -> list[str]:
    failures: list[str] = []
    mutations = (
        "ABORT;",
        "BEGIN;",
        "COMMIT;",
        "END;",
        "ROLLBACK;",
        "SAVEPOINT before_change;",
        "RELEASE SAVEPOINT before_change;",
        "PREPARE TRANSACTION 'contract-check';",
        "START TRANSACTION;",
        "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;",
    )
    for statement in mutations:
        if has_no_migration_transaction_control(f"{statement}\n{sql}"):
            failures.append(
                f"production lexer accepted transaction-control mutation: {statement}"
            )
    return failures


def acl_predicate_mutation_failures(source: str) -> list[str]:
    guard_name = "registry ACL predicates do not hide dangerous privileges"
    mutations = (
        (
            "schema ACL query hides USAGE",
            "WHERE acl.grantee <> 0\n"
            "          AND acl.grantee <> namespace.nspowner",
            "WHERE acl.grantee <> 0\n"
            "          AND acl.grantee <> namespace.nspowner\n"
            "          AND acl.privilege_type <> 'USAGE'",
        ),
        (
            "column ACL query hides INSERT and UPDATE",
            "AND NOT privileged_attribute.attisdropped\n"
            "          AND acl.grantee <> registry.relowner\n"
            "    ) AS non_owner_column_privilege_count",
            "AND NOT privileged_attribute.attisdropped\n"
            "          AND acl.grantee <> registry.relowner\n"
            "          AND acl.privilege_type NOT IN ('INSERT', 'UPDATE')\n"
            "    ) AS non_owner_column_privilege_count",
        ),
    )
    failures: list[str] = []
    for name, target, replacement in mutations:
        if source.count(target) != 1:
            failures.append(f"{name}: mutation target did not match exactly once")
            continue
        mutated = source.replace(target, replacement, 1)
        if guard_name not in validate_guards(mutated, RUNNER_GUARDS):
            failures.append(f"{name}: checker accepted weakened ACL attestation")
    return failures


def main() -> int:
    if not MIGRATION.is_file():
        print(f"FAIL: migration not found: {MIGRATION.relative_to(ROOT)}", file=sys.stderr)
        return 1

    if not RUNNER.is_file():
        print(f"FAIL: runner not found: {RUNNER.relative_to(ROOT)}", file=sys.stderr)
        return 1

    sql = MIGRATION.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    failures: list[str] = []
    try:
        migrations = discover_migrations(MIGRATION.parent)
    except InvalidMigrationSetError as error:
        failures.append(f"production migration discovery rejected the real set: {error}")
    else:
        if MIGRATION.name not in {migration.name for migration in migrations}:
            failures.append("production migration discovery omitted the frozen migration")
    failures.extend(validate(sql))
    failures.extend(mutation_failures(sql))
    failures.extend(transaction_control_mutation_failures(sql))
    failures.extend(
        f"runner {name}" for name in validate_guards(runner, RUNNER_GUARDS)
    )
    failures.extend(
        f"runner {name}" for name in mutation_failures_for(runner, RUNNER_GUARDS)
    )
    failures.extend(acl_predicate_mutation_failures(runner))
    if failures:
        print("FAIL: PostgreSQL contract checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "PASS: M1-01 PostgreSQL contract "
        f"({len(GUARDS)} migration guards and {len(RUNNER_GUARDS)} runner guards, with mutations)"
    )
    print("LIMITATION: static checks only; PostgreSQL 18.4 integration remains required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
