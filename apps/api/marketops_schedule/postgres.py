"""Asyncpg persistence adapter for the append-only M1-03 schedule slice."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, AsyncIterator
from uuid import UUID

from apps.api.marketops_review.service import ApprovedProposal

from .service import (
    PlanApproval,
    PlanReadModel,
    ScheduleAuditEvent,
    ScheduleRepository,
    ScheduleScopeContext,
    ScheduleServiceFailure,
    ScheduleSnapshot,
    ScheduleTransaction,
    WbsPlan,
    WbsPlanVersion,
)


class SchedulePostgresError(RuntimeError):
    """Sanitized persistence failure."""


_SET_SCOPE_SQL = """
SELECT
    set_config('app.workspace_id', $1, true),
    set_config('app.client_id', $2, true),
    set_config('app.project_id', $3, true),
    set_config('app.actor_id', $4, true)
"""

_APPROVED_PROPOSAL_SQL = """
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

_PLAN_BY_ID_SQL = """
SELECT
    id, organization_id, workspace_id, client_id, project_id,
    proposal_artifact_id, proposal_version_id, proposal_sha256,
    source_review_run_id, source_review_snapshot_id, source_review_version,
    created_by, created_at
FROM marketops.wbs_plans
WHERE organization_id = $1 AND workspace_id = $2 AND client_id = $3
  AND project_id = $4 AND id = $5 AND created_by = $6
"""

_PLAN_BY_SOURCE_SQL = """
SELECT
    id, organization_id, workspace_id, client_id, project_id,
    proposal_artifact_id, proposal_version_id, proposal_sha256,
    source_review_run_id, source_review_snapshot_id, source_review_version,
    created_by, created_at
FROM marketops.wbs_plans
WHERE organization_id = $1 AND workspace_id = $2 AND client_id = $3
  AND project_id = $4 AND source_review_run_id = $5
  AND source_review_version = $6 AND created_by = $7
FOR UPDATE
"""

_PLAN_BY_ID_FOR_UPDATE_SQL = _PLAN_BY_ID_SQL + "\nFOR UPDATE"

_PLAN_VERSIONS_SQL = """
SELECT id, plan_id, plan_version, status, schema_version, plan_payload,
       plan_digest, created_by, created_at
FROM marketops.wbs_plan_versions
WHERE plan_id = $1 AND created_by = $2
ORDER BY plan_version
"""

_INSERT_PLAN_SQL = """
INSERT INTO marketops.wbs_plans (
    id, organization_id, workspace_id, client_id, project_id,
    proposal_artifact_id, proposal_version_id, proposal_sha256,
    source_review_run_id, source_review_snapshot_id, source_review_version,
    created_by, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
"""

_INSERT_PLAN_VERSION_SQL = """
INSERT INTO marketops.wbs_plan_versions (
    id, organization_id, workspace_id, client_id, project_id, plan_id,
    plan_version, status, schema_version, plan_payload, plan_digest,
    task_count, created_by, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 1, $9::jsonb, $10, $11, $12, $13)
"""

_INSERT_TASK_SQL = """
INSERT INTO marketops.wbs_tasks (
    organization_id, workspace_id, client_id, project_id, plan_id,
    plan_version_id, source_review_run_id, task_id, ordinal, candidate_id,
    kind, title, duration_workdays, predecessors, owner_role, planned_start,
    planned_finish, hard_deadline, approved_buffer_workdays, is_locked,
    execution_status, source_version_id, source_sha256, task_payload,
    created_by, created_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb,
    $15, $16, $17, $18, $19, $20, $21, $22, $23, $24::jsonb, $25, $26
)
"""

_SCHEDULE_BY_DIGEST_SQL = """
SELECT id, plan_id, plan_version_id, plan_version, project_start, holidays,
       status, plan_digest, schedule_digest, schedule_payload, created_by, created_at
FROM marketops.schedule_snapshots
WHERE plan_version_id = $1 AND schedule_digest = $2
  AND created_by = $3
"""

