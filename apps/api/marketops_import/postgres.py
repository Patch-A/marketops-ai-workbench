"""Asyncpg repository adapter for the atomic project import contract."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator

from .service import (
    AuditEvent,
    ImportClaim,
    ImportFailure,
    ProjectImportRecord,
    ScopeContext,
    StoredObject,
)


class PostgresAdapterError(RuntimeError):
    """A stable, non-sensitive database adapter failure."""

    code = "DATABASE_WRITE_FAILED"
    retryable = False


class PostgresUnavailableError(PostgresAdapterError):
    retryable = True


class PostgresAuthorizationError(PostgresAdapterError):
    code = "AUTHORIZATION_REQUIRED"


class PostgresConstraintError(PostgresAdapterError):
    pass


class PostgresDataError(PostgresAdapterError):
    pass


_SET_SCOPE_SQL = """
SELECT
    set_config('app.workspace_id', $1, true),
    set_config('app.client_id', $2, true),
    set_config('app.project_id', '', true),
    set_config('app.actor_id', $3, true)
"""

_SET_PROJECT_SQL = "SELECT set_config('app.project_id', $1, true)"

_CLAIM_PROJECT_SQL = """
INSERT INTO marketops.projects (
    id, organization_id, workspace_id, client_id, name, status,
    import_request_id, import_manifest_sha256,
    approved_proposal_artifact_id, approved_proposal_version_id,
    approved_proposal_number, created_by
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (workspace_id, client_id, import_request_id) DO NOTHING
RETURNING id
"""

_SELECT_PROJECT_SQL = """
SELECT
    id, organization_id, workspace_id, client_id, import_request_id,
    import_manifest_sha256, name, status, approved_proposal_artifact_id,
    approved_proposal_version_id, approved_proposal_number, created_by
FROM marketops.projects
WHERE organization_id = $1
  AND workspace_id = $2
  AND client_id = $3
  AND import_request_id = $4
"""

_LIST_PROJECTS_SQL = """
SELECT
    id, name, status, approved_proposal_number, created_at
FROM marketops.projects
WHERE organization_id = $1
  AND workspace_id = $2
  AND client_id = $3
ORDER BY created_at DESC, id DESC
LIMIT $4
"""

_READ_PROJECT_SQL = """
SELECT
    id, name, status, approved_proposal_artifact_id,
    approved_proposal_version_id, approved_proposal_number, created_at
FROM marketops.projects
WHERE organization_id = $1
  AND workspace_id = $2
  AND client_id = $3
  AND id = $4
"""

_READ_PROJECT_VERSIONS_SQL = """
SELECT
    artifact.kind, artifact.id AS artifact_id, version.id AS version_id,
    version.proposal_version, version.original_filename, version.media_type,
    version.byte_size, version.approval_status, version.approved_at
FROM marketops.artifacts AS artifact
JOIN marketops.artifact_versions AS version
  ON version.organization_id = artifact.organization_id
 AND version.workspace_id = artifact.workspace_id
 AND version.client_id = artifact.client_id
 AND version.project_id = artifact.project_id
 AND version.artifact_id = artifact.id
WHERE artifact.organization_id = $1
  AND artifact.workspace_id = $2
  AND artifact.client_id = $3
  AND artifact.project_id = $4
ORDER BY artifact.kind, version.id
"""

_INSERT_ARTIFACT_SQL = """
INSERT INTO marketops.artifacts (
    id, organization_id, workspace_id, client_id, project_id, kind, created_by
)
VALUES ($1, $2, $3, $4, $5, $6, $7)
"""

_INSERT_VERSION_SQL = """
INSERT INTO marketops.artifact_versions (
    id, organization_id, workspace_id, client_id, project_id, artifact_id,
    proposal_version, original_filename, media_type, byte_size, storage_key,
    sha256, approval_status, approved_by, approved_at, created_by
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16
)
"""

_SELECT_VERSIONS_SQL = """
SELECT
    artifact.kind, artifact.id AS artifact_id, version.id AS version_id,
    version.proposal_version, version.original_filename, version.media_type,
    version.byte_size, version.storage_key, version.sha256,
    version.approval_status, version.approved_by, version.approved_at,
    version.created_by
FROM marketops.artifacts AS artifact
JOIN marketops.artifact_versions AS version
  ON version.organization_id = artifact.organization_id
 AND version.workspace_id = artifact.workspace_id
 AND version.client_id = artifact.client_id
 AND version.project_id = artifact.project_id
 AND version.artifact_id = artifact.id
WHERE artifact.organization_id = $1
  AND artifact.workspace_id = $2
  AND artifact.client_id = $3
  AND artifact.project_id = $4
ORDER BY artifact.kind, version.id
"""

_INSERT_AUDIT_SQL = """
INSERT INTO marketops.audit_events (
    id, organization_id, workspace_id, client_id, project_id, actor_id,
    action, target_type, target_id, event_data
)
VALUES ($1, $2, $3, $4, $5, $6, $7, 'project', $5, $8::jsonb)
"""


@dataclass(frozen=True)
class ProjectSummary:
    project_id: str
    name: str
    status: str
    approved_proposal_version: int
    created_at: datetime


@dataclass(frozen=True)
class ProjectFileView:
    artifact_id: str
    version_id: str
    filename: str
    media_type: str
    size_bytes: int


@dataclass(frozen=True)
class ApprovedProposalView(ProjectFileView):
    proposal_version: int
    approval_status: str
    approved_at: datetime


@dataclass(frozen=True)
class ProjectDetail:
    project_id: str
    name: str
    status: str
    created_at: datetime
    source_file: ProjectFileView
    approved_proposal: ApprovedProposalView


class AsyncpgImportRepository:
    def __init__(self, pool: Any):
        self._pool = pool

    @classmethod
    async def create(cls, dsn: str, **pool_options: Any) -> "AsyncpgImportRepository":
        try:
            import asyncpg
        except ImportError as error:
            raise RuntimeError("asyncpg runtime dependency is not installed") from error
        translated_error: PostgresAdapterError | None = None
        try:
            pool = await asyncpg.create_pool(dsn=dsn, **pool_options)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            translated_error = _translate_driver_error(error)
        if translated_error is not None:
            raise translated_error
        return cls(pool)

    async def close(self) -> None:
        translated_error: PostgresAdapterError | None = None
        try:
            await self._pool.close()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            translated_error = _translate_driver_error(error)
        if translated_error is not None:
            raise translated_error

    async def list_projects(
        self, scope: ScopeContext, *, limit: int = 20
    ) -> tuple[ProjectSummary, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("project list limit must be between 1 and 100")
        rows = await self._read_in_scope(
            scope,
            lambda connection: connection.fetch(
                _LIST_PROJECTS_SQL,
                scope.organization_id,
                scope.workspace_id,
                scope.client_id,
                limit,
            ),
        )
        try:
            return tuple(_map_project_summary(row) for row in rows)
        except (KeyError, TypeError, ValueError) as error:
            raise PostgresDataError("database rows violated the project list contract") from None

    async def get_project(
        self, scope: ScopeContext, project_id: str
    ) -> ProjectDetail | None:
        async def read(connection: Any) -> tuple[Any, list[Any]] | None:
            await connection.fetchrow(_SET_PROJECT_SQL, project_id)
            project = await connection.fetchrow(
                _READ_PROJECT_SQL,
                scope.organization_id,
                scope.workspace_id,
                scope.client_id,
                project_id,
            )
            if project is None:
                return None
            versions = await connection.fetch(
                _READ_PROJECT_VERSIONS_SQL,
                scope.organization_id,
                scope.workspace_id,
                scope.client_id,
                project_id,
            )
            return project, versions

        result = await self._read_in_scope(scope, read)
        if result is None:
            return None
        try:
            return _map_project_detail(*result)
        except (KeyError, TypeError, ValueError) as error:
            raise PostgresDataError("database rows violated the project detail contract") from None

    async def _read_in_scope(self, scope: ScopeContext, operation: Any) -> Any:
        translated_error: PostgresAdapterError | None = None
        try:
            async with self._pool.acquire() as connection:
                transaction_error: PostgresAdapterError | None = None
                try:
                    async with connection.transaction():
                        await connection.fetchrow(
                            _SET_SCOPE_SQL,
                            scope.workspace_id,
                            scope.client_id,
                            scope.actor_id,
                        )
                        return await operation(connection)
                except asyncio.CancelledError:
                    raise
                except PostgresAdapterError:
                    raise
                except Exception as error:
                    if _connection_is_closed(connection) or _is_asyncpg_connection_loss(error):
                        transaction_error = PostgresUnavailableError(
                            "database operation is temporarily unavailable"
                        )
                    else:
                        transaction_error = _translate_driver_error(error)
                if transaction_error is not None:
                    raise transaction_error
        except asyncio.CancelledError:
            raise
        except PostgresAdapterError:
            raise
        except Exception as error:
            translated_error = _translate_driver_error(error)
        if translated_error is not None:
            raise translated_error
        raise PostgresDataError("database read ended without a result")

    @asynccontextmanager
    async def transaction(
        self, scope: ScopeContext
    ) -> AsyncIterator["AsyncpgImportTransaction"]:
        translated_error: PostgresAdapterError | None = None
        try:
            async with self._pool.acquire() as connection:
                transaction_error: PostgresAdapterError | None = None
                try:
                    async with connection.transaction():
                        await connection.fetchrow(
                            _SET_SCOPE_SQL,
                            scope.workspace_id,
                            scope.client_id,
                            scope.actor_id,
                        )
                        yield AsyncpgImportTransaction(connection, scope)
                except asyncio.CancelledError:
                    raise
                except (ImportFailure, PostgresAdapterError):
                    raise
                except Exception as error:
                    if _connection_is_closed(connection) or _is_asyncpg_connection_loss(
                        error
                    ):
                        transaction_error = PostgresUnavailableError(
                            "database operation is temporarily unavailable"
                        )
                    else:
                        transaction_error = _translate_driver_error(error)
                if transaction_error is not None:
                    raise transaction_error
        except asyncio.CancelledError:
            raise
        except (ImportFailure, PostgresAdapterError):
            raise
        except Exception as error:
            translated_error = _translate_driver_error(error)
        if translated_error is not None:
            raise translated_error


class AsyncpgImportTransaction:
    def __init__(self, connection: Any, scope: ScopeContext):
        self._connection = connection
        self._scope = scope
        self._project_id: str | None = None
        self._claimed_record: ProjectImportRecord | None = None

    async def try_create_project_import(
        self, record: ProjectImportRecord
    ) -> ImportClaim:
        self._require_record_scope(record)
        translated_error: PostgresAdapterError | None = None
        try:
            await self._set_project(record.project_id)
            inserted = await self._connection.fetchrow(
                _CLAIM_PROJECT_SQL,
                record.project_id,
                record.organization_id,
                record.workspace_id,
                record.client_id,
                record.project_name,
                record.status,
                record.idempotency_key,
                bytes.fromhex(record.manifest_sha256),
                record.proposal_artifact_id,
                record.proposal_version_id,
                record.approved_proposal_version,
                record.created_by,
            )
            if inserted is not None:
                await self._insert_artifact(record, kind="source")
                await self._insert_artifact(record, kind="proposal")
                self._claimed_record = record
                return ImportClaim(record=record, created=True)

            project = await self._connection.fetchrow(
                _SELECT_PROJECT_SQL,
                self._scope.organization_id,
                self._scope.workspace_id,
                self._scope.client_id,
                record.idempotency_key,
            )
            if project is None:
                raise PostgresDataError("idempotency winner was not visible in scope")
            await self._set_project(str(project["id"]))
            versions = await self._connection.fetch(
                _SELECT_VERSIONS_SQL,
                self._scope.organization_id,
                self._scope.workspace_id,
                self._scope.client_id,
                str(project["id"]),
            )
            claimed_record = _map_record(project, versions)
            self._claimed_record = claimed_record
            return ImportClaim(record=claimed_record, created=False)
        except asyncio.CancelledError:
            raise
        except PostgresAdapterError:
            raise
        except (ValueError, TypeError, KeyError) as error:
            translated_error = PostgresDataError(
                "database rows violated the import contract"
            )
        except Exception as error:
            translated_error = _translate_driver_error(error)
        if translated_error is not None:
            raise translated_error
        raise PostgresDataError("database claim ended without a result")

    async def append_audit_event(self, event: AuditEvent) -> None:
        record = self._claimed_record
        if record is None or self._project_id is None or event.project_id != self._project_id:
            raise PostgresDataError("audit event does not match the claimed project")
        if event.actor_id != self._scope.actor_id or event.approved_by != self._scope.actor_id:
            raise PostgresAuthorizationError("audit actor does not match server scope")
        if (
            event.manifest_sha256 != record.manifest_sha256
            or event.source_version_id != record.source_version_id
            or event.proposal_version_id != record.proposal_version_id
            or event.approved_at != record.approved_at
        ):
            raise PostgresDataError("audit event does not match the claimed import")
        payload = json.dumps(
            {
                "approvedAt": event.approved_at.isoformat(),
                "approvedBy": event.approved_by,
                "manifestSha256": event.manifest_sha256,
                "proposalVersionId": event.proposal_version_id,
                "sourceVersionId": event.source_version_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        translated_error: PostgresAdapterError | None = None
        try:
            await self._connection.execute(
                _INSERT_AUDIT_SQL,
                event.event_id,
                self._scope.organization_id,
                self._scope.workspace_id,
                self._scope.client_id,
                event.project_id,
                event.actor_id,
                event.action,
                payload,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            translated_error = _translate_driver_error(error)
        if translated_error is not None:
            raise translated_error

    async def _set_project(self, project_id: str) -> None:
        await self._connection.fetchrow(_SET_PROJECT_SQL, project_id)
        self._project_id = project_id

    async def _insert_artifact(
        self, record: ProjectImportRecord, *, kind: str
    ) -> None:
        if kind == "source":
            artifact_id = record.source_artifact_id
            version_id = record.source_version_id
            stored = record.source
            filename = record.source_filename
            media_type = record.source_media_type
            proposal_version = None
            approval_status = "not_applicable"
            approved_by = None
            approved_at = None
        else:
            artifact_id = record.proposal_artifact_id
            version_id = record.proposal_version_id
            stored = record.proposal
            filename = record.proposal_filename
            media_type = record.proposal_media_type
            proposal_version = record.approved_proposal_version
            approval_status = record.approval_status
            approved_by = record.approved_by
            approved_at = record.approved_at

        await self._connection.execute(
            _INSERT_ARTIFACT_SQL,
            artifact_id,
            record.organization_id,
            record.workspace_id,
            record.client_id,
            record.project_id,
            kind,
            record.created_by,
        )
        await self._connection.execute(
            _INSERT_VERSION_SQL,
            version_id,
            record.organization_id,
            record.workspace_id,
            record.client_id,
            record.project_id,
            artifact_id,
            proposal_version,
            filename,
            media_type,
            stored.size_bytes,
            stored.storage_key,
            bytes.fromhex(stored.sha256),
            approval_status,
            approved_by,
            approved_at,
            record.created_by,
        )

    def _require_record_scope(self, record: ProjectImportRecord) -> None:
        if (
            record.organization_id != self._scope.organization_id
            or record.workspace_id != self._scope.workspace_id
            or record.client_id != self._scope.client_id
            or record.created_by != self._scope.actor_id
        ):
            raise PostgresAuthorizationError("record does not match server scope")


def _map_record(project: Any, version_rows: list[Any]) -> ProjectImportRecord:
    versions: dict[str, Any] = {}
    for row in version_rows:
        kind = row["kind"]
        if kind not in {"source", "proposal"} or kind in versions:
            raise PostgresDataError("project artifact mapping is incomplete")
        versions[kind] = row
    if set(versions) != {"source", "proposal"}:
        raise PostgresDataError("project artifact mapping is incomplete")
    source = versions["source"]
    proposal = versions["proposal"]
    if (
        str(project["approved_proposal_artifact_id"]) != str(proposal["artifact_id"])
        or str(project["approved_proposal_version_id"]) != str(proposal["version_id"])
        or project["approved_proposal_number"] != proposal["proposal_version"]
    ):
        raise PostgresDataError("approved proposal mapping is inconsistent")
    if (
        source["proposal_version"] is not None
        or source["approval_status"] != "not_applicable"
        or source["approved_by"] is not None
        or source["approved_at"] is not None
        or proposal["approval_status"] != "approved"
        or proposal["approved_by"] is None
        or proposal["approved_at"] is None
        or str(proposal["approved_by"]) != str(project["created_by"])
        or str(source["created_by"]) != str(project["created_by"])
        or str(proposal["created_by"]) != str(project["created_by"])
    ):
        raise PostgresDataError("artifact approval mapping is inconsistent")
    return ProjectImportRecord(
        project_id=str(project["id"]),
        organization_id=str(project["organization_id"]),
        workspace_id=str(project["workspace_id"]),
        client_id=str(project["client_id"]),
        idempotency_key=project["import_request_id"],
        manifest_sha256=_hex(project["import_manifest_sha256"]),
        project_name=project["name"],
        status=project["status"],
        source_artifact_id=str(source["artifact_id"]),
        source_version_id=str(source["version_id"]),
        source=_stored(source),
        source_filename=source["original_filename"],
        source_media_type=source["media_type"],
        proposal_artifact_id=str(proposal["artifact_id"]),
        proposal_version_id=str(proposal["version_id"]),
        proposal=_stored(proposal),
        proposal_filename=proposal["original_filename"],
        proposal_media_type=proposal["media_type"],
        approved_proposal_version=project["approved_proposal_number"],
        approval_status=proposal["approval_status"],
        approved_by=str(proposal["approved_by"]),
        approved_at=proposal["approved_at"],
        created_by=str(project["created_by"]),
    )


def _map_project_summary(row: Any) -> ProjectSummary:
    created_at = row["created_at"]
    if not isinstance(created_at, datetime):
        raise PostgresDataError("project creation time is invalid")
    return ProjectSummary(
        project_id=str(row["id"]),
        name=row["name"],
        status=row["status"],
        approved_proposal_version=row["approved_proposal_number"],
        created_at=created_at,
    )


def _map_project_detail(project: Any, version_rows: list[Any]) -> ProjectDetail:
    versions: dict[str, Any] = {}
    for row in version_rows:
        kind = row["kind"]
        if kind not in {"source", "proposal"} or kind in versions:
            raise PostgresDataError("project artifact mapping is incomplete")
        versions[kind] = row
    if set(versions) != {"source", "proposal"}:
        raise PostgresDataError("project artifact mapping is incomplete")
    source = versions["source"]
    proposal = versions["proposal"]
    created_at = project["created_at"]
    approved_at = proposal["approved_at"]
    if not isinstance(created_at, datetime) or not isinstance(approved_at, datetime):
        raise PostgresDataError("project timestamps are invalid")
    if (
        str(project["approved_proposal_artifact_id"]) != str(proposal["artifact_id"])
        or str(project["approved_proposal_version_id"]) != str(proposal["version_id"])
        or project["approved_proposal_number"] != proposal["proposal_version"]
        or source["proposal_version"] is not None
        or source["approval_status"] != "not_applicable"
        or source["approved_at"] is not None
        or proposal["approval_status"] != "approved"
    ):
        raise PostgresDataError("approved proposal mapping is inconsistent")
    return ProjectDetail(
        project_id=str(project["id"]),
        name=project["name"],
        status=project["status"],
        created_at=created_at,
        source_file=ProjectFileView(
            artifact_id=str(source["artifact_id"]),
            version_id=str(source["version_id"]),
            filename=source["original_filename"],
            media_type=source["media_type"],
            size_bytes=source["byte_size"],
        ),
        approved_proposal=ApprovedProposalView(
            artifact_id=str(proposal["artifact_id"]),
            version_id=str(proposal["version_id"]),
            filename=proposal["original_filename"],
            media_type=proposal["media_type"],
            size_bytes=proposal["byte_size"],
            proposal_version=proposal["proposal_version"],
            approval_status=proposal["approval_status"],
            approved_at=approved_at,
        ),
    )


def _stored(row: Any) -> StoredObject:
    return StoredObject(
        storage_key=row["storage_key"],
        size_bytes=row["byte_size"],
        sha256=_hex(row["sha256"]),
    )


def _hex(value: Any) -> str:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise PostgresDataError("database hash has an invalid representation")
    return bytes(value).hex()


def _connection_is_closed(connection: Any) -> bool:
    is_closed = getattr(connection, "is_closed", None)
    if not callable(is_closed):
        return False
    try:
        return is_closed() is True
    except Exception:
        return False


def _is_asyncpg_connection_loss(error: Exception) -> bool:
    error_type = type(error)
    if (
        error_type.__name__ != "InterfaceError"
        or not error_type.__module__.startswith("asyncpg.")
    ):
        return False
    message = str(error)
    return (
        "the underlying connection is closed" in message
        or "connection has been released back to the pool" in message
    )


def _translate_driver_error(error: Exception) -> PostgresAdapterError:
    sqlstate = getattr(error, "sqlstate", None)
    if isinstance(sqlstate, str) and sqlstate.startswith("08"):
        return PostgresUnavailableError("database operation is temporarily unavailable")
    if sqlstate in {"42501", "28000", "28P01"}:
        return PostgresAuthorizationError("database authorization rejected the operation")
    if isinstance(sqlstate, str) and sqlstate.startswith("23"):
        return PostgresConstraintError("database constraints rejected the import")
    return PostgresAdapterError("database operation failed")
