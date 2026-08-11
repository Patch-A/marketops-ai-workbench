"""Asyncpg persistence adapter for immutable M1-02 extraction reviews."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from apps.api.marketops_extract import Candidate

from .service import (
    ApprovedProposal,
    ReviewAuditEvent,
    ReviewDecision,
    ReviewFailure,
    ReviewRun,
    ReviewScopeContext,
    ReviewSnapshot,
    ReviewSnapshotItem,
)


class ReviewPostgresError(RuntimeError):
    """A stable error that never includes SQL, credentials, or user content."""


_SET_SCOPE_SQL = """
SELECT
    set_config('app.workspace_id', $1, true),
    set_config('app.client_id', $2, true),
    set_config('app.project_id', $3, true),
    set_config('app.actor_id', $4, true)
"""

_ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"

_APPROVED_PROPOSAL_FOR_UPDATE_SQL = """
SELECT
    project.organization_id,
    project.workspace_id,
    project.client_id,
    project.id AS project_id,
    artifact.id AS artifact_id,
    version.id AS version_id,
    version.proposal_version,
    version.sha256
FROM marketops.projects AS project
JOIN marketops.artifacts AS artifact
  ON artifact.organization_id = project.organization_id
 AND artifact.workspace_id = project.workspace_id
 AND artifact.client_id = project.client_id
 AND artifact.project_id = project.id
 AND artifact.id = project.approved_proposal_artifact_id
JOIN marketops.artifact_versions AS version
  ON version.organization_id = artifact.organization_id
 AND version.workspace_id = artifact.workspace_id
 AND version.client_id = artifact.client_id
 AND version.project_id = artifact.project_id
 AND version.artifact_id = artifact.id
 AND version.id = project.approved_proposal_version_id
WHERE project.id = $1
  AND artifact.kind = 'proposal'
  AND version.approval_status = 'approved'
  AND version.proposal_version = project.approved_proposal_number
