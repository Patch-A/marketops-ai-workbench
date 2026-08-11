"""Immutable, project-scoped human review domain service for M1-02."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncContextManager, Callable, Protocol
from uuid import UUID

from apps.api.marketops_extract import Candidate


SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
REVIEW_ACTIONS = frozenset({"approve", "modify", "reject"})
REVIEW_STATUSES = frozenset({"pending", "approve", "modify", "reject"})


class ReviewFailure(RuntimeError):
    """Stable, sanitized domain failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewScopeContext:
    organization_id: str
    workspace_id: str
    client_id: str
    actor_id: str


@dataclass(frozen=True)
class ApprovedProposal:
    organization_id: str
    workspace_id: str
    client_id: str
    project_id: str
    artifact_id: str
    version_id: str
    version: int
    sha256: str


@dataclass(frozen=True)
class CreateReviewRunRequest:
    project_id: str
    proposal_artifact_id: str
    proposal_version_id: str
    proposal_version: int
    proposal_sha256: str
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class ReviewRun:
    run_id: str
    organization_id: str
    workspace_id: str
    client_id: str
    project_id: str
    proposal_artifact_id: str
    proposal_version_id: str
    proposal_version: int
    proposal_sha256: str
    candidate_count: int
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class ReviewSnapshotItem:
    candidate_id: str
    status: str
    replacement_text: str | None = None


@dataclass(frozen=True)
class ReviewSnapshot:
    snapshot_id: str
    run_id: str
    version: int
    items: tuple[ReviewSnapshotItem, ...]
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class ReviewDecision:
    decision_id: str
    run_id: str
    review_version: int
    candidate_id: str
    action: str
    reason: str
    comment: str | None
    replacement_text: str | None
    actor_id: str
    created_at: datetime


@dataclass(frozen=True)
class ReviewAuditEvent:
    event_id: str
    run_id: str
    project_id: str
    actor_id: str
    action: str
    review_version: int
    candidate_id: str | None
    decision_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class CreateReviewRunResult:
    run: ReviewRun
    snapshot: ReviewSnapshot


@dataclass(frozen=True)
class ReviewRequest:
    expected_review_version: int
    candidate_id: str
    action: str
    reason: str
    comment: str | None = None
    replacement_text: str | None = None


@dataclass(frozen=True)
class ReviewResult:
    run_id: str
    snapshot: ReviewSnapshot
    decision: ReviewDecision


class ReviewTransaction(Protocol):
    async def get_approved_proposal(self, project_id: str) -> ApprovedProposal | None: ...

    async def insert_run(self, run: ReviewRun) -> None: ...

    async def insert_candidates(
        self, run_id: str, candidates: tuple[Candidate, ...]
    ) -> None: ...

    async def get_run(self, run_id: str) -> ReviewRun | None: ...

    async def get_latest_snapshot_for_update(
        self, run_id: str
    ) -> ReviewSnapshot | None: ...

    async def insert_snapshot(self, snapshot: ReviewSnapshot) -> None: ...

    async def append_decision(self, decision: ReviewDecision) -> None: ...

    async def append_audit_event(self, event: ReviewAuditEvent) -> None: ...


class ReviewRepository(Protocol):
    def transaction(
        self, scope: ReviewScopeContext, project_id: str
    ) -> AsyncContextManager[ReviewTransaction]: ...


