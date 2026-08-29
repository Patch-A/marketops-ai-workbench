"""ASGI entry point for the server-backed import and review slices."""

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
from .marketops_review import (
    ApprovedProposalPreparationService,
    AsyncpgApprovedProposalSourceReader,
    AsyncpgReviewRepository,
    ReviewService,
)
from .marketops_extract import RuntimeProposalParser
from .marketops_schedule import AsyncpgScheduleRepository, ScheduleService
from .marketops_execution import AsyncpgExecutionRepository, ExecutionService
from .marketops_retrieval import (
    AsyncpgArtifactVersionSourceReader,
    AsyncpgRetrievalRepository,
    RetrievalApplicationService,
    SourceIndexingService,
)
from .marketops_learning import (
    AsyncpgKnowledgeApprovalRepository,
    AsyncpgLearningRepository,
    LearningApplicationService,
)
from .marketops_models import ModelProfileService
from .marketops_brief import BriefResearchService
from .marketops_geo import GeoSnapshotService
from .marketops_content import ContentAssetService
from .marketops_calendar import CalendarItemService
from .marketops_obsidian import ObsidianReadOnlyService


@dataclass(frozen=True)
class RuntimeSettings:
    database_url: str
    object_root: Path
    bearer_token: str
    basic_username: str
    scope: ScopeContext
    obsidian_vault_root: Path = ObsidianReadOnlyService.DEFAULT_VAULT_ROOT

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        names = {
            "database_url": "MARKETOPS_DATABASE_URL",
            "object_root": "MARKETOPS_OBJECT_ROOT",
            "bearer_token": "MARKETOPS_DEPLOYMENT_TOKEN",
            "basic_username": "MARKETOPS_DEPLOYMENT_USERNAME",
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
            basic_username=values["basic_username"],
            scope=scope,
            obsidian_vault_root=Path(
                os.environ.get(
                    "MARKETOPS_OBSIDIAN_VAULT_ROOT",
                    str(ObsidianReadOnlyService.DEFAULT_VAULT_ROOT),
                )
            ),
        )


@asynccontextmanager
async def _lifespan(application: FastAPI):
    settings = RuntimeSettings.from_environment()
    import_repository = await AsyncpgImportRepository.create(
        settings.database_url,
        min_size=1,
        max_size=10,
        command_timeout=30,
    )
    try:
        pool = import_repository.pool
        review_repository = AsyncpgReviewRepository(pool)
        object_store = LocalObjectStore(settings.object_root)
        review_service = ReviewService(
            repository=review_repository,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        schedule_service = ScheduleService(
            repository=AsyncpgScheduleRepository(pool),
            review_service=review_service,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        retrieval_repository = AsyncpgRetrievalRepository(
            pool,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        application.state.authenticator = StaticBearerAuthenticator(
            settings.bearer_token,
            settings.scope,
            basic_username=settings.basic_username,
        )
        application.state.project_reader = import_repository
        application.state.import_service = ProjectImportService(
            object_store=object_store,
            repository=import_repository,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        application.state.review_service = review_service
        application.state.schedule_service = schedule_service
        application.state.execution_service = ExecutionService(
            AsyncpgExecutionRepository(pool),
            clock=lambda: datetime.now(timezone.utc),
        )
        application.state.review_preparation = ApprovedProposalPreparationService(
            source_reader=AsyncpgApprovedProposalSourceReader(pool),
            object_store=object_store,
            review_service=review_service,
            parser=RuntimeProposalParser(),
        )
        application.state.retrieval_indexing = SourceIndexingService(
            source_reader=AsyncpgArtifactVersionSourceReader(pool),
            object_store=object_store,
            repository=retrieval_repository,
            parser=RuntimeProposalParser(),
        )
        application.state.retrieval_service = RetrievalApplicationService(
            retrieval_repository
        )
        application.state.learning_service = LearningApplicationService(
            AsyncpgLearningRepository(
                pool,
                id_factory=lambda: str(uuid4()),
                clock=lambda: datetime.now(timezone.utc),
            ),
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        application.state.knowledge_approval_service = AsyncpgKnowledgeApprovalRepository(
            pool,
            id_factory=lambda: str(uuid4()),
            clock=lambda: datetime.now(timezone.utc),
        )
        application.state.model_profile_service = ModelProfileService(settings.object_root)
        application.state.brief_research_service = BriefResearchService(settings.object_root)
        application.state.geo_service = GeoSnapshotService(settings.object_root)
        application.state.content_service = ContentAssetService(settings.object_root)
        application.state.calendar_service = CalendarItemService(settings.object_root)
        application.state.obsidian_service = ObsidianReadOnlyService(
            settings.object_root, vault_root=settings.obsidian_vault_root
        )
        yield
    finally:
        await import_repository.close()


app = create_app(
    lifespan=_lifespan,
    static_root=Path(__file__).resolve().parents[2],
)
