#!/usr/bin/env python3
"""Exercise the WP5D browser-to-PostgreSQL import and refresh path."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.marketops_import.service import ScopeContext, StoredObject  # noqa: E402
from apps.api.marketops_import.storage import LocalObjectStore  # noqa: E402
from scripts.run_m1_01_restart_recovery_gate import (  # noqa: E402
    cleanup_scope,
    fixture_paths,
    identifier,
    required_environment,
    seed_scope,
    write_json,
)


BROWSER_RESULT_KEYS = {
    "authChallengeObserved",
    "importPostCount",
    "detailGetAfterCreate",
    "refreshDidNotRepeatPost",
    "localStorageIgnored",
    "indexedDbIgnored",
    "networkFailureDidNotFallback",
    "retryRecoveredFromServer",
    "rootRecoveredLatestProject",
    "noExternalRequests",
    "noConsoleFailures",
    "credentialAbsentFromBrowserState",
    "viewportResults",
}
EVIDENCE_KEYS = {
    "schemaVersion",
    "taskId",
    "workPackage",
    "generatedAt",
    "browser",
    "serverFacts",
    "claimBoundary",
}
MAX_SAFE_FAILURE_LENGTH = 800


def safe_browser_failure_message(
    stderr: str | None, *, sensitive_values: tuple[str, ...]
) -> str:
    """Return a bounded diagnostic from the controlled Node browser gate."""
    message = (stderr or "").strip()
    if not message:
        return "browser gate returned no diagnostic"

    for value in sorted(
        {item for item in sensitive_values if item}, key=len, reverse=True
    ):
        message = message.replace(value, "[redacted]")
        message = message.replace(value.replace("\\", "/"), "[redacted]")

    message = re.sub(
        r"(?i)postgres(?:ql)?://[^\s]+", "[redacted-dsn]", message
    )
    message = re.sub(
        r"(?i)\b(?:authorization|password|token)\s*[:=]\s*[^\s]+",
        "[redacted-credential]",
        message,
    )
    message = " ".join(message.split())
    if len(message) > MAX_SAFE_FAILURE_LENGTH:
        message = message[: MAX_SAFE_FAILURE_LENGTH - 3] + "..."
    return message


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def validate_browser_result(value: dict[str, Any]) -> None:
    if set(value) != BROWSER_RESULT_KEYS:
        raise RuntimeError("browser result fields do not match the gate contract")
    for key in BROWSER_RESULT_KEYS - {"viewportResults", "importPostCount"}:
        if value[key] is not True:
            raise RuntimeError(f"browser result did not pass: {key}")
    if value["importPostCount"] != 1:
        raise RuntimeError("browser performed an unexpected import count")
    viewports = value["viewportResults"]
    if not isinstance(viewports, dict) or set(viewports) != {"375", "768", "1024", "1440"}:
        raise RuntimeError("browser viewport result set is incomplete")
    if any(result is not True for result in viewports.values()):
        raise RuntimeError("browser viewport result failed")


def validate_evidence(value: dict[str, Any]) -> None:
    if set(value) != EVIDENCE_KEYS:
        raise RuntimeError("browser evidence fields do not match the public contract")
    validate_browser_result(value["browser"])
    expected_server = {
        "projectCount",
        "artifactCount",
        "artifactVersionCount",
        "auditEventCount",
        "approvedProposalVersion",
        "allObjectHashesVerified",
    }
    server = value.get("serverFacts")
    if not isinstance(server, dict) or set(server) != expected_server:
        raise RuntimeError("browser evidence server facts are incomplete")
    if server != {
        "projectCount": 1,
        "artifactCount": 2,
        "artifactVersionCount": 2,
        "auditEventCount": 1,
        "approvedProposalVersion": 3,
        "allObjectHashesVerified": True,
    }:
        raise RuntimeError("browser evidence server facts failed")
    serialized = json.dumps(value, sort_keys=True)
    forbidden = (
        "postgresql://",
        "MARKETOPS_DEPLOYMENT_TOKEN",
        str(ROOT.resolve()),
    )
    if any(item in serialized for item in forbidden):
        raise RuntimeError("browser evidence contains a sensitive or host-local value")


def wait_for_server(base_url: str, username: str, token: str) -> None:
    authorization = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        request = urllib.request.Request(
            base_url + "/",
            headers={"Authorization": "Basic " + authorization},
        )
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    raise RuntimeError("FastAPI browser runtime did not become ready")


async def verify_server_facts(
    asyncpg: Any,
    admin_dsn: str,
    scope: ScopeContext,
    object_root: Path,
) -> dict[str, Any]:
    connection = await asyncpg.connect(admin_dsn)
    try:
        project = await connection.fetchrow(
            """
            SELECT id, approved_proposal_number
            FROM marketops.projects
            WHERE organization_id = $1 AND workspace_id = $2 AND client_id = $3
            """,
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
        )
        if project is None:
            raise RuntimeError("browser import did not create a server project")
        project_id = str(project["id"])
        counts = {}
        for label, table in (
            ("projectCount", "projects"),
            ("artifactCount", "artifacts"),
            ("artifactVersionCount", "artifact_versions"),
            ("auditEventCount", "audit_events"),
        ):
            project_column = "id" if table == "projects" else "project_id"
            counts[label] = int(
                await connection.fetchval(
                    f"""
                    SELECT count(*) FROM marketops.{table}
                    WHERE organization_id = $1 AND {project_column} = $2
                    """,
                    scope.organization_id,
                    project_id,
                )
            )
        versions = await connection.fetch(
            """
            SELECT artifact.kind, version.storage_key, version.byte_size,
                   pg_catalog.encode(version.sha256, 'hex') AS sha256
            FROM marketops.artifacts AS artifact
            JOIN marketops.artifact_versions AS version
              ON version.organization_id = artifact.organization_id
             AND version.workspace_id = artifact.workspace_id
             AND version.client_id = artifact.client_id
             AND version.project_id = artifact.project_id
             AND version.artifact_id = artifact.id
            WHERE artifact.organization_id = $1 AND artifact.project_id = $2
            ORDER BY artifact.kind
            """,
            scope.organization_id,
            project_id,
        )
    finally:
        await connection.close()

    store = LocalObjectStore(object_root)
    for row in versions:
        stored = StoredObject(
            storage_key=str(row["storage_key"]),
            size_bytes=int(row["byte_size"]),
            sha256=str(row["sha256"]),
        )
        kind = PurePosixPath(stored.storage_key).parts[-2]
        await store.verify_immutable(kind=kind, stored=stored)
    return {
        **counts,
        "approvedProposalVersion": int(project["approved_proposal_number"]),
        "allObjectHashesVerified": len(versions) == 2,
    }


def raise_cleanup_failure(
    primary_failure: BaseException | None, cleanup_failure: BaseException | None
) -> None:
    if primary_failure is None and cleanup_failure is not None:
        raise RuntimeError("browser gate cleanup failed") from cleanup_failure


async def run_cleanup_steps(primary_failure: BaseException | None, *cleanup_steps):
    failures = []
    for cleanup_step in cleanup_steps:
        try:
            await cleanup_step()
        except BaseException as error:
            failures.append(error)
    if len(failures) > 1:
        cleanup_failure = ExceptionGroup("browser gate cleanup failures", failures)
    else:
        cleanup_failure = failures[0] if failures else None
    raise_cleanup_failure(primary_failure, cleanup_failure)
    return tuple(failures)


async def run(browser: Path, work_root: Path, output: Path, node: str) -> None:
    primary_failure = None
    try:
        import asyncpg
    except ImportError as error:  # pragma: no cover - runtime gate only
        raise RuntimeError("asyncpg is required for the browser cutover gate") from error

    admin_dsn = required_environment("MARKETOPS_TEST_ADMIN_DATABASE_URL")
    app_dsn = required_environment("MARKETOPS_TEST_DATABASE_URL")
    scope = ScopeContext(identifier(), identifier(), identifier(), identifier())
    username = "marketops"
    token = secrets.token_urlsafe(32)
    object_root = work_root / "objects"
    profile = work_root / "chromium-profile"
    source, proposal = fixture_paths(work_root)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    environment.update(
        {
            "MARKETOPS_DATABASE_URL": app_dsn,
            "MARKETOPS_OBJECT_ROOT": str(object_root),
            "MARKETOPS_DEPLOYMENT_TOKEN": token,
            "MARKETOPS_DEPLOYMENT_USERNAME": username,
            "MARKETOPS_ORGANIZATION_ID": scope.organization_id,
            "MARKETOPS_WORKSPACE_ID": scope.workspace_id,
            "MARKETOPS_CLIENT_ID": scope.client_id,
            "MARKETOPS_ACTOR_ID": scope.actor_id,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    browser_environment = environment.copy()
    browser_environment.update(
        {
            "MARKETOPS_BROWSER_USERNAME": username,
            "MARKETOPS_BROWSER_TOKEN": token,
        }
    )

    work_root.mkdir(parents=True, exist_ok=True)
    await seed_scope(asyncpg, admin_dsn, scope)
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "apps.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    async def cleanup_server() -> None:
        server.terminate()
        try:
            await asyncio.to_thread(server.wait, 5)
        except subprocess.TimeoutExpired:
            server.kill()
            await asyncio.to_thread(server.wait)

    async def cleanup_database() -> None:
        await cleanup_scope(asyncpg, admin_dsn, scope.organization_id)

    try:
        await asyncio.to_thread(wait_for_server, base_url, username, token)
        completed = await asyncio.to_thread(
            subprocess.run,
            [
                node,
                str(ROOT / "scripts" / "run_m1_01_browser_flow.mjs"),
                "--browser",
                str(browser),
                "--base-url",
                base_url,
                "--source",
                str(source),
                "--proposal",
                str(proposal),
                "--profile",
                str(profile),
            ],
            cwd=ROOT,
            env=browser_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            diagnostic = safe_browser_failure_message(
                completed.stderr,
                sensitive_values=(
                    token,
                    admin_dsn,
                    app_dsn,
                    username,
                    str(ROOT.resolve()),
                    str(browser.resolve()),
                    str(work_root.resolve()),
                    str(object_root.resolve()),
                    str(profile.resolve()),
                    str(source.resolve()),
                    str(proposal.resolve()),
                ),
            )
            raise RuntimeError(f"real Chromium browser flow failed: {diagnostic}")
        try:
            browser_result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("browser flow returned invalid evidence") from error
        validate_browser_result(browser_result)
        server_facts = await verify_server_facts(
            asyncpg, admin_dsn, scope, object_root
        )
        evidence = {
            "schemaVersion": 1,
            "taskId": "M1-01",
            "workPackage": "WP5D-browser-server-api-cutover",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "browser": browser_result,
            "serverFacts": server_facts,
            "claimBoundary": (
                "This synthetic Linux CI result covers one private-deployment actor, "
                "FastAPI, PostgreSQL 18.4, the local immutable-object adapter, and one "
                "Chromium browser flow. It does not establish production authentication, "
                "actor membership, multi-user authorization, production security, broad "
                "browser support, demand, ROI, time savings, repeat use, or payment."
            ),
        }
        validate_evidence(evidence)
        write_json(output, evidence)
        print(output.read_text(encoding="utf-8"), end="")
    except BaseException as error:
        primary_failure = error
        raise
    finally:
        await run_cleanup_steps(primary_failure, cleanup_server, cleanup_database)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node", default="node")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    asyncio.run(run(args.browser, args.work_root, args.output, args.node))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