_SCHEDULE_BY_ID_FOR_UPDATE_SQL = """
SELECT id, plan_id, plan_version_id, plan_version, project_start, holidays,
       status, plan_digest, schedule_digest, schedule_payload, created_by, created_at
FROM marketops.schedule_snapshots
WHERE organization_id = $1 AND workspace_id = $2 AND client_id = $3
  AND project_id = $4 AND id = $5 AND created_by = $6
"""

_APPROVAL_BY_PLAN_VERSION_SQL = """
SELECT id, plan_id, plan_version_id, plan_version, schedule_snapshot_id,
       plan_digest, schedule_digest, reason, approved_by, approved_at
FROM marketops.wbs_plan_approvals
WHERE organization_id = $1 AND workspace_id = $2 AND client_id = $3
  AND project_id = $4 AND plan_id = $5 AND plan_version_id = $6
  AND approved_by = $7
"""

_APPROVAL_BY_VERSION_FOR_UPDATE_SQL = """
SELECT id, plan_id, plan_version_id, plan_version, schedule_snapshot_id,
       plan_digest, schedule_digest, reason, approved_by, approved_at
FROM marketops.wbs_plan_approvals
WHERE organization_id = $1 AND workspace_id = $2 AND client_id = $3
  AND project_id = $4 AND plan_version_id = $5 AND approved_by = $6
"""

_INSERT_APPROVAL_SQL = """
INSERT INTO marketops.wbs_plan_approvals (
    id, organization_id, workspace_id, client_id, project_id, plan_id,
    plan_version_id, plan_version, schedule_snapshot_id, plan_digest,
    schedule_digest, reason, approved_by, approved_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
)
"""

_INSERT_SCHEDULE_SQL = """
INSERT INTO marketops.schedule_snapshots (
    id, organization_id, workspace_id, client_id, project_id,
    plan_id, plan_version_id, plan_version, project_start, holidays, status,
    plan_digest, schedule_digest, schedule_payload, created_by, created_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13,
    $14::jsonb, $15, $16
)
"""

_INSERT_AUDIT_SQL = """
INSERT INTO marketops.audit_events (
    id, organization_id, workspace_id, client_id, project_id, actor_id,
    action, target_type, target_id, event_data, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
"""


class AsyncpgScheduleRepository(ScheduleRepository):
    def __init__(self, pool: Any):
        self._pool = pool

    async def get_plan(
        self,
        scope: ScheduleScopeContext,
        project_id: str,
        plan_id: str,
        plan_version: int | None,
    ) -> PlanReadModel | None:
        try:
            async with self._read_connection(scope, project_id) as connection:
                row = await connection.fetchrow(
                    _PLAN_BY_ID_SQL,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    project_id,
                    plan_id,
                    scope.actor_id,
                )
                if row is None:
                    return None
                plan = _map_plan(row)
                return await _read_plan_versions(connection, plan, plan_version, scope.actor_id)
        except asyncio.CancelledError:
            raise
        except (SchedulePostgresError, ScheduleServiceFailure):
            raise
        except Exception:
            raise SchedulePostgresError("schedule database read failed") from None

    async def get_plan_approval(
        self,
        scope: ScheduleScopeContext,
        project_id: str,
        plan_id: str,
        plan_version_id: str,
    ) -> PlanApproval | None:
        try:
            async with self._read_connection(scope, project_id) as connection:
                row = await connection.fetchrow(
                    _APPROVAL_BY_PLAN_VERSION_SQL,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    project_id,
                    plan_id,
                    plan_version_id,
                    scope.actor_id,
                )
                return None if row is None else _map_approval(row)
        except asyncio.CancelledError:
            raise
        except SchedulePostgresError:
            raise
        except Exception:
            raise SchedulePostgresError("schedule database read failed") from None

    @asynccontextmanager
    async def _read_connection(
        self, scope: ScheduleScopeContext, project_id: str
    ) -> AsyncIterator[Any]:
        try:
            async with self._pool.acquire() as connection:
                async with connection.transaction(readonly=True):
                    await connection.fetchrow(
                        _SET_SCOPE_SQL,
                        scope.workspace_id,
                        scope.client_id,
                        project_id,
                        scope.actor_id,
                    )
                    yield connection
        except asyncio.CancelledError:
            raise
        except SchedulePostgresError:
            raise
        except Exception:
            raise SchedulePostgresError("schedule database read failed") from None

    @asynccontextmanager
    async def transaction(
        self, scope: ScheduleScopeContext, project_id: str
    ) -> AsyncIterator["AsyncpgScheduleTransaction"]:
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
                    yield AsyncpgScheduleTransaction(connection, scope, project_id)
        except asyncio.CancelledError:
            raise
        except (SchedulePostgresError, ScheduleServiceFailure):
            raise
        except Exception:
            raise SchedulePostgresError("schedule database transaction failed") from None


