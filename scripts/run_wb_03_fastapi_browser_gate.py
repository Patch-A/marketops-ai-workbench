#!/usr/bin/env python3
"""Run the WB-03 browser gate against a real FastAPI process."""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]


def write_server(module_path: Path, object_root: Path, token: str) -> None:
    module_path.write_text(dedent(f'''\
        from pathlib import Path
        from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
        from apps.api.marketops_import.service import ScopeContext
        from apps.api.marketops_geo import GeoSnapshotService

        scope = ScopeContext({str(uuid4())!r}, {str(uuid4())!r}, {str(uuid4())!r}, {str(uuid4())!r})

        class EmptyProjectReader:
            async def list_projects(self, scope, *, limit):
                return []

        app = create_app(
            authenticator=StaticBearerAuthenticator({token!r}, scope, basic_username="marketops"),
            project_reader=EmptyProjectReader(),
            geo_service=GeoSnapshotService(Path({str(object_root)!r})),
            static_root=Path({str(ROOT)!r}),
        )
    '''), encoding='utf-8')


def wait_for_server(url: str, username: str, token: str) -> None:
    auth = base64.b64encode(f"{username}:{token}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    raise RuntimeError("FastAPI browser runtime did not become ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    parser.add_argument("--node", default="node")
    parser.add_argument("--port", type=int, default=8878)
    args = parser.parse_args()
    token = secrets.token_urlsafe(24)
    username = "marketops"
    with tempfile.TemporaryDirectory(prefix="wb03-fastapi-") as temp:
        temp_path = Path(temp)
        module_path = temp_path / "server_app.py"
        object_root = temp_path / "objects"
        write_server(module_path, object_root, token)
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        server_log_path = temp_path / "uvicorn.log"
        server_log = server_log_path.open("w", encoding="utf-8")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server_app:app", "--host", "127.0.0.1", "--port", str(args.port), "--log-level", "warning"],
            cwd=temp_path, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=server_log, text=True,
        )
        try:
            base_url = f"http://127.0.0.1:{args.port}"
            wait_for_server(base_url + "/", username, token)
            result = subprocess.run(
                [args.node, str(ROOT / "scripts" / "run_wb_03_browser_gate.mjs"), "--browser", args.browser, "--profile", str(temp_path / "chrome-profile"), "--base-url", base_url, "--username", username, "--token", token],
                cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False,
            )
            if result.returncode:
                server_log.flush()
                details = server_log_path.read_text(encoding="utf-8", errors="replace").strip()
                message = (result.stderr or "").strip() or "WB-03 browser gate failed"
                raise RuntimeError(f"{message}; FastAPI stderr: {details[-2000:]}")
            print(result.stdout, end="")
            return 0
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            server_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
