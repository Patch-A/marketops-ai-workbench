from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from apps.api.marketops_import.service import StoredObject
from scripts.run_m1_01_orphan_cleanup_gate import (
    DRY_RUN_EVIDENCE_KEYS,
    EVIDENCE_KEYS,
    FAILURE_MATRIX_EVIDENCE_KEYS,
    INTEGRITY_EVIDENCE_KEYS,
    RACE_EVIDENCE_KEYS,
    build_parser,
    managed_payload_inventory,
    physical_key,
    validate_non_sensitive_evidence,
)


class OrphanCleanupGateContractTests(unittest.TestCase):
    @staticmethod
    def evidence():
        return {
            "schemaVersion": 1,
            "taskId": "M1-01",
            "workPackage": "WP5C-orphan-cleanup",
            "generatedAt": "2026-08-09T00:00:00+00:00",
            "dryRun": {key: True for key in DRY_RUN_EVIDENCE_KEYS},
            "failureMatrix": {
                key: "EXPECTED_FAILURE" for key in FAILURE_MATRIX_EVIDENCE_KEYS
            },
            "crossProcessRace": {key: True for key in RACE_EVIDENCE_KEYS},
            "integrity": {key: True for key in INTEGRITY_EVIDENCE_KEYS},
            "claimBoundary": "bounded synthetic result",
        }

    def test_public_cli_is_one_command_with_default_state(self):
        args = build_parser().parse_args(
            ["--work-root", "runner-root", "--output", "evidence.json"]
        )
        self.assertEqual(args.work_root, Path("runner-root"))
        self.assertEqual(args.output, Path("evidence.json"))
        self.assertIsNone(args.state)
        self.assertIsNone(args.child_mode)

    def test_physical_key_is_hash_of_canonical_storage_key(self):
        stored = StoredObject(
            storage_key=(
                "workspaces/00000000-0000-4000-8000-000000000001/clients/"
                "00000000-0000-4000-8000-000000000002/imports/"
                + "a" * 64
                + "/"
                + "b" * 64
                + "/source/"
                + "c" * 64
            ),
            size_bytes=1,
            sha256="c" * 64,
        )
        self.assertEqual(
            physical_key(stored),
            hashlib.sha256(stored.storage_key.encode("ascii")).hexdigest(),
        )

    def test_inventory_reads_only_canonical_payload_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = b"synthetic"
            key = hashlib.sha256(b"key").hexdigest()
            shard = root / key[:2]
            shard.mkdir()
            (shard / key[2:]).write_bytes(payload)
            (root / ".marketops-object-store.lock").write_text("", encoding="ascii")
            (root / "unknown").write_text("ignored", encoding="ascii")
            self.assertEqual(
                managed_payload_inventory(root),
                {
                    key: (
                        len(payload),
                        hashlib.sha256(payload).hexdigest(),
                    )
                },
            )

    def test_evidence_contract_rejects_dsn_absolute_path_and_field_drift(self):
        evidence = self.evidence()
        self.assertEqual(set(evidence), EVIDENCE_KEYS)
        validate_non_sensitive_evidence(evidence)

        for secret in (
            "postgresql://user:pass@host/db",
            str(Path.cwd().resolve()),
            "/runner/temp/object-root",
        ):
            changed = dict(evidence)
            changed["claimBoundary"] = secret
            with self.subTest(secret=secret), self.assertRaises(RuntimeError):
                validate_non_sensitive_evidence(changed)

        changed = dict(evidence)
        changed["unexpected"] = True
        with self.assertRaises(RuntimeError):
            validate_non_sensitive_evidence(changed)

        changed = self.evidence()
        changed["failureMatrix"].pop("symbolicLinkLayout")
        with self.assertRaises(RuntimeError):
            validate_non_sensitive_evidence(changed)

    def test_cross_process_race_requires_observed_flock_contention_marker(self):
        root = Path(__file__).resolve().parents[3]
        source = (
            root / "scripts" / "run_m1_01_orphan_cleanup_gate.py"
        ).read_text(encoding="utf-8")
        required = [
            'except BlockingIOError:',
            '_write_atomic_marker(blocked_marker)',
            'await _wait_for_path(blocked, timeout=PROCESS_TIMEOUT_SECONDS)',
            'if applier.poll() is not None:',
            'release.write_text("release\\n", encoding="ascii")',
        ]
        positions = []
        for marker in required:
            with self.subTest(marker=marker):
                self.assertEqual(source.count(marker), 1)
                positions.append(source.index(marker))
        self.assertLess(positions[0], positions[1])
        self.assertLess(positions[2], positions[3])
        self.assertLess(positions[3], positions[4])
        self.assertNotIn("await asyncio.sleep(0.4)", source)
        self.assertNotIn("applyWaitedForImporter", source)

    def test_postgres_reference_reader_is_project_scoped(self):
        root = Path(__file__).resolve().parents[3]
        source = (root / "scripts" / "run_m1_01_orphan_cleanup_gate.py").read_text(encoding="utf-8")
        self.assertIn("def __init__(self, admin_dsn: str, workspace_id: str, client_id: str):", source)
        self.assertIn("WHERE workspace_id = $1 AND client_id = $2", source)
        self.assertIn("self._workspace_id,", source)
        self.assertIn("self._client_id,", source)
        self.assertIn("state[\"scope\"][\"workspaceId\"]", source)

    def test_runtime_job_places_cleanup_between_backup_and_connection_loss(self):
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
        markers = [
            "run_m1_01_backup_restore_gate.py",
            "run_m1_01_orphan_cleanup_gate.py --work-root",
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
