"""Asyncpg persistence adapter for immutable M1-02 extraction reviews."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from collections.abc import Mapping, Sequence
from typing import Any, AsyncIterator
from uuid import UUID

from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation

from .service import (
    ApprovedProposal,
    ReviewAuditEvent,
    ReviewDecision,
    ReviewFailure,
    ReviewCandidateView,
    ReviewReadModel,
    ReviewRun,
    ReviewRunSummary,
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
FOR UPDATE OF project
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
FOR UPDATE
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

_LIST_REVIEW_RUNS_SQL = """
SELECT
    run.id, run.organization_id, run.workspace_id, run.client_id, run.project_id,
    run.proposal_artifact_id, run.proposal_version_id, run.proposal_version,
    run.proposal_sha256, run.candidate_count, run.created_by, run.created_at,
    candidate_stats.actual_candidate_count,
    snapshot_stats.snapshot_count,
    snapshot_stats.min_review_version,
    snapshot_stats.latest_review_version
FROM marketops.extraction_runs AS run
CROSS JOIN LATERAL (
    SELECT count(*)::integer AS actual_candidate_count
    FROM marketops.extraction_candidates AS candidate
    WHERE candidate.run_id = run.id
) AS candidate_stats
CROSS JOIN LATERAL (
    SELECT
        count(*)::integer AS snapshot_count,
        min(snapshot.review_version)::integer AS min_review_version,
        max(snapshot.review_version)::integer AS latest_review_version
    FROM marketops.review_snapshots AS snapshot
    WHERE snapshot.run_id = run.id
) AS snapshot_stats
WHERE run.organization_id = $1
  AND run.workspace_id = $2
  AND run.client_id = $3
  AND run.project_id = $4
  AND run.created_by = $5
ORDER BY run.created_at DESC, run.id DESC
LIMIT $6
"""

_READ_REVIEW_RUN_SQL = """
SELECT
    id, organization_id, workspace_id, client_id, project_id,
    proposal_artifact_id, proposal_version_id, proposal_version,
    proposal_sha256, candidate_count, created_by, created_at
FROM marketops.extraction_runs
WHERE organization_id = $1
  AND workspace_id = $2
  AND client_id = $3
  AND project_id = $4
  AND created_by = $5
  AND id = $6
"""

_READ_SNAPSHOT_HEADERS_SQL = """
SELECT id, run_id, review_version, created_by, created_at
FROM marketops.review_snapshots
WHERE run_id = $1
ORDER BY review_version
"""

_READ_REVIEW_CANDIDATES_SQL = """
SELECT
    candidate.id AS candidate_id,
    candidate.ordinal AS candidate_ordinal,
    candidate.kind,
    candidate.candidate_text,
    candidate.classification,
    candidate.confidence,
    candidate.source_version_id,
    candidate.source_sha256,
    candidate.source_location,
    candidate.section_path,
    candidate.source_quote,
    candidate.review_status,
    item.ordinal AS item_ordinal,
    item.status,
    item.replacement_text,
    source.id AS validated_source_version_id,
    source.sha256 AS validated_source_sha256
FROM marketops.extraction_candidates AS candidate
JOIN marketops.review_snapshot_items AS item
  ON item.run_id = candidate.run_id
 AND item.candidate_id = candidate.id
 AND item.snapshot_id = $2
LEFT JOIN marketops.artifact_versions AS source
  ON source.organization_id = candidate.organization_id
 AND source.workspace_id = candidate.workspace_id
 AND source.client_id = candidate.client_id
 AND source.project_id = candidate.project_id
 AND source.artifact_id = $3
 AND source.id = candidate.source_version_id
WHERE candidate.run_id = $1
ORDER BY candidate.ordinal
"""

_READ_REVIEW_DECISION_SQL = """
SELECT
    id, run_id, review_version, candidate_id, action, reason, comment,
    replacement_text, actor_id, created_at
