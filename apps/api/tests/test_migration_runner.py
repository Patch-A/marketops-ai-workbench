from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from marketops_import.migrations import (  # noqa: E402
    EXPECTED_REGISTRY_COLUMNS,
    EXPECTED_REGISTRY_CONSTRAINTS,
    DEFAULT_ALLOWED_SCHEMA_USAGE_ROLES,
    INSERT_REGISTRY_SQL,
    LOCK_SQL,
    READ_REGISTRY_SQL,
    REGISTRY_ATTESTATION_SQL,
    REGISTRY_SETUP_SQL,
    InvalidMigrationSetError,
    MigrationDriftError,
    MigrationHistoryError,
    RegistryContractError,
    discover_migrations,
    run_migrations,
)


def valid_registry_attestation() -> dict[str, Any]:
    return {
        "current_user": "migration_owner",
        "schema_owner": "migration_owner",
        "table_owner": "migration_owner",
        "function_owner": "migration_owner",
        "relkind": "r",
        "relpersistence": "p",
        "has_rules": False,
        "has_subclass": False,
        "rls_enabled": False,
        "force_rls_enabled": False,
        "inheritance_edge_count": 0,
        "columns": list(EXPECTED_REGISTRY_COLUMNS),
        "constraints": list(EXPECTED_REGISTRY_CONSTRAINTS),
        "trigger_count": 2,
        "row_trigger_count": 1,
        "truncate_trigger_count": 1,
        "public_schema_privilege_count": 0,
        "non_owner_schema_privileges": [
            f"{role}:USAGE:not-grantable"
            for role in DEFAULT_ALLOWED_SCHEMA_USAGE_ROLES
        ],
        "public_table_privilege_count": 0,
        "non_owner_table_privilege_count": 0,
        "non_owner_column_privilege_count": 0,
        "public_function_privilege_count": 0,
        "non_owner_function_privilege_count": 0,
        "owner_can_read_and_insert": True,
    }


class FakeDatabase:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.registry: list[dict[str, str]] = []
        self.executed_migrations: list[str] = []


class FakeTransaction:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection

    async def __aenter__(self) -> "FakeTransaction":
        if self.connection.in_transaction:
            raise AssertionError("nested transaction")
        self.connection.in_transaction = True
        self.connection.events.append("transaction:begin")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        connection = self.connection
        if exc_type is None:
            connection.database.registry.extend(connection.pending_registry)
            connection.database.executed_migrations.extend(
                connection.pending_migrations
            )
            connection.events.append("transaction:commit")
        else:
            connection.events.append("transaction:rollback")
        connection.pending_registry.clear()
        connection.pending_migrations.clear()
        connection.in_transaction = False
        if connection.holds_lock:
            connection.database.lock.release()
            connection.holds_lock = False


class FakeConnection:
    def __init__(
        self,
        database: FakeDatabase | None = None,
        *,
        fail_sql: str | None = None,
        fail_registry_insert: bool = False,
        registry_insert_result: str = "INSERT 0 1",
        registry_attestation: dict[str, Any] | None = None,
    ) -> None:
        self.database = database or FakeDatabase()
        self.fail_sql = fail_sql
        self.fail_registry_insert = fail_registry_insert
        self.registry_insert_result = registry_insert_result
        self.registry_attestation = (
            registry_attestation
            if registry_attestation is not None
            else valid_registry_attestation()
        )
        self.in_transaction = False
        self.holds_lock = False
        self.pending_registry: list[dict[str, str]] = []
        self.pending_migrations: list[str] = []
        self.events: list[str] = []
        self.migration_queries: list[str] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def execute(self, query: str, *args: Any) -> str:
        if not self.in_transaction:
            raise AssertionError("all runner statements must use its transaction")
        if query == LOCK_SQL:
            await self.database.lock.acquire()
            self.holds_lock = True
            self.events.append("lock")
            return "SELECT 1"
        if query == REGISTRY_SETUP_SQL:
            if not self.holds_lock:
                raise AssertionError("registry setup must follow the advisory lock")
            self.events.append("registry:setup")
            return "REVOKE"
        if query == INSERT_REGISTRY_SQL:
            if self.fail_registry_insert:
                raise RuntimeError("registry insert failed")
            migration_name, checksum = args
            self.pending_registry.append(
                {"migration_name": migration_name, "sha256": checksum.hex()}
            )
            self.events.append(f"registry:insert:{migration_name}")
            return self.registry_insert_result

        self.events.append("migration:execute")
        self.migration_queries.append(query)
        if query == self.fail_sql:
            raise RuntimeError("migration execution failed")
        self.pending_migrations.append(query)
        await asyncio.sleep(0)
        return "CREATE"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if query == REGISTRY_ATTESTATION_SQL:
            if not self.holds_lock:
                raise AssertionError("registry attestation must follow the advisory lock")
            self.events.append("registry:attest")
            return [dict(self.registry_attestation)]
        if query != READ_REGISTRY_SQL:
            raise AssertionError(f"unexpected registry query: {query}")
        if not self.holds_lock:
            raise AssertionError("registry read must follow the advisory lock")
        self.events.append("registry:read")
        return [dict(row) for row in self.database.registry]