class AsyncpgScheduleTransaction(ScheduleTransaction):
    def __init__(
        self, connection: Any, scope: ScheduleScopeContext, project_id: str
    ) -> None:
        self._connection = connection
        self._scope = scope
        self._project_id = project_id
        self._plans: dict[str, WbsPlan] = {}

    async def get_approved_proposal_for_update(
        self, project_id: str
    ) -> ApprovedProposal | None:
        self._require_project(project_id)
        row = await self._fetchrow(_APPROVED_PROPOSAL_SQL, project_id)
        if row is None:
            return None
        try:
            return ApprovedProposal(
                organization_id=_uuid(row["organization_id"]),
                workspace_id=_uuid(row["workspace_id"]),
                client_id=_uuid(row["client_id"]),
                project_id=_uuid(row["project_id"]),
                artifact_id=_uuid(row["artifact_id"]),
                version_id=_uuid(row["version_id"]),
                version=_positive_int(row["proposal_version"]),
                sha256=_hex(row["sha256"]),
            )
        except (KeyError, TypeError, ValueError):
            raise SchedulePostgresError("approved proposal row is invalid") from None

    async def get_plan_by_source_for_update(
        self, review_run_id: str, review_version: int
    ) -> PlanReadModel | None:
        row = await self._fetchrow(
            _PLAN_BY_SOURCE_SQL,
            self._scope.organization_id,
            self._scope.workspace_id,
            self._scope.client_id,
            self._project_id,
            review_run_id,
            review_version,
            self._scope.actor_id,
        )
        return None if row is None else await self._load_plan(row, None)

    async def get_plan_for_update(self, plan_id: str) -> PlanReadModel | None:
        row = await self._fetchrow(
            _PLAN_BY_ID_FOR_UPDATE_SQL,
            self._scope.organization_id,
            self._scope.workspace_id,
            self._scope.client_id,
            self._project_id,
            plan_id,
            self._scope.actor_id,
        )
        return None if row is None else await self._load_plan(row, None)

    async def insert_plan(self, plan: WbsPlan) -> None:
        self._require_plan_scope(plan)
        await self._execute(
            _INSERT_PLAN_SQL,
            plan.plan_id,
            plan.organization_id,
            plan.workspace_id,
            plan.client_id,
            plan.project_id,
            plan.proposal_artifact_id,
            plan.proposal_version_id,
            bytes.fromhex(plan.proposal_sha256),
            plan.source_review_run_id,
            plan.source_review_snapshot_id,
            plan.source_review_version,
            plan.created_by,
            plan.created_at,
        )
        self._plans[plan.plan_id] = plan

    async def insert_plan_version(self, version: WbsPlanVersion) -> None:
        plan = self._known_plan(version.plan_id)
        tasks = version.payload.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise SchedulePostgresError("WBS task batch is incomplete")
        await self._execute(
            _INSERT_PLAN_VERSION_SQL,
            version.version_id,
            plan.organization_id,
            plan.workspace_id,
            plan.client_id,
            plan.project_id,
            plan.plan_id,
            version.version,
            version.status,
            _json(version.payload),
            bytes.fromhex(version.digest),
            len(tasks),
            version.created_by,
            version.created_at,
        )

    async def insert_plan_tasks(self, version: WbsPlanVersion) -> None:
        plan = self._known_plan(version.plan_id)
        tasks = version.payload.get("tasks")
        if not isinstance(tasks, list):
            raise SchedulePostgresError("WBS task batch is invalid")
        for ordinal, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                raise SchedulePostgresError("WBS task row is invalid")
            citation = task.get("sourceCitation")
            if not isinstance(citation, dict):
                raise SchedulePostgresError("WBS task citation is invalid")
            await self._execute(
                _INSERT_TASK_SQL,
                plan.organization_id,
                plan.workspace_id,
                plan.client_id,
                plan.project_id,
                plan.plan_id,
                version.version_id,
                plan.source_review_run_id,
                task.get("taskId"),
                ordinal,
                task.get("candidateId"),
                task.get("kind"),
                task.get("title"),
                task.get("durationWorkdays"),
                _json(task.get("predecessors", [])),
                task.get("ownerRole", ""),
                _date_or_none(task.get("plannedStart")),
                _date_or_none(task.get("plannedFinish")),
                _date_or_none(task.get("hardDeadline")),
                task.get("approvedBufferWorkdays", 0),
                task.get("isLocked"),
                task.get("status"),
                citation.get("sourceVersionId"),
                bytes.fromhex(citation.get("sourceSha256", "")),
                _json(task),
                version.created_by,
                version.created_at,
            )

    async def get_schedule_by_digest(
        self, plan_version_id: str, schedule_digest: str
    ) -> ScheduleSnapshot | None:
        row = await self._fetchrow(
            _SCHEDULE_BY_DIGEST_SQL,
            plan_version_id,
            bytes.fromhex(schedule_digest),
            self._scope.actor_id,
        )
        return None if row is None else _map_schedule(row)

    async def get_schedule_for_update(
        self, schedule_snapshot_id: str
    ) -> ScheduleSnapshot | None:
        row = await self._fetchrow(
            _SCHEDULE_BY_ID_FOR_UPDATE_SQL,
            self._scope.organization_id,
            self._scope.workspace_id,
            self._scope.client_id,
            self._project_id,
            schedule_snapshot_id,
            self._scope.actor_id,
        )
        return None if row is None else _map_schedule(row)

    async def get_approval_by_plan_version(
        self, plan_version_id: str
    ) -> PlanApproval | None:
        row = await self._fetchrow(
            _APPROVAL_BY_VERSION_FOR_UPDATE_SQL,
            self._scope.organization_id,
            self._scope.workspace_id,
            self._scope.client_id,
            self._project_id,
            plan_version_id,
            self._scope.actor_id,
        )
        return None if row is None else _map_approval(row)

    async def insert_schedule(self, snapshot: ScheduleSnapshot) -> None:
        plan = self._known_plan(snapshot.plan_id)
        if snapshot.created_by != self._scope.actor_id:
            raise SchedulePostgresError("schedule snapshot actor does not match scope")
        await self._execute(
            _INSERT_SCHEDULE_SQL,
            snapshot.snapshot_id,
            plan.organization_id,
            plan.workspace_id,
            plan.client_id,
            plan.project_id,
            snapshot.plan_id,
            snapshot.plan_version_id,
            snapshot.plan_version,
            date.fromisoformat(snapshot.project_start),
            _json(list(snapshot.holidays)),
            snapshot.status,
            bytes.fromhex(snapshot.plan_digest),
            bytes.fromhex(snapshot.schedule_digest),
            _json(snapshot.payload),
            snapshot.created_by,
            snapshot.created_at,
        )

    async def insert_approval(self, approval: PlanApproval) -> None:
        plan = self._known_plan(approval.plan_id)
        if approval.approved_by != self._scope.actor_id:
            raise SchedulePostgresError("plan approval does not match transaction scope")
        await self._execute(
            _INSERT_APPROVAL_SQL,
            approval.approval_id,
            plan.organization_id,
            plan.workspace_id,
            plan.client_id,
            plan.project_id,
            approval.plan_id,
            approval.plan_version_id,
            approval.plan_version,
            approval.schedule_snapshot_id,
            bytes.fromhex(approval.plan_digest),
            bytes.fromhex(approval.schedule_digest),
            approval.reason,
            approval.approved_by,
            approval.approved_at,
        )

    async def append_audit_event(self, event: ScheduleAuditEvent) -> None:
        if event.project_id != self._project_id or event.actor_id != self._scope.actor_id:
            raise SchedulePostgresError("schedule audit event does not match scope")
        await self._execute(
            _INSERT_AUDIT_SQL,
            event.event_id,
            self._scope.organization_id,
            self._scope.workspace_id,
            self._scope.client_id,
            event.project_id,
            event.actor_id,
            event.action,
            event.target_type,
            event.target_id,
            _json(dict(event.event_data)),
            event.created_at,
        )

    async def _load_plan(self, row: Any, requested_version: int | None) -> PlanReadModel:
        plan = _map_plan(row)
        self._require_plan_scope(plan)
        self._plans[plan.plan_id] = plan
        rows = await self._fetch(_PLAN_VERSIONS_SQL, plan.plan_id, self._scope.actor_id)
        return _map_plan_versions(plan, rows, requested_version)

    def _known_plan(self, plan_id: str) -> WbsPlan:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise SchedulePostgresError("WBS plan was not loaded in this transaction")
        return plan

    def _require_project(self, project_id: str) -> None:
        if project_id != self._project_id:
            raise SchedulePostgresError("schedule project does not match transaction scope")

    def _require_plan_scope(self, plan: WbsPlan) -> None:
        if (
            plan.organization_id != self._scope.organization_id
            or plan.workspace_id != self._scope.workspace_id
            or plan.client_id != self._scope.client_id
            or plan.project_id != self._project_id
            or plan.created_by != self._scope.actor_id
        ):
            raise SchedulePostgresError("WBS plan does not match transaction scope")

    async def _execute(self, query: str, *args: Any) -> Any:
        try:
            return await self._connection.execute(query, *args)
        except asyncio.CancelledError:
            raise
        except SchedulePostgresError:
            raise
        except Exception:
            raise SchedulePostgresError("schedule database write failed") from None

    async def _fetchrow(self, query: str, *args: Any) -> Any:
        try:
            return await self._connection.fetchrow(query, *args)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise SchedulePostgresError("schedule database read failed") from None

    async def _fetch(self, query: str, *args: Any) -> Any:
        try:
            return await self._connection.fetch(query, *args)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise SchedulePostgresError("schedule database read failed") from None


