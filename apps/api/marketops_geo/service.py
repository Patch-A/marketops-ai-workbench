"""Bounded, user-supplied GEO observations for a private deployment.

This service deliberately does not fetch search engines or AI platforms. It
stores a stable query set, timestamped observations, citations, and proposed
content-gap tasks within the authenticated deployment scope.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import UUID, uuid4


class GeoError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class GeoScope:
    def __init__(self, organization_id: str, workspace_id: str, client_id: str, actor_id: str):
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.client_id = client_id
        self.actor_id = actor_id


_VISIBILITY = {"mentioned", "not_mentioned", "unclear"}
_PLATFORMS = {"Google Search", "Bing Search", "ChatGPT", "其他（人工记录）"}
_TEXT = re.compile(r"^\S[\s\S]{0,3999}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid(value: Any, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise GeoError("INVALID_INPUT", f"{field} must be a UUID") from exc


def _text(value: Any, field: str, maximum: int = 4000) -> str:
    if not isinstance(value, str):
        raise GeoError("INVALID_INPUT", f"{field} must be text")
    value = value.strip()
    if not value or len(value) > maximum:
        raise GeoError("INVALID_INPUT", f"{field} is outside its allowed length")
    return value


def _scope(scope: GeoScope) -> GeoScope:
    return GeoScope(*(_uuid(value, field) for value, field in zip(
        (scope.organization_id, scope.workspace_id, scope.client_id, scope.actor_id),
        ("organizationId", "workspaceId", "clientId", "actorId"),
    )))


def _match(item: Mapping[str, Any], scope: GeoScope) -> bool:
    return all(item[key] == value for key, value in (
        ("organizationId", scope.organization_id), ("workspaceId", scope.workspace_id),
        ("clientId", scope.client_id), ("createdBy", scope.actor_id),
    ))


def _citation(value: Any) -> str | None:
    if value in (None, ""):
        return None
    result = _text(value, "citation", 1000)
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise GeoError("INVALID_INPUT", "citation must be a public HTTP(S) URL")
    return result


class GeoSnapshotService:
    def __init__(self, root: Path, *, id_factory=uuid4, clock=_now):
        self._path = Path(root) / "geo-workbench" / "records.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._id_factory = id_factory
        self._clock = clock
        self._lock = asyncio.Lock()

    async def list_query_sets(self, scope: GeoScope) -> list[dict[str, Any]]:
        scope = _scope(scope)
        async with self._lock:
            records = self._read()
        return [self._public_query_set(item) for item in records["querySets"] if _match(item, scope)]

    async def create_query_set(self, scope: GeoScope, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        if set(payload) != {"product", "market", "language", "queries"}:
            raise GeoError("INVALID_INPUT", "query set fields are incomplete or unknown")
        product = _text(payload["product"], "product", 200)
        market = _text(payload["market"], "market", 120)
        language = _text(payload["language"], "language", 80)
        raw_queries = payload["queries"]
        if not isinstance(raw_queries, list) or not 1 <= len(raw_queries) <= 20 or any(not isinstance(item, str) for item in raw_queries):
            raise GeoError("INVALID_INPUT", "queries must contain between 1 and 20 text items")
        queries = []
        seen: set[str] = set()
        for text in raw_queries:
            value = _text(text, "query", 400)
            if value in seen:
                raise GeoError("INVALID_INPUT", "queries must not contain duplicates")
            seen.add(value)
            queries.append({"queryId": self._new_id(), "text": value})
        item = {
            "querySetId": self._new_id(), "organizationId": scope.organization_id,
            "workspaceId": scope.workspace_id, "clientId": scope.client_id,
            "createdBy": scope.actor_id, "createdAt": self._clock(), "version": 1,
            "product": product, "market": market, "language": language, "queries": queries,
        }
        async with self._lock:
            records = self._read()
            records["querySets"].append(item)
            self._write(records)
        return self._public_query_set(item)

    async def list_snapshots(self, scope: GeoScope, query_set_id: str) -> list[dict[str, Any]]:
        scope = _scope(scope)
        query_set = await self._find(scope, "querySets", query_set_id, "querySetId")
        async with self._lock:
            records = self._read()
        return [self._public_snapshot(item) for item in records["snapshots"] if item["querySetId"] == query_set["querySetId"] and _match(item, scope)]

    async def list_tasks(self, scope: GeoScope, query_set_id: str) -> list[dict[str, Any]]:
        scope = _scope(scope)
        query_set = await self._find(scope, "querySets", query_set_id, "querySetId")
        async with self._lock:
            records = self._read()
        return [self._public_task(item) for item in records["tasks"] if item["querySetId"] == query_set["querySetId"] and _match(item, scope)]

    async def create_snapshot(self, scope: GeoScope, query_set_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        query_set = await self._find(scope, "querySets", query_set_id, "querySetId")
        if set(payload) != {"platform", "queryId", "visibility", "observation", "observedAt", "citation"}:
            raise GeoError("INVALID_INPUT", "snapshot fields are incomplete or unknown")
        platform = _text(payload["platform"], "platform", 80)
        if platform not in _PLATFORMS:
            raise GeoError("INVALID_INPUT", "platform is unsupported")
        query_id = _uuid(payload["queryId"], "queryId")
        query = next((item for item in query_set["queries"] if item["queryId"] == query_id), None)
        if query is None:
            raise GeoError("GEO_NOT_FOUND", "query was not found in this query set")
        visibility = payload["visibility"]
        if visibility not in _VISIBILITY:
            raise GeoError("INVALID_INPUT", "visibility is unsupported")
        observed_at = _text(payload["observedAt"], "observedAt", 80)
        try:
            date.fromisoformat(observed_at)
        except ValueError as exc:
            raise GeoError("INVALID_INPUT", "observedAt must be an ISO date") from exc
        snapshot = {
            "snapshotId": self._new_id(), "querySetId": query_set["querySetId"],
            "organizationId": scope.organization_id, "workspaceId": scope.workspace_id,
            "clientId": scope.client_id, "createdBy": scope.actor_id,
            "createdAt": self._clock(), "platform": platform, "queryId": query_id,
            "queryText": query["text"], "visibility": visibility,
            "observation": _text(payload["observation"], "observation", 2000),
            "observedAt": observed_at, "citation": _citation(payload["citation"]),
        }
        task = None
        if visibility in {"not_mentioned", "unclear"}:
            task = {
                "taskId": self._new_id(), "snapshotId": snapshot["snapshotId"],
                "querySetId": query_set["querySetId"], "organizationId": scope.organization_id,
                "workspaceId": scope.workspace_id, "clientId": scope.client_id,
                "createdBy": scope.actor_id, "createdAt": self._clock(), "status": "needs_review",
                "title": f"补充“{query['text']}”相关内容与证据", "platform": platform,
                "observedAt": observed_at,
            }
        async with self._lock:
            records = self._read()
            records["snapshots"].append(snapshot)
            if task is not None:
                records["tasks"].append(task)
            self._write(records)
        return {"snapshot": self._public_snapshot(snapshot), "task": self._public_task(task) if task else None}

    async def _find(self, scope: GeoScope, collection: str, value: Any, key: str) -> dict[str, Any]:
        identifier = _uuid(value, key)
        async with self._lock:
            records = self._read()
        item = next((entry for entry in records[collection] if entry[key] == identifier and _match(entry, scope)), None)
        if item is None:
            raise GeoError("GEO_NOT_FOUND", "GEO item was not found")
        return item

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self._path.exists():
            return {"querySets": [], "snapshots": [], "tasks": []}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping):
                raise ValueError
            return {name: list(value.get(name, [])) for name in ("querySets", "snapshots", "tasks")}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise GeoError("GEO_STORE_FAILED", "GEO store is unreadable") from exc

    def _write(self, records: Mapping[str, list[dict[str, Any]]]) -> None:
        payload = {"schemaVersion": 1, **records}
        handle, name = tempfile.mkstemp(prefix="geo-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self._path)
        except OSError as exc:
            try: os.unlink(name)
            except OSError: pass
            raise GeoError("GEO_STORE_FAILED", "GEO store could not be written") from exc

    def _new_id(self) -> str:
        return _uuid(str(self._id_factory()), "id")

    @staticmethod
    def _public_query_set(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in ("querySetId", "createdAt", "version", "product", "market", "language", "queries")}

    @staticmethod
    def _public_snapshot(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in ("snapshotId", "querySetId", "createdAt", "platform", "queryId", "queryText", "visibility", "observation", "observedAt", "citation")}

    @staticmethod
    def _public_task(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in ("taskId", "snapshotId", "querySetId", "createdAt", "status", "title", "platform", "observedAt")}