"""

_INSERT_RUN_SQL = """
INSERT INTO marketops.extraction_runs (
    id, organization_id, workspace_id, client_id, project_id,
    proposal_artifact_id, proposal_version_id, proposal_version,
    proposal_sha256, candidate_count, created_by, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
"""

_INSERT_CANDIDATE_SQL = """
INSERT INTO marketops.extraction_candidates (
    id, organization_id, workspace_id, client_id, project_id, run_id,
    ordinal, kind, candidate_text, classification, confidence,
    source_version_id, source_sha256, source_location, section_path,
    source_quote, review_status, created_by, created_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
    $12, $13, $14::jsonb, $15::jsonb, $16, $17, $18, $19
)
"""

_GET_RUN_SQL = """
SELECT
    id, organization_id, workspace_id, client_id, project_id,
    proposal_artifact_id, proposal_version_id, proposal_version,
    proposal_sha256, candidate_count, created_by, created_at
FROM marketops.extraction_runs
WHERE id = $1
"""

_VISIBLE_RUN_SQL = """
SELECT id
FROM marketops.extraction_runs
WHERE id = $1
"""

_LATEST_SNAPSHOT_SQL = """
SELECT id, run_id, review_version, created_by, created_at
FROM marketops.review_snapshots
WHERE run_id = $1
ORDER BY review_version DESC
LIMIT 1
"""

_SNAPSHOT_ITEMS_SQL = """
SELECT candidate_id, status, replacement_text
FROM marketops.review_snapshot_items
WHERE snapshot_id = $1
ORDER BY ordinal
"""

_INSERT_SNAPSHOT_SQL = """
INSERT INTO marketops.review_snapshots (
    id, organization_id, workspace_id, client_id, project_id,
    run_id, review_version, created_by, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
"""

_INSERT_SNAPSHOT_ITEM_SQL = """
INSERT INTO marketops.review_snapshot_items (
    organization_id, workspace_id, client_id, project_id, run_id,
    snapshot_id, candidate_id, ordinal, status, replacement_text,
    created_by, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
"""

_INSERT_DECISION_SQL = """
INSERT INTO marketops.review_decisions (
    id, organization_id, workspace_id, client_id, project_id, run_id,
    review_version, candidate_id, action, reason, comment,
    replacement_text, actor_id, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
"""

_INSERT_AUDIT_SQL = """
INSERT INTO marketops.audit_events (
    id, organization_id, workspace_id, client_id, project_id, actor_id,
    action, target_type, target_id, event_data, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
"""


class AsyncpgReviewRepository:
    def __init__(self, pool: Any):
        self._pool = pool

    @asynccontextmanager
    async def transaction(
        self, scope: ReviewScopeContext, project_id: str
    ) -> AsyncIterator["AsyncpgReviewTransaction"]:
        try:
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    await connection.fetchrow(
                        _SET_SCOPE_SQL,
                        scope.workspace_id,
                        scope.client_id,
                        project_id,
                        scope.actor_id,
                    )
                    yield AsyncpgReviewTransaction(connection, scope, project_id)
        except asyncio.CancelledError:
            raise
        except (ReviewFailure, ReviewPostgresError):
            raise
        except Exception:
            raise ReviewPostgresError("review database transaction failed") from None


class AsyncpgReviewTransaction:
    def __init__(
        self, connection: Any, scope: ReviewScopeContext, project_id: str
    ) -> None:
        self._connection = connection
        self._scope = scope
        self._project_id = project_id
        self._runs: dict[str, ReviewRun] = {}

    async def get_approved_proposal_for_update(
        self, project_id: str
    ) -> ApprovedProposal | None:
        self._require_project(project_id)
        await self._fetchrow(_ADVISORY_LOCK_SQL, f"review-project:{project_id}")
        row = await self._fetchrow(_APPROVED_PROPOSAL_FOR_UPDATE_SQL, project_id)
        if row is None:
            return None
        try:
            return ApprovedProposal(
                organization_id=str(row["organization_id"]),
                workspace_id=str(row["workspace_id"]),
                client_id=str(row["client_id"]),
                project_id=str(row["project_id"]),
                artifact_id=str(row["artifact_id"]),
                version_id=str(row["version_id"]),
                version=int(row["proposal_version"]),
                sha256=_hex(row["sha256"]),
            )
        except (KeyError, TypeError, ValueError):
            raise ReviewPostgresError("approved proposal row is invalid") from None

    async def insert_run(self, run: ReviewRun) -> None:
        self._require_run_scope(run)
        await self._execute(
            _INSERT_RUN_SQL,
            run.run_id,
            run.organization_id,
            run.workspace_id,
            run.client_id,
            run.project_id,
            run.proposal_artifact_id,
            run.proposal_version_id,
            run.proposal_version,
            bytes.fromhex(run.proposal_sha256),
            run.candidate_count,
            run.created_by,
            run.created_at,
        )
        self._runs[run.run_id] = run

    async def insert_candidates(
        self, run_id: str, candidates: tuple[Candidate, ...]
    ) -> None:
        run = self._known_run(run_id)
        if len(candidates) != run.candidate_count:
            raise ReviewPostgresError("candidate batch does not match the review run")
        for ordinal, candidate in enumerate(candidates, start=1):
            citation = candidate.source_citation
            await self._execute(
                _INSERT_CANDIDATE_SQL,
                candidate.candidate_id,
                run.organization_id,
                run.workspace_id,
                run.client_id,
                run.project_id,
                run_id,
                ordinal,
                candidate.kind,
                candidate.text,
                candidate.classification,
                float(candidate.confidence),
                citation.source_version_id,
                bytes.fromhex(citation.source_sha256),
                _json(citation.location.as_dict()),
                _json(list(citation.section_path)),
                citation.quote,
                candidate.review_status,
                self._scope.actor_id,
                run.created_at,
            )

    async def get_run(self, run_id: str) -> ReviewRun | None:
        row = await self._fetchrow(_GET_RUN_SQL, run_id)
        if row is None:
            return None
        run = _map_run(row)
        self._require_run_scope(run)
        self._runs[run.run_id] = run
        return run

    async def get_latest_snapshot_for_update(
        self, run_id: str
    ) -> ReviewSnapshot | None:
        run = self._known_run(run_id)
        await self._fetchrow(_ADVISORY_LOCK_SQL, f"review-run:{run_id}")
        if await self._fetchrow(_VISIBLE_RUN_SQL, run_id) is None:
            return None
        row = await self._fetchrow(_LATEST_SNAPSHOT_SQL, run_id)
        if row is None:
            return None
        items = await self._fetch(_SNAPSHOT_ITEMS_SQL, str(row["id"]))
        try:
            return ReviewSnapshot(
                snapshot_id=str(row["id"]),
                run_id=str(row["run_id"]),
                version=int(row["review_version"]),
                items=tuple(
                    ReviewSnapshotItem(
                        candidate_id=str(item["candidate_id"]),
                        status=item["status"],
                        replacement_text=item["replacement_text"],
                    )
                    for item in items
                ),
                created_by=str(row["created_by"]),
                created_at=_datetime(row["created_at"]),
            )
        except (KeyError, TypeError, ValueError):
            raise ReviewPostgresError("review snapshot rows are invalid") from None

    async def insert_snapshot(self, snapshot: ReviewSnapshot) -> None:
        run = self._known_run(snapshot.run_id)
        if len(snapshot.items) != run.candidate_count:
            raise ReviewPostgresError("review snapshot is incomplete")
        await self._execute(
            _INSERT_SNAPSHOT_SQL,
            snapshot.snapshot_id,
            run.organization_id,
            run.workspace_id,
            run.client_id,
            run.project_id,
            snapshot.run_id,
            snapshot.version,
            snapshot.created_by,
            snapshot.created_at,
        )
        for ordinal, item in enumerate(snapshot.items, start=1):
            await self._execute(
                _INSERT_SNAPSHOT_ITEM_SQL,
                run.organization_id,
                run.workspace_id,
                run.client_id,
                run.project_id,
                snapshot.run_id,
                snapshot.snapshot_id,
                item.candidate_id,
                ordinal,
                item.status,
                item.replacement_text,
                snapshot.created_by,
                snapshot.created_at,
            )

    async def append_decision(self, decision: ReviewDecision) -> None:
        run = self._known_run(decision.run_id)
        if decision.actor_id != self._scope.actor_id:
            raise ReviewPostgresError("review decision actor does not match scope")
        await self._execute(
            _INSERT_DECISION_SQL,
            decision.decision_id,
            run.organization_id,
            run.workspace_id,
            run.client_id,
            run.project_id,
            decision.run_id,
            decision.review_version,
            decision.candidate_id,
            decision.action,
            decision.reason,
            decision.comment,
            decision.replacement_text,
            decision.actor_id,
            decision.created_at,
        )

    async def append_audit_event(self, event: ReviewAuditEvent) -> None:
        run = self._known_run(event.run_id)
        if (
            event.project_id != self._project_id
            or event.actor_id != self._scope.actor_id
        ):
            raise ReviewPostgresError("review audit event does not match scope")
        target_type = "review_decision" if event.decision_id else "review_run"
        target_id = event.decision_id or event.run_id
        payload = {
            "candidateId": event.candidate_id,
            "decisionId": event.decision_id,
            "reviewVersion": event.review_version,
            "runId": event.run_id,
        }
        await self._execute(
            _INSERT_AUDIT_SQL,
            event.event_id,
            run.organization_id,
            run.workspace_id,
            run.client_id,
            run.project_id,
            event.actor_id,
            event.action,
            target_type,
            target_id,
            _json(payload),
            event.created_at,
        )

    def _known_run(self, run_id: str) -> ReviewRun:
        run = self._runs.get(run_id)
        if run is None:
            raise ReviewPostgresError("review run was not loaded in this transaction")
        return run

    def _require_project(self, project_id: str) -> None:
        if project_id != self._project_id:
            raise ReviewPostgresError("review project does not match transaction scope")

    def _require_run_scope(self, run: ReviewRun) -> None:
        if (
            run.organization_id != self._scope.organization_id
            or run.workspace_id != self._scope.workspace_id
            or run.client_id != self._scope.client_id
            or run.project_id != self._project_id
            or run.created_by != self._scope.actor_id
        ):
            raise ReviewPostgresError("review run does not match transaction scope")

    async def _execute(self, query: str, *args: Any) -> Any:
        try:
            return await self._connection.execute(query, *args)
        except asyncio.CancelledError:
            raise
        except ReviewPostgresError:
            raise
        except Exception:
            raise ReviewPostgresError("review database write failed") from None

    async def _fetchrow(self, query: str, *args: Any) -> Any:
        try:
            return await self._connection.fetchrow(query, *args)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ReviewPostgresError("review database read failed") from None

    async def _fetch(self, query: str, *args: Any) -> Any:
        try:
            return await self._connection.fetch(query, *args)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ReviewPostgresError("review database read failed") from None


def _map_run(row: Any) -> ReviewRun:
    try:
        return ReviewRun(
            run_id=str(row["id"]),
            organization_id=str(row["organization_id"]),
            workspace_id=str(row["workspace_id"]),
            client_id=str(row["client_id"]),
            project_id=str(row["project_id"]),
            proposal_artifact_id=str(row["proposal_artifact_id"]),
            proposal_version_id=str(row["proposal_version_id"]),
            proposal_version=int(row["proposal_version"]),
            proposal_sha256=_hex(row["proposal_sha256"]),
            candidate_count=int(row["candidate_count"]),
            created_by=str(row["created_by"]),
            created_at=_datetime(row["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ReviewPostgresError("review run row is invalid") from None


def _hex(value: Any) -> str:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ReviewPostgresError("database hash representation is invalid")
    return bytes(value).hex()


def _datetime(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ReviewPostgresError("database timestamp is invalid")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
