#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, os, secrets, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path
from textwrap import dedent
from uuid import uuid4
ROOT = Path(__file__).resolve().parents[1]
def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument('--browser', default=r'C:\Program Files\Google\Chrome\Application\chrome.exe'); parser.add_argument('--node', default='node'); parser.add_argument('--port', type=int, default=8898); args = parser.parse_args(); token = secrets.token_urlsafe(24); username = 'marketops'
    with tempfile.TemporaryDirectory(prefix='wb04-fastapi-') as temp:
        path = Path(temp); module = path / 'server_app.py'; root = path / 'objects'; scope = [str(uuid4()) for _ in range(4)]
        vault = path / 'synthetic-vault'; vault.mkdir(); body_marker = 'synthetic-private-body-marker'
        (vault / 'Browser gate.md').write_text('# 浏览器门禁知识\n' + body_marker, encoding='utf-8')
        module.write_text(dedent(f'''\
            from pathlib import Path
            from apps.api.marketops_import.http import StaticBearerAuthenticator, create_app
            from apps.api.marketops_import.service import ScopeContext
            from apps.api.marketops_content import ContentAssetService
            from apps.api.marketops_calendar import CalendarItemService
            from apps.api.marketops_obsidian import ObsidianReadOnlyService
            scope = ScopeContext({scope[0]!r}, {scope[1]!r}, {scope[2]!r}, {scope[3]!r})
            class EmptyProjectReader:
                async def list_projects(self, scope, *, limit): return []
            app = create_app(authenticator=StaticBearerAuthenticator({token!r}, scope, basic_username='marketops'), project_reader=EmptyProjectReader(), content_service=ContentAssetService(Path({str(root)!r})), calendar_service=CalendarItemService(Path({str(root)!r})), obsidian_service=ObsidianReadOnlyService(Path({str(root)!r}), vault_root=Path({str(vault)!r})), static_root=Path({str(ROOT)!r}))
        '''), encoding='utf-8')
        env = os.environ.copy(); env['PYTHONDONTWRITEBYTECODE'] = '1'; env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', ''); log = (path / 'uvicorn.log').open('w', encoding='utf-8'); server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server_app:app', '--host', '127.0.0.1', '--port', str(args.port), '--log-level', 'warning'], cwd=path, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, text=True)
        try:
            base = 'http://127.0.0.1:' + str(args.port); auth = base64.b64encode((username + ':' + token).encode()).decode(); req = urllib.request.Request(base + '/', headers={'Authorization': 'Basic ' + auth}); deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(req, timeout=1) as response:
                        if response.status == 200: break
                except (urllib.error.URLError, TimeoutError, ConnectionError): time.sleep(0.1)
            else: raise RuntimeError('FastAPI browser runtime did not become ready')
            result = subprocess.run([args.node, str(ROOT / 'scripts' / 'run_wb_04_browser_gate.mjs'), '--browser', args.browser, '--profile', str(path / 'chrome-profile'), '--base-url', base, '--username', username, '--token', token, '--vault-path', str(vault), '--body-marker', body_marker], cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120, check=False)
            if result.returncode: raise RuntimeError((result.stderr or 'WB-04 browser gate failed') + '; FastAPI stderr: ' + (path / 'uvicorn.log').read_text(encoding='utf-8', errors='replace')[-2000:])
            print(result.stdout, end=''); return 0
        finally:
            server.terminate()
            try: server.wait(timeout=5)
            except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
            log.close()
if __name__ == '__main__': raise SystemExit(main())
