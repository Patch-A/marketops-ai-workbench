"""Pure M2-03 knowledge approval and citation state machine.

Candidate knowledge is an immutable M2-02 record.  This module only models the
append-only decision history that may make a rendering of that candidate usable.
Persistence, authorization roles, and idempotent database writes deliberately
remain outside this dependency-free domain contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .service import LearningFailure, LearningScopeContext


APPROVAL_ACTIONS = frozenset({"approve", "revise", "reject", "revoke", "elevate"})
APPROVED_STATUS = "approved"
REJECTED_STATUS = "rejected"
REVOKED_STATUS = "revoked"
# Workspace-wide reuse needs a separate, verifiable role/consent model.  M2-03
# intentionally stops at an explicit client elevation until that exists.
DECISION_SCOPES = ("project", "client")
MAX_APPROVED_CONTENT_LENGTH = 4000
MAX_DECISION_REASON_LENGTH = 1000
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CandidateKnowledge:
    """Immutable candidate facts copied from the server-derived M2-02 record."""

    knowledge_id: str
    source_project_id: str
    content: str
    content_sha256: str
    evidence_digest: str


@dataclass(frozen=True)
class ApprovalDecisionRequest:
    """Closed decision input; scope facts always come from server context."""

    action: str
    expected_version: int
    effective_scope: str | None = None
    revised_content: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class KnowledgePromotion:
    """One immutable decision/promotion version in chronological order."""

    version: int
    action: str
    status: str
    effective_scope: str | None
    content: str | None
    content_sha256: str | None
    reason: str | None
    decided_by: str
    request_digest: str
    replay_digest: str


@dataclass(frozen=True)
class KnowledgeApprovalState:
    """Source tenant facts plus immutable candidate and append-only decisions."""

    candidate: CandidateKnowledge
    organization_id: str
    workspace_id: str
    client_id: str
    decisions: tuple[KnowledgePromotion, ...] = ()

    @property
    def version(self) -> int:
        return len(self.decisions)

    @property
    def current(self) -> KnowledgePromotion | None:
        return self.decisions[-1] if self.decisions else None

    @property
    def replay_digest(self) -> str:
        return _canonical_sha256(_state_material(self))


@dataclass(frozen=True)
class CitationAuthorization:
    """A server-side authorization result to bind into an eventual citation row."""

    knowledge_id: str
    promotion_version: int
    source_project_id: str
    target_project_id: str
    effective_scope: str
    content: str
    content_sha256: str
    reason: str
    authorization_digest: str


def new_approval_state(
    candidate: CandidateKnowledge, source_scope: LearningScopeContext
) -> KnowledgeApprovalState:
    """Validate server-derived candidate and source tenant context once."""
    _validate_scope(source_scope)
    _validate_candidate(candidate)
    return KnowledgeApprovalState(
        candidate=candidate,
        organization_id=source_scope.organization_id,
        workspace_id=source_scope.workspace_id,
        client_id=source_scope.client_id,
    )


def apply_approval_decision(
    state: KnowledgeApprovalState,
    request: ApprovalDecisionRequest,
    actor_scope: LearningScopeContext,
) -> KnowledgeApprovalState:
    """Append one valid decision without mutating candidate facts or history."""
    _validate_state(state)
    _validate_scope(actor_scope)
    _validate_request(request)
    _require_same_source_tenant(state, actor_scope)
    if request.expected_version != state.version:
        raise LearningFailure("DECISION_VERSION_CONFLICT", "knowledge decision version is stale")

    current = state.current
    if current is None:
        promotion = _initial_promotion(state, request, actor_scope)
    elif current.status == APPROVED_STATUS:
        promotion = _approved_promotion(state, current, request, actor_scope)
    else:
        raise LearningFailure("INVALID_TRANSITION", "knowledge decision is terminal")
    return KnowledgeApprovalState(
        candidate=state.candidate,
        organization_id=state.organization_id,
        workspace_id=state.workspace_id,
        client_id=state.client_id,
        decisions=state.decisions + (promotion,),
    )


def approval_request_digest(
    state: KnowledgeApprovalState,
    request: ApprovalDecisionRequest,
    actor_scope: LearningScopeContext,
) -> str:
    """Return a canonical identity for a request at a particular prior version."""
    _validate_state(state)
    _validate_scope(actor_scope)
    _validate_request(request)
    _require_same_source_tenant(state, actor_scope)
    return _request_digest(state, request, actor_scope)


def authorize_citation(
    state: KnowledgeApprovalState,
    target_project_id: str,
    target_scope: LearningScopeContext,
    reason: str,
) -> CitationAuthorization:
    """Fail closed unless the latest approved promotion covers the target project."""
    _validate_state(state)
    _validate_scope(target_scope)
    _uuid(target_project_id, "target project id")
    bounded_reason = _required_text(reason, "citation reason", MAX_DECISION_REASON_LENGTH)
    current = state.current
    if current is None or current.status != APPROVED_STATUS:
        raise LearningFailure("KNOWLEDGE_NOT_ELIGIBLE", "knowledge is not approved for citation")
    _require_target_scope_coverage(state, current.effective_scope, target_project_id, target_scope)
    assert current.effective_scope is not None
    assert current.content is not None
    assert current.content_sha256 is not None
    material = {
        "knowledgeId": state.candidate.knowledge_id,
        "promotionVersion": current.version,
        "sourceProjectId": state.candidate.source_project_id,
        "targetProjectId": target_project_id,
        "effectiveScope": current.effective_scope,
        "contentSha256": current.content_sha256,
        "reason": bounded_reason,
    }
    return CitationAuthorization(
        knowledge_id=state.candidate.knowledge_id,
        promotion_version=current.version,
        source_project_id=state.candidate.source_project_id,
        target_project_id=target_project_id,
        effective_scope=current.effective_scope,
        content=current.content,
        content_sha256=current.content_sha256,
        reason=bounded_reason,
        authorization_digest=_canonical_sha256(material),
    )


def _initial_promotion(
    state: KnowledgeApprovalState,
    request: ApprovalDecisionRequest,
    actor_scope: LearningScopeContext,
) -> KnowledgePromotion:
    if request.action == "reject":
        reason = _required_reason(request.reason)
        _require_absent(request.effective_scope, "effective scope")
        _require_absent(request.revised_content, "revised content")
        return _promotion(state, request, actor_scope, REJECTED_STATUS, None, None, None, reason)
    if request.action not in {"approve", "revise"}:
        raise LearningFailure("INVALID_TRANSITION", "candidate must be approved, revised, or rejected")
    _require_scope(request.effective_scope, "project")
    if request.action == "approve":
        _require_absent(request.revised_content, "revised content")
        _require_absent(request.reason, "reason")
        content = state.candidate.content
    else:
        content = _required_text(request.revised_content, "revised content", MAX_APPROVED_CONTENT_LENGTH)
        if content == state.candidate.content:
            raise LearningFailure("INVALID_INPUT", "revised content must differ from candidate content")
        _required_reason(request.reason)
    return _promotion(
        state,
        request,
        actor_scope,
        APPROVED_STATUS,
        "project",
        content,
        _sha256_text(content),
        request.reason,
    )


def _approved_promotion(
    state: KnowledgeApprovalState,
    current: KnowledgePromotion,
    request: ApprovalDecisionRequest,
    actor_scope: LearningScopeContext,
) -> KnowledgePromotion:
    if request.action == "revoke":
        _require_absent(request.effective_scope, "effective scope")
        _require_absent(request.revised_content, "revised content")
        return _promotion(state, request, actor_scope, REVOKED_STATUS, None, None, None, _required_reason(request.reason))
    if request.action == "revise":
        _require_scope(request.effective_scope, current.effective_scope)
        content = _required_text(request.revised_content, "revised content", MAX_APPROVED_CONTENT_LENGTH)
        if content == current.content:
            raise LearningFailure("INVALID_INPUT", "revised content must differ from current content")
        return _promotion(
            state,
            request,
            actor_scope,
            APPROVED_STATUS,
            current.effective_scope,
            content,
            _sha256_text(content),
            _required_reason(request.reason),
        )
    if request.action == "elevate":
        _require_absent(request.revised_content, "revised content")
        _require_next_scope(current.effective_scope, request.effective_scope)
        return _promotion(
            state,
            request,
            actor_scope,
            APPROVED_STATUS,
            request.effective_scope,
            current.content,
            current.content_sha256,
            _required_reason(request.reason),
        )
    raise LearningFailure("INVALID_TRANSITION", "approved knowledge can only be revised, elevated, or revoked")


def _promotion(
    state: KnowledgeApprovalState,
    request: ApprovalDecisionRequest,
    actor_scope: LearningScopeContext,
    status: str,
    effective_scope: str | None,
    content: str | None,
    content_sha256: str | None,
    reason: str | None,
) -> KnowledgePromotion:
    version = state.version + 1
    request_digest = _request_digest(state, request, actor_scope)
    material = {
        "candidate": _candidate_material(state.candidate),
        "priorReplayDigest": state.replay_digest,
        "version": version,
        "action": request.action,
        "status": status,
        "effectiveScope": effective_scope,
        "contentSha256": content_sha256,
        "reason": reason,
        "decidedBy": actor_scope.actor_id,
        "requestDigest": request_digest,
    }
    return KnowledgePromotion(
        version=version,
        action=request.action,
        status=status,
        effective_scope=effective_scope,
        content=content,
        content_sha256=content_sha256,
        reason=reason,
        decided_by=actor_scope.actor_id,
        request_digest=request_digest,
        replay_digest=_canonical_sha256(material),
    )


def _validate_state(state: KnowledgeApprovalState) -> None:
    if not isinstance(state, KnowledgeApprovalState):
        raise LearningFailure("INVALID_INPUT", "knowledge approval state is invalid")
    _validate_candidate(state.candidate)
    for identity in (state.organization_id, state.workspace_id, state.client_id):
        _uuid(identity, "source scope identity")
    if not isinstance(state.decisions, tuple):
        raise LearningFailure("INVALID_INPUT", "knowledge decision history is invalid")
    expected_version = 1
    for decision in state.decisions:
        if not isinstance(decision, KnowledgePromotion) or decision.version != expected_version:
            raise LearningFailure("INVALID_INPUT", "knowledge decision history is invalid")
        expected_version += 1


def _validate_candidate(candidate: CandidateKnowledge) -> None:
    if not isinstance(candidate, CandidateKnowledge):
        raise LearningFailure("INVALID_INPUT", "candidate knowledge is invalid")
    _uuid(candidate.knowledge_id, "knowledge id")
    _uuid(candidate.source_project_id, "source project id")
    content = _required_text(candidate.content, "candidate content", MAX_APPROVED_CONTENT_LENGTH)
    _sha256(candidate.content_sha256, "candidate content digest")
    _sha256(candidate.evidence_digest, "candidate evidence digest")
    if candidate.content_sha256 != _sha256_text(content):
        raise LearningFailure("INVALID_INPUT", "candidate content digest does not match content")


def _validate_request(request: ApprovalDecisionRequest) -> None:
    if not isinstance(request, ApprovalDecisionRequest) or request.action not in APPROVAL_ACTIONS:
        raise LearningFailure("INVALID_INPUT", "knowledge decision action is invalid")
    if isinstance(request.expected_version, bool) or not isinstance(request.expected_version, int) or request.expected_version < 0:
        raise LearningFailure("INVALID_INPUT", "knowledge decision version is invalid")


def _validate_scope(scope: LearningScopeContext) -> None:
    if not isinstance(scope, LearningScopeContext):
        raise LearningFailure("INVALID_SCOPE", "learning scope is invalid")
    for identity in (scope.organization_id, scope.workspace_id, scope.client_id, scope.actor_id):
        _uuid(identity, "scope identity", "INVALID_SCOPE")


def _require_same_source_tenant(state: KnowledgeApprovalState, scope: LearningScopeContext) -> None:
    if (scope.organization_id, scope.workspace_id, scope.client_id) != (
        state.organization_id,
        state.workspace_id,
        state.client_id,
    ):
        raise LearningFailure("KNOWLEDGE_SCOPE_DENIED", "knowledge is outside the source tenant scope")


def _require_target_scope_coverage(
    state: KnowledgeApprovalState,
    effective_scope: str | None,
    target_project_id: str,
    target_scope: LearningScopeContext,
) -> None:
    if target_scope.organization_id != state.organization_id:
        raise LearningFailure("KNOWLEDGE_SCOPE_DENIED", "knowledge is outside the target organization")
    if effective_scope == "project":
        if target_project_id != state.candidate.source_project_id or (
            target_scope.workspace_id != state.workspace_id
            or target_scope.client_id != state.client_id
        ):
            raise LearningFailure("KNOWLEDGE_SCOPE_DENIED", "project knowledge is not available to the target project")
    elif effective_scope == "client":
        if target_scope.workspace_id != state.workspace_id or target_scope.client_id != state.client_id:
            raise LearningFailure("KNOWLEDGE_SCOPE_DENIED", "client knowledge is not available to the target project")
    else:
        raise LearningFailure("KNOWLEDGE_NOT_ELIGIBLE", "knowledge has no valid effective scope")


def _request_digest(state: KnowledgeApprovalState, request: ApprovalDecisionRequest, scope: LearningScopeContext) -> str:
    return _canonical_sha256(
        {
            "knowledgeId": state.candidate.knowledge_id,
            "priorReplayDigest": state.replay_digest,
            "action": request.action,
            "expectedVersion": request.expected_version,
            "effectiveScope": request.effective_scope,
            "revisedContent": request.revised_content,
            "reason": request.reason,
            "actorId": scope.actor_id,
        }
    )


def _candidate_material(candidate: CandidateKnowledge) -> dict[str, str]:
    return {
        "knowledgeId": candidate.knowledge_id,
        "sourceProjectId": candidate.source_project_id,
        "contentSha256": candidate.content_sha256,
        "evidenceDigest": candidate.evidence_digest,
    }


def _state_material(state: KnowledgeApprovalState) -> dict[str, Any]:
    return {
        "candidate": _candidate_material(state.candidate),
        "organizationId": state.organization_id,
        "workspaceId": state.workspace_id,
        "clientId": state.client_id,
        "decisions": [
            {
                "version": item.version,
                "action": item.action,
                "status": item.status,
                "effectiveScope": item.effective_scope,
                "contentSha256": item.content_sha256,
                "reason": item.reason,
                "decidedBy": item.decided_by,
                "requestDigest": item.request_digest,
                "replayDigest": item.replay_digest,
            }
            for item in state.decisions
        ],
    }


def _require_scope(value: str | None, expected: str | None) -> None:
    if value != expected:
        raise LearningFailure("INVALID_SCOPE", "knowledge decision scope is invalid")


def _require_next_scope(current: str | None, next_scope: str | None) -> None:
    try:
        if DECISION_SCOPES.index(next_scope) != DECISION_SCOPES.index(current) + 1:
            raise ValueError
    except ValueError:
        raise LearningFailure("INVALID_SCOPE", "knowledge scope elevation must be one level") from None


def _require_absent(value: object, label: str) -> None:
    if value is not None:
        raise LearningFailure("INVALID_INPUT", f"{label} is not allowed for this action")


def _required_reason(value: object) -> str:
    return _required_text(value, "reason", MAX_DECISION_REASON_LENGTH)


def _required_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise LearningFailure("INVALID_INPUT", f"{label} is invalid")
    return value.strip()


def _uuid(value: object, label: str, code: str = "INVALID_INPUT") -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value.lower():
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise LearningFailure(code, f"{label} is invalid") from None
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise LearningFailure("INVALID_INPUT", f"{label} is invalid")
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
