from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, AsyncIterator, Mapping
from uuid import UUID, uuid4

from .service import (
    ExecutionFailure,
    ExecutionScopeContext,
    ExecutionState,
    ExecutionUpdateRequest,
    ExecutionUpdateResult,
)


_SET_SCOPE_SQL = """
SELECT
    set_config('app.workspace_id', $1, true),
    set_config('app.client_id', $2, true),
    set_config('app.project_id', $3, true),
    set_config('app.actor_id', $4, true)
"""

_APPROVED_PLAN_SQL = """
SELECT version.id
FROM marketops.wbs_plan_versions AS version
JOIN marketops.wbs_plans AS plan
  ON plan.id = version.plan_id
JOIN marketops.wbs_plan_approvals AS approval
  ON approval.plan_version_id = version.id
WHERE plan.organization_id = $1
  AND plan.workspace_id = $2
  AND plan.client_id = $3
  AND plan.project_id = $4
  AND plan.id = $5
  AND version.id = $6
  AND plan.created_by = $7
"""

_LOCK_PLAN_SQL = """
SELECT id
FROM marketops.wbs_plans
WHERE organization_id = $1
  AND workspace_id = $2
  AND client_id = $3
  AND project_id = $4
  AND id = $5
  AND created_by = $6
FOR UPDATE
"""

_LATEST_PLAN_VERSION_SQL = """
SELECT id
FROM marketops.wbs_plan_versions
WHERE organization_id = $1
  AND workspace_id = $2
  AND client_id = $3
  AND project_id = $4
  AND plan_id = $5
  AND created_by = $6
ORDER BY plan_version DESC
LIMIT 1
"""

_TASK_SQL = """
SELECT task_id
FROM marketops.wbs_tasks
WHERE organization_id = $1
  AND workspace_id = $2
  AND client_id = $3
  AND project_id = $4
  AND plan_id = $5
  AND plan_version_id = $6
  AND task_id = $7
  AND created_by = $8
"""

_LATEST_SQL = """
SELECT task_id, sequence_no, status, blocker_reason, actual_start,
       actual_finish, note, updated_by, updated_at
FROM marketops.wbs_task_execution_updates
WHERE plan_version_id = $1 AND task_id = $2 AND updated_by = $3
ORDER BY sequence_no DESC
LIMIT 1
"""

_LIST_SQL = """
SELECT DISTINCT ON (task_id)
       task_id, sequence_no, status, blocker_reason, actual_start,
       actual_finish, note, updated_by, updated_at
FROM marketops.wbs_task_execution_updates
WHERE project_id = $1 AND plan_version_id = $2 AND updated_by = $3
ORDER BY task_id, sequence_no DESC
"""

_INSERT_SQL = """
INSERT INTO marketops.wbs_task_execution_updates (
    id, organization_id, workspace_id, client_id, project_id, plan_id,
    plan_version_id, task_id, sequence_no, status, blocker_reason,
    actual_start, actual_finish, note, updated_by, updated_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
    $12, $13, $14, $15, $16
)
"""

_AUDIT_SQL = """
INSERT INTO marketops.audit_events (
    id, organization_id, workspace_id, client_id, project_id, actor_id,
    action, target_type, target_id, event_data, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
"""


class AsyncpgExecutionRepository:
    def __init__(self, pool: Any):
        self.pool = pool

    async def assert_approved_plan(
        self,
        scope: ExecutionScopeContext,
        project_id: str,
        plan_id: str,
        plan_version_id: str,
    ) -> None:
        try:
            async with self._read_connection(scope, project_id) as connection:
                row = await connection.fetchrow(
                    _APPROVED_PLAN_SQL,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    project_id,
                    plan_id,
                    plan_version_id,
                    scope.actor_id,
                )
                if row is None:
                    raise ExecutionFailure(
                        "PLAN_NOT_APPROVED",
                        "execution requires an approved WBS version",
                    )
        except ExecutionFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ExecutionFailure(
                "EXECUTION_READ_FAILED", "approved plan could not be verified"
            ) from None

    async def list_states(
        self,
        scope: ExecutionScopeContext,
        project_id: str,
        plan_version_id: str,
    ) -> Mapping[str, ExecutionState]:
        try:
            async with self._read_connection(scope, project_id) as connection:
                rows = await connection.fetch(
                    _LIST_SQL, project_id, plan_version_id, scope.actor_id
                )
                return {str(row["task_id"]): _map_state(row) for row in rows}
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ExecutionFailure(
                "EXECUTION_READ_FAILED", "execution state could not be read"
            ) from None

    @asynccontextmanager
    async def transaction(
        self, scope: ExecutionScopeContext, project_id: str
    ) -> AsyncIterator["AsyncpgExecutionTransaction"]:
        try:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    await connection.fetchrow(
                        _SET_SCOPE_SQL,
                        scope.workspace_id,
                        scope.client_id,
                        project_id,
                        scope.actor_id,
                    )
                    yield AsyncpgExecutionTransaction(
                        connection, scope, project_id
                    )
        except ExecutionFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ExecutionFailure(
                "EXECUTION_WRITE_FAILED", "execution transaction failed"
            ) from None

    @asynccontextmanager
    async def _read_connection(
        self, scope: ExecutionScopeContext, project_id: str
    ) -> AsyncIterator[Any]:
        async with self.pool.acquire() as connection:
            async with connection.transaction(readonly=True):
                await connection.fetchrow(
                    _SET_SCOPE_SQL,
                    scope.workspace_id,
                    scope.client_id,
                    project_id,
                    scope.actor_id,
                )
                yield connection


