"""ASGI entry point for the server-backed M1-01 import slice."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI

from .marketops_import.http import StaticBearerAuthenticator, create_app
from .marketops_import.postgres import AsyncpgImportRepository
from .marketops_import.service import ProjectImportService, ScopeContext
from .marketops_import.storage import LocalObjectStore


@dataclass(frozen=True)
class RuntimeSettings:
    database_url: str
    object_root: Path
    bearer_token: str
    scope: ScopeContext

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        names = {
            "database_url": "MARKETOPS_DATABASE_URL",
            "object_root": "MARKETOPS_OBJECT_ROOT",
            "bearer_token": "MARKETOPS_DEPLOYMENT_TOKEN",
            "organization_id": "MARKETOPS_ORGANIZATION_ID",
            "workspace_id": "MARKETOPS_WORKSPACE_ID",
            "client_id": "MARKETOPS_CLIENT_ID",
            "actor_id": "MARKETOPS_ACTOR_ID",
        }
        values = {key: os.environ.get(name, "").strip() for key, name in names.items()}
        missing = [names[key] for key, value in values.items() if not value]
        if missing:
            raise RuntimeError("missing required server configuration: " + ", ".join(missing))
        try:
            canonical_scope = {
                name: str(UUID(values[name]))
                for name in ("organization_id", "workspace_id", "client_id", "actor_id")
            }
        except ValueError as error:
            raise RuntimeError("server scope configuration must contain UUIDs") from error
        scope = ScopeContext(**canonical_scope)
        return cls(
            database_url=values["database_url"],
            object_root=Path(values["object_root"]),
            bearer_token=values["bearer_token"],
            scope=scope,
        )


@asynccontextmanager
async def _lifespan(application: FastAPI):
    settings = RuntimeSettings.from_environment()
    repository = await AsyncpgImportRepository.create(
        settings.database_url,
        min_size=1,
        max_size=10,
        command_timeout=30,
    )
    application.state.authenticator = StaticBearerAuthenticator(
        settings.bearer_token, settings.scope
    )
    application.state.import_service = ProjectImportService(
        object_store=LocalObjectStore(settings.object_root),
        repository=repository,
        id_factory=lambda: str(uuid4()),
        clock=lambda: datetime.now(timezone.utc),
    )
    try:
        yield
    finally:
        await repository.close()


app = create_app(lifespan=_lifespan)
