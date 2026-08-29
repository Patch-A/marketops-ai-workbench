"""Scoped content Brief and asset task persistence for WB-04."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

class ContentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

class ContentScope:
    def __init__(self, organization_id: str, workspace_id: str, client_id: str, actor_id: str):
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.client_id = client_id
        self.actor_id = actor_id

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _uuid(value: Any, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ContentError("INVALID_INPUT", f"{field} must be a UUID") from exc

def _text(value: Any, field: str, maximum: int, optional: bool = False) -> str:
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ContentError("INVALID_INPUT", f"{field} must be text")
    value = value.strip()
    if (not value and not optional) or len(value) > maximum:
        raise ContentError("INVALID_INPUT", f"{field} is outside its allowed length")
    return value

def _scope(scope: ContentScope) -> ContentScope:
    return ContentScope(*(_uuid(value, field) for value, field in zip(
        (scope.organization_id, scope.workspace_id, scope.client_id, scope.actor_id),
        ("organizationId", "workspaceId", "clientId", "actorId"),
    )))

def _match(item: Mapping[str, Any], scope: ContentScope) -> bool:
    return all(item[key] == value for key, value in (
        ("organizationId", scope.organization_id), ("workspaceId", scope.workspace_id),
        ("clientId", scope.client_id), ("createdBy", scope.actor_id),
    ))

class ContentAssetService:
    def __init__(self, root: Path, *, id_factory=uuid4, clock=_now):
        self._path = Path(root) / "content-workbench" / "records.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._id_factory = id_factory
        self._clock = clock
        self._lock = asyncio.Lock()

    async def list_briefs(self, scope: ContentScope) -> list[dict[str, Any]]:
        scope = _scope(scope)
        async with self._lock:
            records = self._read()
        return [self._public_brief(item) for item in records["briefs"] if _match(item, scope)]

    async def create_brief(self, scope: ContentScope, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        required = {"topic", "channel", "format", "audience"}
        allowed = required | {"briefId", "expectedVersion"}
        if set(payload) - allowed or not required.issubset(payload):
            raise ContentError("INVALID_INPUT", "content Brief fields are incomplete or unknown")
        topic = _text(payload["topic"], "topic", 200)
        channel = _text(payload["channel"], "channel", 100)
        content_format = _text(payload["format"], "format", 100)
        audience = _text(payload.get("audience"), "audience", 2000, optional=True)
        async with self._lock:
            records = self._read()
            if payload.get("briefId") is None:
                item = {
                    "briefId": self._new_id(), "organizationId": scope.organization_id,
                    "workspaceId": scope.workspace_id, "clientId": scope.client_id,
                    "createdBy": scope.actor_id, "createdAt": self._clock(),
                    "updatedAt": self._clock(), "version": 1, "status": "draft",
                    "topic": topic, "channel": channel, "format": content_format, "audience": audience,
                }
                records["briefs"].append(item)
            else:
                identifier = _uuid(payload["briefId"], "briefId")
                index = next((i for i, entry in enumerate(records["briefs"]) if entry["briefId"] == identifier and _match(entry, scope)), None)
                if index is None:
                    raise ContentError("BRIEF_NOT_FOUND", "content Brief was not found")
                current = records["briefs"][index]
                expected = payload.get("expectedVersion")
                if isinstance(expected, bool) or not isinstance(expected, int) or expected != current["version"]:
                    raise ContentError("BRIEF_CONFLICT", "content Brief changed and must be refreshed")
                item = {**current, "version": current["version"] + 1, "status": "draft", "topic": topic, "channel": channel, "format": content_format, "audience": audience, "updatedAt": self._clock()}
                records["briefs"][index] = item
            self._write(records)
            return self._public_brief(item)

    async def approve_brief(self, scope: ContentScope, brief_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        if set(payload) != {"expectedVersion"} or isinstance(payload["expectedVersion"], bool) or not isinstance(payload["expectedVersion"], int) or payload["expectedVersion"] < 1:
            raise ContentError("INVALID_INPUT", "expectedVersion is required")
        identifier = _uuid(brief_id, "briefId")
        async with self._lock:
            records = self._read()
            index = next((i for i, entry in enumerate(records["briefs"]) if entry["briefId"] == identifier and _match(entry, scope)), None)
            if index is None:
                raise ContentError("BRIEF_NOT_FOUND", "content Brief was not found")
            current = records["briefs"][index]
            if current["version"] != payload["expectedVersion"]:
                raise ContentError("BRIEF_CONFLICT", "content Brief changed and must be refreshed")
            current = {**current, "status": "approved", "approvedAt": self._clock(), "updatedAt": self._clock()}
            records["briefs"][index] = current
            self._write(records)
            return self._public_brief(current)

    async def list_assets(self, scope: ContentScope) -> list[dict[str, Any]]:
        scope = _scope(scope)
        async with self._lock:
            records = self._read()
        return [self._public_asset(item) for item in records["assets"] if _match(item, scope)]

    async def create_asset(self, scope: ContentScope, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        required = {"briefId", "title", "channel", "format", "assetType"}
        allowed = required | {"prompt"}
        if set(payload) - allowed or not required.issubset(payload):
            raise ContentError("INVALID_INPUT", "asset fields are incomplete or unknown")
        brief_id = _uuid(payload["briefId"], "briefId")
        asset_type = payload["assetType"]
        if asset_type not in {"content", "image"}:
            raise ContentError("INVALID_INPUT", "assetType is unsupported")
        async with self._lock:
            records = self._read()
            brief = next((entry for entry in records["briefs"] if entry["briefId"] == brief_id and _match(entry, scope)), None)
            if brief is None:
                raise ContentError("BRIEF_NOT_FOUND", "content Brief was not found")
            if brief["status"] != "approved":
                raise ContentError("BRIEF_NOT_APPROVED", "approve the current Brief before creating an asset")
            prompt = _text(payload.get("prompt"), "prompt", 2000, optional=True)
            if asset_type == "image" and not prompt:
                raise ContentError("INVALID_INPUT", "image assets require a prompt")
            item = {
                "assetId": self._new_id(), "briefId": brief_id, "organizationId": scope.organization_id,
                "workspaceId": scope.workspace_id, "clientId": scope.client_id, "createdBy": scope.actor_id,
                "createdAt": self._clock(), "updatedAt": self._clock(), "version": 1,
                "title": _text(payload["title"], "title", 300), "channel": _text(payload["channel"], "channel", 100),
                "format": _text(payload["format"], "format", 100), "assetType": asset_type, "prompt": prompt,
                "status": "needs_authorization" if asset_type == "image" else "draft",
            }
            records["assets"].append(item)
            self._write(records)
            return self._public_asset(item)

    async def update_asset(self, scope: ContentScope, asset_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        if set(payload) != {"expectedVersion", "status"} or isinstance(payload["expectedVersion"], bool) or not isinstance(payload["expectedVersion"], int) or payload["expectedVersion"] < 1 or payload["status"] not in {"draft", "needs_authorization", "queued", "ready", "failed"}:
            raise ContentError("INVALID_INPUT", "asset update is invalid")
        identifier = _uuid(asset_id, "assetId")
        async with self._lock:
            records = self._read()
            index = next((i for i, entry in enumerate(records["assets"]) if entry["assetId"] == identifier and _match(entry, scope)), None)
            if index is None:
                raise ContentError("ASSET_NOT_FOUND", "asset was not found")
            current = records["assets"][index]
            if current["version"] != payload["expectedVersion"]:
                raise ContentError("ASSET_CONFLICT", "asset changed and must be refreshed")
            item = {**current, "status": payload["status"], "version": current["version"] + 1, "updatedAt": self._clock()}
            records["assets"][index] = item
            self._write(records)
            return self._public_asset(item)

    def _new_id(self) -> str:
        return _uuid(str(self._id_factory()), "id")

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self._path.exists():
            return {"briefs": [], "assets": []}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping):
                raise ValueError
            return {name: list(value.get(name, [])) for name in ("briefs", "assets")}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ContentError("CONTENT_STORE_FAILED", "content store is unreadable") from exc

    def _write(self, records: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        handle, name = tempfile.mkstemp(prefix="content-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"schemaVersion": 1, **{key: list(value) for key, value in records.items()}}, stream, ensure_ascii=False, sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self._path)
        except OSError as exc:
            try: os.unlink(name)
            except OSError: pass
            raise ContentError("CONTENT_STORE_FAILED", "content store could not be written") from exc

    @staticmethod
    def _public_brief(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item.get(key) for key in ("briefId", "createdAt", "updatedAt", "version", "topic", "channel", "format", "audience", "status", "approvedAt")}

    @staticmethod
    def _public_asset(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item.get(key) for key in ("assetId", "briefId", "createdAt", "updatedAt", "version", "title", "channel", "format", "assetType", "prompt", "status")}
