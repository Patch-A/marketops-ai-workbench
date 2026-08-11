#!/usr/bin/env python3
"""Static fail-closed checks for the reviewed M1-02 PostgreSQL slice."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.marketops_import.migrations import (  # noqa: E402
    InvalidMigrationSetError,
    validate_no_transaction_control,
)

MIGRATION = ROOT / "apps/api/migrations/0002_extraction_review.sql"
ADAPTER = ROOT / "apps/api/marketops_review/postgres.py"
TABLES = (
    "extraction_runs",
    "extraction_candidates",
    "review_snapshots",
    "review_snapshot_items",
    "review_decisions",
)
CREATOR_COLUMNS = {
    "extraction_runs": "created_by",
    "extraction_candidates": "created_by",
    "review_snapshots": "created_by",
    "review_snapshot_items": "created_by",
    "review_decisions": "actor_id",
}
SCOPE_PREDICATES = {
    "actor": r"marketops\.current_actor_id\(\)\s+is\s+not\s+null",
    "workspace": r"\bworkspace_id\s*=\s*marketops\.current_workspace_id\(\)",
    "client": r"\bclient_id\s*=\s*marketops\.current_client_id\(\)",
    "project": r"\bproject_id\s*=\s*marketops\.current_project_id\(\)",
}


def require(pattern: str, text: str, label: str, failures: list[str]) -> None:
    if re.search(pattern, text, re.IGNORECASE | re.DOTALL) is None:
        failures.append(label)


def _normalized(value: str) -> str:
    return " ".join(value.lower().split())


def _policy_halves(sql: str, table: str) -> tuple[str, str] | None:
    match = re.search(
        rf"create\s+policy\s+{table}_scope\s+on\s+marketops\.{table}\s+"
        rf"using\s*\((?P<using>.*?)\)\s+with\s+check\s*\((?P<check>.*?)\)\s*;",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    return match.group("using"), match.group("check")


def _validate_policy(sql: str, table: str, failures: list[str]) -> None:
    halves = _policy_halves(sql, table)
    if halves is None:
        failures.append(f"missing exact scope policy {table}")
        return
    for half_name, expression in zip(("USING", "WITH CHECK"), halves):
        for predicate_name, pattern in SCOPE_PREDICATES.items():
            if re.search(pattern, expression, re.IGNORECASE) is None:
                failures.append(
                    f"{half_name} missing {predicate_name} scope {table}"
                )
    creator = CREATOR_COLUMNS[table]
    require(
        rf"\b{creator}\s*=\s*marketops\.current_actor_id\(\)",
        halves[1],
        f"WITH CHECK missing actor ownership {table}",
        failures,
    )


def _validate_trigger(
    sql: str,
    *,
    table: str,
    suffix: str,
    expected_body: str,
    failures: list[str],
) -> None:
    name = f"{table}_{suffix}"
    matches = list(
        re.finditer(
            rf"create\s+trigger\s+{name}\b(?P<body>.*?);",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
    )
    if len(matches) != 1:
        failures.append(f"expected exactly one immutable trigger {name}")
        return
    body = matches[0].group("body")
    if re.search(r"\bwhen\b", body, re.IGNORECASE):
        failures.append(f"conditional immutable trigger {name}")
    if _normalized(body) != _normalized(expected_body):
        failures.append(f"unexpected immutable trigger shape {name}")


def validate_contract(sql: str, adapter: str) -> list[str]:
    failures: list[str] = []
    for table in TABLES:
        require(
            rf"create\s+table\s+marketops\.{table}\b",
            sql,
            f"missing table {table}",
            failures,
        )
        require(
            rf"alter\s+table\s+marketops\.{table}\s+enable\s+row\s+level\s+security\s*;",
            sql,
            f"missing RLS enable {table}",
            failures,
        )
        require(
            rf"alter\s+table\s+marketops\.{table}\s+force\s+row\s+level\s+security\s*;",
            sql,
            f"missing forced RLS {table}",
            failures,
        )
        _validate_trigger(
            sql,
            table=table,
            suffix="immutable",
            expected_body=(
                f"BEFORE UPDATE OR DELETE ON marketops.{table} "
                "FOR EACH ROW EXECUTE FUNCTION marketops.reject_immutable_row_change()"
            ),
            failures=failures,
        )
        _validate_trigger(
            sql,
            table=table,
            suffix="truncate_immutable",
            expected_body=(
                f"BEFORE TRUNCATE ON marketops.{table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION marketops.reject_immutable_row_change()"
            ),
            failures=failures,
        )
        require(
            rf"revoke\s+all\s+on\s+marketops\.{table}\s+from\s+public\s*;",
            sql,
            f"missing PUBLIC revoke {table}",
            failures,
        )
        _validate_policy(sql, table, failures)

    for token in (
        "check_extraction_run_integrity",
        "check_review_snapshot_integrity",
        "candidate batch is incomplete",
        "review snapshot is incomplete",
        "review snapshot versions must be contiguous",
        "review snapshot must match exactly one candidate decision",
    ):
        if token not in sql:
            failures.append(f"missing database invariant: {token}")

    if re.search(r"\bgrant\b", sql, re.IGNORECASE):
        failures.append("migration must not grant application privileges")
    try:
        validate_no_transaction_control(sql, MIGRATION.name)
    except InvalidMigrationSetError:
        failures.append("migration contains transaction control")

    for token in (
        "set_config('app.workspace_id', $1, true)",
        "set_config('app.client_id', $2, true)",
        "set_config('app.project_id', $3, true)",
        "set_config('app.actor_id', $4, true)",
        "ORDER BY review_version DESC",
        'ReviewPostgresError("review database transaction failed") from None',
    ):
        if token not in adapter:
            failures.append(f"missing adapter contract: {token}")
    require(
        r"from\s+marketops\.projects\s+as\s+project.*?for\s+update\s+of\s+project",
        adapter,
        "approved proposal row is not locked FOR UPDATE",
        failures,
    )
    require(
        r"from\s+marketops\.extraction_runs\s+where\s+id\s*=\s*\$1\s+for\s+update",
        adapter,
        "extraction run row is not locked FOR UPDATE",
        failures,
    )
    if "pg_advisory_xact_lock" in adapter:
        failures.append("adapter still relies on advisory locks")
    return failures


def _mutate_once(value: str, pattern: str, replacement: str, label: str) -> str:
    mutated, count = re.subn(
        pattern,
        replacement,
        value,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if count != 1:
        raise RuntimeError(f"self-mutation setup failed: {label}")
    return mutated


def validate_self_mutations(sql: str, adapter: str) -> list[str]:
    cases = (
        (
            "workspace predicate removal",
            _mutate_once(
                sql,
                r"\s+and\s+workspace_id\s*=\s*marketops\.current_workspace_id\(\)",
                "",
                "workspace predicate removal",
            ),
            adapter,
            "USING missing workspace scope extraction_runs",
        ),
        (
            "actor predicate removal",
            _mutate_once(
                sql,
                r"marketops\.current_actor_id\(\)\s+is\s+not\s+null\s+and\s+",
                "",
                "actor predicate removal",
            ),
            adapter,
            "USING missing actor scope extraction_runs",
        ),
        (
            "disabled immutable trigger",
            _mutate_once(
                sql,
                r"(create\s+trigger\s+extraction_runs_immutable\s+before\s+update\s+or\s+delete\s+on\s+marketops\.extraction_runs)\s+",
                r"\1 WHEN (false) ",
                "disabled immutable trigger",
            ),
            adapter,
            "conditional immutable trigger extraction_runs_immutable",
        ),
        (
            "forced RLS removal",
            _mutate_once(
                sql,
                r"alter\s+table\s+marketops\.extraction_runs\s+force\s+row\s+level\s+security\s*;",
                "",
                "forced RLS removal",
            ),
            adapter,
            "missing forced RLS extraction_runs",
        ),
        (
            "proposal row lock removal",
            sql,
            _mutate_once(
                adapter,
                r"for\s+update\s+of\s+project",
                "",
                "proposal row lock removal",
            ),
            "approved proposal row is not locked FOR UPDATE",
        ),
    )
    failures: list[str] = []
    for case_name, mutated_sql, mutated_adapter, expected in cases:
        observed = validate_contract(mutated_sql, mutated_adapter)
        if expected not in observed:
            failures.append(
                f"self-mutation escaped detection: {case_name} (expected {expected!r})"
            )
    return failures


def main() -> int:
    try:
        sql = MIGRATION.read_text(encoding="utf-8")
        adapter = ADAPTER.read_text(encoding="utf-8")
    except OSError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    failures = validate_contract(sql, adapter)
    if not failures:
        try:
            failures.extend(validate_self_mutations(sql, adapter))
        except RuntimeError as error:
            failures.append(str(error))
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(
        f"PASS: M1-02 PostgreSQL static contract "
        f"({len(TABLES)} forced-RLS append-only tables; 5 self-mutations rejected)"
    )
    print("LIMITATION: static checks do not replace PostgreSQL 18.4 runtime and recovery evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
