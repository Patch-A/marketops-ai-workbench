"""Scoped asyncpg persistence for the M2-03 approval ledger.

The repository deliberately has no cross-project discovery read.  A caller may
only create a target-project citation through ``cite_approved_knowledge``;
within that one transaction it derives the source approval under the source
project's RLS scope, then validates and writes under the target project's scope.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Callable
from uuid import UUID

from .approval import (
    ApprovalDecisionRequest,
    CandidateKnowledge,
    CitationAuthorization,
    KnowledgeApprovalState,
    KnowledgePromotion,
    apply_approval_decision,
    approval_request_digest,
    authorize_citation,
    new_approval_state,
)
from .service import LearningFailure, LearningScopeContext


_SET_SCOPE_SQL = (
    "SELECT set_config('app.workspace_id', $1, true), "
    "set_config('app.client_id', $2, true), "
    "set_config('app.project_id', $3, true), "
    "set_config('app.actor_id', $4, true)"
)
_LOCK_SQL = "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1::text, 0))"
_CANDIDATE_SQL = """
SELECT item.id AS knowledge_id, item.project_id AS source_project_id,
       item.content, item.content_sha256, version.evidence_digest
FROM marketops.knowledge_items AS item
JOIN marketops.knowledge_item_versions AS version
  ON version.knowledge_id = item.id AND version.version = 1
WHERE item.id = $1
  AND item.organization_id = $2 AND item.workspace_id = $3
  AND item.client_id = $4 AND item.project_id = $5 AND item.created_by = $6
  AND item.scope = 'project' AND item.status = 'candidate'
  AND version.organization_id = $2 AND version.workspace_id = $3
  AND version.client_id = $4 AND version.project_id = $5 AND version.created_by = $6
"""
_ROOT_SQL = """
SELECT id, knowledge_id
FROM marketops.knowledge_promotion_roots
WHERE organization_id = $1 AND workspace_id = $2 AND client_id = $3
  AND project_id = $4 AND knowledge_id = $5 AND created_by = $6
"""
_HEAD_LOCK_SQL = """
SELECT version
FROM marketops.knowledge_promotion_versions
WHERE promotion_id = $1 AND organization_id = $2 AND workspace_id = $3
  AND client_id = $4 AND project_id = $5 AND created_by = $6
ORDER BY version DESC
LIMIT 1
"""
_VERSIONS_SQL = """
SELECT version, action, status, effective_scope, content, content_sha256,
       reason, created_by, request_sha256, replay_sha256
FROM marketops.knowledge_promotion_versions
WHERE promotion_id = $1 AND organization_id = $2 AND workspace_id = $3
  AND client_id = $4 AND project_id = $5 AND created_by = $6
ORDER BY version
"""
_REQUEST_REPLAY_SQL = """
SELECT version
FROM marketops.knowledge_promotion_versions
WHERE promotion_id = $1 AND request_sha256 = decode($2, 'hex')
  AND organization_id = $3 AND workspace_id = $4 AND client_id = $5
  AND project_id = $6 AND created_by = $7
