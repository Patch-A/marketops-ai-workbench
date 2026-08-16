"""Server-owned source loading and parsing for scoped retrieval indexes."""

from __future__ import annotations

import asyncio
import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from apps.api.marketops_extract.contract import ParserBlock, TableCell
from apps.api.marketops_extract.parser import (
    MAX_PARSER_BLOCKS,
    ParserWarning,
    RuntimeParseFailure,
    RuntimeParserResult,
    RuntimeProposalParser,
)
from apps.api.marketops_import.service import MAX_FILE_SIZE_BYTES, StoredObject

from .postgres import PersistIndexResult
from .service import (
    ParsedBlock,
    RetrievalFailure,
    RetrievalScopeContext,
    SourceIdentity,
    SourceIndex,
    build_source_index,
)


PARSER_VERSION = "marketops-parser-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_SET_SCOPE_SQL = """
SELECT
    set_config('app.workspace_id', $1, true),
    set_config('app.client_id', $2, true),
    set_config('app.project_id', $3, true),
    set_config('app.actor_id', $4, true)
"""

_READ_SOURCE_SQL = """
SELECT
    artifact.organization_id,
    artifact.workspace_id,
    artifact.client_id,
    artifact.project_id,
    artifact.id AS artifact_id,
    artifact.kind AS artifact_kind,
    version.id AS artifact_version_id,
    version.original_filename,
    version.media_type,
    version.byte_size,
    version.storage_key,
    version.sha256
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
  AND version.id = $5
  AND artifact.created_by = $6
  AND version.created_by = $6
