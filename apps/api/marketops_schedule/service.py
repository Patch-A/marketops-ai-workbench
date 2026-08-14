"""Scoped, append-only WBS and deterministic schedule service for M1-03."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncContextManager, Callable, Mapping, Protocol, Sequence
from uuid import UUID

from apps.api.marketops_review import (
    ApprovedProposal,
    ReviewFailure,
    ReviewReadModel,
    ReviewScopeContext,
    ReviewService,
)

from .deterministic import ScheduleFailure, build_wbs_draft, calculate_schedule, revise_wbs


class ScheduleServiceFailure(RuntimeError):
    """Stable service failure that does not expose storage or source content."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ScheduleScopeContext:
    organization_id: str
    workspace_id: str
    client_id: str
    actor_id: str


@dataclass(frozen=True)
class CreatePlanRequest:
    project_id: str
    review_run_id: str
    review_version: int


@dataclass(frozen=True)
class WbsPlan:
    plan_id: str
    organization_id: str
    workspace_id: str
    client_id: str
    project_id: str
    proposal_artifact_id: str
    proposal_version_id: str
    proposal_sha256: str
    source_review_run_id: str
    source_review_snapshot_id: str
    source_review_version: int
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class WbsPlanVersion:
    version_id: str
    plan_id: str
    version: int
    status: str
    payload: Mapping[str, Any]
    digest: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class PlanReadModel:
    plan: WbsPlan
    version: WbsPlanVersion
    available_versions: tuple[int, ...]


@dataclass(frozen=True)
class CreatePlanResult:
    plan: PlanReadModel
    replayed: bool = False


@dataclass(frozen=True)
class ScheduleSnapshot:
    snapshot_id: str
    plan_id: str
    plan_version_id: str
    plan_version: int
    project_start: str
    holidays: tuple[str, ...]
    status: str
    plan_digest: str
    schedule_digest: str
    payload: Mapping[str, Any]
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class CreateScheduleResult:
    snapshot: ScheduleSnapshot
    replayed: bool = False


@dataclass(frozen=True)
class ScheduleAuditEvent:
    event_id: str
    project_id: str
    actor_id: str
    action: str
    target_type: str
    target_id: str
    event_data: Mapping[str, Any]
    created_at: datetime


class ScheduleTransaction(Protocol):
    async def get_approved_proposal_for_update(
        self, project_id: str
    ) -> ApprovedProposal | None: ...

    async def get_plan_by_source_for_update(
        self, review_run_id: str, review_version: int
    ) -> PlanReadModel | None: ...

    async def get_plan_for_update(self, plan_id: str) -> PlanReadModel | None: ...

    async def insert_plan(self, plan: WbsPlan) -> None: ...

    async def insert_plan_version(self, version: WbsPlanVersion) -> None: ...

    async def insert_plan_tasks(self, version: WbsPlanVersion) -> None: ...

    async def get_schedule_by_digest(
        self, plan_version_id: str, schedule_digest: str
    ) -> ScheduleSnapshot | None: ...

    async def insert_schedule(self, snapshot: ScheduleSnapshot) -> None: ...

    async def append_audit_event(self, event: ScheduleAuditEvent) -> None: ...


class ScheduleRepository(Protocol):
    def transaction(
        self, scope: ScheduleScopeContext, project_id: str
    ) -> AsyncContextManager[ScheduleTransaction]: ...

    async def get_plan(
        self,
        scope: ScheduleScopeContext,
        project_id: str,
        plan_id: str,
        plan_version: int | None,
    ) -> PlanReadModel | None: ...