class ReviewService:
    def __init__(
        self,
        *,
        repository: ReviewRepository,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime],
    ):
        self.repository = repository
        self.id_factory = id_factory
        self.clock = clock

    async def create_run(
        self, request: CreateReviewRunRequest, scope: ReviewScopeContext
    ) -> CreateReviewRunResult:
        self._validate_scope(scope)
        self._validate_create_request(request)
        run_id, snapshot_id, audit_id = self._new_ids(3)
        created_at = self._now()
        run = ReviewRun(
            run_id=run_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            client_id=scope.client_id,
            project_id=request.project_id,
            proposal_artifact_id=request.proposal_artifact_id,
            proposal_version_id=request.proposal_version_id,
            proposal_version=request.proposal_version,
            proposal_sha256=request.proposal_sha256.lower(),
            candidate_count=len(request.candidates),
            created_by=scope.actor_id,
            created_at=created_at,
        )
        snapshot = ReviewSnapshot(
            snapshot_id=snapshot_id,
            run_id=run_id,
            version=1,
            items=tuple(
                ReviewSnapshotItem(candidate_id=candidate.candidate_id, status="pending")
                for candidate in request.candidates
            ),
            created_by=scope.actor_id,
            created_at=created_at,
        )
        audit = ReviewAuditEvent(
            event_id=audit_id,
            run_id=run_id,
            project_id=request.project_id,
            actor_id=scope.actor_id,
            action="review_run_created",
            review_version=1,
            candidate_id=None,
            decision_id=None,
            created_at=created_at,
        )
        try:
            async with self.repository.transaction(
                scope, request.project_id
            ) as transaction:
                approved = await transaction.get_approved_proposal(request.project_id)
                if not self._proposal_matches(approved, request, scope):
                    raise ReviewFailure(
                        "INVALID_PROPOSAL_STATE",
                        "approved proposal does not match the requested review source",
                    )
                await transaction.insert_run(run)
                await transaction.insert_candidates(run_id, request.candidates)
                await transaction.insert_snapshot(snapshot)
                await transaction.append_audit_event(audit)
        except ReviewFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ReviewFailure(
                "REPOSITORY_FAILURE", "review repository operation failed"
            ) from None
        return CreateReviewRunResult(run=run, snapshot=snapshot)

    async def review_candidate(
        self,
        project_id: str,
        run_id: str,
        request: ReviewRequest,
        scope: ReviewScopeContext,
    ) -> ReviewResult:
        self._validate_scope(scope)
        self._uuid(project_id, "project id")
        self._uuid(run_id, "run id")
        normalized = self._normalize_review_request(request)
        snapshot_id, decision_id, audit_id = self._new_ids(3)
        created_at = self._now()
        try:
            async with self.repository.transaction(scope, project_id) as transaction:
                run = await transaction.get_run(run_id)
                if not self._run_in_scope(run, project_id, scope):
                    raise ReviewFailure("REVIEW_NOT_FOUND", "review run is not available")
                previous = await transaction.get_latest_snapshot_for_update(run_id)
                if previous is None or not self._complete_snapshot(run, previous):
                    raise ReviewFailure(
                        "REPOSITORY_FAILURE", "review repository state is incomplete"
                    )
                if previous.version != normalized.expected_review_version:
                    raise ReviewFailure("REVIEW_CONFLICT", "review version is stale")
                candidate_index = next(
                    (
                        index
                        for index, item in enumerate(previous.items)
                        if item.candidate_id == normalized.candidate_id
                    ),
                    None,
                )
                if candidate_index is None:
                    raise ReviewFailure(
                        "CANDIDATE_NOT_FOUND",
                        "candidate is not available in this review run",
                    )
                next_items = list(previous.items)
                next_items[candidate_index] = ReviewSnapshotItem(
                    candidate_id=normalized.candidate_id,
                    status={
                        "approve": "approve",
                        "modify": "modify",
                        "reject": "reject",
                    }[normalized.action],
                    replacement_text=normalized.replacement_text,
                )
                next_version = previous.version + 1
                snapshot = ReviewSnapshot(
                    snapshot_id=snapshot_id,
                    run_id=run_id,
                    version=next_version,
                    items=tuple(next_items),
                    created_by=scope.actor_id,
                    created_at=created_at,
                )
                decision = ReviewDecision(
                    decision_id=decision_id,
                    run_id=run_id,
                    review_version=next_version,
                    candidate_id=normalized.candidate_id,
                    action=normalized.action,
                    reason=normalized.reason,
                    comment=normalized.comment,
                    replacement_text=normalized.replacement_text,
                    actor_id=scope.actor_id,
                    created_at=created_at,
                )
                audit = ReviewAuditEvent(
                    event_id=audit_id,
                    run_id=run_id,
                    project_id=run.project_id,
                    actor_id=scope.actor_id,
                    action=f"candidate_{normalized.action}",
                    review_version=next_version,
                    candidate_id=normalized.candidate_id,
                    decision_id=decision_id,
                    created_at=created_at,
                )
                await transaction.insert_snapshot(snapshot)
                await transaction.append_decision(decision)
                await transaction.append_audit_event(audit)
        except ReviewFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ReviewFailure(
                "REPOSITORY_FAILURE", "review repository operation failed"
            ) from None
        return ReviewResult(run_id=run_id, snapshot=snapshot, decision=decision)

    def _validate_create_request(self, request: CreateReviewRunRequest) -> None:
        if not isinstance(request, CreateReviewRunRequest):
            raise ReviewFailure("INVALID_INPUT", "invalid create review request")
        self._uuid(request.project_id, "project id")
        self._uuid(request.proposal_artifact_id, "proposal artifact id")
        self._uuid(request.proposal_version_id, "proposal version id")
        if (
            isinstance(request.proposal_version, bool)
            or not isinstance(request.proposal_version, int)
            or request.proposal_version < 1
        ):
            raise ReviewFailure("INVALID_INPUT", "proposal version must be positive")
        self._sha256(request.proposal_sha256, "proposal hash")
        if not isinstance(request.candidates, tuple):
            raise ReviewFailure("INVALID_INPUT", "candidate batch must be immutable")
        if not request.candidates:
            raise ReviewFailure("INVALID_INPUT", "candidate batch must not be empty")
        candidate_ids: set[str] = set()
        for candidate in request.candidates:
            if not isinstance(candidate, Candidate):
                raise ReviewFailure("INVALID_INPUT", "candidate batch is invalid")
            if candidate.candidate_id in candidate_ids:
                raise ReviewFailure("INVALID_INPUT", "candidate ids must be unique")
            candidate_ids.add(candidate.candidate_id)
            citation = candidate.source_citation
            if (
                citation.source_version_id != request.proposal_version_id
                or citation.source_sha256.lower() != request.proposal_sha256.lower()
            ):
                raise ReviewFailure(
                    "SOURCE_CITATION_INVALID",
                    "candidate citation does not match the approved proposal source",
                )

    def _normalize_review_request(self, request: ReviewRequest) -> ReviewRequest:
        if not isinstance(request, ReviewRequest):
            raise ReviewFailure("INVALID_INPUT", "invalid review request")
        if (
            isinstance(request.expected_review_version, bool)
            or not isinstance(request.expected_review_version, int)
            or request.expected_review_version < 1
        ):
            raise ReviewFailure(
                "INVALID_INPUT", "expected review version must be positive"
            )
        self._uuid(request.candidate_id, "candidate id")
        if request.action not in REVIEW_ACTIONS:
            raise ReviewFailure("INVALID_INPUT", "unsupported review action")
        reason = self._required_text(request.reason, "reason")
        comment = self._optional_text(request.comment, "comment")
        replacement = self._optional_text(request.replacement_text, "replacement text")
        if request.action == "modify" and replacement is None:
            raise ReviewFailure(
                "INVALID_INPUT", "modify requires non-empty replacement text"
            )
        if request.action != "modify" and replacement is not None:
            raise ReviewFailure(
                "INVALID_INPUT", "replacement text is only valid for modify"
            )
        return ReviewRequest(
            expected_review_version=request.expected_review_version,
            candidate_id=request.candidate_id,
            action=request.action,
            reason=reason,
            comment=comment,
            replacement_text=replacement,
        )

    def _proposal_matches(
        self,
        approved: ApprovedProposal | None,
        request: CreateReviewRunRequest,
        scope: ReviewScopeContext,
    ) -> bool:
        return approved is not None and (
            approved.organization_id,
            approved.workspace_id,
            approved.client_id,
            approved.project_id,
            approved.artifact_id,
            approved.version_id,
            approved.version,
            approved.sha256.lower(),
        ) == (
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            request.project_id,
            request.proposal_artifact_id,
            request.proposal_version_id,
            request.proposal_version,
            request.proposal_sha256.lower(),
        )

    @staticmethod
    def _run_in_scope(
        run: ReviewRun | None,
        project_id: str,
        scope: ReviewScopeContext,
    ) -> bool:
        return run is not None and (
            run.organization_id,
            run.workspace_id,
            run.client_id,
            run.project_id,
        ) == (
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            project_id,
        )

    @staticmethod
    def _complete_snapshot(run: ReviewRun, snapshot: ReviewSnapshot) -> bool:
        return (
            snapshot.run_id == run.run_id
            and len(snapshot.items) == run.candidate_count
            and len({item.candidate_id for item in snapshot.items})
            == run.candidate_count
            and all(item.status in REVIEW_STATUSES for item in snapshot.items)
        )

    def _validate_scope(self, scope: ReviewScopeContext) -> None:
        if not isinstance(scope, ReviewScopeContext):
            raise ReviewFailure("AUTHORIZATION_REQUIRED", "server scope is required")
        for value in (
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            scope.actor_id,
        ):
            try:
                canonical = self._uuid(
                    value, "scope id", code="AUTHORIZATION_REQUIRED"
                )
            except ReviewFailure:
                raise ReviewFailure(
                    "AUTHORIZATION_REQUIRED", "server scope is invalid"
                ) from None
            if value != canonical:
                raise ReviewFailure(
                    "AUTHORIZATION_REQUIRED", "server scope is invalid"
                )

    def _new_ids(self, count: int) -> tuple[str, ...]:
        values: list[str] = []
        for _ in range(count):
            try:
                value = self.id_factory()
            except Exception:
                raise ReviewFailure(
                    "INVALID_SERVER_ID", "server id generation failed"
                ) from None
            self._uuid(value, "server id", code="INVALID_SERVER_ID")
            values.append(value)
        return tuple(values)

    def _now(self) -> datetime:
        try:
            value = self.clock()
        except Exception:
            raise ReviewFailure(
                "INVALID_SERVER_CLOCK", "server clock is unavailable"
            ) from None
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ReviewFailure(
                "INVALID_SERVER_CLOCK", "server clock must be timezone-aware"
            )
        return value

    @staticmethod
    def _uuid(value: object, label: str, *, code: str = "INVALID_INPUT") -> str:
        if not isinstance(value, str):
            raise ReviewFailure(code, f"{label} must be a UUID")
        try:
            parsed = UUID(value)
        except (ValueError, AttributeError, TypeError):
            raise ReviewFailure(code, f"{label} must be a UUID") from None
        if str(parsed) != value.lower():
            raise ReviewFailure(code, f"{label} must use canonical UUID form")
        return str(parsed)

    @staticmethod
    def _sha256(value: object, label: str) -> str:
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
            raise ReviewFailure(
                "INVALID_INPUT", f"{label} must be a SHA-256 value"
            )
        return value.lower()

    @staticmethod
    def _required_text(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ReviewFailure("INVALID_INPUT", f"{label} must be non-empty")
        return value.strip()

    @staticmethod
    def _optional_text(value: object, label: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ReviewFailure(
                "INVALID_INPUT", f"{label} must be non-empty when supplied"
            )
        return value.strip()

