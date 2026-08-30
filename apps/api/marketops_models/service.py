"""Bounded model metadata storage for the first workbench expansion slice.

This slice never receives or stores a secret. It stores only a server-side
environment variable reference such as ``env:DEEPSEEK_API_KEY`` and exposes
metadata needed for deterministic task matching.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from uuid import UUID, uuid4


_CAPABILITIES = frozenset(
    {
        "chat",
        "structured_output",
        "tools",
        "vision",
        "image_generation",
        "image_edit",
        "embeddings",
    }
)
_TASK_REQUIREMENTS = {
    "research": ("chat",),
    "outline": ("chat", "structured_output"),
    "long_form": ("chat",),
    "keyword_cluster": ("chat", "structured_output"),
    "review": ("chat", "structured_output"),
    "image_generation": ("image_generation",),
    "image_edit": ("image_generation", "image_edit"),
    "embedding": ("embeddings",),
}
_ENV_REF = re.compile(r"^env:[A-Z][A-Z0-9_]{2,127}$")
_PROTOCOLS = frozenset({"openai-compatible"})
_STATUSES = frozenset({"enabled", "disabled"})


class ModelCenterError(ValueError):
    """Stable public error with a safe code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModelScope:
    organization_id: str
    workspace_id: str
    client_id: str
    actor_id: str


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    organization_id: str
    workspace_id: str
    client_id: str
    created_by: str
    provider: str
    display_name: str
    protocol: str
    endpoint: str
    model_name: str
    capabilities: tuple[str, ...]
    context_window: int | None
    region: str
    data_retention: str
    credential_ref: str
    status: str
    health: str
    version: int
    created_at: str
    updated_at: str
    last_error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid(value: str, field: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ModelCenterError("INVALID_INPUT", f"{field} must be a UUID") from exc


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ModelCenterError("INVALID_INPUT", f"{field} must be text")
    value = value.strip()
    if not value or len(value) > maximum:
        raise ModelCenterError("INVALID_INPUT", f"{field} is outside its allowed length")
    return value


def _endpoint(value: Any) -> str:
    endpoint = _text(value, "endpoint", 500).rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelCenterError("INVALID_INPUT", "endpoint must be an HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ModelCenterError("INVALID_INPUT", "endpoint must not contain query or fragment data")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ModelCenterError("INVALID_INPUT", "non-local endpoints must use HTTPS")
    if parsed.username or parsed.password:
        raise ModelCenterError("INVALID_INPUT", "endpoint must not contain credentials")
    return endpoint


def _capabilities(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ModelCenterError("INVALID_INPUT", "capabilities must be a non-empty list")
    result = tuple(sorted(set(value)))
    if any(item not in _CAPABILITIES for item in result):
        raise ModelCenterError("INVALID_INPUT", "capabilities contains an unsupported value")
    return result


def _context_window(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1_000 <= value <= 2_000_000:
        raise ModelCenterError("INVALID_INPUT", "contextWindow is outside its allowed range")
    return value


def _credential_ref(value: Any) -> str:
    result = _text(value, "credentialRef", 140)
    if not _ENV_REF.fullmatch(result):
        raise ModelCenterError("INVALID_INPUT", "credentialRef must be an env: reference")
    return result


def _credential_configured(reference: str) -> bool:
    """Return only whether the referenced server variable has a non-empty value."""
    variable = reference[4:]
    return bool(os.environ.get(variable, "").strip())


def _validate_scope(scope: ModelScope) -> ModelScope:
    return ModelScope(*(_uuid(value, name) for name, value in zip(
        ("organizationId", "workspaceId", "clientId", "actorId"),
        (scope.organization_id, scope.workspace_id, scope.client_id, scope.actor_id),
    )))


def _model_from_dict(value: Mapping[str, Any]) -> ModelProfile:
    protocol = _text(value["protocol"], "protocol", 40)
    if protocol not in _PROTOCOLS:
        raise ModelCenterError("MODEL_STORE_FAILED", "model profile protocol is unsupported")
    status = value.get("status")
    if status not in _STATUSES:
        raise ModelCenterError("MODEL_STORE_FAILED", "model profile status is invalid")
    return ModelProfile(
        profile_id=_uuid(value["profile_id"], "profile_id"),
        organization_id=_uuid(value["organization_id"], "organization_id"),
        workspace_id=_uuid(value["workspace_id"], "workspace_id"),
        client_id=_uuid(value["client_id"], "client_id"),
        created_by=_uuid(value["created_by"], "created_by"),
        provider=_text(value["provider"], "provider", 100),
        display_name=_text(value["display_name"], "display_name", 120),
        protocol=protocol,
        endpoint=_endpoint(value["endpoint"]),
        model_name=_text(value["model_name"], "model_name", 200),
        capabilities=_capabilities(list(value["capabilities"])),
        context_window=_context_window(value.get("context_window")),
        region=_text(value.get("region", "未声明"), "region", 80),
        data_retention=_text(value.get("data_retention", "未声明"), "data_retention", 200),
        credential_ref=_credential_ref(value["credential_ref"]),
        status=status,
        health=value.get("health", "unverified") if value.get("health", "unverified") in {"unverified", "healthy", "unhealthy", "unconfigured"} else "unverified",
        version=int(value["version"]),
        created_at=_text(value["created_at"], "created_at", 80),
        updated_at=_text(value["updated_at"], "updated_at", 80),
        last_error=value.get("last_error") if isinstance(value.get("last_error"), str) else None,
    )


def _scope_match(profile: ModelProfile, scope: ModelScope) -> bool:
    return (
        profile.organization_id == scope.organization_id
        and profile.workspace_id == scope.workspace_id
        and profile.client_id == scope.client_id
        and profile.created_by == scope.actor_id
    )


class ModelProfileService:
    def __init__(self, root: Path, *, id_factory=uuid4, clock=_now):
        self._path = Path(root) / "model-center" / "profiles.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._id_factory = id_factory
        self._clock = clock
        self._lock = asyncio.Lock()

    async def list_profiles(self, scope: ModelScope) -> tuple[ModelProfile, ...]:
        scope = _validate_scope(scope)
        async with self._lock:
            profiles = self._read()
        return tuple(item for item in profiles if _scope_match(item, scope))

    async def create_profile(self, scope: ModelScope, payload: Mapping[str, Any]) -> ModelProfile:
        scope = _validate_scope(scope)
        profile = self._new_profile(scope, payload)
        async with self._lock:
            profiles = self._read()
            if any(item.display_name == profile.display_name and _scope_match(item, scope) for item in profiles):
                raise ModelCenterError("MODEL_CONFLICT", "displayName is already used in this scope")
            self._write((*profiles, profile))
        return profile

    async def update_profile(self, scope: ModelScope, profile_id: str, payload: Mapping[str, Any]) -> ModelProfile:
        scope = _validate_scope(scope)
        profile_id = _uuid(profile_id, "profileId")
        expected = payload.get("expectedVersion")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise ModelCenterError("INVALID_INPUT", "expectedVersion must be a positive integer")
        async with self._lock:
            profiles = list(self._read())
            index = next((i for i, item in enumerate(profiles) if item.profile_id == profile_id and _scope_match(item, scope)), None)
            if index is None:
                raise ModelCenterError("MODEL_NOT_FOUND", "model profile was not found")
            current = profiles[index]
            if current.version != expected:
                raise ModelCenterError("MODEL_CONFLICT", "model profile changed and must be refreshed")
            updated = self._replace_profile(current, payload)
            if any(item.profile_id != profile_id and item.display_name == updated.display_name and _scope_match(item, scope) for item in profiles):
                raise ModelCenterError("MODEL_CONFLICT", "displayName is already used in this scope")
            profiles[index] = updated
            self._write(profiles)
            return updated

    async def match_task(self, scope: ModelScope, task_type: str) -> dict[str, Any]:
        scope = _validate_scope(scope)
        task_type = _text(task_type, "taskType", 60)
        required = _TASK_REQUIREMENTS.get(task_type)
        if required is None:
            raise ModelCenterError("INVALID_INPUT", "taskType is unsupported")
        profiles = await self.list_profiles(scope)
        enabled = [item for item in profiles if item.status == "enabled"]
        ranked = sorted(
            enabled,
            key=lambda item: (not all(cap in item.capabilities for cap in required), item.display_name.lower()),
        )
        compatible = [
            item
            for item in ranked
            if all(cap in item.capabilities for cap in required)
            and _credential_configured(item.credential_ref)
        ]
        preferred = compatible[0] if compatible else None
        backup = compatible[1] if len(compatible) > 1 else None
        return {
            "taskType": task_type,
            "requiredCapabilities": list(required),
            "status": "matched" if preferred else "unavailable",
            "preferred": _public(preferred) if preferred else None,
            "backup": _public(backup) if backup else None,
            "reasons": (
                [f"匹配能力且服务端凭据已配置：{', '.join(required)}"]
                if preferred
                else [f"没有启用、具备能力且已配置凭据的模型：{', '.join(required)}"]
            ),
        }

    async def probe_profile(self, scope: ModelScope, profile_id: str) -> ModelProfile:
        """Probe an OpenAI-compatible endpoint without exposing the credential."""
        scope = _validate_scope(scope)
        profile_id = _uuid(profile_id, "profileId")
        async with self._lock:
            profiles = list(self._read())
            index = next((i for i, item in enumerate(profiles) if item.profile_id == profile_id and _scope_match(item, scope)), None)
            if index is None:
                raise ModelCenterError("MODEL_NOT_FOUND", "model profile was not found")
            current = profiles[index]
        token = os.environ.get(current.credential_ref[4:], "").strip()
        if not token:
            updated = replace(current, health="unconfigured", last_error="服务端凭据未配置", updated_at=self._clock())
        else:
            updated = await asyncio.to_thread(self._probe, current, token)
        async with self._lock:
            profiles = list(self._read())
            index = next((i for i, item in enumerate(profiles) if item.profile_id == profile_id and _scope_match(item, scope)), None)
            if index is None:
                raise ModelCenterError("MODEL_NOT_FOUND", "model profile was removed during probe")
            updated = replace(updated, version=profiles[index].version + 1, updated_at=self._clock())
            profiles[index] = updated
            self._write(profiles)
            return updated

    @staticmethod
    def _probe(profile: ModelProfile, token: str) -> ModelProfile:
        url = profile.endpoint.rstrip("/") + "/models"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                if not 200 <= response.status < 300:
                    raise urllib.error.HTTPError(url, response.status, "unexpected status", response.headers, None)
            return replace(profile, health="healthy", last_error=None)
        except urllib.error.HTTPError as error:
            return replace(profile, health="unhealthy", last_error=f"服务返回 HTTP {error.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            return replace(profile, health="unhealthy", last_error=f"连接失败：{str(reason)[:160]}")

    def _new_profile(self, scope: ModelScope, payload: Mapping[str, Any]) -> ModelProfile:
        now = self._clock()
        protocol = _text(payload.get("protocol", "openai-compatible"), "protocol", 40)
        status = payload.get("status", "enabled")
        if protocol not in _PROTOCOLS or status not in _STATUSES:
            raise ModelCenterError("INVALID_INPUT", "protocol or status is unsupported")
        return ModelProfile(
            profile_id=_uuid(str(self._id_factory()), "profileId"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            client_id=scope.client_id,
            created_by=scope.actor_id,
            provider=_text(payload.get("provider"), "provider", 100),
            display_name=_text(payload.get("displayName"), "displayName", 120),
            protocol=protocol,
            endpoint=_endpoint(payload.get("endpoint")),
            model_name=_text(payload.get("modelName"), "modelName", 200),
            capabilities=_capabilities(payload.get("capabilities")),
            context_window=_context_window(payload.get("contextWindow")),
            region=_text(payload.get("region", "未声明"), "region", 80),
            data_retention=_text(payload.get("dataRetention", "未声明"), "dataRetention", 200),
            credential_ref=_credential_ref(payload.get("credentialRef")),
            status=status,
            health="unverified",
            version=1,
            created_at=now,
            updated_at=now,
        )

    def _replace_profile(self, current: ModelProfile, payload: Mapping[str, Any]) -> ModelProfile:
        allowed = {
            "provider", "displayName", "protocol", "endpoint", "modelName", "capabilities",
            "contextWindow", "region", "dataRetention", "credentialRef", "status",
        }
        if set(payload) - {"expectedVersion"} - allowed:
            raise ModelCenterError("INVALID_INPUT", "unknown model profile field")
        values = {
            "provider": _text(payload.get("provider", current.provider), "provider", 100),
            "display_name": _text(payload.get("displayName", current.display_name), "displayName", 120),
            "protocol": _text(payload.get("protocol", current.protocol), "protocol", 40),
            "endpoint": _endpoint(payload.get("endpoint", current.endpoint)),
            "model_name": _text(payload.get("modelName", current.model_name), "modelName", 200),
            "capabilities": _capabilities(payload.get("capabilities", list(current.capabilities))),
            "context_window": _context_window(payload.get("contextWindow", current.context_window)),
            "region": _text(payload.get("region", current.region), "region", 80),
            "data_retention": _text(payload.get("dataRetention", current.data_retention), "dataRetention", 200),
            "credential_ref": _credential_ref(payload.get("credentialRef", current.credential_ref)),
            "status": payload.get("status", current.status),
        }
        if values["protocol"] not in _PROTOCOLS or values["status"] not in _STATUSES:
            raise ModelCenterError("INVALID_INPUT", "protocol or status is unsupported")
        return replace(current, **values, version=current.version + 1, updated_at=self._clock(), health="unverified", last_error=None)

    def _read(self) -> tuple[ModelProfile, ...]:
        if not self._path.exists():
            return ()
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            profiles = value.get("profiles", [])
            if not isinstance(profiles, list):
                raise ValueError
            return tuple(_model_from_dict(item) for item in profiles)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ModelCenterError("MODEL_STORE_FAILED", "model profile store is unreadable") from exc

    def _write(self, profiles: Sequence[ModelProfile]) -> None:
        payload = {"schemaVersion": 1, "profiles": [asdict(item) for item in profiles]}
        payload["profiles"] = [dict(item, capabilities=list(item["capabilities"])) for item in payload["profiles"]]
        handle, name = tempfile.mkstemp(prefix="profiles-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._path)
        except OSError as exc:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise ModelCenterError("MODEL_STORE_FAILED", "model profile store could not be written") from exc


def _public(profile: ModelProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "profileId": profile.profile_id,
        "provider": profile.provider,
        "displayName": profile.display_name,
        "protocol": profile.protocol,
        "endpoint": profile.endpoint,
        "modelName": profile.model_name,
        "capabilities": list(profile.capabilities),
        "contextWindow": profile.context_window,
        "region": profile.region,
        "dataRetention": profile.data_retention,
        "credentialRef": profile.credential_ref,
        "credentialConfigured": _credential_configured(profile.credential_ref),
        "status": profile.status,
        "health": profile.health,
        "version": profile.version,
        "createdAt": profile.created_at,
        "updatedAt": profile.updated_at,
        "lastError": profile.last_error,
    }


def public_profile(profile: ModelProfile) -> dict[str, Any]:
    result = _public(profile)
    assert result is not None
    return result