class ScheduleService:
    def __init__(
        self,
        *,
        repository: ScheduleRepository,
        review_service: ReviewService,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime],
    ):
        self.repository = repository
        self.review_service = review_service
        self.id_factory = id_factory
        self.clock = clock

    async def create_plan(
        self, request: CreatePlanRequest, scope: ScheduleScopeContext
    ) -> CreatePlanResult:
        self._validate_scope(scope)
        project_id = self._uuid(request.project_id, "project id")
        run_id = self._uuid(request.review_run_id, "review run id")
        review_version = self._positive_int(request.review_version, "review version")
        review = await self._read_review(project_id, run_id, review_version, scope)
        if any(candidate.status == "pending" for candidate in review.candidates):
            raise ScheduleServiceFailure(
                "REVIEW_INCOMPLETE", "every review candidate needs a human decision"
            )
        domain_review = _review_domain_payload(project_id, review)
        proposal_payload = {
            "versionId": review.run.proposal_version_id,
            "sha256": review.run.proposal_sha256,
        }
        try:
            plan_payload = build_wbs_draft(project_id, proposal_payload, domain_review)
        except ScheduleFailure as failure:
            raise ScheduleServiceFailure(failure.code, failure.message) from None
        plan_payload["sourceReviewSnapshotId"] = review.snapshot.snapshot_id

        plan_id, version_id, audit_id = self._new_ids(3)
        created_at = self._now()
        plan = WbsPlan(
            plan_id=plan_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            client_id=scope.client_id,
            project_id=project_id,
            proposal_artifact_id=review.run.proposal_artifact_id,
            proposal_version_id=review.run.proposal_version_id,
            proposal_sha256=review.run.proposal_sha256.lower(),
            source_review_run_id=run_id,
            source_review_snapshot_id=review.snapshot.snapshot_id,
            source_review_version=review_version,
            created_by=scope.actor_id,
            created_at=created_at,
        )
        version = _plan_version(
            version_id, plan_id, plan_payload, scope.actor_id, created_at
        )
        audit = ScheduleAuditEvent(
            audit_id,
            project_id,
            scope.actor_id,
            "wbs_plan_created",
            "wbs_plan",
            plan_id,
            {
                "planVersion": 1,
                "sourceReviewRunId": run_id,
                "sourceReviewSnapshotId": review.snapshot.snapshot_id,
                "sourceReviewVersion": review_version,
                "planDigest": version.digest,
            },
            created_at,
        )
        try:
            async with self.repository.transaction(scope, project_id) as transaction:
                approved = await transaction.get_approved_proposal_for_update(project_id)
                self._require_source(approved, review, scope)
                existing = await transaction.get_plan_by_source_for_update(
                    run_id, review_version
                )
                if existing is not None:
                    self._validate_plan_model(existing, project_id, scope)
                    return CreatePlanResult(existing, replayed=True)
                await transaction.insert_plan(plan)
                await transaction.insert_plan_version(version)
                await transaction.insert_plan_tasks(version)
                await transaction.append_audit_event(audit)
        except ScheduleServiceFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ScheduleServiceFailure(
                "REPOSITORY_FAILURE", "schedule repository operation failed"
            ) from None
        return CreatePlanResult(PlanReadModel(plan, version, (1,)))

    async def read_plan(
        self,
        project_id: str,
        plan_id: str,
        scope: ScheduleScopeContext,
        *,
        plan_version: int | None = None,
    ) -> PlanReadModel:
        self._validate_scope(scope)
        canonical_project = self._uuid(project_id, "project id")
        canonical_plan = self._uuid(plan_id, "plan id")
        if plan_version is not None:
            plan_version = self._positive_int(plan_version, "plan version")
        try:
            model = await self.repository.get_plan(
                scope, canonical_project, canonical_plan, plan_version
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ScheduleServiceFailure(
                "REPOSITORY_FAILURE", "schedule repository operation failed"
            ) from None
        if model is None:
            raise ScheduleServiceFailure("PLAN_NOT_FOUND", "WBS plan is not available")
        self._validate_plan_model(model, canonical_project, scope, plan_version)
        return model

    async def revise_plan(
        self,
        project_id: str,
        plan_id: str,
        expected_plan_version: int,
        task_updates: Sequence[Mapping[str, Any]],
        scope: ScheduleScopeContext,
    ) -> PlanReadModel:
        self._validate_scope(scope)
        canonical_project = self._uuid(project_id, "project id")
        canonical_plan = self._uuid(plan_id, "plan id")
        expected = self._positive_int(expected_plan_version, "expected plan version")
        version_id, audit_id = self._new_ids(2)
        created_at = self._now()
        try:
            async with self.repository.transaction(scope, canonical_project) as transaction:
                current = await transaction.get_plan_for_update(canonical_plan)
                if current is None:
                    raise ScheduleServiceFailure(
                        "PLAN_NOT_FOUND", "WBS plan is not available"
                    )
                self._validate_plan_model(current, canonical_project, scope)
                if current.version.version != expected:
                    raise ScheduleServiceFailure(
                        "PLAN_CONFLICT", "the WBS plan changed before this revision"
                    )
                try:
                    payload = revise_wbs(current.version.payload, expected, task_updates)
                except ScheduleFailure as failure:
                    raise ScheduleServiceFailure(failure.code, failure.message) from None
                version = _plan_version(
                    version_id, canonical_plan, payload, scope.actor_id, created_at
                )
                await transaction.insert_plan_version(version)
                await transaction.insert_plan_tasks(version)
                await transaction.append_audit_event(
                    ScheduleAuditEvent(
                        audit_id,
                        canonical_project,
                        scope.actor_id,
                        "wbs_plan_revised",
                        "wbs_plan",
                        canonical_plan,
                        {
                            "fromVersion": expected,
                            "toVersion": version.version,
                            "planDigest": version.digest,
                        },
                        created_at,
                    )
                )
                return PlanReadModel(
                    current.plan,
                    version,
                    current.available_versions + (version.version,),
                )
        except ScheduleServiceFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ScheduleServiceFailure(
                "REPOSITORY_FAILURE", "schedule repository operation failed"
            ) from None

    async def calculate_plan_schedule(
        self,
        project_id: str,
        plan_id: str,
        expected_plan_version: int,
        project_start: str,
        holidays: Sequence[str],
        scope: ScheduleScopeContext,
    ) -> CreateScheduleResult:
        self._validate_scope(scope)
        canonical_project = self._uuid(project_id, "project id")
        canonical_plan = self._uuid(plan_id, "plan id")
        expected = self._positive_int(expected_plan_version, "expected plan version")
        snapshot_id, audit_id = self._new_ids(2)
        created_at = self._now()
        try:
            async with self.repository.transaction(scope, canonical_project) as transaction:
                current = await transaction.get_plan_for_update(canonical_plan)
                if current is None:
                    raise ScheduleServiceFailure(
                        "PLAN_NOT_FOUND", "WBS plan is not available"
                    )
                self._validate_plan_model(current, canonical_project, scope)
                if current.version.version != expected:
                    raise ScheduleServiceFailure(
                        "PLAN_CONFLICT", "the WBS plan changed before calculation"
                    )
                try:
                    payload = calculate_schedule(
                        current.version.payload, project_start, holidays
                    )
                except ScheduleFailure as failure:
                    raise ScheduleServiceFailure(failure.code, failure.message) from None
                if payload["planDigest"] != current.version.digest:
                    raise ScheduleServiceFailure(
                        "REPOSITORY_FAILURE", "stored plan digest is inconsistent"
                    )
                existing = await transaction.get_schedule_by_digest(
                    current.version.version_id, payload["scheduleDigest"]
                )
                if existing is not None:
                    self._validate_schedule(existing, current, scope)
                    return CreateScheduleResult(existing, replayed=True)
                snapshot = ScheduleSnapshot(
                    snapshot_id,
                    canonical_plan,
                    current.version.version_id,
                    expected,
                    payload["projectStart"],
                    tuple(payload["calendar"]["holidays"]),
                    payload["status"],
                    payload["planDigest"],
                    payload["scheduleDigest"],
                    payload,
                    scope.actor_id,
                    created_at,
                )
                await transaction.insert_schedule(snapshot)
                await transaction.append_audit_event(
                    ScheduleAuditEvent(
                        audit_id,
                        canonical_project,
                        scope.actor_id,
                        "schedule_snapshot_created",
                        "schedule_snapshot",
                        snapshot_id,
                        {
                            "planId": canonical_plan,
                            "planVersion": expected,
                            "status": snapshot.status,
                            "scheduleDigest": snapshot.schedule_digest,
                        },
                        created_at,
                    )
                )
                return CreateScheduleResult(snapshot)
        except ScheduleServiceFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ScheduleServiceFailure(
                "REPOSITORY_FAILURE", "schedule repository operation failed"
            ) from None

    async def _read_review(
        self,
        project_id: str,
        run_id: str,
        review_version: int,
        scope: ScheduleScopeContext,
    ) -> ReviewReadModel:
        try:
            return await self.review_service.read_review(
                project_id,
                run_id,
                ReviewScopeContext(
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    scope.actor_id,
                ),
                review_version=review_version,
            )
        except ReviewFailure as failure:
            raise ScheduleServiceFailure(failure.code, str(failure)) from None

    @staticmethod
    def _require_source(
        approved: ApprovedProposal | None,
        review: ReviewReadModel,
        scope: ScheduleScopeContext,
    ) -> None:
        if approved is None:
            raise ScheduleServiceFailure(
                "APPROVED_PROPOSAL_NOT_FOUND", "approved proposal is not available"
            )
        run = review.run
        if (
            approved.organization_id != scope.organization_id
            or approved.workspace_id != scope.workspace_id
            or approved.client_id != scope.client_id
            or approved.project_id != run.project_id
            or approved.artifact_id != run.proposal_artifact_id
            or approved.version_id != run.proposal_version_id
            or approved.version != run.proposal_version
            or approved.sha256.lower() != run.proposal_sha256.lower()
        ):
            raise ScheduleServiceFailure(
                "SOURCE_CONFLICT", "review source is not the current approved proposal"
            )

    @staticmethod
    def _validate_plan_model(
        model: PlanReadModel,
        project_id: str,
        scope: ScheduleScopeContext,
        requested_version: int | None = None,
    ) -> None:
        if not isinstance(model, PlanReadModel):
            raise ScheduleServiceFailure(
                "REPOSITORY_FAILURE", "schedule repository state is incomplete"
            )
        plan = model.plan
        version = model.version
        if (
            plan.project_id != project_id
            or plan.organization_id != scope.organization_id
            or plan.workspace_id != scope.workspace_id
            or plan.client_id != scope.client_id
            or plan.created_by != scope.actor_id
            or version.plan_id != plan.plan_id
            or version.created_by != scope.actor_id
            or version.version not in model.available_versions
            or model.available_versions != tuple(sorted(set(model.available_versions)))
            or (requested_version is not None and version.version != requested_version)
            or version.payload.get("sourceReviewRunId") != plan.source_review_run_id
            or version.payload.get("sourceReviewSnapshotId") != plan.source_review_snapshot_id
            or version.payload.get("sourceReviewVersion") != plan.source_review_version
            or _payload_digest(version.payload) != version.digest
        ):
            raise ScheduleServiceFailure(
                "REPOSITORY_FAILURE", "schedule repository state is incomplete"
            )

    @staticmethod
    def _validate_schedule(
        snapshot: ScheduleSnapshot,
        plan: PlanReadModel,
        scope: ScheduleScopeContext,
    ) -> None:
        if (
            not isinstance(snapshot, ScheduleSnapshot)
            or snapshot.plan_id != plan.plan.plan_id
            or snapshot.plan_version_id != plan.version.version_id
            or snapshot.plan_version != plan.version.version
            or snapshot.plan_digest != plan.version.digest
            or snapshot.created_by != scope.actor_id
            or snapshot.payload.get("scheduleDigest") != snapshot.schedule_digest
        ):
            raise ScheduleServiceFailure(
                "REPOSITORY_FAILURE", "schedule repository state is incomplete"
            )

    @staticmethod
    def _validate_scope(scope: ScheduleScopeContext) -> None:
        if not isinstance(scope, ScheduleScopeContext):
            raise ScheduleServiceFailure("INVALID_INPUT", "scope is invalid")
        for label, value in (
            ("organization id", scope.organization_id),
            ("workspace id", scope.workspace_id),
            ("client id", scope.client_id),
            ("actor id", scope.actor_id),
        ):
            ScheduleService._uuid(value, label)

    @staticmethod
    def _uuid(value: Any, label: str) -> str:
        if not isinstance(value, str):
            raise ScheduleServiceFailure("INVALID_INPUT", f"{label} must be a UUID")
        try:
            return str(UUID(value))
        except ValueError:
            raise ScheduleServiceFailure(
                "INVALID_INPUT", f"{label} must be a UUID"
            ) from None

    @staticmethod
    def _positive_int(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ScheduleServiceFailure(
                "INVALID_INPUT", f"{label} must be a positive integer"
            )
        return value

    def _new_ids(self, count: int) -> tuple[str, ...]:
        values = tuple(self._uuid(self.id_factory(), "generated id") for _ in range(count))
        if len(set(values)) != len(values):
            raise ScheduleServiceFailure(
                "INTERNAL_FAILURE", "generated identifiers must be unique"
            )
        return values

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ScheduleServiceFailure(
                "INTERNAL_FAILURE", "clock must return a timezone-aware datetime"
            )
        return value


def _review_domain_payload(project_id: str, model: ReviewReadModel) -> dict[str, Any]:
    latest_version = model.available_review_versions[-1]
    candidates: list[dict[str, Any]] = []
    for view in model.candidates:
        candidate = view.candidate.as_dict()
        decision = view.last_decision
        candidate["review"] = {
            "status": view.status,
            "replacementText": view.replacement_text,
            "lastDecision": (
                {
                    "decisionId": decision.decision_id,
                    "runId": decision.run_id,
                    "reviewVersion": decision.review_version,
                    "candidateId": decision.candidate_id,
                    "action": decision.action,
                    "reason": decision.reason,
                    "comment": decision.comment,
                    "replacementText": decision.replacement_text,
                    "actorId": decision.actor_id,
                    "createdAt": decision.created_at.isoformat(),
                }
                if decision is not None
                else None
            ),
        }
        candidate["ordinal"] = view.ordinal
        candidates.append(candidate)
    return {
        "run": {
            "runId": model.run.run_id,
            "projectId": project_id,
            "proposalVersionId": model.run.proposal_version_id,
            "proposalSha256": model.run.proposal_sha256,
            "candidateCount": model.run.candidate_count,
            "latestReviewVersion": latest_version,
            "createdAt": model.run.created_at.isoformat(),
        },
        "selectedReviewVersion": model.snapshot.version,
        "availableReviewVersions": list(model.available_review_versions),
        "candidates": candidates,
    }


def _payload_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_version(
    version_id: str,
    plan_id: str,
    payload: Mapping[str, Any],
    actor_id: str,
    created_at: datetime,
) -> WbsPlanVersion:
    version = payload.get("planVersion")
    status = payload.get("status")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ScheduleServiceFailure("invalid_plan_version", "plan version is invalid")
    if status != "draft":
        raise ScheduleServiceFailure("invalid_plan_status", "plan status is invalid")
    return WbsPlanVersion(
        version_id,
        plan_id,
        version,
        status,
        dict(payload),
        _payload_digest(payload),
        actor_id,
        created_at,
    )
