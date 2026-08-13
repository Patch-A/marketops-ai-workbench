from __future__ import annotations

import asyncio
import subprocess
import unittest
from pathlib import Path

from scripts.run_m1_01_browser_cutover_gate import (
    BROWSER_RESULT_KEYS,
    EVIDENCE_KEYS,
    MAX_SAFE_FAILURE_LENGTH,
    build_parser,
    raise_cleanup_failure,
    run_cleanup_steps,
    safe_browser_failure_message,
    start_server_after_scope_seed,
    validate_browser_result,
    validate_evidence,
)


class BrowserCutoverGateContractTests(unittest.TestCase):
    @staticmethod
    def browser_result():
        value = {key: True for key in BROWSER_RESULT_KEYS}
        value["importPostCount"] = 1
        value["viewportResults"] = {
            "375": True,
            "768": True,
            "1024": True,
            "1440": True,
        }
        return value

    @classmethod
    def evidence(cls):
        return {
            "schemaVersion": 1,
            "taskId": "M1-01",
            "workPackage": "WP5D-browser-server-api-cutover",
            "generatedAt": "2026-08-10T00:00:00+00:00",
            "browser": cls.browser_result(),
            "serverFacts": {
                "projectCount": 1,
                "artifactCount": 2,
                "artifactVersionCount": 2,
                "auditEventCount": 1,
                "approvedProposalVersion": 3,
                "allObjectHashesVerified": True,
            },
            "claimBoundary": "bounded synthetic Chromium result",
        }

    def test_cli_requires_explicit_browser_and_temporary_paths(self):
        args = build_parser().parse_args(
            [
                "--browser",
                "chrome",
                "--work-root",
                "runner-root",
                "--output",
                "evidence.json",
            ]
        )
        self.assertEqual(args.browser, Path("chrome"))
        self.assertEqual(args.work_root, Path("runner-root"))
        self.assertEqual(args.output, Path("evidence.json"))
        self.assertEqual(args.node, "node")

    def test_evidence_is_exact_bounded_and_non_sensitive(self):
        evidence = self.evidence()
        self.assertEqual(set(evidence), EVIDENCE_KEYS)
        validate_evidence(evidence)

        changed = self.evidence()
        changed["unexpected"] = True
        with self.assertRaises(RuntimeError):
            validate_evidence(changed)

        changed = self.evidence()
        changed["browser"].pop("localStorageIgnored")
        with self.assertRaises(RuntimeError):
            validate_evidence(changed)

        changed = self.evidence()
        changed["serverFacts"]["projectCount"] = 2
        with self.assertRaises(RuntimeError):
            validate_evidence(changed)

        changed = self.evidence()
        changed["claimBoundary"] = "postgresql://user:secret@host/db"
        with self.assertRaises(RuntimeError):
            validate_evidence(changed)

    def test_browser_result_requires_all_viewports_and_one_post(self):
        result = self.browser_result()
        validate_browser_result(result)

        changed = self.browser_result()
        changed["importPostCount"] = 2
        with self.assertRaises(RuntimeError):
            validate_browser_result(changed)

        changed = self.browser_result()
        changed["viewportResults"].pop("375")
        with self.assertRaises(RuntimeError):
            validate_browser_result(changed)

    def test_browser_failure_diagnostic_is_bounded_and_redacted(self):
        token = "deployment-token-value"
        dsn = "postgresql://user:password@database.example/marketops"
        username = "marketops-user"
        local_path = "/home/runner/work/repository/chromium-profile"
        stderr = (
            "browser gate failed: token="
            + token
            + " dsn="
            + dsn
            + " user="
            + username
            + " path="
            + local_path
            + " "
            + ("failure-context " * 100)
        )

        diagnostic = safe_browser_failure_message(
            stderr,
            sensitive_values=(token, dsn, username, local_path),
        )

        for forbidden in (token, dsn, username, local_path, "password"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, diagnostic)
        self.assertLessEqual(len(diagnostic), MAX_SAFE_FAILURE_LENGTH)
        self.assertTrue(diagnostic.endswith("..."))

    def test_empty_browser_failure_diagnostic_stays_actionable(self):
        self.assertEqual(
            safe_browser_failure_message(None, sensitive_values=()),
            "browser gate returned no diagnostic",
        )

    def test_node_flow_uses_cdp_auth_and_exercises_fact_source_failures(self):
        root = Path(__file__).resolve().parents[3]
        source = (root / "scripts" / "run_m1_01_browser_flow.mjs").read_text(
            encoding="utf-8"
        )
        required = (
            "Fetch.authRequired",
            "ProvideCredentials",
            "MARKETOPS_BROWSER_TOKEN",
            "POST did not transition through a server detail GET",
            "localStorage.setItem('marketops.projects.v1'",
            "indexedDB.open('marketops-files-v1'",
            "Storage.clearDataForOrigin",
            "failNextProjectRead = true",
            "expectedNetworkFailureLogConsumed",
            "path === expected.path",
            "expectedNetworkFailureRequestId",
            "Network.loadingFailed",
            "net::ERR_CONNECTION_RESET",
            "matchesExpectedConnectionResetLog",
            "injected network failure was not bound to the expected request and error",
            "injected network failure did not target the server project read",
            "refresh repeated the import POST",
            "credentials appeared in the browser URL",
            "external executable or asset",
            "review-workbench.js",
            "browser emitted console/runtime failures:",
            "safePath(params.entry?.url || '', origin)",
            "maxRetries: 10",
            "if (!primaryFailure)",
            "browser profile cleanup failed:",
            "[375, 768, 1024, 1440]",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, source)
        self.assertNotIn("from 'playwright'", source)
        self.assertNotIn("from 'puppeteer'", source)
        self.assertNotIn("console.log(token", source)

    def test_connection_reset_allowlist_rejects_same_path_other_errors(self):
        root = Path(__file__).resolve().parents[3]
        completed = subprocess.run(
            [
                "node",
                str(root / "scripts" / "run_m1_01_browser_flow.mjs"),
                "--self-test-network-failure-matcher",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.strip(), "browser network failure matcher passed"
        )

    def test_cleanup_failure_does_not_replace_primary_failure(self):
        primary = ValueError("primary browser failure")
        cleanup = RuntimeError("cleanup failure")
        self.assertIsNone(raise_cleanup_failure(primary, cleanup))
        with self.assertRaisesRegex(RuntimeError, "browser gate cleanup failed") as raised:
            raise_cleanup_failure(None, cleanup)
        self.assertIs(raised.exception.__cause__, cleanup)

        root = Path(__file__).resolve().parents[3]
        source = (root / "scripts" / "run_m1_01_browser_cutover_gate.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("primary_failure = error", source)
        self.assertIn(
            "await run_cleanup_steps(primary_failure, cleanup_server, cleanup_database)",
            source,
        )

    def test_both_cleanup_steps_run_and_double_failure_preserves_primary(self):
        calls = []

        async def server_cleanup():
            calls.append("server")
            raise RuntimeError("server cleanup failed")

        async def database_cleanup():
            calls.append("database")
            raise RuntimeError("database cleanup failed")

        primary = ValueError("primary browser failure")
        failures = asyncio.run(
            run_cleanup_steps(primary, server_cleanup, database_cleanup)
        )
        self.assertEqual(calls, ["server", "database"])
        self.assertEqual(len(failures), 2)

        calls.clear()
        with self.assertRaisesRegex(RuntimeError, "browser gate cleanup failed") as raised:
            asyncio.run(run_cleanup_steps(None, server_cleanup, database_cleanup))
        self.assertEqual(calls, ["server", "database"])
        self.assertIsInstance(raised.exception.__cause__, ExceptionGroup)
        self.assertEqual(len(raised.exception.__cause__.exceptions), 2)

    def test_cancelled_cleanup_double_failure_preserves_primary(self):
        calls = []

        async def server_cleanup():
            calls.append("server")
            raise asyncio.CancelledError("server cleanup cancelled")

        async def database_cleanup():
            calls.append("database")
            raise RuntimeError("database cleanup failed")

        primary = ValueError("primary browser failure")
        failures = asyncio.run(
            run_cleanup_steps(primary, server_cleanup, database_cleanup)
        )
        self.assertEqual(calls, ["server", "database"])
        self.assertEqual(len(failures), 2)

        calls.clear()
        with self.assertRaisesRegex(RuntimeError, "browser gate cleanup failed") as raised:
            asyncio.run(run_cleanup_steps(None, server_cleanup, database_cleanup))
        self.assertEqual(calls, ["server", "database"])
        self.assertIsInstance(raised.exception.__cause__, BaseExceptionGroup)
        self.assertNotIsInstance(raised.exception.__cause__, ExceptionGroup)
        self.assertEqual(len(raised.exception.__cause__.exceptions), 2)

    def test_server_start_failure_cleans_seeded_scope_and_preserves_start_error(self):
        calls = []

        def start_server():
            calls.append("start")
            raise OSError("server start failed")

        async def database_cleanup():
            calls.append("database")
            raise asyncio.CancelledError("database cleanup cancelled")

        with self.assertRaisesRegex(OSError, "server start failed"):
            asyncio.run(
                start_server_after_scope_seed(start_server, database_cleanup)
            )
        self.assertEqual(calls, ["start", "database"])

    def test_runtime_job_pins_node_and_runs_browser_gate(self):
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
        runtime_job = workflow.split("  m1-01-runtime:", 1)[1]
        self.assertIn("node-version: 22", runtime_job)
        self.assertEqual(
            runtime_job.count("run_m1_01_browser_cutover_gate.py"), 1
        )
        self.assertEqual(
            runtime_job.count("m1-01-browser-cutover.json"), 3
        )


if __name__ == "__main__":
    unittest.main()