"""
_INSERT_ROOT_SQL = """
INSERT INTO marketops.knowledge_promotion_roots(
    id, organization_id, workspace_id, client_id, project_id, knowledge_id,
    created_by, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""
_INSERT_VERSION_SQL = """
INSERT INTO marketops.knowledge_promotion_versions(
    promotion_id, version, organization_id, workspace_id, client_id, project_id,
    knowledge_id, action, status, effective_scope, content, content_sha256,
    reason, request_sha256, replay_sha256, created_by, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, decode($12, 'hex'),
          $13, decode($14, 'hex'), decode($15, 'hex'), $16, $17)
"""
_TARGET_PROJECT_SQL = """
SELECT id
FROM marketops.projects
WHERE id = $1 AND organization_id = $2 AND workspace_id = $3 AND client_id = $4
  AND created_by = $5
"""
_CITATION_REPLAY_SQL = """
SELECT id, source_project_id, source_knowledge_id, source_promotion_id,
       source_promotion_version, source_scope, content, content_sha256, reason,
       citation_sha256, created_at
FROM marketops.knowledge_citations
WHERE project_id = $1 AND source_promotion_id = $2 AND source_promotion_version = $3
  AND citation_sha256 = decode($4, 'hex')
  AND organization_id = $5 AND workspace_id = $6 AND client_id = $7 AND created_by = $8
"""
_INSERT_CITATION_SQL = """
INSERT INTO marketops.knowledge_citations(
    id, organization_id, workspace_id, client_id, project_id, source_project_id,
    source_knowledge_id, source_promotion_id, source_promotion_version,
    source_scope, content, content_sha256, reason, citation_sha256, created_by,
    created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, decode($12, 'hex'),
          $13, decode($14, 'hex'), $15, $16)
"""
_TARGET_CITATIONS_SQL = """
SELECT id, source_project_id, source_knowledge_id, source_promotion_id,
       source_promotion_version, source_scope, content, content_sha256, reason,
       citation_sha256, created_at
FROM marketops.knowledge_citations
WHERE organization_id = $1 AND workspace_id = $2 AND client_id = $3
  AND project_id = $4 AND created_by = $5
ORDER BY created_at, id
"""
_INSERT_AUDIT_SQL = """
INSERT INTO marketops.audit_events(
    id, organization_id, workspace_id, client_id, project_id, actor_id, action,
    target_type, target_id, event_data, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
"""


@dataclass(frozen=True)
class ApprovalHistory:
    promotion_id: str | None
    state: KnowledgeApprovalState


@dataclass(frozen=True)
class ApprovalDecisionResult:
    history: ApprovalHistory
    replayed: bool


@dataclass(frozen=True)
class KnowledgeCitation:
    citation_id: str
    authorization: CitationAuthorization
    created_at: datetime


@dataclass(frozen=True)
class CitationWriteResult:
    citation: KnowledgeCitation
    replayed: bool


class AsyncpgKnowledgeApprovalRepository:
    """Append immutable decisions and citations under transaction-local RLS scope."""

    def __init__(
        self,
        pool: Any,
        *,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime],
    ) -> None:
        self._pool = pool
        self._id_factory = id_factory
        self._clock = clock

    async def decide(
        self,
        scope: LearningScopeContext,
        source_project_id: str,
        knowledge_id: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResult:
        """Append a source-project decision or replay the exact prior request."""
        _validate_scope(scope)
        _validate_uuid(source_project_id, "source project id")
        _validate_uuid(knowledge_id, "knowledge id")
        try:
            async with self._transaction(scope, source_project_id) as connection:
                # This serializes absent-root creation as well as head-version changes.
                await connection.fetchval(_LOCK_SQL, knowledge_id)
                try:
                    history = await self._load_source_history(
                        connection, scope, source_project_id, knowledge_id, lock_head=True
                    )
                except _CandidateNotFound:
                    raise LearningFailure(
                        "KNOWLEDGE_NOT_FOUND", "candidate knowledge was not found"
                    ) from None
                previous = _prefix(history.state, request.expected_version)
                request_digest = approval_request_digest(previous, request, scope)
                if history.promotion_id is not None:
                    replay = await connection.fetchrow(
                        _REQUEST_REPLAY_SQL,
                        history.promotion_id,
                        request_digest,
                        *_scope_values(scope, source_project_id),
                    )
                    if replay is not None:
                        replay_version = _row_int(replay, "version")
                        return ApprovalDecisionResult(
                            ApprovalHistory(
                                history.promotion_id,
                                _prefix(history.state, replay_version),
                            ),
                            True,
                        )

                next_state = apply_approval_decision(history.state, request, scope)
                promotion = next_state.current
                assert promotion is not None
                promotion_id = history.promotion_id or _new_id(self._id_factory)
                now = self._clock()
                if history.promotion_id is None:
                    await connection.execute(
                        _INSERT_ROOT_SQL,
                        promotion_id,
                        *_scope_values(scope, source_project_id)[:4],
                        knowledge_id,
                        scope.actor_id,
                        now,
                    )
                await connection.execute(
                    _INSERT_VERSION_SQL,
                    promotion_id,
                    promotion.version,
                    *_scope_values(scope, source_project_id)[:4],
                    knowledge_id,
                    promotion.action,
                    promotion.status,
                    promotion.effective_scope,
                    promotion.content,
                    promotion.content_sha256,
                    promotion.reason,
                    promotion.request_digest,
                    promotion.replay_digest,
                    scope.actor_id,
                    now,
                )
                await self._append_audit(
                    connection,
                    scope,
                    source_project_id,
                    "knowledge_promotion.decided",
                    "knowledge_promotion",
                    promotion_id,
                    {
                        "action": promotion.action,
                        "knowledgeId": knowledge_id,
                        "promotionVersion": promotion.version,
                        "replaySha256": promotion.replay_digest,
                        "status": promotion.status,
                    },
                    now,
                )
                return ApprovalDecisionResult(
                    ApprovalHistory(promotion_id, next_state), False
                )
        except (LearningFailure, asyncio.CancelledError):
            raise
        except Exception:
            raise LearningFailure(
                "KNOWLEDGE_APPROVAL_WRITE_FAILED",
                "knowledge decision could not be persisted",
            ) from None

    async def read_history(
        self,
        scope: LearningScopeContext,
        source_project_id: str,
        knowledge_id: str,
    ) -> ApprovalHistory | None:
        """Read one source-project candidate and its decisions; never cross-project."""
        _validate_scope(scope)
        _validate_uuid(source_project_id, "source project id")
        _validate_uuid(knowledge_id, "knowledge id")
        try:
            async with self._read_transaction(scope, source_project_id) as connection:
                try:
                    return await self._load_source_history(
                        connection, scope, source_project_id, knowledge_id, lock_head=False
                    )
                except _CandidateNotFound:
                    return None
        except (LearningFailure, asyncio.CancelledError):
            raise
        except Exception:
            raise LearningFailure(
                "KNOWLEDGE_APPROVAL_READ_FAILED",
                "knowledge approval history could not be read",
            ) from None

    async def cite_approved_knowledge(
        self,
        scope: LearningScopeContext,
        *,
        source_project_id: str,
        source_knowledge_id: str,
        target_project_id: str,
        reason: str,
    ) -> CitationWriteResult:
        """Create a citation only after server-side source and target checks.

        This is intentionally an action, not a generic source-project read API.
        The database trigger repeats the final head/scope/content check at commit.
        """
        _validate_scope(scope)
        _validate_uuid(source_project_id, "source project id")
        _validate_uuid(source_knowledge_id, "source knowledge id")
        _validate_uuid(target_project_id, "target project id")
        try:
            async with self._dual_scope_transaction(
                scope, source_project_id
            ) as connection:
                await connection.fetchval(_LOCK_SQL, source_knowledge_id)
                try:
                    history = await self._load_source_history(
                        connection,
                        scope,
                        source_project_id,
                        source_knowledge_id,
                        lock_head=True,
                    )
                except _CandidateNotFound:
                    raise LearningFailure(
                        "KNOWLEDGE_NOT_ELIGIBLE", "knowledge is not approved for citation"
                    ) from None
                if history.promotion_id is None:
                    raise LearningFailure(
                        "KNOWLEDGE_NOT_ELIGIBLE", "knowledge is not approved for citation"
                    )
                authorization = authorize_citation(
                    history.state, target_project_id, scope, reason
                )
                await self._set_scope(connection, scope, target_project_id)
                target = await connection.fetchrow(
                    _TARGET_PROJECT_SQL,
                    target_project_id,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    scope.actor_id,
                )
                if target is None:
                    raise LearningFailure(
                        "TARGET_PROJECT_NOT_FOUND", "target project is unavailable"
                    )
                replay = await connection.fetchrow(
                    _CITATION_REPLAY_SQL,
                    target_project_id,
                    history.promotion_id,
                    authorization.promotion_version,
                    authorization.authorization_digest,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    scope.actor_id,
                )
                if replay is not None:
                    return CitationWriteResult(
                        _citation_from_row(replay, authorization), True
                    )

                now = self._clock()
                citation_id = _new_id(self._id_factory)
                await connection.execute(
                    _INSERT_CITATION_SQL,
                    citation_id,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    target_project_id,
                    authorization.source_project_id,
                    authorization.knowledge_id,
                    history.promotion_id,
                    authorization.promotion_version,
                    authorization.effective_scope,
                    authorization.content,
                    authorization.content_sha256,
                    authorization.reason,
                    authorization.authorization_digest,
                    scope.actor_id,
                    now,
                )
                await self._append_audit(
                    connection,
                    scope,
                    target_project_id,
                    "knowledge_citation.created",
                    "knowledge_citation",
                    citation_id,
                    {
                        "citationSha256": authorization.authorization_digest,
                        "promotionId": history.promotion_id,
                        "promotionVersion": authorization.promotion_version,
                        "sourceKnowledgeId": authorization.knowledge_id,
                        "sourceProjectId": authorization.source_project_id,
                    },
                    now,
                )
                # The deferred citation trigger validates the source approval at
                # commit time. Restore its source-project RLS scope after the
                # target-scoped writes have completed.
                await self._set_scope(connection, scope, source_project_id)
                return CitationWriteResult(
                    KnowledgeCitation(citation_id, authorization, now), False
                )
        except (LearningFailure, asyncio.CancelledError):
            raise
        except Exception:
            raise LearningFailure(
                "KNOWLEDGE_CITATION_WRITE_FAILED",
                "knowledge citation could not be persisted",
            ) from None

    async def list_effective_citations(
        self, scope: LearningScopeContext, target_project_id: str
    ) -> tuple[KnowledgeCitation, ...]:
        """List only target citations whose source approval remains effective.

        Citation rows are immutable provenance snapshots, not authorization grants.
        Each row is re-authorized under its source project's RLS scope before it
        is returned, so revocation and superseding decisions fail closed.
        """
        _validate_scope(scope)
        _validate_uuid(target_project_id, "target project id")
        try:
            async with self._read_transaction(scope, target_project_id) as connection:
                rows = await connection.fetch(
                    _TARGET_CITATIONS_SQL,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    target_project_id,
                    scope.actor_id,
                )
            effective: list[KnowledgeCitation] = []
            for row in rows:
                source_project_id = _row_uuid(row, "source_project_id")
                source_knowledge_id = _row_uuid(row, "source_knowledge_id")
                history = await self.read_history(scope, source_project_id, source_knowledge_id)
                if history is None or history.promotion_id is None:
                    continue
                try:
                    authorization = authorize_citation(
                        history.state, target_project_id, scope, _row_text(row, "reason")
                    )
                except LearningFailure:
                    continue
                if (
                    history.promotion_id != _row_uuid(row, "source_promotion_id")
                    or authorization.promotion_version != _row_int(row, "source_promotion_version")
                    or authorization.effective_scope != _row_text(row, "source_scope")
                    or authorization.content != _row_text(row, "content")
                    or authorization.content_sha256 != _row_hash(row, "content_sha256")
                ):
                    continue
                effective.append(_citation_from_row(row, authorization))
            return tuple(effective)
        except (LearningFailure, asyncio.CancelledError):
            raise
        except Exception:
            raise LearningFailure(
                "KNOWLEDGE_APPROVAL_READ_FAILED",
                "effective knowledge citations could not be read",
            ) from None

    async def _load_source_history(
        self,
        connection: Any,
        scope: LearningScopeContext,
        source_project_id: str,
        knowledge_id: str,
        *,
        lock_head: bool,
    ) -> ApprovalHistory:
        values = _scope_values(scope, source_project_id)
        candidate_row = await connection.fetchrow(_CANDIDATE_SQL, knowledge_id, *values)
        if candidate_row is None:
            raise _CandidateNotFound()
        candidate = CandidateKnowledge(
            knowledge_id=_row_uuid(candidate_row, "knowledge_id"),
            source_project_id=_row_uuid(candidate_row, "source_project_id"),
            content=_row_text(candidate_row, "content"),
            content_sha256=_row_hash(candidate_row, "content_sha256"),
            evidence_digest=_row_hash(candidate_row, "evidence_digest"),
        )
        state = new_approval_state(candidate, scope)
        root = await connection.fetchrow(_ROOT_SQL, *values[:4], knowledge_id, scope.actor_id)
        if root is None:
            return ApprovalHistory(None, state)
        promotion_id = _row_uuid(root, "id")
        if _row_uuid(root, "knowledge_id") != candidate.knowledge_id:
            raise LearningFailure("KNOWLEDGE_APPROVAL_INVALID", "approval source is invalid")
        if lock_head:
            await connection.fetchrow(_HEAD_LOCK_SQL, promotion_id, *values)
        rows = await connection.fetch(_VERSIONS_SQL, promotion_id, *values)
        decisions = tuple(_promotion_from_row(row) for row in rows)
        return ApprovalHistory(
            promotion_id,
            KnowledgeApprovalState(
                candidate=candidate,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                client_id=scope.client_id,
                decisions=decisions,
            ),
        )

    async def _append_audit(
        self,
        connection: Any,
        scope: LearningScopeContext,
        project_id: str,
        action: str,
        target_type: str,
        target_id: str,
        event_data: dict[str, object],
        now: datetime,
    ) -> None:
        # The payload intentionally includes ids, versions, and hashes only.
        import json

        await connection.execute(
            _INSERT_AUDIT_SQL,
            _new_id(self._id_factory),
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            project_id,
            scope.actor_id,
            action,
            target_type,
            target_id,
            json.dumps(event_data, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            now,
        )

    @asynccontextmanager
    async def _transaction(
        self, scope: LearningScopeContext, project_id: str
    ) -> AsyncIterator[Any]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await self._set_scope(connection, scope, project_id)
                yield connection

    @asynccontextmanager
    async def _read_transaction(
        self, scope: LearningScopeContext, project_id: str
    ) -> AsyncIterator[Any]:
        async with self._pool.acquire() as connection:
            async with connection.transaction(readonly=True):
                await self._set_scope(connection, scope, project_id)
                yield connection

    @asynccontextmanager
    async def _dual_scope_transaction(
        self, scope: LearningScopeContext, source_project_id: str
    ) -> AsyncIterator[Any]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await self._set_scope(connection, scope, source_project_id)
                yield connection

    @staticmethod
    async def _set_scope(
        connection: Any, scope: LearningScopeContext, project_id: str
    ) -> None:
        await connection.execute(
            _SET_SCOPE_SQL,
            scope.workspace_id,
            scope.client_id,
            project_id,
            scope.actor_id,
        )


class _CandidateNotFound(Exception):
    pass


def _scope_values(scope: LearningScopeContext, project_id: str) -> tuple[str, str, str, str, str]:
    return (
        scope.organization_id,
        scope.workspace_id,
        scope.client_id,
        project_id,
        scope.actor_id,
    )


def _prefix(state: KnowledgeApprovalState, version: int) -> KnowledgeApprovalState:
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise LearningFailure("INVALID_INPUT", "knowledge decision version is invalid")
    if version > state.version:
        # Let the pure transition report a stable stale-version failure afterwards.
        return state
    return KnowledgeApprovalState(
        candidate=state.candidate,
        organization_id=state.organization_id,
        workspace_id=state.workspace_id,
        client_id=state.client_id,
        decisions=state.decisions[:version],
    )


def _promotion_from_row(row: Any) -> KnowledgePromotion:
    return KnowledgePromotion(
        version=_row_int(row, "version"),
        action=_row_text(row, "action"),
        status=_row_text(row, "status"),
        effective_scope=_row_optional_text(row, "effective_scope"),
        content=_row_optional_text(row, "content"),
        content_sha256=_row_optional_hash(row, "content_sha256"),
        reason=_row_optional_text(row, "reason"),
        decided_by=_row_uuid(row, "created_by"),
        request_digest=_row_hash(row, "request_sha256"),
        replay_digest=_row_hash(row, "replay_sha256"),
    )


def _citation_from_row(row: Any, authorization: CitationAuthorization) -> KnowledgeCitation:
    if (
        _row_uuid(row, "source_project_id") != authorization.source_project_id
        or _row_uuid(row, "source_knowledge_id") != authorization.knowledge_id
        or _row_int(row, "source_promotion_version") != authorization.promotion_version
        or _row_text(row, "source_scope") != authorization.effective_scope
        or _row_text(row, "content") != authorization.content
        or _row_hash(row, "content_sha256") != authorization.content_sha256
        or _row_text(row, "reason") != authorization.reason
        or _row_hash(row, "citation_sha256") != authorization.authorization_digest
        or not isinstance(row["created_at"], datetime)
    ):
        raise LearningFailure("KNOWLEDGE_CITATION_INVALID", "citation replay is invalid")
    return KnowledgeCitation(_row_uuid(row, "id"), authorization, row["created_at"])


def _validate_scope(scope: LearningScopeContext) -> None:
    if not isinstance(scope, LearningScopeContext):
        raise LearningFailure("INVALID_SCOPE", "learning scope is invalid")
    for value in (
        scope.organization_id,
        scope.workspace_id,
        scope.client_id,
        scope.actor_id,
    ):
        _validate_uuid(value, "scope identity", code="INVALID_SCOPE")


def _validate_uuid(value: object, label: str, *, code: str = "INVALID_INPUT") -> None:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value.lower():
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise LearningFailure(code, f"{label} is invalid") from None


def _new_id(factory: Callable[[], str]) -> str:
    value = factory()
    _validate_uuid(value, "generated identity")
    return value


def _row_uuid(row: Any, field: str) -> str:
    value = str(row[field])
    _validate_uuid(value, "database identity", code="KNOWLEDGE_APPROVAL_INVALID")
    return value


def _row_int(row: Any, field: str) -> int:
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LearningFailure("KNOWLEDGE_APPROVAL_INVALID", "database version is invalid")
    return value


def _row_text(row: Any, field: str) -> str:
    value = row[field]
    if not isinstance(value, str):
        raise LearningFailure("KNOWLEDGE_APPROVAL_INVALID", "database text is invalid")
    return value


def _row_optional_text(row: Any, field: str) -> str | None:
    value = row[field]
    if value is not None and not isinstance(value, str):
        raise LearningFailure("KNOWLEDGE_APPROVAL_INVALID", "database text is invalid")
    return value


def _row_hash(row: Any, field: str) -> str:
    value = row[field]
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) != 32:
        raise LearningFailure("KNOWLEDGE_APPROVAL_INVALID", "database hash is invalid")
    return bytes(value).hex()


def _row_optional_hash(row: Any, field: str) -> str | None:
    return None if row[field] is None else _row_hash(row, field)