async def _read_plan_versions(
    connection: Any,
    plan: WbsPlan,
    requested_version: int | None,
    actor_id: str,
) -> PlanReadModel | None:
    try:
        rows = await connection.fetch(_PLAN_VERSIONS_SQL, plan.plan_id, actor_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise SchedulePostgresError("schedule database read failed") from None
    return _map_plan_versions(plan, rows, requested_version)


def _map_plan_versions(
    plan: WbsPlan, rows: Any, requested_version: int | None
) -> PlanReadModel | None:
    if not rows:
        raise SchedulePostgresError("WBS plan has no versions")
    versions: list[WbsPlanVersion] = []
    selected: WbsPlanVersion | None = None
    for expected, row in enumerate(rows, start=1):
        version = _map_plan_version(row)
        if version.plan_id != plan.plan_id or version.version != expected:
            raise SchedulePostgresError("WBS plan version history is inconsistent")
        versions.append(version)
        if requested_version == version.version:
            selected = version
    if requested_version is None:
        selected = versions[-1]
    if selected is None:
        return None
    return PlanReadModel(plan, selected, tuple(version.version for version in versions))


def _map_plan(row: Any) -> WbsPlan:
    try:
        return WbsPlan(
            plan_id=_uuid(row["id"]),
            organization_id=_uuid(row["organization_id"]),
            workspace_id=_uuid(row["workspace_id"]),
            client_id=_uuid(row["client_id"]),
            project_id=_uuid(row["project_id"]),
            proposal_artifact_id=_uuid(row["proposal_artifact_id"]),
            proposal_version_id=_uuid(row["proposal_version_id"]),
            proposal_sha256=_hex(row["proposal_sha256"]),
            source_review_run_id=_uuid(row["source_review_run_id"]),
            source_review_snapshot_id=_uuid(row["source_review_snapshot_id"]),
            source_review_version=_positive_int(row["source_review_version"]),
            created_by=_uuid(row["created_by"]),
            created_at=_datetime(row["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise SchedulePostgresError("WBS plan row is invalid") from None


def _map_plan_version(row: Any) -> WbsPlanVersion:
    try:
        payload = _json_mapping(row["plan_payload"])
        return WbsPlanVersion(
            version_id=_uuid(row["id"]),
            plan_id=_uuid(row["plan_id"]),
            version=_positive_int(row["plan_version"]),
            status=row["status"],
            payload=payload,
            digest=_hex(row["plan_digest"]),
            created_by=_uuid(row["created_by"]),
            created_at=_datetime(row["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise SchedulePostgresError("WBS plan version row is invalid") from None


def _map_schedule(row: Any) -> ScheduleSnapshot:
    try:
        payload = _json_mapping(row["schedule_payload"])
        holidays = tuple(_json_text_sequence(row["holidays"]))
        return ScheduleSnapshot(
            snapshot_id=_uuid(row["id"]),
            plan_id=_uuid(row["plan_id"]),
            plan_version_id=_uuid(row["plan_version_id"]),
            plan_version=_positive_int(row["plan_version"]),
            project_start=_date(row["project_start"]).isoformat(),
            holidays=holidays,
            status=row["status"],
            plan_digest=_hex(row["plan_digest"]),
            schedule_digest=_hex(row["schedule_digest"]),
            payload=payload,
            created_by=_uuid(row["created_by"]),
            created_at=_datetime(row["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise SchedulePostgresError("schedule snapshot row is invalid") from None


def _map_approval(row: Any) -> PlanApproval:
    try:
        reason = row["reason"]
        if (
            not isinstance(reason, str)
            or reason != reason.strip()
            or not reason
            or len(reason) > 1000
        ):
            raise ValueError("approval reason is invalid")
        return PlanApproval(
            approval_id=_uuid(row["id"]),
            plan_id=_uuid(row["plan_id"]),
            plan_version_id=_uuid(row["plan_version_id"]),
            plan_version=_positive_int(row["plan_version"]),
            schedule_snapshot_id=_uuid(row["schedule_snapshot_id"]),
            plan_digest=_hex(row["plan_digest"]),
            schedule_digest=_hex(row["schedule_digest"]),
            reason=reason,
            approved_by=_uuid(row["approved_by"]),
            approved_at=_datetime(row["approved_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise SchedulePostgresError("plan approval row is invalid") from None


def _uuid(value: Any) -> str:
    return str(UUID(str(value)))


def _hex(value: Any) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, (bytes, bytearray)) or len(value) != 32:
        raise ValueError("hash is invalid")
    return bytes(value).hex()


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("positive integer is invalid")
    return value


def _datetime(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamp is invalid")
    return value


def _date(value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("date is invalid")
    return value


def _date_or_none(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise SchedulePostgresError("WBS date is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SchedulePostgresError("WBS date is invalid") from None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("JSON object is invalid")
    return value


def _json_text_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("JSON text list is invalid")
    return tuple(value)
