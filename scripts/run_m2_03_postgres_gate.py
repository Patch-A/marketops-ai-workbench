"""Provision the M2 PostgreSQL baseline and run M2-03 approval runtime evidence."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_m2_02_postgres_gate import provision


def main() -> int:
    evidence = asyncio.run(provision())
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "apps.api.tests.postgres.test_m2_03_approval_runtime",
            "-v",
        ],
        cwd=ROOT,
        check=False,
    )
    evidence["taskId"] = "M2-03"
    evidence["runtimeTestExitCode"] = completed.returncode
    if completed.returncode != 0:
        raise RuntimeError("M2-03 PostgreSQL runtime tests failed")
    print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
