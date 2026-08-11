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
MAX_REVIEW_CANDIDATES = 1000


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


@dataclass(frozen=True)
class ReviewRunSummary:
    run: ReviewRun
    latest_review_version: int


@dataclass(frozen=True)
class ReviewCandidateView:
    ordinal: int
    candidate: Candidate
    status: str
    replacement_text: str | None
    last_decision: ReviewDecision | None = None


@dataclass(frozen=True)
class ReviewReadModel:
    run: ReviewRun
    snapshot: ReviewSnapshot
    candidates: tuple[ReviewCandidateView, ...]
    available_review_versions: tuple[int, ...]
    selected_decision: ReviewDecision | None


class ReviewTransaction(Protocol):
    async def get_approved_proposal_for_update(
        self, project_id: str
    ) -> ApprovedProposal | None: ...

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

    async def list_review_runs(
        self, scope: ReviewScopeContext, project_id: str, limit: int
    ) -> tuple[ReviewRunSummary, ...]: ...

    async def get_review(
        self,
        scope: ReviewScopeContext,
        project_id: str,
        run_id: str,
        review_version: int | None,
    ) -> ReviewReadModel | None: ...


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

    async def list_runs(
        self,
        project_id: str,
        scope: ReviewScopeContext,
        *,
        limit: int = 50,
    ) -> tuple[ReviewRunSummary, ...]:
        self._validate_scope(scope)
        self._uuid(project_id, "project id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ReviewFailure("INVALID_INPUT", "limit must be between 1 and 100")
        try:
            summaries = await self.repository.list_review_runs(scope, project_id, limit)
        except ReviewFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ReviewFailure(
                "REPOSITORY_FAILURE", "review repository operation failed"
            ) from None
        if not self._valid_run_summaries(summaries, project_id, scope, limit):
            raise ReviewFailure(
                "REPOSITORY_FAILURE", "review repository state is incomplete"
            )
        return summaries

    async def read_review(
        self,
        project_id: str,
        run_id: str,
        scope: ReviewScopeContext,
        *,
        review_version: int | None = None,
    ) -> ReviewReadModel:
        self._validate_scope(scope)
        self._uuid(project_id, "project id")
        self._uuid(run_id, "run id")
        if review_version is not None and (
            isinstance(review_version, bool)
            or not isinstance(review_version, int)
            or review_version < 1
        ):
            raise ReviewFailure("INVALID_INPUT", "review version must be positive")
        try:
            result = await self.repository.get_review(
                scope, project_id, run_id, review_version
            )
        except ReviewFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ReviewFailure(
                "REPOSITORY_FAILURE", "review repository operation failed"
            ) from None
        if result is None:
            raise ReviewFailure("REVIEW_NOT_FOUND", "review run is not available")
        if (
            not isinstance(result, ReviewReadModel)
            or not self._read_run_in_scope(result.run, project_id, scope)
            or result.run.run_id != run_id
        ):
            raise ReviewFailure("REVIEW_NOT_FOUND", "review run is not available")
        if not self._valid_read_model(
            result, project_id, run_id, scope, review_version
        ):
            raise ReviewFailure(
                "REPOSITORY_FAILURE", "review repository state is incomplete"
            )
        return result

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
                approved = await transaction.get_approved_proposal_for_update(
                    request.project_id
                )
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
                current_item = previous.items[candidate_index]
                next_status = normalized.action
                if (
                    current_item.status == next_status
                    and current_item.replacement_text == normalized.replacement_text
                ):
                    raise ReviewFailure(
                        "INVALID_INPUT", "review action must change candidate state"
                    )
                next_items = list(previous.items)
                next_items[candidate_index] = ReviewSnapshotItem(
                    candidate_id=normalized.candidate_id,
                    status=next_status,
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
        if len(request.candidates) > MAX_REVIEW_CANDIDATES:
            raise ReviewFailure("INVALID_INPUT", "candidate batch exceeds the review limit")
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

    @classmethod
    def _valid_run_summaries(
        cls,
        summaries: object,
        project_id: str,
        scope: ReviewScopeContext,
        limit: int,
    ) -> bool:
        if not isinstance(summaries, tuple) or len(summaries) > limit:
            return False
        seen: set[str] = set()
        previous: tuple[datetime, str] | None = None
        for summary in summaries:
            if not isinstance(summary, ReviewRunSummary):
                return False
            run = summary.run
            if (
                not cls._read_run_in_scope(run, project_id, scope)
                or run.run_id in seen
                or not cls._valid_run_shape(run)
                or isinstance(summary.latest_review_version, bool)
                or not isinstance(summary.latest_review_version, int)
                or summary.latest_review_version < 1
            ):
                return False
            key = (run.created_at, run.run_id)
            if previous is not None and previous < key:
                return False
            previous = key
            seen.add(run.run_id)
        return True

    @classmethod
    def _valid_read_model(
        cls,
        result: object,
        project_id: str,
        run_id: str,
        scope: ReviewScopeContext,
        requested_version: int | None,
    ) -> bool:
        if not isinstance(result, ReviewReadModel):
            return False
        run = result.run
        snapshot = result.snapshot
        versions = result.available_review_versions
        if (
            not isinstance(snapshot, ReviewSnapshot)
            or not isinstance(snapshot.items, tuple)
            or any(not isinstance(item, ReviewSnapshotItem) for item in snapshot.items)
            or not isinstance(versions, tuple)
            or not versions
            or any(
                isinstance(version, bool)
                or not isinstance(version, int)
                or version < 1
                for version in versions
            )
        ):
            return False
        if (
            not cls._read_run_in_scope(run, project_id, scope)
            or run.run_id != run_id
            or not cls._valid_run_shape(run)
            or versions != tuple(range(1, versions[-1] + 1))
            or snapshot.version not in versions
            or not isinstance(snapshot.version, int)
            or isinstance(snapshot.version, bool)
            or (requested_version is None and snapshot.version != versions[-1])
            or (requested_version is not None and snapshot.version != requested_version)
            or snapshot.run_id != run.run_id
            or not cls._canonical_uuid(snapshot.snapshot_id)
            or snapshot.created_by != scope.actor_id
            or not cls._aware(snapshot.created_at)
            or not cls._complete_snapshot(run, snapshot)
            or not isinstance(result.candidates, tuple)
            or len(result.candidates) != run.candidate_count
        ):
            return False
        snapshot_items = {item.candidate_id: item for item in snapshot.items}
        candidate_ids: set[str] = set()
        for expected_ordinal, view in enumerate(result.candidates, start=1):
            if not isinstance(view, ReviewCandidateView) or view.ordinal != expected_ordinal:
                return False
            candidate = view.candidate
            if not isinstance(candidate, Candidate) or candidate.candidate_id in candidate_ids:
                return False
            item = snapshot_items.get(candidate.candidate_id)
            citation = candidate.source_citation
            if (
                item is None
                or item.status != view.status
                or item.replacement_text != view.replacement_text
                or candidate.review_status != "pending"
                or citation.source_version_id != run.proposal_version_id
                or citation.source_sha256.lower() != run.proposal_sha256.lower()
                or view.status not in REVIEW_STATUSES
                or (view.status == "modify") != (view.replacement_text is not None)
                or (
                    view.replacement_text is not None
                    and (not isinstance(view.replacement_text, str) or not view.replacement_text.strip())
                )
            ):
                return False
            candidate_ids.add(candidate.candidate_id)
        if tuple(item.candidate_id for item in snapshot.items) != tuple(
            view.candidate.candidate_id for view in result.candidates
        ):
            return False
        for view in result.candidates:
            last = view.last_decision
            if view.status == "pending":
                if last is not None:
                    return False
                continue
            if (
                not isinstance(last, ReviewDecision)
                or last.run_id != run.run_id
                or not 2 <= last.review_version <= snapshot.version
                or last.candidate_id != view.candidate.candidate_id
                or last.action != view.status
                or last.replacement_text != view.replacement_text
                or last.actor_id != scope.actor_id
                or not isinstance(last.reason, str)
                or not last.reason.strip()
                or not cls._canonical_uuid(last.decision_id)
                or (
                    last.comment is not None
                    and (not isinstance(last.comment, str) or not last.comment.strip())
                )
                or not cls._aware(last.created_at)
            ):
                return False
        decision = result.selected_decision
        if snapshot.version == 1:
            return decision is None
        if not isinstance(decision, ReviewDecision):
            return False
        decided = next(
            (
                view
                for view in result.candidates
                if view.candidate.candidate_id == decision.candidate_id
            ),
            None,
        )
        return bool(
            decision.run_id == run.run_id
            and decision.review_version == snapshot.version
            and decision.action in REVIEW_ACTIONS
            and isinstance(decision.reason, str)
            and decision.reason.strip()
            and cls._canonical_uuid(decision.decision_id)
            and (
                decision.comment is None
                or (isinstance(decision.comment, str) and bool(decision.comment.strip()))
            )
            and decision.actor_id == scope.actor_id
            and cls._aware(decision.created_at)
            and decided is not None
            and decided.status == decision.action
            and decided.replacement_text == decision.replacement_text
            and decided.last_decision == decision
            and (decision.action == "modify") == (decision.replacement_text is not None)
        )

    @staticmethod
    def _read_run_in_scope(
        run: object, project_id: str, scope: ReviewScopeContext
    ) -> bool:
        return isinstance(run, ReviewRun) and (
            run.organization_id,
            run.workspace_id,
            run.client_id,
            run.project_id,
            run.created_by,
        ) == (
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            project_id,
            scope.actor_id,
        )

    @classmethod
    def _valid_run_shape(cls, run: ReviewRun) -> bool:
        return bool(
            all(
                cls._canonical_uuid(value)
                for value in (
                    run.run_id,
                    run.organization_id,
                    run.workspace_id,
                    run.client_id,
                    run.project_id,
                    run.proposal_artifact_id,
                    run.proposal_version_id,
                    run.created_by,
                )
            )
            and isinstance(run.candidate_count, int)
            and not isinstance(run.candidate_count, bool)
            and run.candidate_count > 0
            and isinstance(run.proposal_version, int)
            and not isinstance(run.proposal_version, bool)
            and run.proposal_version > 0
            and isinstance(run.proposal_sha256, str)
            and SHA256_PATTERN.fullmatch(run.proposal_sha256) is not None
            and cls._aware(run.created_at)
        )

    @staticmethod
    def _canonical_uuid(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return str(UUID(value)) == value
        except (ValueError, TypeError, AttributeError):
            return False

    @staticmethod
    def _aware(value: object) -> bool:
        return (
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None
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
        if str(parsed) != value:
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

