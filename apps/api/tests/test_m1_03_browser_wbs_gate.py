from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class WbsBrowserGateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[3]
        cls.script = cls.root / "scripts" / "run_m1_03_browser_wbs_gate.mjs"
        cls.source = cls.script.read_text(encoding="utf-8")

    def test_gate_self_test_passes(self):
        completed = subprocess.run(
            ["node", str(self.script), "--self-test"],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "M1-03 browser WBS gate contract passed")

    def test_gate_covers_wbs_schedule_and_boundaries(self):
        for marker in (
            "wbsCreated", "wbsReplayStable", "taskEditSent", "PLAN_CONFLICT",
            "historicalPlanReadOnly", "scheduleCalculated", "scheduleDigestRendered",
            "expectedPlanVersion", "plannedStart", "plannedFinish", "hardDeadline",
            "approvedBufferWorkdays", "isLocked", "predecessors",
            "Emulation.setDeviceMetricsOverride",
            "blockedApprovalPrevented", "approvalReconciled", "approvalRendered",
            "historicalApprovalReadOnly", "approvalRequestBoundary",
            "differentSnapshotApprovalRejected", "approvalReadFailurePreservesPlan",
            "versionChronologyLabels", "PLAN_ALREADY_APPROVED",
            "scheduleSnapshotId", "Ready for synthetic execution",
            "noConsoleFailures", "noExternalRequests", "scope|actor|digest|timestamp",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_gate_uses_synthetic_server_only(self):
        self.assertNotIn("MARKETOPS_BROWSER_TOKEN", self.source)
        self.assertNotIn("proposalFile", self.source)
        self.assertNotIn("validation/fixtures", self.source)
        self.assertIn("Synthetic WBS Browser Gate", self.source)


if __name__ == "__main__":
    unittest.main()
