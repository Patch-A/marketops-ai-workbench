from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from apps.api.marketops_import.backup import BUSINESS_TABLES, LEGACY_BUSINESS_TABLES
from scripts.run_m1_01_backup_restore_gate import (
    create_database_dump,
    migration_manifest_rows,
    postgres_tool_version,
    publish_after_toc_validation,
    replace_database_in_dsn,
    restore_database_dump,
    validate_database_name,
    validate_dump_toc,
)


class Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = "password=secret"


class BackupRestoreCommandTests(unittest.TestCase):
    def test_database_names_and_dsn_replacement_are_narrow(self):
        name = "marketops_restore_0123456789abcdef"
        self.assertEqual(validate_database_name(name), name)
        self.assertEqual(
            replace_database_in_dsn("postgresql://user:pass@host:5432/source?sslmode=disable", name),
            f"postgresql://user:pass@host:5432/{name}?sslmode=disable",
        )
        for invalid in ("marketops_test", "marketops_restore_short", 'marketops_restore_0123456789abcde"x'):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                validate_database_name(invalid)

    def test_tool_version_requires_exact_reviewed_client(self):
        runner = lambda *_args, **_kwargs: Result(stdout="pg_dump (PostgreSQL) 18.4 (Debian)\n")
        self.assertEqual(postgres_tool_version("a" * 64, "pg_dump", runner=runner), 180004)
        for output in (
            "pg_dump (PostgreSQL) 17.9\n",
            "pg_dump (PostgreSQL) 18.4beta1\n",
            "pg_dump (PostgreSQL) 18.4-foo\n",
        ):
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, "18.4"):
                postgres_tool_version(
                    "a" * 64,
                    "pg_dump",
                    runner=lambda *_args, **_kwargs: Result(stdout=output),
                )

    def test_dump_command_uses_snapshot_allowlist_and_no_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "database.dump"
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                kwargs["stdout"].write(b"dump")
                return Result()

            create_database_dump("b" * 64, "00000003-0000001B-1", output, runner=runner)
            command = calls[0][0]
            self.assertIn("--snapshot=00000003-0000001B-1", command)
            self.assertIn("--exclude-table-data=marketops.schema_migrations", command)
            self.assertEqual(
                [item for item in command if item.startswith("--table=")],
                [f"--table=marketops.{table}" for table in BUSINESS_TABLES],
            )
            self.assertNotIn("password", " ".join(command).lower())

    def test_legacy_dump_command_uses_exact_seven_table_allowlist(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "legacy.dump"
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                kwargs["stdout"].write(b"legacy dump")
                return Result()

            create_database_dump(
                "b" * 64,
                "00000003-0000001B-1",
                output,
                tables=LEGACY_BUSINESS_TABLES,
                runner=runner,
            )

            self.assertEqual(
                [item for item in calls[0] if item.startswith("--table=")],
                [f"--table=marketops.{table}" for table in LEGACY_BUSINESS_TABLES],
            )

    def test_command_failures_do_not_expose_subprocess_stderr(self):
        with tempfile.TemporaryDirectory() as temporary:
            dump = Path(temporary) / "database.dump"
            dump.write_bytes(b"dump")
            with self.assertRaisesRegex(RuntimeError, "restore failed") as context:
                restore_database_dump(
                    "c" * 64,
                    "marketops_restore_0123456789abcdef",
                    dump,
                    runner=lambda *_args, **_kwargs: Result(returncode=1),
                )
            self.assertNotIn("secret", str(context.exception))


class DumpTocTests(unittest.TestCase):
    def valid_toc(self, tables=BUSINESS_TABLES):
        lines = ["; PostgreSQL database dump", ";"]
        for index, table in enumerate(tables, start=100):
            lines.append(f"{index}; 0 200 TABLE DATA marketops {table} marketops_migrator")
        return "\n".join(lines)

    def test_accepts_exact_business_table_data(self):
        self.assertEqual(set(validate_dump_toc(self.valid_toc())), set(BUSINESS_TABLES))
        self.assertEqual(
            set(
                validate_dump_toc(
                    self.valid_toc(LEGACY_BUSINESS_TABLES), LEGACY_BUSINESS_TABLES
                )
            ),
            set(LEGACY_BUSINESS_TABLES),
        )

    def test_rejects_schema_registry_unknown_and_duplicate_entries(self):
        cases = {
            "registry": self.valid_toc() + "\n999; 0 200 TABLE DATA marketops schema_migrations marketops_migrator",
            "schema": self.valid_toc() + "\n999; 2615 200 SCHEMA - marketops marketops_migrator",
            "duplicate": self.valid_toc() + "\n999; 0 200 TABLE DATA marketops projects marketops_migrator",
        }
        for label, toc in cases.items():
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                validate_dump_toc(toc)

    def test_rejects_each_missing_required_table_entry(self):
        valid_lines = self.valid_toc().splitlines()
        for table in BUSINESS_TABLES:
            with self.subTest(table=table), self.assertRaisesRegex(
                RuntimeError, "incomplete or duplicated"
            ):
                validate_dump_toc(
                    "\n".join(
                        line
                        for line in valid_lines
                        if f" TABLE DATA marketops {table} " not in line
                    )
                )

    def test_toc_drift_fails_before_bundle_publisher_runs(self):
        published = []

        def publisher():
            published.append(True)
            return object()

        with self.assertRaisesRegex(RuntimeError, "non-allowlisted"):
            publish_after_toc_validation(
                "a" * 64,
                Path("unread.dump"),
                publisher,
                toc_reader=lambda *_args: "999; 2615 200 SCHEMA - marketops owner",
            )
        self.assertEqual(published, [])

    def test_migration_registry_rows_map_to_manifest_shape(self):
        self.assertEqual(
            migration_manifest_rows(
                [{"migration_name": "0001_project_import.sql", "sha256": "a" * 64}]
            ),
            [{"name": "0001_project_import.sql", "sha256": "a" * 64}],
        )
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            migration_manifest_rows([{"name": "wrong-shape", "sha256": "a" * 64}])


class BackupRestoreWorkflowContractTests(unittest.TestCase):
    def test_service_database_suppresses_failure_row_context(self):
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'POSTGRES_INITDB_ARGS: "--set=log_error_verbosity=terse '
            '--set=log_min_error_statement=panic"',
            workflow,
        )

    def test_runtime_job_runs_backup_restore_between_restart_verify_and_cleanup(self):
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
        markers = [
            "run_m1_01_restart_recovery_gate.py verify",
            "run_m1_01_backup_restore_gate.py",
            "run_m1_01_restart_recovery_gate.py connection-loss",
        ]
        positions = []
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertEqual(workflow.count(marker), 1)
                positions.append(workflow.index(marker))
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
