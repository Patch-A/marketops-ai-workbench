"""Authenticated-project capsule and feedback orchestration over PostgreSQL."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from typing import Callable, Protocol
from uuid import NAMESPACE_URL, uuid5

from .postgres import (
    CapsuleReadModel,
    CapsuleSummary,
    KnowledgeReadModel,
    PersistCapsuleResult,
)
from .service import (
    KnowledgeItem,
    LearningFailure,
    LearningScopeContext,
    OutcomeInput,
    OutcomeRecord,
    RetrospectiveInput,
    RetrospectiveRecord,
    normalize_outcome,
    normalize_retrospective,
)


class LearningRepository(Protocol):
    async def finalize_capsule(
        self,
        scope: LearningScopeContext,
        project_id: str,
        outcomes: tuple[OutcomeRecord, ...],
        retrospectives: tuple[RetrospectiveRecord, ...],
    ) -> PersistCapsuleResult: ...

    async def read_capsule(
        self, scope: LearningScopeContext, project_id: str, capsule_id: str
    ) -> CapsuleReadModel | None: ...

    async def list_capsules(
        self, scope: LearningScopeContext, project_id: str
    ) -> tuple[CapsuleSummary, ...]: ...

    async def list_knowledge(
        self, scope: LearningScopeContext, project_id: str
    ) -> tuple[KnowledgeItem, ...]: ...

    async def read_knowledge(
        self, scope: LearningScopeContext, project_id: str, knowledge_id: str
    ) -> KnowledgeReadModel | None: ...


class LearningApplicationService:
    def __init__(
        self,
        repository: LearningRepository,
        *,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime],
    ):
        self.repository = repository
        self.id_factory = id_factory
        self.clock = clock

    async def finalize_capsule(
        self,
        project_id: str,
        outcomes: tuple[OutcomeInput, ...],
        retrospectives: tuple[RetrospectiveInput, ...],
        scope: LearningScopeContext,
    ) -> PersistCapsuleResult:
        self._validate_scope(scope)
        if not _uuid_like(project_id):
            raise LearningFailure("INVALID_INPUT", "project id is invalid")
        if not isinstance(outcomes, tuple) or not isinstance(retrospectives, tuple):
            raise LearningFailure("INVALID_INPUT", "feedback is invalid")
        normalized_outcomes = tuple(
            self._normalize_outcome(project_id, value, scope) for value in outcomes
        )
        normalized_retrospectives = tuple(
            self._normalize_retrospective(project_id, value, scope)
            for value in retrospectives
        )
        try:
            return await self.repository.finalize_capsule(
                scope, project_id, normalized_outcomes, normalized_retrospectives
            )
        except LearningFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise LearningFailure(
                "LEARNING_WRITE_FAILED", "project capsule could not be created"
            ) from None

    async def read_capsule(
        self, project_id: str, capsule_id: str, scope: LearningScopeContext
    ) -> CapsuleReadModel:
        self._validate_scope(scope)
        if not _uuid_like(project_id) or not _uuid_like(capsule_id):
            raise LearningFailure("INVALID_INPUT", "capsule identity is invalid")
        try:
            capsule = await self.repository.read_capsule(scope, project_id, capsule_id)
        except LearningFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise LearningFailure(
                "LEARNING_READ_FAILED", "project capsule could not be read"
            ) from None
        if capsule is None:
            raise LearningFailure("CAPSULE_NOT_FOUND", "project capsule was not found")
        return capsule

    async def list_capsules(
        self, project_id: str, scope: LearningScopeContext
    ) -> tuple[CapsuleSummary, ...]:
        self._validate_scope(scope)
        if not _uuid_like(project_id):
            raise LearningFailure("INVALID_INPUT", "project id is invalid")
        try:
            return await self.repository.list_capsules(scope, project_id)
        except LearningFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise LearningFailure(
                "LEARNING_READ_FAILED", "project capsules could not be read"
            ) from None

    async def list_knowledge(
        self, project_id: str, scope: LearningScopeContext
    ) -> tuple[KnowledgeItem, ...]:
        self._validate_scope(scope)
        if not _uuid_like(project_id):
            raise LearningFailure("INVALID_INPUT", "project id is invalid")
        try:
            return await self.repository.list_knowledge(scope, project_id)
        except LearningFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise LearningFailure(
                "LEARNING_READ_FAILED", "candidate knowledge could not be read"
            ) from None

    async def read_knowledge(
        self, project_id: str, knowledge_id: str, scope: LearningScopeContext
    ) -> KnowledgeReadModel:
        self._validate_scope(scope)
        if not _uuid_like(project_id) or not _uuid_like(knowledge_id):
            raise LearningFailure("INVALID_INPUT", "knowledge identity is invalid")
        try:
            model = await self.repository.read_knowledge(scope, project_id, knowledge_id)
        except LearningFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise LearningFailure(
                "LEARNING_READ_FAILED", "candidate knowledge could not be read"
            ) from None
        if model is None:
            raise LearningFailure(
                "KNOWLEDGE_NOT_FOUND", "candidate knowledge was not found"
            )
        return model

    @staticmethod
    def _normalize_outcome(
        project_id: str, value: OutcomeInput, scope: LearningScopeContext
    ) -> OutcomeRecord:
        provisional = str(uuid5(NAMESPACE_URL, f"marketops:learning:outcome:{project_id}"))
        normalized = normalize_outcome(value, scope, provisional)
        return replace(
            normalized,
            outcome_id=_feedback_id(
                project_id,
                "outcome",
                {
                    "metric": normalized.metric,
                    "plannedValue": normalized.planned_value,
                    "actualValue": normalized.actual_value,
                    "unit": normalized.unit,
                    "source": _source_identity(normalized.source),
                },
            ),
        )

    @staticmethod
    def _normalize_retrospective(
        project_id: str, value: RetrospectiveInput, scope: LearningScopeContext
    ) -> RetrospectiveRecord:
        provisional = str(uuid5(NAMESPACE_URL, f"marketops:learning:retrospective:{project_id}"))
        normalized = normalize_retrospective(value, scope, provisional)
        return replace(
            normalized,
            retrospective_id=_feedback_id(
                project_id,
                "retrospective",
                {
                    "finding": normalized.finding,
                    "classification": normalized.classification,
                    "reusableCandidate": normalized.reusable_candidate,
                    "evidence": sorted(
                        (_source_identity(source) for source in normalized.evidence),
                        key=lambda item: (item["sourceType"], item["sourceId"]),
                    ),
                },
            ),
        )

    @staticmethod
    def _validate_scope(scope: LearningScopeContext) -> None:
        if not isinstance(scope, LearningScopeContext) or any(
            not _uuid_like(value)
            for value in (
                scope.organization_id,
                scope.workspace_id,
                scope.client_id,
                scope.actor_id,
            )
        ):
            raise LearningFailure("INVALID_SCOPE", "learning scope is invalid")


def _uuid_like(value: object) -> bool:
    from uuid import UUID

    try:
        return isinstance(value, str) and str(UUID(value)) == value.lower()
    except (ValueError, TypeError, AttributeError):
        return False


def _feedback_id(project_id: str, kind: str, value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return str(uuid5(NAMESPACE_URL, f"marketops:learning:{kind}:{project_id}:{canonical}"))


def _source_identity(source: object) -> dict[str, str]:
    if not hasattr(source, "source_type") or not hasattr(source, "source_id"):
        return {"sourceType": "invalid", "sourceId": "invalid"}
    return {"sourceType": str(source.source_type), "sourceId": str(source.source_id)}
