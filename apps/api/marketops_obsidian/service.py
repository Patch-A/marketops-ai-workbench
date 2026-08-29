"""Read-only Obsidian vault index for the local private deployment."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID, uuid4


class ObsidianError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ObsidianScope:
    def __init__(self, organization_id: str, workspace_id: str, client_id: str, actor_id: str):
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.client_id = client_id
        self.actor_id = actor_id


def _uuid(value: Any, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise ObsidianError("INVALID_INPUT", f"{field} must be a UUID") from exc


def _scope(scope: ObsidianScope) -> ObsidianScope:
    values = (scope.organization_id, scope.workspace_id, scope.client_id, scope.actor_id)
    fields = ("organizationId", "workspaceId", "clientId", "actorId")
    return ObsidianScope(*(_uuid(value, field) for value, field in zip(values, fields)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ObsidianReadOnlyService:
    MAX_FILES = 2000
    MAX_FILE_BYTES = 2 * 1024 * 1024
    DEFAULT_VAULT_ROOT = Path(r"C:\Users\Patch\OneDrive\文档\Obsidian Vault")

    def __init__(self, root: Path, *, vault_root: Path | None = None, id_factory=uuid4, clock=_now):
        self._path = Path(root) / "obsidian" / "connections.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._vault_root = (vault_root or self.DEFAULT_VAULT_ROOT).resolve()
        self._id_factory = id_factory
        self._clock = clock
        self._lock = asyncio.Lock()

    async def get_connection(self, scope: ObsidianScope) -> dict[str, Any] | None:
        scope = _scope(scope)
        async with self._lock:
            records = self._read()
        item = next((value for value in records if self._matches(value, scope)), None)
        return self._public(item) if item else None

    async def connect(self, scope: ObsidianScope, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        if set(payload) != {"vaultPath", "relativePaths"} or not isinstance(payload["relativePaths"], list) or len(payload["relativePaths"]) > 200:
            raise ObsidianError("INVALID_INPUT", "vaultPath and relativePaths are required")
        try:
            vault = Path(str(payload["vaultPath"])).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ObsidianError("VAULT_NOT_FOUND", "Obsidian vault was not found") from exc
        if vault != self._vault_root:
            raise ObsidianError("VAULT_NOT_ALLOWED", "Only the configured local Obsidian vault is allowed")
        relative_paths: list[str] = []
        for value in payload["relativePaths"]:
            if not isinstance(value, str):
                raise ObsidianError("INVALID_INPUT", "relativePaths must contain text paths")
            normalized = value.strip().replace("\\", "/")
            if not normalized:
                raise ObsidianError("INVALID_INPUT", "relativePaths entries must not be empty")
            candidate = Path(normalized)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ObsidianError("INVALID_INPUT", "relativePaths must be safe relative paths")
            relative = candidate.as_posix().strip("/")
            if relative not in relative_paths:
                relative_paths.append(relative)
        async with self._lock:
            records = self._read()
            item = next((value for value in records if self._matches(value, scope)), None)
            if item is None:
                item = {"connectionId": str(self._id_factory()), "organizationId": scope.organization_id, "workspaceId": scope.workspace_id, "clientId": scope.client_id, "createdBy": scope.actor_id, "createdAt": self._clock()}
                records.append(item)
            item.update({"vaultPath": str(vault), "relativePaths": relative_paths, "status": "connected", "updatedAt": self._clock()})
            self._write(records)
            return self._public(item)

    async def list_notes(self, scope: ObsidianScope) -> dict[str, Any]:
        connection = await self.get_connection(scope)
        if not connection:
            raise ObsidianError("CONNECTION_NOT_FOUND", "Connect an Obsidian vault first")
        vault = Path(connection["vaultPath"]).resolve()
        notes: list[dict[str, Any]] = []
        indexed_paths: set[Path] = set()
        paths = connection["relativePaths"] or [""]
        for relative in paths:
            base = (vault / relative).resolve()
            if base != vault and vault not in base.parents:
                raise ObsidianError("PATH_ESCAPE", "Configured path escapes the vault")
            if not base.exists() or base.is_symlink():
                continue
            candidates = [base] if base.is_file() else list(base.rglob("*.md"))
            for path in candidates:
                if path.suffix.lower() != ".md" or path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved != vault and vault not in resolved.parents:
                    continue
                if resolved in indexed_paths:
                    continue
                if len(notes) >= self.MAX_FILES:
                    raise ObsidianError("NOTE_LIMIT_EXCEEDED", "Too many notes in selected scope")
                stat = resolved.stat()
                if stat.st_size > self.MAX_FILE_BYTES:
                    continue
                data = resolved.read_bytes()
                if len(data) > self.MAX_FILE_BYTES:
                    continue
                title = resolved.stem
                for line in data.decode("utf-8", errors="replace").splitlines():
                    if line.startswith("#"):
                        title = line.lstrip("#").strip() or title
                        break
                notes.append({"relativePath": resolved.relative_to(vault).as_posix(), "title": title, "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), "sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": stat.st_size})
                indexed_paths.add(resolved)
        notes.sort(key=lambda value: value["relativePath"])
        return {"connection": connection, "notes": notes, "readOnly": True, "syncedAt": self._clock()}

    @staticmethod
    def _matches(item: Mapping[str, Any], scope: ObsidianScope) -> bool:
        return all(item.get(key) == value for key, value in (("organizationId", scope.organization_id), ("workspaceId", scope.workspace_id), ("clientId", scope.client_id), ("createdBy", scope.actor_id)))

    @staticmethod
    def _public(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item.get(key) for key in ("connectionId", "vaultPath", "relativePaths", "status", "createdAt", "updatedAt")}

    def _read(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            return list(value) if isinstance(value, list) else []
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ObsidianError("OBSIDIAN_STORE_FAILED", "Obsidian connection store is unreadable") from exc

    def _write(self, records: list[dict[str, Any]]) -> None:
        handle, name = tempfile.mkstemp(prefix="obsidian-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(records, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._path)
        except OSError as exc:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise ObsidianError("OBSIDIAN_STORE_FAILED", "Obsidian connection could not be saved") from exc