class AsyncpgExecutionTransaction:
    def __init__(
        self,
        connection: Any,
        scope: ExecutionScopeContext,
        project_id: str,
    ):
        self.connection = connection
        self.scope = scope
        self.project_id = project_id

    async def append_update(
        self,
        request: ExecutionUpdateRequest,
        scope: ExecutionScopeContext,
        updated_at,
    ) -> ExecutionUpdateResult:
        plan = await self.connection.fetchrow(
            _LOCK_PLAN_SQL,
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            request.project_id,
            request.plan_id,
            scope.actor_id,
        )
        if plan is None:
            raise ExecutionFailure(
                "PLAN_NOT_APPROVED", "execution requires an approved WBS version"
            )
        latest_version = await self.connection.fetchrow(
            _LATEST_PLAN_VERSION_SQL,
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            request.project_id,
            request.plan_id,
            scope.actor_id,
        )
        if (
            latest_version is None
            or str(latest_version["id"]) != request.plan_version_id
        ):
            raise ExecutionFailure(
                "EXECUTION_CONFLICT", "historical WBS versions are read-only"
            )
        await self.connection.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"{request.plan_version_id}:{request.task_id}",
        )
        approved = await self.connection.fetchrow(
            _APPROVED_PLAN_SQL,
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            request.project_id,
            request.plan_id,
            request.plan_version_id,
            scope.actor_id,
        )
        task = await self.connection.fetchrow(
            _TASK_SQL,
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            request.project_id,
            request.plan_id,
            request.plan_version_id,
            request.task_id,
            scope.actor_id,
        )
        if approved is None:
            raise ExecutionFailure(
                "PLAN_NOT_APPROVED", "execution requires an approved WBS version"
            )
        if task is None:
            raise ExecutionFailure("TASK_NOT_FOUND", "execution task is unavailable")
        latest_row = await self.connection.fetchrow(
            _LATEST_SQL,
            request.plan_version_id,
            request.task_id,
            scope.actor_id,
        )
        latest = None if latest_row is None else _map_state(latest_row)
        current_sequence = 0 if latest is None else latest.sequence_no
        expected_state = _state_values(request)
        if (
            latest is not None
            and request.expected_sequence + 1 == latest.sequence_no
            and _state_tuple(latest) == expected_state
        ):
            return ExecutionUpdateResult(latest, replayed=True)
        if current_sequence != request.expected_sequence:
            raise ExecutionFailure(
                "EXECUTION_CONFLICT", "execution state changed before this update"
            )
        sequence = current_sequence + 1
        update_id = str(uuid4())
        await self.connection.execute(
            _INSERT_SQL,
            update_id,
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            request.project_id,
            request.plan_id,
            request.plan_version_id,
            request.task_id,
            sequence,
            request.status,
            _trim(request.blocker_reason),
            _date(request.actual_start),
            _date(request.actual_finish),
            _trim(request.note),
            scope.actor_id,
            updated_at,
        )
        await self.connection.execute(
            _AUDIT_SQL,
            str(uuid4()),
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            request.project_id,
            scope.actor_id,
            "wbs_task_execution_updated",
            "wbs_task_execution_update",
            update_id,
            json.dumps(
                {
                    "planId": request.plan_id,
                    "planVersionId": request.plan_version_id,
                    "taskId": request.task_id,
                    "sequence": sequence,
                    "status": request.status,
                    "hasBlocker": bool(request.blocker_reason),
                    "actualStart": request.actual_start,
                    "actualFinish": request.actual_finish,
                },
                separators=(",", ":"),
            ),
            updated_at,
        )
        return ExecutionUpdateResult(
            ExecutionState(
                request.task_id,
                sequence,
                request.status,
                _trim(request.blocker_reason),
                request.actual_start,
                request.actual_finish,
                _trim(request.note),
                scope.actor_id,
                updated_at,
            )
        )


def _map_state(row: Any) -> ExecutionState:
    return ExecutionState(
        task_id=str(row["task_id"]),
        sequence_no=int(row["sequence_no"]),
        status=str(row["status"]),
        blocker_reason=row["blocker_reason"],
        actual_start=(
            None if row["actual_start"] is None else row["actual_start"].isoformat()
        ),
        actual_finish=(
            None
            if row["actual_finish"] is None
            else row["actual_finish"].isoformat()
        ),
        note=row["note"],
        updated_by=str(UUID(str(row["updated_by"]))),
        updated_at=row["updated_at"],
    )


def _trim(value: str | None) -> str | None:
    if value is None:
        return None
    result = value.strip()
    return result or None


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _state_values(request: ExecutionUpdateRequest) -> tuple[Any, ...]:
    return (
        request.status,
        _trim(request.blocker_reason),
        request.actual_start,
        request.actual_finish,
        _trim(request.note),
    )


def _state_tuple(state: ExecutionState) -> tuple[Any, ...]:
    return (
        state.status,
        state.blocker_reason,
        state.actual_start,
        state.actual_finish,
        state.note,
    )
