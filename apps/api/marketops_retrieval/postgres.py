from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Callable
from uuid import UUID

from .service import (
    RetrievalFailure,
    RetrievalScopeContext,
    SourceChunk,
    SourceIdentity,
    SourceIndex,
)


_SET_SCOPE_SQL = """
SELECT
    set_config('app.workspace_id', $1, true),
    set_config('app.client_id', $2, true),
    set_config('app.project_id', $3, true),
    set_config('app.actor_id', $4, true)
"""

_LOCK_INDEX_REQUEST_SQL = """
SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))
"""

_SOURCE_VERSION_SQL = """
SELECT id, sha256
FROM marketops.artifact_versions
WHERE id = $1
  AND organization_id = $2
  AND workspace_id = $3
  AND client_id = $4
  AND project_id = $5
  AND created_by = $6
"""

_INDEX_BY_ID_SQL = """
SELECT id, organization_id, workspace_id, client_id, project_id,
       artifact_version_id, source_sha256, parser_version, chunker_version,
       status, index_sha256, created_by, created_at, withdrawn_by, withdrawn_at
FROM marketops.source_indexes
WHERE id = $1
  AND organization_id = $2
  AND workspace_id = $3
  AND client_id = $4
  AND project_id = $5
  AND created_by = $6
"""

_LOCK_INDEX_SQL = _INDEX_BY_ID_SQL + "\nFOR UPDATE"

_LIST_INDEXES_SQL = """
SELECT id, organization_id, workspace_id, client_id, project_id,
       artifact_version_id, source_sha256, parser_version, chunker_version,
       status, index_sha256, created_by, created_at, withdrawn_by, withdrawn_at
FROM marketops.source_indexes
WHERE organization_id = $1
  AND workspace_id = $2
  AND client_id = $3
  AND project_id = $4
  AND created_by = $5
  AND status = 'ready'
ORDER BY created_at, id
"""

_CHUNKS_SQL = """
SELECT id, index_id, organization_id, workspace_id, client_id, project_id,
       artifact_version_id, ordinal, chunk_text, content_sha256, location,
       source_sha256, parser_version, chunker_version, created_by, created_at
FROM marketops.source_chunks
WHERE index_id = $1
  AND organization_id = $2
  AND workspace_id = $3
  AND client_id = $4
  AND project_id = $5
  AND created_by = $6
ORDER BY ordinal
"""

_INSERT_INDEX_SQL = """
INSERT INTO marketops.source_indexes (
    id, organization_id, workspace_id, client_id, project_id,
    artifact_version_id, source_sha256, parser_version, chunker_version,
    status, index_sha256, created_by, created_at
) VALUES (
    $1, $2, $3, $4, $5, $6, decode($7, 'hex'), $8, $9,
    'ready', decode($10, 'hex'), $11, $12
)
"""

_INSERT_CHUNK_SQL = """
INSERT INTO marketops.source_chunks (
    id, index_id, organization_id, workspace_id, client_id, project_id,
    artifact_version_id, ordinal, chunk_text, content_sha256, location,
    source_sha256, parser_version, chunker_version, created_by, created_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, decode($10, 'hex'),
    $11::jsonb, decode($12, 'hex'), $13, $14, $15, $16
)
"""

_WITHDRAW_INDEX_SQL = """
UPDATE marketops.source_indexes
SET status = 'withdrawn', withdrawn_by = $2, withdrawn_at = $3
WHERE id = $1 AND status = 'ready'
"""

_DELETE_CHUNKS_SQL = "DELETE FROM marketops.source_chunks WHERE index_id = $1"

_INSERT_AUDIT_SQL = """
INSERT INTO marketops.audit_events (
    id, organization_id, workspace_id, client_id, project_id, actor_id,
    action, target_type, target_id, event_data, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, 'source_index', $8, $9::jsonb, $10)
"""


@dataclass(frozen=True)
class PersistIndexResult:
    index: SourceIndex
    replayed: bool


@dataclass(frozen=True)
class WithdrawIndexResult:
    index: SourceIndex
    replayed: bool