FROM marketops.review_decisions
WHERE run_id = $1 AND review_version <= $2
ORDER BY review_version
"""


class AsyncpgReviewRepository:
    def __init__(self, pool: Any):
        self._pool = pool

    async def list_review_runs(
        self, scope: ReviewScopeContext, project_id: str, limit: int
    ) -> tuple[ReviewRunSummary, ...]:
        try:
            async with self._read_connection(scope, project_id) as connection:
                rows = await self._fetch(
                    connection,
                    _LIST_REVIEW_RUNS_SQL,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    project_id,
                    scope.actor_id,
                    limit,
                )
            return tuple(_map_run_summary(row) for row in rows)
        except asyncio.CancelledError:
            raise
        except ReviewPostgresError:
            raise
        except Exception:
            raise ReviewPostgresError("review database read failed") from None

    async def get_review(
        self,
        scope: ReviewScopeContext,
        project_id: str,
        run_id: str,
        review_version: int | None,
    ) -> ReviewReadModel | None:
        try:
            async with self._read_connection(scope, project_id) as connection:
                run_row = await self._fetchrow(
                    connection,
                    _READ_REVIEW_RUN_SQL,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    project_id,
                    scope.actor_id,
                    run_id,
                )
                if run_row is None:
                    return None
                run = _map_run(run_row)
                headers = await self._fetch(
                    connection, _READ_SNAPSHOT_HEADERS_SQL, run_id
                )
                versions, selected = _select_snapshot(headers, run, review_version)
                if selected is None:
                    return None
                candidate_rows = await self._fetch(
                    connection,
                    _READ_REVIEW_CANDIDATES_SQL,
                    run_id,
                    str(selected["id"]),
                    run.proposal_artifact_id,
                )
                candidates, snapshot_items = _map_candidate_views(candidate_rows, run)
                decision_rows = await self._fetch(
                    connection,
                    _READ_REVIEW_DECISION_SQL,
                    run_id,
                    int(selected["review_version"]),
                )
            snapshot = ReviewSnapshot(
                snapshot_id=str(selected["id"]),
                run_id=str(selected["run_id"]),
                version=int(selected["review_version"]),
                items=snapshot_items,
                created_by=str(selected["created_by"]),
                created_at=_datetime(selected["created_at"]),
            )
            candidates, selected_decision = _map_decision_history(
                decision_rows, run, snapshot, candidates
            )
            return ReviewReadModel(
                run, snapshot, candidates, versions, selected_decision
            )
        except asyncio.CancelledError:
            raise
        except ReviewPostgresError:
            raise
        except Exception:
            raise ReviewPostgresError("review database read failed") from None

    @asynccontextmanager
    async def _read_connection(
        self, scope: ReviewScopeContext, project_id: str
    ) -> AsyncIterator[Any]:
        try:
            async with self._pool.acquire() as connection:
                async with connection.transaction(readonly=True):
                    await self._fetchrow(
                        connection,
                        _SET_SCOPE_SQL,
                        scope.workspace_id,
                        scope.client_id,
                        project_id,
                        scope.actor_id,
                    )
                    yield connection
        except asyncio.CancelledError:
            raise
        except ReviewPostgresError:
            raise
        except Exception:
            raise ReviewPostgresError("review database read failed") from None

    @staticmethod
    async def _fetchrow(connection: Any, query: str, *args: Any) -> Any:
        try:
            return await connection.fetchrow(query, *args)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ReviewPostgresError("review database read failed") from None

    @staticmethod
    async def _fetch(connection: Any, query: str, *args: Any) -> Any:
        try:
            return await connection.fetch(query, *args)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ReviewPostgresError("review database read failed") from None

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
            run_id=_uuid(row["id"]),
            organization_id=_uuid(row["organization_id"]),
            workspace_id=_uuid(row["workspace_id"]),
            client_id=_uuid(row["client_id"]),
            project_id=_uuid(row["project_id"]),
            proposal_artifact_id=_uuid(row["proposal_artifact_id"]),
            proposal_version_id=_uuid(row["proposal_version_id"]),
            proposal_version=_positive_int(row["proposal_version"]),
            proposal_sha256=_hex(row["proposal_sha256"]),
            candidate_count=_positive_int(row["candidate_count"]),
            created_by=_uuid(row["created_by"]),
            created_at=_datetime(row["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ReviewPostgresError("review run row is invalid") from None


def _map_run_summary(row: Any) -> ReviewRunSummary:
    run = _map_run(row)
    try:
        actual_candidates = _nonnegative_int(row["actual_candidate_count"])
        snapshot_count = _positive_int(row["snapshot_count"])
        minimum = _positive_int(row["min_review_version"])
        latest = _positive_int(row["latest_review_version"])
    except (KeyError, TypeError, ValueError):
        raise ReviewPostgresError("review summary row is invalid") from None
    if (
        actual_candidates != run.candidate_count
        or minimum != 1
        or latest != snapshot_count
    ):
        raise ReviewPostgresError("review summary state is inconsistent")
    return ReviewRunSummary(run=run, latest_review_version=latest)


def _select_snapshot(
    rows: Any, run: ReviewRun, requested_version: int | None
) -> tuple[tuple[int, ...], Any | None]:
    if not _rows(rows):
        raise ReviewPostgresError("review snapshot history is invalid")
    versions: list[int] = []
    selected = None
    snapshot_ids: set[str] = set()
    try:
        for row in rows:
            snapshot_id = _uuid(row["id"])
            version = _positive_int(row["review_version"])
            if (
                _uuid(row["run_id"]) != run.run_id
                or _uuid(row["created_by"]) != run.created_by
                or snapshot_id in snapshot_ids
            ):
                raise ReviewPostgresError("review snapshot history is inconsistent")
            _datetime(row["created_at"])
            versions.append(version)
            snapshot_ids.add(snapshot_id)
            if requested_version == version:
                selected = row
        if tuple(versions) != tuple(range(1, len(versions) + 1)):
            raise ReviewPostgresError("review snapshot history is inconsistent")
        if requested_version is None:
            selected = rows[-1]
    except ReviewPostgresError:
        raise
    except (KeyError, TypeError, ValueError, IndexError):
        raise ReviewPostgresError("review snapshot history is invalid") from None
    return tuple(versions), selected


def _map_candidate_views(
    rows: Any, run: ReviewRun
) -> tuple[tuple[ReviewCandidateView, ...], tuple[ReviewSnapshotItem, ...]]:
    if not _row_sequence(rows):
        raise ReviewPostgresError("review candidate rows are invalid")
    views: list[ReviewCandidateView] = []
    items: list[ReviewSnapshotItem] = []
    candidate_ids: set[str] = set()
    try:
        for expected_ordinal, row in enumerate(rows, start=1):
            candidate_ordinal = _positive_int(row["candidate_ordinal"])
            item_ordinal = _positive_int(row["item_ordinal"])
            candidate_id = _uuid(row["candidate_id"])
            source_version_id = _uuid(row["source_version_id"])
            source_hash = _hex(row["source_sha256"])
            validated_source_id = _uuid(row["validated_source_version_id"])
            validated_source_hash = _hex(row["validated_source_sha256"])
            if (
                candidate_ordinal != expected_ordinal
                or item_ordinal != expected_ordinal
                or candidate_id in candidate_ids
                or source_version_id != run.proposal_version_id
                or validated_source_id != source_version_id
                or source_hash != run.proposal_sha256
                or validated_source_hash != source_hash
            ):
                raise ReviewPostgresError("review candidate state is inconsistent")
            location = SourceLocation.from_mapping(_json_mapping(row["source_location"]))
            section_path = tuple(_json_text_sequence(row["section_path"]))
            candidate = Candidate(
                candidate_id=candidate_id,
                kind=row["kind"],
                text=row["candidate_text"],
                classification=row["classification"],
                confidence=float(row["confidence"]),
                source_citation=SourceCitation(
                    source_version_id=source_version_id,
                    source_sha256=source_hash,
                    location=location,
                    section_path=section_path,
                    quote=row["source_quote"],
                ),
                review_status=row["review_status"],
            )
            status = row["status"]
            replacement = row["replacement_text"]
            if (
                status not in {"pending", "approve", "modify", "reject"}
                or (status == "modify") != (replacement is not None)
                or (
                    replacement is not None
                    and (not isinstance(replacement, str) or not replacement.strip())
                )
            ):
                raise ReviewPostgresError("review snapshot item is inconsistent")
            views.append(
                ReviewCandidateView(expected_ordinal, candidate, status, replacement)
            )
            items.append(ReviewSnapshotItem(candidate_id, status, replacement))
            candidate_ids.add(candidate_id)
    except ReviewPostgresError:
        raise
    except Exception:
        raise ReviewPostgresError("review candidate rows are invalid") from None
    if len(views) != run.candidate_count:
        raise ReviewPostgresError("review candidate state is incomplete")
    return tuple(views), tuple(items)


def _map_decision_history(
    rows: Any,
    run: ReviewRun,
    snapshot: ReviewSnapshot,
    candidates: tuple[ReviewCandidateView, ...],
) -> tuple[tuple[ReviewCandidateView, ...], ReviewDecision | None]:
    if not _row_sequence(rows):
        raise ReviewPostgresError("review decision rows are invalid")
    if snapshot.version == 1:
        if rows:
            raise ReviewPostgresError("review decision state is inconsistent")
        return candidates, None
    if len(rows) != snapshot.version - 1:
        raise ReviewPostgresError("review decision state is incomplete")
    by_candidate = {view.candidate.candidate_id: view for view in candidates}
    last_by_candidate: dict[str, ReviewDecision] = {}
    decisions: list[ReviewDecision] = []
    decision_ids: set[str] = set()
    for expected_version, row in enumerate(rows, start=2):
        try:
            decision = ReviewDecision(
                decision_id=_uuid(row["id"]),
                run_id=_uuid(row["run_id"]),
                review_version=_positive_int(row["review_version"]),
                candidate_id=_uuid(row["candidate_id"]),
                action=row["action"],
                reason=_nonempty_text(row["reason"]),
                comment=_optional_text(row["comment"]),
                replacement_text=_optional_text(row["replacement_text"]),
                actor_id=_uuid(row["actor_id"]),
                created_at=_datetime(row["created_at"]),
            )
        except ReviewPostgresError:
            raise
        except Exception:
            raise ReviewPostgresError("review decision row is invalid") from None
        if (
            decision.run_id != run.run_id
            or decision.review_version != expected_version
            or decision.actor_id != run.created_by
            or decision.action not in {"approve", "modify", "reject"}
            or decision.candidate_id not in by_candidate
            or decision.decision_id in decision_ids
            or (decision.action == "modify") != (decision.replacement_text is not None)
        ):
            raise ReviewPostgresError("review decision state is inconsistent")
        decisions.append(decision)
        decision_ids.add(decision.decision_id)
        last_by_candidate[decision.candidate_id] = decision
    result: list[ReviewCandidateView] = []
    for view in candidates:
        last = last_by_candidate.get(view.candidate.candidate_id)
        if (view.status == "pending") != (last is None) or (
            last is not None
            and (
                view.status != last.action
                or view.replacement_text != last.replacement_text
            )
        ):
            raise ReviewPostgresError("review decision state is inconsistent")
        result.append(
            ReviewCandidateView(
                view.ordinal,
                view.candidate,
                view.status,
                view.replacement_text,
                last,
            )
        )
    return tuple(result), decisions[-1]


def _rows(value: Any) -> bool:
    return _row_sequence(value) and bool(value)


def _row_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _json_mapping(value: Any) -> Mapping[str, Any]:
    parsed = _json_value(value)
    if not isinstance(parsed, Mapping):
        raise ReviewPostgresError("database JSON object is invalid")
    return parsed


def _json_text_sequence(value: Any) -> Sequence[str]:
    parsed = _json_value(value)
    if (
        not _row_sequence(parsed)
        or not parsed
        or any(not isinstance(item, str) or not item.strip() for item in parsed)
    ):
        raise ReviewPostgresError("database JSON text array is invalid")
    return parsed


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise ReviewPostgresError("database JSON representation is invalid") from None
    return value


def _uuid(value: Any) -> str:
    try:
        parsed = UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ReviewPostgresError("database UUID representation is invalid") from None
    if str(parsed) != str(value).lower():
        raise ReviewPostgresError("database UUID representation is invalid")
    return str(parsed)


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReviewPostgresError("database positive integer is invalid")
    return value


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewPostgresError("database non-negative integer is invalid")
    return value


def _nonempty_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewPostgresError("database text is invalid")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return _nonempty_text(value)


def _hex(value: Any) -> str:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ReviewPostgresError("database hash representation is invalid")
    raw = bytes(value)
    if len(raw) != 32:
        raise ReviewPostgresError("database hash representation is invalid")
    return raw.hex()


def _datetime(value: Any) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ReviewPostgresError("database timestamp is invalid")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
