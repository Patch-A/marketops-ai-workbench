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


def require(pattern: str, text: str, label: str, failures: list[str]) -> None:
    if re.search(pattern, text, re.IGNORECASE | re.DOTALL) is None:
        failures.append(label)


def main() -> int:
    failures: list[str] = []
    try:
        sql = MIGRATION.read_text(encoding="utf-8")
        adapter = ADAPTER.read_text(encoding="utf-8")
    except OSError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    for table in TABLES:
        require(rf"create\s+table\s+marketops\.{table}\b", sql, f"missing table {table}", failures)
        require(rf"alter\s+table\s+marketops\.{table}\s+enable\s+row\s+level\s+security", sql, f"missing RLS enable {table}", failures)
        require(rf"alter\s+table\s+marketops\.{table}\s+force\s+row\s+level\s+security", sql, f"missing forced RLS {table}", failures)
        require(rf"before\s+update\s+or\s+delete\s+on\s+marketops\.{table}", sql, f"missing append-only row trigger {table}", failures)
        require(rf"before\s+truncate\s+on\s+marketops\.{table}", sql, f"missing append-only truncate trigger {table}", failures)
        require(rf"revoke\s+all\s+on\s+marketops\.{table}\s+from\s+public", sql, f"missing PUBLIC revoke {table}", failures)
        policy = re.search(rf"create\s+policy\s+\w+\s+on\s+marketops\.{table}(.*?);", sql, re.IGNORECASE | re.DOTALL)
        if policy is None or not all(token in policy.group(1).lower() for token in (
            "current_actor_id()", "current_workspace_id()", "current_client_id()", "current_project_id()"
        )):
            failures.append(f"incomplete scope policy {table}")

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
        "pg_advisory_xact_lock",
        "review-project:",
        "review-run:",
        "ORDER BY review_version DESC",
        "ReviewPostgresError(\"review database transaction failed\") from None",
    ):
        if token not in adapter:
            failures.append(f"missing adapter contract: {token}")
    if "FOR UPDATE" in adapter.upper():
        failures.append("adapter requires UPDATE privilege through a row-lock clause")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"PASS: M1-02 PostgreSQL static contract ({len(TABLES)} forced-RLS append-only tables)")
    print("LIMITATION: static checks do not replace PostgreSQL 18.4 runtime and recovery evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