class AsyncpgRetrievalRepository:
    def __init__(
        self,
        pool: Any,
        *,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime],
    ):
        self.pool = pool
        self.id_factory = id_factory
        self.clock = clock

    async def persist_index(
        self, index: SourceIndex, scope: RetrievalScopeContext
    ) -> PersistIndexResult:
        _require_index_scope(index, scope, index.source.project_id)
        created_at = self.clock()
        try:
            async with self._transaction(scope, index.source.project_id) as connection:
                await connection.fetchval(_LOCK_INDEX_REQUEST_SQL, index.index_id)
                source = await connection.fetchrow(
                    _SOURCE_VERSION_SQL,
                    index.source.artifact_version_id,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    index.source.project_id,
                    scope.actor_id,
                )
                if source is None or _hex(source["sha256"]) != index.source.source_sha256:
                    raise RetrievalFailure("SOURCE_NOT_FOUND", "source version was not found")
                existing_row = await connection.fetchrow(
                    _LOCK_INDEX_SQL,
                    index.index_id,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    index.source.project_id,
                    scope.actor_id,
                )
                if existing_row is not None:
                    existing = await _load_index(connection, existing_row, scope)
                    if existing != index:
                        raise RetrievalFailure(
                            "INDEX_CONFLICT", "source index identity already exists"
                        )
                    return PersistIndexResult(existing, True)
                await connection.execute(
                    _INSERT_INDEX_SQL,
                    index.index_id,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    index.source.project_id,
                    index.source.artifact_version_id,
                    index.source.source_sha256,
                    index.source.parser_version,
                    index.chunker_version,
                    index.index_sha256,
                    scope.actor_id,
                    created_at,
                )
                for chunk in index.chunks:
                    await connection.execute(
                        _INSERT_CHUNK_SQL,
                        chunk.chunk_id,
                        index.index_id,
                        scope.organization_id,
                        scope.workspace_id,
                        scope.client_id,
                        index.source.project_id,
                        index.source.artifact_version_id,
                        chunk.ordinal,
                        chunk.text,
                        chunk.content_sha256,
                        _json(dict(chunk.location)),
                        index.source.source_sha256,
                        index.source.parser_version,
                        index.chunker_version,
                        scope.actor_id,
                        created_at,
                    )
                await self._append_audit(
                    connection,
                    scope,
                    index.source.project_id,
                    "source_index.created",
                    index.index_id,
                    {
                        "artifactVersionId": index.source.artifact_version_id,
                        "chunkCount": len(index.chunks),
                        "indexSha256": index.index_sha256,
                        "parserVersion": index.source.parser_version,
                        "chunkerVersion": index.chunker_version,
                    },
                    created_at,
                )
                return PersistIndexResult(index, False)
        except RetrievalFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RetrievalFailure(
                "RETRIEVAL_WRITE_FAILED", "source index could not be persisted"
            ) from None

    async def list_ready_indexes(
        self,
        scope: RetrievalScopeContext,
        project_id: str,
    ) -> tuple[SourceIndex, ...]:
        try:
            async with self._read_connection(scope, project_id) as connection:
                rows = await connection.fetch(
                    _LIST_INDEXES_SQL,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    project_id,
                    scope.actor_id,
                )
                return tuple(
                    [await _load_index(connection, row, scope) for row in rows]
                )
        except asyncio.CancelledError:
            raise
        except RetrievalFailure:
            raise
        except Exception:
            raise RetrievalFailure(
                "RETRIEVAL_READ_FAILED", "source indexes could not be read"
            ) from None

    async def withdraw_index(
        self,
        index_id: str,
        project_id: str,
        scope: RetrievalScopeContext,
    ) -> WithdrawIndexResult:
        withdrawn_at = self.clock()
        try:
            async with self._transaction(scope, project_id) as connection:
                row = await connection.fetchrow(
                    _LOCK_INDEX_SQL,
                    index_id,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.client_id,
                    project_id,
                    scope.actor_id,
                )
                if row is None:
                    raise RetrievalFailure("INDEX_NOT_FOUND", "source index was not found")
                current = await _load_index(connection, row, scope)
                if current.status == "withdrawn":
                    return WithdrawIndexResult(current, True)
                await connection.execute(
                    _WITHDRAW_INDEX_SQL, index_id, scope.actor_id, withdrawn_at
                )
                await connection.execute(_DELETE_CHUNKS_SQL, index_id)
                await self._append_audit(
                    connection,
                    scope,
                    project_id,
                    "source_index.withdrawn",
                    index_id,
                    {"artifactVersionId": current.source.artifact_version_id},
                    withdrawn_at,
                )
                return WithdrawIndexResult(
                    SourceIndex(
                        index_id=current.index_id,
                        source=current.source,
                        chunker_version=current.chunker_version,
                        status="withdrawn",
                        index_sha256=current.index_sha256,
                        chunks=(),
                    ),
                    False,
                )
        except RetrievalFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RetrievalFailure(
                "RETRIEVAL_WRITE_FAILED", "source index could not be withdrawn"
            ) from None

    async def _append_audit(
        self,
        connection: Any,
        scope: RetrievalScopeContext,
        project_id: str,
        action: str,
        index_id: str,
        event_data: dict[str, Any],
        created_at: datetime,
    ) -> None:
        await connection.execute(
            _INSERT_AUDIT_SQL,
            self.id_factory(),
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            project_id,
            scope.actor_id,
            action,
            index_id,
            _json(event_data),
            created_at,
        )

    @asynccontextmanager
    async def _transaction(
        self, scope: RetrievalScopeContext, project_id: str
    ) -> AsyncIterator[Any]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.fetchrow(
                    _SET_SCOPE_SQL,
                    scope.workspace_id,
                    scope.client_id,
                    project_id,
                    scope.actor_id,
                )
                yield connection

    @asynccontextmanager
    async def _read_connection(
        self, scope: RetrievalScopeContext, project_id: str
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


async def _load_index(
    connection: Any, row: Any, scope: RetrievalScopeContext
) -> SourceIndex:
    source = SourceIdentity(
        organization_id=_uuid(row["organization_id"]),
        workspace_id=_uuid(row["workspace_id"]),
        client_id=_uuid(row["client_id"]),
        project_id=_uuid(row["project_id"]),
        artifact_version_id=_uuid(row["artifact_version_id"]),
        source_sha256=_hex(row["source_sha256"]),
        parser_version=_text(row["parser_version"]),
    )
    status = _text(row["status"])
    chunker_version = _text(row["chunker_version"])
    index = SourceIndex(
        index_id=_uuid(row["id"]),
        source=source,
        chunker_version=chunker_version,
        status=status,
        index_sha256=_hex(row["index_sha256"]),
        chunks=(),
    )
    _require_index_scope(index, scope, source.project_id)
    if status == "withdrawn":
        return index
    rows = await connection.fetch(
        _CHUNKS_SQL,
        index.index_id,
        scope.organization_id,
        scope.workspace_id,
        scope.client_id,
        source.project_id,
        scope.actor_id,
    )
    chunks = tuple(_map_chunk(chunk, index, scope) for chunk in rows)
    if not chunks or [chunk.ordinal for chunk in chunks] != list(range(len(chunks))):
        raise RetrievalFailure("RETRIEVAL_READ_FAILED", "source chunk history is invalid")
    return SourceIndex(
        index_id=index.index_id,
        source=index.source,
        chunker_version=index.chunker_version,
        status=index.status,
        index_sha256=index.index_sha256,
        chunks=chunks,
    )


def _map_chunk(
    row: Any, index: SourceIndex, scope: RetrievalScopeContext
) -> SourceChunk:
    location = row["location"]
    if isinstance(location, str):
        location = json.loads(location)
    if not isinstance(location, dict):
        raise RetrievalFailure("RETRIEVAL_READ_FAILED", "source chunk row is invalid")
    chunk = SourceChunk(
        chunk_id=_uuid(row["id"]),
        index_id=_uuid(row["index_id"]),
        organization_id=_uuid(row["organization_id"]),
        workspace_id=_uuid(row["workspace_id"]),
        client_id=_uuid(row["client_id"]),
        project_id=_uuid(row["project_id"]),
        artifact_version_id=_uuid(row["artifact_version_id"]),
        ordinal=_nonnegative_int(row["ordinal"]),
        text=_text(row["chunk_text"]),
        content_sha256=_hex(row["content_sha256"]),
        location=location,
        source_sha256=_hex(row["source_sha256"]),
        parser_version=_text(row["parser_version"]),
        chunker_version=_text(row["chunker_version"]),
    )
    if (
        chunk.index_id != index.index_id
        or chunk.organization_id != scope.organization_id
        or chunk.workspace_id != scope.workspace_id
        or chunk.client_id != scope.client_id
        or chunk.project_id != index.source.project_id
        or chunk.artifact_version_id != index.source.artifact_version_id
        or chunk.source_sha256 != index.source.source_sha256
        or chunk.parser_version != index.source.parser_version
        or chunk.chunker_version != index.chunker_version
    ):
        raise RetrievalFailure("SCOPE_CONTAMINATION", "source chunk scope is invalid")
    return chunk


def _require_index_scope(
    index: SourceIndex, scope: RetrievalScopeContext, project_id: str
) -> None:
    if (
        index.source.organization_id != scope.organization_id
        or index.source.workspace_id != scope.workspace_id
        or index.source.client_id != scope.client_id
        or index.source.project_id != project_id
    ):
        raise RetrievalFailure("SCOPE_CONTAMINATION", "source index scope is invalid")


def _uuid(value: Any) -> str:
    return str(UUID(str(value)))


def _hex(value: Any) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, (bytes, bytearray)) or len(value) != 32:
        raise ValueError("hash is invalid")
    return bytes(value).hex()


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("text is invalid")
    return value


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("integer is invalid")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
