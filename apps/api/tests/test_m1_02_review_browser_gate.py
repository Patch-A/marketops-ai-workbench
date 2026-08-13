from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class ReviewBrowserGateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[3]
        cls.script = cls.root / "scripts" / "run_m1_02_review_browser_gate.mjs"
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
        self.assertEqual(completed.stdout.strip(), "M1-02 browser gate contract passed")

    def test_gate_covers_review_failures_and_boundaries(self):
        required = (
            "sameKeyReplayObserved",
            "conflictReconciledByGet",
            "uncertainDecisionReconciledByGet",
            "historyIsReadOnly",
            "requestBoundaryPassed",
            "uncertain: true",
            "REVIEW_CONFLICT",
            "expectedReviewVersion",
            "sourceCitation",
            "reviewVersionSelect",
            "Emulation.setDeviceMetricsOverride",
            "noConsoleFailures",
            "noExternalRequests",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_gate_uses_only_synthetic_files_and_server_contract(self):
        self.assertNotIn("MARKETOPS_BROWSER_TOKEN", self.source)
        self.assertNotIn("proposalFile", self.source)
        self.assertNotIn("DOM.setFileInputFiles", self.source)
        self.assertNotIn("validation/fixtures", self.source)
        self.assertNotIn("customer", self.source.lower())
        self.assertIn("M1-02 Synthetic Review", self.source)


if __name__ == "__main__":
    unittest.main()
