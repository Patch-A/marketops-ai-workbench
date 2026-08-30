"""Scoped, human-confirmed 7/30-day workbench calendar items."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

class CalendarError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

class CalendarScope:
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
        raise CalendarError("INVALID_INPUT", f"{field} must be a UUID") from exc

def _text(value: Any, field: str, maximum: int, optional: bool = False) -> str:
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise CalendarError("INVALID_INPUT", f"{field} must be text")
    value = value.strip()
    if (not value and not optional) or len(value) > maximum:
        raise CalendarError("INVALID_INPUT", f"{field} is outside its allowed length")
    return value

def _scope(scope: CalendarScope) -> CalendarScope:
    return CalendarScope(*(_uuid(value, field) for value, field in zip(
        (scope.organization_id, scope.workspace_id, scope.client_id, scope.actor_id),
        ("organizationId", "workspaceId", "clientId", "actorId"),
    )))

def _match(item: Mapping[str, Any], scope: CalendarScope) -> bool:
    return all(item[key] == value for key, value in (
        ("organizationId", scope.organization_id), ("workspaceId", scope.workspace_id),
        ("clientId", scope.client_id), ("createdBy", scope.actor_id),
    ))

class CalendarItemService:
    def __init__(self, root: Path, *, id_factory=uuid4, clock=_now):
        self._path = Path(root) / "calendar-workbench" / "items.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._id_factory = id_factory
        self._clock = clock
        self._lock = asyncio.Lock()

    async def list_items(self, scope: CalendarScope, period: str = "all") -> list[dict[str, Any]]:
        scope = _scope(scope)
        if period not in {"week", "month", "all"}:
            raise CalendarError("INVALID_INPUT", "period must be week, month, or all")
        async with self._lock:
            records = self._read()
        return [self._public(item) for item in records if _match(item, scope) and self._in_period(item["date"], period)]

    async def create_item(self, scope: CalendarScope, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        required = {"title", "date", "source", "note"}
        optional = {"originSuggestionId"}
        if not required.issubset(payload) or not set(payload).issubset(required | optional):
            raise CalendarError("INVALID_INPUT", "calendar item fields are incomplete or unknown")
        origin_suggestion_id = _origin_suggestion_id(payload.get("originSuggestionId"))
        item = {
            "itemId": self._new_id(), "organizationId": scope.organization_id, "workspaceId": scope.workspace_id,
            "clientId": scope.client_id, "createdBy": scope.actor_id, "createdAt": self._clock(), "updatedAt": self._clock(),
            "version": 1, "title": _text(payload["title"], "title", 200), "date": _date(payload["date"]),
            "source": _text(payload["source"], "source", 100), "note": _text(payload["note"], "note", 1000, optional=True),
            "originSuggestionId": origin_suggestion_id,
            "status": "draft",
        }
        async with self._lock:
            records = self._read(); records.append(item); self._write(records)
        return self._public(item)

    async def update_item(self, scope: CalendarScope, item_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        if set(payload) != {"expectedVersion", "status"} or isinstance(payload["expectedVersion"], bool) or not isinstance(payload["expectedVersion"], int) or payload["expectedVersion"] < 1 or payload["status"] not in {"draft", "confirmed"}:
            raise CalendarError("INVALID_INPUT", "calendar update is invalid")
        identifier = _uuid(item_id, "itemId")
        async with self._lock:
            records = self._read()
            index = next((i for i, entry in enumerate(records) if entry["itemId"] == identifier and _match(entry, scope)), None)
            if index is None:
                raise CalendarError("CALENDAR_NOT_FOUND", "calendar item was not found")
            current = records[index]
            if current["version"] != payload["expectedVersion"]:
                raise CalendarError("CALENDAR_CONFLICT", "calendar item changed and must be refreshed")
            item = {**current, "status": payload["status"], "version": current["version"] + 1, "updatedAt": self._clock()}
            records[index] = item; self._write(records)
            return self._public(item)

    def _new_id(self) -> str:
        return _uuid(str(self._id_factory()), "id")

    @staticmethod
    def _in_period(value: str, period: str) -> bool:
        if period == "all": return True
        delta = (date.fromisoformat(value) - date.today()).days
        return 0 <= delta <= (7 if period == "week" else 31)

    @staticmethod
    def _public(item: Mapping[str, Any]) -> dict[str, Any]:
        keys = ("itemId", "createdAt", "updatedAt", "version", "title", "date", "source", "note", "status")
        return {**{key: item[key] for key in keys}, "originSuggestionId": item.get("originSuggestionId")}

    def _read(self) -> list[dict[str, Any]]:
        if not self._path.exists(): return []
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping) or not isinstance(value.get("items"), list): raise ValueError
            return list(value["items"])
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CalendarError("CALENDAR_STORE_FAILED", "calendar store is unreadable") from exc

    def _write(self, records: Sequence[Mapping[str, Any]]) -> None:
        handle, name = tempfile.mkstemp(prefix="calendar-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"schemaVersion": 1, "items": list(records)}, stream, ensure_ascii=False, sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self._path)
        except OSError as exc:
            try: os.unlink(name)
            except OSError: pass
            raise CalendarError("CALENDAR_STORE_FAILED", "calendar store could not be written") from exc

def _date(value: Any) -> str:
    if not isinstance(value, str):
        raise CalendarError("INVALID_INPUT", "date must be an ISO calendar date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise CalendarError("INVALID_INPUT", "date must be an ISO calendar date") from exc

def _origin_suggestion_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    value = _text(value, "originSuggestionId", 300)
    if not re.fullmatch(r"(?:research|geo):[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value, flags=re.IGNORECASE):
        raise CalendarError("INVALID_INPUT", "originSuggestionId is invalid")
    return value