class MigrationDirectory:
    def __init__(self, test: unittest.TestCase) -> None:
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)

    def write(self, name: str, content: bytes) -> None:
        (self.path / name).write_bytes(content)


class MigrationRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_applies_raw_bytes_in_order_and_records_exact_sha256(self) -> None:
        migrations = MigrationDirectory(self)
        first = b"CREATE TABLE first_table (id integer);\r\n"
        second = b"CREATE TABLE second_table (id integer);\n"
        migrations.write("0002_second.sql", second)
        migrations.write("0001_first.sql", first)
        connection = FakeConnection()

        applied = await run_migrations(connection, migrations.path)

        self.assertEqual(applied, ("0001_first.sql", "0002_second.sql"))
        self.assertEqual(
            connection.migration_queries,
            [first.decode("utf-8"), second.decode("utf-8")],
        )
        self.assertEqual(
            [row["sha256"] for row in connection.database.registry],
            [hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest()],
        )
        self.assertEqual(connection.events[0:5], [
            "transaction:begin",
            "lock",
            "registry:setup",
            "registry:attest",
            "registry:read",
        ])
        self.assertEqual(connection.events[-1], "transaction:commit")

    async def test_concurrent_runners_serialize_and_apply_once(self) -> None:
        migrations = MigrationDirectory(self)
        sql = b"CREATE TABLE only_once (id integer);"
        migrations.write("0001_only_once.sql", sql)
        database = FakeDatabase()
        first = FakeConnection(database)
        second = FakeConnection(database)

        results = await asyncio.gather(
            run_migrations(first, migrations.path),
            run_migrations(second, migrations.path),
        )

        self.assertEqual(sorted(results), [(), ("0001_only_once.sql",)])
        self.assertEqual(database.executed_migrations, [sql.decode("utf-8")])
        self.assertEqual(len(database.registry), 1)
        for connection in (first, second):
            self.assertLess(
                connection.events.index("lock"),
                connection.events.index("registry:read"),
            )

    async def test_checksum_drift_is_rejected_before_any_sql_is_applied(self) -> None:
        migrations = MigrationDirectory(self)
        migrations.write("0001_initial.sql", b"SELECT 1;")
        database = FakeDatabase()
        database.registry.append(
            {"migration_name": "0001_initial.sql", "sha256": "00" * 32}
        )
        connection = FakeConnection(database)

        with self.assertRaises(MigrationDriftError):
            await run_migrations(connection, migrations.path)

        self.assertEqual(connection.migration_queries, [])
        self.assertEqual(connection.events[-1], "transaction:rollback")

    async def test_missing_prefix_is_rejected_without_reordering_history(self) -> None:
        migrations = MigrationDirectory(self)
        migrations.write("0001_initial.sql", b"SELECT 1;")
        migrations.write("0002_next.sql", b"SELECT 2;")
        database = FakeDatabase()
        database.registry.append(
            {
                "migration_name": "0002_next.sql",
                "sha256": hashlib.sha256(b"SELECT 2;").hexdigest(),
            }
        )
        connection = FakeConnection(database)

        with self.assertRaises(MigrationHistoryError):
            await run_migrations(connection, migrations.path)

        self.assertEqual(connection.migration_queries, [])
        self.assertEqual(connection.events[-1], "transaction:rollback")

    async def test_partial_failure_rolls_back_migrations_and_registry(self) -> None:
        migrations = MigrationDirectory(self)
        first = "CREATE TABLE first_table (id integer);"
        second = "BROKEN MIGRATION;"
        migrations.write("0001_first.sql", first.encode())
        migrations.write("0002_broken.sql", second.encode())
        connection = FakeConnection(fail_sql=second)

        with self.assertRaisesRegex(RuntimeError, "migration execution failed"):
            await run_migrations(connection, migrations.path)

        self.assertEqual(connection.database.executed_migrations, [])
        self.assertEqual(connection.database.registry, [])
        self.assertEqual(connection.events[-1], "transaction:rollback")

    async def test_registry_failure_rolls_back_applied_sql(self) -> None:
        migrations = MigrationDirectory(self)
        migrations.write("0001_initial.sql", b"CREATE TABLE initial (id integer);")
        connection = FakeConnection(fail_registry_insert=True)

        with self.assertRaisesRegex(RuntimeError, "registry insert failed"):
            await run_migrations(connection, migrations.path)

        self.assertEqual(connection.database.executed_migrations, [])
        self.assertEqual(connection.database.registry, [])
        self.assertEqual(connection.events[-1], "transaction:rollback")

    async def test_registry_zero_row_insert_rolls_back_applied_sql(self) -> None:
        migrations = MigrationDirectory(self)
        migrations.write("0001_initial.sql", b"CREATE TABLE initial (id integer);")
        connection = FakeConnection(registry_insert_result="INSERT 0 0")

        with self.assertRaisesRegex(
            RegistryContractError, "did not affect exactly one row"
        ):
            await run_migrations(connection, migrations.path)

        self.assertEqual(connection.database.executed_migrations, [])
        self.assertEqual(connection.database.registry, [])
        self.assertEqual(connection.events[-1], "transaction:rollback")

    async def test_function_body_with_semicolons_is_sent_as_one_statement(self) -> None:
        migrations = MigrationDirectory(self)
        function_sql = b"""CREATE FUNCTION demo() RETURNS void AS $$
BEGIN
    PERFORM 1;
    PERFORM 2;
END;
$$ LANGUAGE plpgsql;
"""
        migrations.write("0001_function.sql", function_sql)
        connection = FakeConnection()

        await run_migrations(connection, migrations.path)

        self.assertEqual(connection.migration_queries, [function_sql.decode("utf-8")])

    async def test_transaction_words_in_lexical_regions_are_allowed(self) -> None:
        migrations = MigrationDirectory(self)
        sql = b'''-- BEGIN; COMMIT;\r/* ROLLBACK; /* START TRANSACTION; */ END; */
SELECT 'BEGIN; COMMIT;', "ROLLBACK";
SELECT E'escaped quote \\'; COMMIT; remains text';
DO $procedure$
BEGIN
    PERFORM 'END;';
END;
$procedure$;
'''
        migrations.write("0001_lexical_regions.sql", sql)
        connection = FakeConnection()

        await run_migrations(connection, migrations.path)

        self.assertEqual(connection.migration_queries, [sql.decode("utf-8")])

    async def test_sql_standard_atomic_function_body_is_not_transaction_control(self) -> None:
        migrations = MigrationDirectory(self)
        sql = b"""CREATE FUNCTION demo_atomic(value integer)
RETURNS integer
LANGUAGE SQL
BEGIN ATOMIC
    SELECT CASE WHEN value > 0 THEN value ELSE 0 END;
END;
"""
        migrations.write("0001_atomic_function.sql", sql)
        connection = FakeConnection()

        await run_migrations(connection, migrations.path)

        self.assertEqual(connection.migration_queries, [sql.decode("utf-8")])

    async def test_unterminated_atomic_function_body_fails_before_transaction(self) -> None:
        migrations = MigrationDirectory(self)
        migrations.write(
            "0001_unterminated_atomic.sql",
            b"CREATE FUNCTION broken() RETURNS integer LANGUAGE SQL "
            b"BEGIN ATOMIC SELECT 1;",
        )
        connection = FakeConnection()

        with self.assertRaisesRegex(
            InvalidMigrationSetError, "unterminated BEGIN ATOMIC body"
        ):
            await run_migrations(connection, migrations.path)

        self.assertEqual(connection.events, [])

    async def test_top_level_transaction_controls_fail_before_transaction(self) -> None:
        forbidden_statements = (
            "BEGIN;",
            "COMMIT WORK;",
            "ROLLBACK TO SAVEPOINT before_change;",
            "START TRANSACTION;",
            "END TRANSACTION;",
            "ABORT;",
            "SAVEPOINT before_change;",
            "RELEASE SAVEPOINT before_change;",
            "PREPARE TRANSACTION 'migration';",
            "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;",
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;",
            "SELECT 'ordinary backslash \\'; COMMIT; SELECT 'still valid';",
        )
        for statement in forbidden_statements:
            with self.subTest(statement=statement):
                migrations = MigrationDirectory(self)
                migrations.write(
                    "0001_forbidden.sql",
                    f"SELECT 1;\n{statement}\nSELECT 2;".encode(),
                )
                connection = FakeConnection()

                with self.assertRaisesRegex(
                    InvalidMigrationSetError, "top-level transaction control"
                ):
                    await run_migrations(connection, migrations.path)

                self.assertEqual(connection.events, [])

    async def test_registry_owner_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(schema_owner="other_owner")

    async def test_registry_column_shape_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(
            columns=[*EXPECTED_REGISTRY_COLUMNS, "injected:text:false"]
        )

    async def test_registry_relation_shape_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(relkind="p")

    async def test_registry_rewrite_rule_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(has_rules=True)

    async def test_registry_subclass_flag_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(has_subclass=True)

    async def test_registry_inheritance_edge_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(inheritance_edge_count=1)

    async def test_registry_constraint_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(
            constraints=[EXPECTED_REGISTRY_CONSTRAINTS[0]]
        )

    async def test_registry_trigger_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(row_trigger_count=0)

    async def test_registry_privilege_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(non_owner_table_privilege_count=1)

    async def test_registry_column_privilege_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(non_owner_column_privilege_count=1)

    async def test_registry_named_schema_privilege_mismatch_fails_closed(self) -> None:
        await self._assert_registry_rejected(
            non_owner_schema_privileges=["application_role:USAGE:not-grantable"]
        )

    async def test_registry_named_schema_privilege_requires_explicit_allowlist(self) -> None:
        migrations = MigrationDirectory(self)
        migrations.write("0001_initial.sql", b"SELECT 1;")
        privilege = "application_role:USAGE:not-grantable"
        attestation = valid_registry_attestation()
        attestation["non_owner_schema_privileges"] = [privilege]
        connection = FakeConnection(registry_attestation=attestation)

        applied = await run_migrations(
            connection,
            migrations.path,
            allowed_schema_usage_roles=("application_role",),
        )

        self.assertEqual(applied, ("0001_initial.sql",))
        self.assertEqual(connection.events[-1], "transaction:commit")

    async def test_registry_schema_allowlist_accepts_only_safe_role_names(self) -> None:
        unsafe_allowlists = (
            ("attacker:CREATE:grantable",),
            ("PUBLIC",),
            ("public",),
            ("migration_owner",),
            ("application_role", "application_role"),
            ("role with spaces",),
        )
        for roles in unsafe_allowlists:
            with self.subTest(roles=roles):
                migrations = MigrationDirectory(self)
                migrations.write("0001_initial.sql", b"SELECT 1;")
                connection = FakeConnection()
                with self.assertRaises(RegistryContractError):
                    await run_migrations(
                        connection,
                        migrations.path,
                        allowed_schema_usage_roles=roles,
                    )
                self.assertEqual(connection.migration_queries, [])
                self.assertEqual(connection.events[-1], "transaction:rollback")

    async def test_registry_schema_allowlist_cannot_hide_create_or_grantable_acl(self) -> None:
        migrations = MigrationDirectory(self)
        migrations.write("0001_initial.sql", b"SELECT 1;")
        for privilege in (
            "application_role:CREATE:not-grantable",
            "application_role:USAGE:grantable",
        ):
            with self.subTest(privilege=privilege):
                attestation = valid_registry_attestation()
                attestation["non_owner_schema_privileges"] = [privilege]
                connection = FakeConnection(registry_attestation=attestation)
                with self.assertRaises(RegistryContractError):
                    await run_migrations(
                        connection,
                        migrations.path,
                        allowed_schema_usage_roles=("application_role",),
                    )
                self.assertEqual(connection.migration_queries, [])
                self.assertEqual(connection.events[-1], "transaction:rollback")

    def test_registry_attestation_rejects_update_of_trigger_shape(self) -> None:
        self.assertIn(
            "trigger_record.tgattr = ''::pg_catalog.int2vector",
            REGISTRY_ATTESTATION_SQL,
        )

    async def _assert_registry_rejected(self, **changes: Any) -> None:
        migrations = MigrationDirectory(self)
        migrations.write("0001_initial.sql", b"SELECT 1;")
        attestation = valid_registry_attestation()
        attestation.update(changes)
        connection = FakeConnection(registry_attestation=attestation)

        with self.assertRaises(RegistryContractError):
            await run_migrations(connection, migrations.path)

        self.assertEqual(connection.migration_queries, [])
        self.assertEqual(connection.database.registry, [])
        self.assertEqual(connection.events[-1], "transaction:rollback")

    async def test_applied_prefix_is_not_reexecuted(self) -> None:
        migrations = MigrationDirectory(self)
        first = b"SELECT 1;"
        second = b"SELECT 2;"
        migrations.write("0001_first.sql", first)
        migrations.write("0002_second.sql", second)
        database = FakeDatabase()
        database.registry.append(
            {
                "migration_name": "0001_first.sql",
                "sha256": hashlib.sha256(first).hexdigest(),
            }
        )
        connection = FakeConnection(database)

        applied = await run_migrations(connection, migrations.path)

        self.assertEqual(applied, ("0002_second.sql",))
        self.assertEqual(connection.migration_queries, [second.decode("utf-8")])

    def test_noncontiguous_local_files_are_rejected(self) -> None:
        migrations = MigrationDirectory(self)
        migrations.write("0001_first.sql", b"SELECT 1;")
        migrations.write("0003_third.sql", b"SELECT 3;")

        with self.assertRaises(InvalidMigrationSetError):
            discover_migrations(migrations.path)


if __name__ == "__main__":
    unittest.main()