"""


@dataclass(frozen=True)
class IndexSourceRequest:
    project_id: str
    artifact_version_id: str


@dataclass(frozen=True)
class ArtifactVersionSource:
    organization_id: str
    workspace_id: str
    client_id: str
    project_id: str
    artifact_id: str
    artifact_kind: str
    artifact_version_id: str
    filename: str
    media_type: str
    size_bytes: int
    storage_key: str
    sha256: str


@dataclass(frozen=True)
class IndexSourceResult:
    index: SourceIndex
    document_format: str
    replayed: bool


class ArtifactVersionSourceReader(Protocol):
    async def read_source(
        self,
        scope: RetrievalScopeContext,
        project_id: str,
        artifact_version_id: str,
    ) -> ArtifactVersionSource | None: ...


class VerifiedObjectReader(Protocol):
    def import_guard(self) -> AbstractAsyncContextManager[None]: ...

    async def read_verified_immutable(
        self, *, kind: str, stored: StoredObject
    ) -> bytes: ...


class SourceParser(Protocol):
    async def parse(
        self, *, payload: bytes, filename: str, media_type: str
    ) -> RuntimeParserResult: ...


class SourceIndexRepository(Protocol):
    async def persist_index(
        self, index: SourceIndex, scope: RetrievalScopeContext
    ) -> PersistIndexResult: ...


class AsyncpgArtifactVersionSourceReader:
    """Read one explicitly selected artifact version through forced RLS."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def read_source(
        self,
        scope: RetrievalScopeContext,
        project_id: str,
        artifact_version_id: str,
    ) -> ArtifactVersionSource | None:
        try:
            async with self.pool.acquire() as connection:
                async with connection.transaction(readonly=True):
                    await connection.fetchrow(
                        _SET_SCOPE_SQL,
                        scope.workspace_id,
                        scope.client_id,
                        project_id,
                        scope.actor_id,
                    )
                    row = await connection.fetchrow(
                        _READ_SOURCE_SQL,
                        scope.organization_id,
                        scope.workspace_id,
                        scope.client_id,
                        project_id,
                        artifact_version_id,
                        scope.actor_id,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RetrievalFailure(
                "SOURCE_READ_FAILED", "source version could not be read"
            ) from None
        if row is None:
            return None
        try:
            return ArtifactVersionSource(
                organization_id=_uuid(row["organization_id"]),
                workspace_id=_uuid(row["workspace_id"]),
                client_id=_uuid(row["client_id"]),
                project_id=_uuid(row["project_id"]),
                artifact_id=_uuid(row["artifact_id"]),
                artifact_kind=_text(row["artifact_kind"]),
                artifact_version_id=_uuid(row["artifact_version_id"]),
                filename=_text(row["original_filename"]),
                media_type=_text(row["media_type"]),
                size_bytes=_positive_int(row["byte_size"]),
                storage_key=_text(row["storage_key"]),
                sha256=_hex(row["sha256"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise RetrievalFailure(
                "SOURCE_READ_FAILED", "source version row is invalid"
            ) from None


class SourceIndexingService:
    def __init__(
        self,
        *,
        source_reader: ArtifactVersionSourceReader,
        object_store: VerifiedObjectReader,
        repository: SourceIndexRepository,
        parser: SourceParser | None = None,
    ) -> None:
        self.source_reader = source_reader
        self.object_store = object_store
        self.repository = repository
        self.parser = parser or RuntimeProposalParser()

    async def index_source(
        self, request: IndexSourceRequest, scope: RetrievalScopeContext
    ) -> IndexSourceResult:
        _validate_request(request, scope)
        try:
            async with self.object_store.import_guard():
                source = await self._read_source(request, scope)
                payload = await self._read_object(source)
                parsed = await self._parse_source(source, payload)
                blocks = _to_retrieval_blocks(parsed.blocks)
                index = build_source_index(
                    SourceIdentity(
                        organization_id=source.organization_id,
                        workspace_id=source.workspace_id,
                        client_id=source.client_id,
                        project_id=source.project_id,
                        artifact_version_id=source.artifact_version_id,
                        source_sha256=source.sha256,
                        parser_version=PARSER_VERSION,
                    ),
                    blocks,
                    scope,
                )
                persisted = await self.repository.persist_index(index, scope)
        except asyncio.CancelledError:
            raise
        except RetrievalFailure:
            raise
        except Exception:
            raise RetrievalFailure(
                "SOURCE_READ_FAILED", "source indexing is temporarily unavailable"
            ) from None
        if (
            not isinstance(persisted, PersistIndexResult)
            or persisted.index != index
        ):
            raise RetrievalFailure(
                "INDEX_WRITE_FAILED", "source index repository returned invalid output"
            )
        return IndexSourceResult(
            index=persisted.index,
            document_format=parsed.document_format,
            replayed=persisted.replayed,
        )

    async def _read_source(
        self, request: IndexSourceRequest, scope: RetrievalScopeContext
    ) -> ArtifactVersionSource:
        try:
            source = await self.source_reader.read_source(
                scope, request.project_id, request.artifact_version_id
            )
        except asyncio.CancelledError:
            raise
        except RetrievalFailure:
            raise
        except Exception:
            raise RetrievalFailure(
                "SOURCE_READ_FAILED", "source version could not be read"
            ) from None
        if source is None:
            raise RetrievalFailure("SOURCE_NOT_FOUND", "source version was not found")
        _validate_source(source, request, scope)
        return source

    async def _read_object(self, source: ArtifactVersionSource) -> bytes:
        try:
            payload = await self.object_store.read_verified_immutable(
                kind=source.artifact_kind,
                stored=StoredObject(
                    storage_key=source.storage_key,
                    size_bytes=source.size_bytes,
                    sha256=source.sha256,
                ),
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise RetrievalFailure(
                "SOURCE_READ_FAILED", "source object is temporarily unavailable"
            ) from None
        except Exception:
            raise RetrievalFailure(
                "SOURCE_INTEGRITY_FAILED", "source object failed integrity verification"
            ) from None
        if not isinstance(payload, bytes):
            raise RetrievalFailure(
                "SOURCE_INTEGRITY_FAILED", "source object failed integrity verification"
            )
        return payload

    async def _parse_source(
        self, source: ArtifactVersionSource, payload: bytes
    ) -> RuntimeParserResult:
        try:
            result = await self.parser.parse(
                payload=payload,
                filename=source.filename,
                media_type=source.media_type,
            )
        except asyncio.CancelledError:
            raise
        except RuntimeParseFailure as error:
            if error.code == "DOCUMENT_LIMIT_EXCEEDED":
                code = "PARSER_LIMIT_EXCEEDED"
            elif error.code == "UNSUPPORTED_FORMAT":
                code = "UNSUPPORTED_FORMAT"
            else:
                code = "PARSER_FAILED"
            raise RetrievalFailure(code, "source document could not be parsed") from None
        except Exception:
            raise RetrievalFailure(
                "PARSER_FAILED", "source document could not be parsed"
            ) from None
        _validate_parser_result(result, source)
        return result


def _to_retrieval_blocks(blocks: tuple[ParserBlock, ...]) -> tuple[ParsedBlock, ...]:
    converted: list[ParsedBlock] = []
    for block in blocks:
        common = {
            "blockKind": block.kind,
            "sectionPath": list(block.section_path),
        }
        if block.text is not None:
            location = block.location.as_dict()
            location.update(common)
            text = (
                f"{block.column_name}: {block.text}"
                if block.column_name is not None
                else block.text
            )
            converted.append(ParsedBlock(text=text, location=location))
            continue
        for cell in block.cells:
            location = cell.location.as_dict()
            location.update(common)
            location["tableLocation"] = block.location.as_dict()
            converted.append(
                ParsedBlock(text=f"{cell.column}: {cell.value}", location=location)
            )
    if not converted:
        raise RetrievalFailure("INVALID_SOURCE", "parsed source has no indexable text")
    return tuple(converted)


def _validate_request(
    request: IndexSourceRequest, scope: RetrievalScopeContext
) -> None:
    if not isinstance(scope, RetrievalScopeContext) or any(
        not _canonical_uuid(value)
        for value in (
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            scope.actor_id,
        )
    ):
        raise RetrievalFailure("INVALID_SCOPE", "retrieval scope is invalid")
    if not isinstance(request, IndexSourceRequest) or not all(
        _canonical_uuid(value)
        for value in (request.project_id, request.artifact_version_id)
    ):
        raise RetrievalFailure("INVALID_INPUT", "source index request is invalid")


def _validate_source(
    source: ArtifactVersionSource,
    request: IndexSourceRequest,
    scope: RetrievalScopeContext,
) -> None:
    if not isinstance(source, ArtifactVersionSource):
        raise RetrievalFailure("SOURCE_NOT_FOUND", "source version was not found")
    identities = (
        source.organization_id,
        source.workspace_id,
        source.client_id,
        source.project_id,
        source.artifact_id,
        source.artifact_version_id,
    )
    if not all(_canonical_uuid(value) for value in identities):
        raise RetrievalFailure("SOURCE_NOT_FOUND", "source version was not found")
    if (
        source.organization_id,
        source.workspace_id,
        source.client_id,
        source.project_id,
        source.artifact_version_id,
    ) != (
        scope.organization_id,
        scope.workspace_id,
        scope.client_id,
        request.project_id,
        request.artifact_version_id,
    ):
        raise RetrievalFailure("SOURCE_NOT_FOUND", "source version was not found")
    if (
        source.artifact_kind not in {"source", "proposal"}
        or not isinstance(source.filename, str)
        or not source.filename.strip()
        or not isinstance(source.media_type, str)
        or not source.media_type.strip()
        or not isinstance(source.storage_key, str)
        or not source.storage_key
        or isinstance(source.size_bytes, bool)
        or not isinstance(source.size_bytes, int)
        or not 1 <= source.size_bytes <= MAX_FILE_SIZE_BYTES
        or not isinstance(source.sha256, str)
        or _SHA256.fullmatch(source.sha256) is None
    ):
        raise RetrievalFailure("SOURCE_NOT_FOUND", "source version was not found")


def _validate_parser_result(
    result: RuntimeParserResult, source: ArtifactVersionSource
) -> None:
    if (
        type(result) is not RuntimeParserResult
        or not isinstance(result.document_format, str)
        or not result.document_format
        or result.size_bytes != source.size_bytes
        or result.source_sha256 != source.sha256
        or not isinstance(result.blocks, tuple)
        or not result.blocks
        or len(result.blocks) > MAX_PARSER_BLOCKS
        or any(type(block) is not ParserBlock for block in result.blocks)
        or not isinstance(result.warnings, tuple)
        or any(type(warning) is not ParserWarning for warning in result.warnings)
    ):
        raise RetrievalFailure(
            "SOURCE_INTEGRITY_FAILED", "source parser identity is invalid"
        )
    if result.warnings:
        raise RetrievalFailure(
            "PARSER_WARNING", "source document contains unsupported content"
        )


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _uuid(value: object) -> str:
    return str(UUID(str(value)))


def _hex(value: object) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, (bytes, bytearray)) or len(value) != 32:
        raise ValueError("hash is invalid")
    return bytes(value).hex()


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text is invalid")
    return value.strip()


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("integer is invalid")
    return value
