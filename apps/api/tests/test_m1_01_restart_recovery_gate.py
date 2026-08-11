from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from scripts.run_m1_01_restart_recovery_gate import (
    TABLE_COUNTS,
    restart_postgres_container,
    validate_committed_snapshot,
    validate_container_id,
    validate_uncommitted_snapshot,
)


class RestartCommandTests(unittest.TestCase):
    def test_accepts_canonical_container_id_and_uses_no_shell(self):
        container_id = "a" * 64
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))

        restart_postgres_container(container_id, runner=runner)

        self.assertEqual(
            calls,
            [
                (
                    ["docker", "restart", container_id],
                    {"check": True, "capture_output": True, "text": True},
                )
            ],
        )

    def test_rejects_noncanonical_or_option_like_container_ids(self):
        invalid = ["", "abc", "-" + "a" * 63, "a" * 65, "A" * 64, "a" * 63 + "/"]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "ID is invalid"):
                validate_container_id(value)

    def test_restart_failure_does_not_expose_subprocess_output(self):
        def runner(*_args, **_kwargs):
            raise subprocess.CalledProcessError(
                1, ["docker", "restart"], output="token=secret", stderr="password=secret"
            )

        with self.assertRaisesRegex(RuntimeError, "restart failed") as context:
            restart_postgres_container("b" * 64, runner=runner)

        self.assertNotIn("secret", str(context.exception))


class RecoveryEvidenceValidationTests(unittest.TestCase):
    def committed_snapshot(self):
        return {
            "counts": dict(TABLE_COUNTS),
            "approvedProposalSelected": True,
            "artifactKinds": ["proposal", "source"],
        }

    def test_exact_committed_snapshot_passes(self):
        validate_committed_snapshot(self.committed_snapshot())

    def test_any_missing_committed_row_fails_closed(self):
        for name in TABLE_COUNTS:
            snapshot = self.committed_snapshot()
            snapshot["counts"][name] -= 1
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                validate_committed_snapshot(snapshot)

    def test_unapproved_or_incomplete_committed_snapshot_fails_closed(self):
        snapshot = self.committed_snapshot()
        snapshot["approvedProposalSelected"] = False
        with self.assertRaisesRegex(RuntimeError, "approved proposal"):
            validate_committed_snapshot(snapshot)

        snapshot = self.committed_snapshot()
        snapshot["artifactKinds"] = ["source"]
        with self.assertRaisesRegex(RuntimeError, "artifact versions"):
            validate_committed_snapshot(snapshot)

    def test_exact_empty_uncommitted_snapshot_passes(self):
        validate_uncommitted_snapshot(
            {"counts": {name: 0 for name in TABLE_COUNTS}}
        )

    def test_any_partial_uncommitted_row_fails_closed(self):
        for name in TABLE_COUNTS:
            counts = {table: 0 for table in TABLE_COUNTS}
            counts[name] = 1
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "partial"):
                validate_uncommitted_snapshot({"counts": counts})


class RecoveryWorkflowContractTests(unittest.TestCase):
    def test_import_snapshot_counts_only_import_audit_events(self):
        root = Path(__file__).resolve().parents[3]
        source = (root / "scripts" / "run_m1_01_restart_recovery_gate.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("action = 'project_imported'", source)

    def test_runtime_job_runs_all_recovery_phases_in_order(self):
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
        required = [
            "MARKETOPS_TEST_POSTGRES_CONTAINER_ID: ${{ job.services.postgres.id }}",
            "run_m1_01_restart_recovery_gate.py prepare",
            "run_m1_01_restart_recovery_gate.py restart",
            "run_m1_01_restart_recovery_gate.py verify",
            "run_m1_01_restart_recovery_gate.py connection-loss",
        ]
        positions = []
        for marker in required:
            with self.subTest(marker=marker):
                self.assertEqual(workflow.count(marker), 1)
                positions.append(workflow.index(marker))
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
