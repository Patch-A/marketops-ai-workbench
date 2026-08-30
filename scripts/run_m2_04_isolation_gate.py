"""Run the M2-04 synthetic engineering isolation evidence.

This gate deliberately reuses the M2-03 PostgreSQL suite: it establishes
authorization and isolation behavior, not planning usefulness.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_m2_03_postgres_gate.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise RuntimeError("M2-04 isolation evidence failed")
    source_evidence = json.loads(completed.stdout.strip())
    print(
        json.dumps(
            {
                "taskId": "M2-04",
                "evidenceClass": "synthetic engineering isolation",
                "sourceTaskId": source_evidence["taskId"],
                "runtimeTestExitCode": source_evidence["runtimeTestExitCode"],
                "claimBoundary": (
                    "This proves only the bounded authorization and isolation "
                    "checks. It is not usefulness, demand, ROI, time-savings, "
                    "repeat-use, or payment evidence."
                ),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
