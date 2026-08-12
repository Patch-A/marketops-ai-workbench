"""Server-owned approved proposal preparation for M1-02 review runs."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import AsyncContextManager, Protocol
from uuid import UUID

from apps.api.marketops_extract.contract import (
    Candidate,
    ExtractionContractError,
    ParserBlock,
    SourceLocation,
    TableCell,
)
from apps.api.marketops_extract.deterministic import DeterministicExtractor
from apps.api.marketops_extract.parser import (
    MAX_BLOCK_TEXT_CHARS,
    MAX_PARSER_BLOCKS,
    MAX_PARSER_TABLE_CELLS,
    MAX_PARSER_WARNINGS,
    RuntimeParseFailure,
    RuntimeParserResult,
    RuntimeProposalParser,
)
from apps.api.marketops_import.service import MAX_FILE_SIZE_BYTES, StoredObject
from apps.api.marketops_review.service import (
    CreateReviewRunRequest,
    CreateReviewRunResult,
    MAX_REVIEW_CANDIDATES,
    ReviewFailure,
    ReviewScopeContext,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_SET_SOURCE_SCOPE_SQL = """
SELECT
    set_config('app.workspace_id', $1, true),
    set_config('app.client_id', $2, true),
    set_config('app.project_id', $3, true),
    set_config('app.actor_id', $4, true)
"""

_READ_APPROVED_PROPOSAL_SOURCE_SQL = """
SELECT
    project.organization_id,
    project.workspace_id,
    project.client_id,
    project.id AS project_id,
    artifact.id AS artifact_id,
    version.id AS version_id,
    version.proposal_version,
    version.original_filename,
    version.media_type,
    version.byte_size,
    version.storage_key,
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
WHERE project.organization_id = $1
  AND project.workspace_id = $2
  AND project.client_id = $3
  AND project.id = $4
  AND project.created_by = $5
  AND artifact.kind = 'proposal'
  AND version.approval_status = 'approved'
  AND version.proposal_version = project.approved_proposal_number
"""


class PreparationFailure(RuntimeError):
    """Stable, sanitized preparation failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class PreparationRequest:
    project_id: str
    expected_proposal_version_id: str | None = None
    expected_proposal_sha256: str | None = None


@dataclass(frozen=True)
class ApprovedProposalSource:
    organization_id: str
    workspace_id: str
    client_id: str
    project_id: str
    artifact_id: str
    version_id: str
    version: int
    filename: str
    media_type: str
    size_bytes: int
    storage_key: str
    sha256: str


@dataclass(frozen=True)
class PreparedReviewRunResult:
    source: ApprovedProposalSource
    parser_result: RuntimeParserResult
    candidates: tuple[Candidate, ...]
    review_result: CreateReviewRunResult


class ApprovedProposalSourceReader(Protocol):
    async def read_approved_proposal(
        self, scope: ReviewScopeContext, project_id: str
    ) -> ApprovedProposalSource | None: ...


class ApprovedProposalSourceReadError(RuntimeError):
    """Sanitized adapter failure for the forced-RLS source lookup."""


class AsyncpgApprovedProposalSourceReader:
    """Read the current approved proposal through transaction-local RLS scope."""

    def __init__(self, pool: object) -> None:
        self._pool = pool

    async def read_approved_proposal(
        self, scope: ReviewScopeContext, project_id: str
    ) -> ApprovedProposalSource | None:
        try:
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    await connection.fetchrow(
                        _SET_SOURCE_SCOPE_SQL,
                        scope.workspace_id,
                        scope.client_id,
                        project_id,
                        scope.actor_id,
                    )
                    row = await connection.fetchrow(
                        _READ_APPROVED_PROPOSAL_SOURCE_SQL,
                        scope.organization_id,
                        scope.workspace_id,
                        scope.client_id,
                        project_id,
                        scope.actor_id,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ApprovedProposalSourceReadError(
                "approved proposal source lookup failed"
            ) from None
        if row is None:
            return None
        try:
            return ApprovedProposalSource(
                organization_id=str(row["organization_id"]),
                workspace_id=str(row["workspace_id"]),
                client_id=str(row["client_id"]),
                project_id=str(row["project_id"]),
                artifact_id=str(row["artifact_id"]),
                version_id=str(row["version_id"]),
                version=int(row["proposal_version"]),
                filename=str(row["original_filename"]),
                media_type=str(row["media_type"]),
                size_bytes=int(row["byte_size"]),
                storage_key=str(row["storage_key"]),
                sha256=self._hex(row["sha256"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ApprovedProposalSourceReadError(
                "approved proposal source row is invalid"
            ) from None

    @staticmethod
    def _hex(value: object) -> str:
        if isinstance(value, (bytes, bytearray, memoryview)):
            rendered = bytes(value).hex()
        elif isinstance(value, str):
            rendered = value.lower()
        else:
            raise TypeError("source hash is invalid")
        if _SHA256.fullmatch(rendered) is None:
            raise ValueError("source hash is invalid")
        return rendered


class VerifiedObjectReader(Protocol):
    def import_guard(self) -> AsyncContextManager[None]: ...

    async def read_verified_immutable(
        self, *, kind: str, stored: StoredObject
    ) -> bytes: ...


class ProposalParser(Protocol):
    async def parse(
        self, *, payload: bytes, filename: str, media_type: str
    ) -> RuntimeParserResult: ...


class ReviewRunCreator(Protocol):
    async def create_run(
        self, request: CreateReviewRunRequest, scope: ReviewScopeContext
    ) -> CreateReviewRunResult: ...


class ApprovedProposalPreparationService:
    def __init__(
        self,
        *,
        source_reader: ApprovedProposalSourceReader,
        object_store: VerifiedObjectReader,
        review_service: ReviewRunCreator,
        parser: ProposalParser | None = None,
        extractor: DeterministicExtractor | None = None,
    ) -> None:
        self.source_reader = source_reader
        self.object_store = object_store
        self.review_service = review_service
        self.parser = parser or RuntimeProposalParser()
        self.extractor = extractor or DeterministicExtractor()

    async def prepare_and_create_run(
        self, request: PreparationRequest, scope: ReviewScopeContext
    ) -> PreparedReviewRunResult:
        self._validate_request(request, scope)
        try:
            async with self.object_store.import_guard():
                source = await self._read_source(request, scope)
                payload = await self._read_object(source)
                parsed = await self._parse(source, payload)
                candidates = self._extract(source, parsed)
                review_result = await self._create_review(source, candidates, scope)
        except asyncio.CancelledError:
            raise
        except PreparationFailure:
            raise
        except Exception:
            raise PreparationFailure(
                "SOURCE_READ_FAILED",
                "approved proposal preparation is temporarily unavailable",
                retryable=True,
            ) from None
        return PreparedReviewRunResult(source, parsed, candidates, review_result)

    async def _read_source(
        self, request: PreparationRequest, scope: ReviewScopeContext
    ) -> ApprovedProposalSource:
        try:
            source = await self.source_reader.read_approved_proposal(
                scope, request.project_id
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PreparationFailure(
                "SOURCE_READ_FAILED",
                "approved proposal source could not be read",
                retryable=True,
            ) from None
        if source is None:
            raise PreparationFailure(
                "PROPOSAL_NOT_AVAILABLE", "approved proposal is not available"
            )
        self._validate_source(source, request, scope)
        return source

    async def _read_object(self, source: ApprovedProposalSource) -> bytes:
        try:
            payload = await self.object_store.read_verified_immutable(
                kind="proposal",
                stored=StoredObject(
                    storage_key=source.storage_key,
                    size_bytes=source.size_bytes,
                    sha256=source.sha256,
                ),
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise PreparationFailure(
                "SOURCE_READ_FAILED",
                "approved proposal object is temporarily unavailable",
                retryable=True,
            ) from None
        except Exception:
            raise PreparationFailure(
                "SOURCE_INTEGRITY_FAILED",
                "approved proposal object failed integrity verification",
            ) from None
        if not isinstance(payload, bytes):
            raise PreparationFailure(
                "SOURCE_INTEGRITY_FAILED",
                "approved proposal object failed integrity verification",
            )
        return payload

    async def _parse(
        self, source: ApprovedProposalSource, payload: bytes
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
            raise PreparationFailure(
                (
                    "PARSER_LIMIT_EXCEEDED"
                    if error.code == "DOCUMENT_LIMIT_EXCEEDED"
                    else "PARSER_FAILED"
                ),
                "approved proposal could not be parsed",
            ) from None
        except Exception:
            raise PreparationFailure(
                "PARSER_FAILED", "approved proposal could not be parsed"
            ) from None
        if not isinstance(result, RuntimeParserResult):
            raise PreparationFailure(
                "PARSER_FAILED", "approved proposal parser returned invalid output"
            )
        if not isinstance(result.blocks, tuple) or not isinstance(
            result.warnings, tuple
        ):
            raise PreparationFailure(
                "PARSER_FAILED", "approved proposal parser returned invalid output"
            )
        if (
            len(result.blocks) > MAX_PARSER_BLOCKS
            or len(result.warnings) > MAX_PARSER_WARNINGS
        ):
            raise PreparationFailure(
                "PARSER_LIMIT_EXCEEDED",
                "approved proposal parser output exceeds review limits",
            )
        try:
            normalized_blocks = self._normalize_parser_blocks(result.blocks)
        except PreparationFailure:
            raise
        except (ExtractionContractError, TypeError, ValueError, AttributeError):
            raise PreparationFailure(
                "PARSER_FAILED", "approved proposal parser returned invalid output"
            ) from None
        result = RuntimeParserResult(
            result.document_format,
            result.size_bytes,
            result.source_sha256,
            normalized_blocks,
            result.warnings,
        )
        if (
            result.size_bytes != source.size_bytes
            or result.source_sha256 != source.sha256
        ):
            raise PreparationFailure(
                "SOURCE_INTEGRITY_FAILED",
                "approved proposal parser source identity changed",
            )
        if result.warnings:
            raise PreparationFailure(
                "PARSER_WARNING",
                "approved proposal contains unsupported or ambiguous content",
            )
        return result

    @classmethod
    def _normalize_parser_blocks(
        cls, raw_blocks: tuple[object, ...]
    ) -> tuple[ParserBlock, ...]:
        normalized: list[ParserBlock] = []
        table_cells = 0
        for raw in raw_blocks:
            if isinstance(raw, ParserBlock):
                block = raw
            elif isinstance(raw, Mapping):
                block = ParserBlock.from_mapping(raw)
            else:
                raise ExtractionContractError(
                    "INVALID_BLOCK", "parser block must be an object"
                )
            table_cells += cls._validate_parser_block(block)
            if table_cells > MAX_PARSER_TABLE_CELLS:
                raise PreparationFailure(
                    "PARSER_LIMIT_EXCEEDED",
                    "approved proposal parser output exceeds review limits",
                )
            normalized.append(block)
        return tuple(normalized)

    @staticmethod
    def _validate_parser_block(block: ParserBlock) -> int:
        if block.kind not in {"heading", "paragraph", "table"}:
            raise ExtractionContractError(
                "INVALID_BLOCK", "parser block kind is invalid"
            )
        if not isinstance(block.section_path, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in block.section_path
        ):
            raise ExtractionContractError(
                "INVALID_SECTION", "parser block section path is invalid"
            )
        if any(len(item) > MAX_BLOCK_TEXT_CHARS for item in block.section_path):
            raise PreparationFailure(
                "PARSER_LIMIT_EXCEEDED",
                "approved proposal parser output exceeds review limits",
            )
        if not isinstance(block.location, SourceLocation):
            raise ExtractionContractError(
                "INVALID_LOCATION", "parser block location is invalid"
            )
        SourceLocation.from_mapping(block.location.as_dict())
        if not isinstance(block.cells, tuple):
            raise ExtractionContractError(
                "INVALID_TABLE", "parser block cells are invalid"
            )

        if block.kind != "table":
            if (
                not isinstance(block.text, str)
                or not block.text.strip()
                or block.column_name is not None
                or block.cells
            ):
                raise ExtractionContractError(
                    "INVALID_BLOCK", "parser text block is invalid"
                )
        elif block.cells:
            if block.text is not None or block.column_name is not None:
                raise ExtractionContractError(
                    "INVALID_TABLE", "parser table block is invalid"
                )
            for cell in block.cells:
                if not isinstance(cell, TableCell):
                    raise ExtractionContractError(
                        "INVALID_TABLE", "parser table cell is invalid"
                    )
                TableCell.from_mapping(
                    {
                        "column": cell.column,
                        "value": cell.value,
                        "location": cell.location.as_dict(),
                    }
                )
            ParserBlock._validate_table_cells(block.location, block.cells)
        elif (
            not isinstance(block.text, str)
            or not block.text.strip()
            or not isinstance(block.column_name, str)
            or not block.column_name.strip()
        ):
            raise ExtractionContractError(
                "INVALID_TABLE", "parser table block is invalid"
            )

        bounded_text = (block.text, block.column_name, *(cell.column for cell in block.cells))
        if any(
            value is not None and len(value) > MAX_BLOCK_TEXT_CHARS
            for value in bounded_text
        ) or any(len(cell.value) > MAX_BLOCK_TEXT_CHARS for cell in block.cells):
            raise PreparationFailure(
                "PARSER_LIMIT_EXCEEDED",
                "approved proposal parser output exceeds review limits",
            )
        return len(block.cells)

    def _extract(
        self, source: ApprovedProposalSource, result: RuntimeParserResult
    ) -> tuple[Candidate, ...]:
        try:
            candidates = self.extractor.extract(
                proposal_version_id=source.version_id,
                proposal_sha256=source.sha256,
                blocks=result.blocks,
            )
        except ExtractionContractError:
            raise PreparationFailure(
                "EXTRACTION_FAILED", "approved proposal extraction failed"
            ) from None
        except Exception:
            raise PreparationFailure(
                "EXTRACTION_FAILED", "approved proposal extraction failed"
            ) from None
        if not isinstance(candidates, tuple) or not candidates:
            raise PreparationFailure(
                "NO_CANDIDATES", "approved proposal produced no review candidates"
            )
        if len(candidates) > MAX_REVIEW_CANDIDATES:
            raise PreparationFailure(
                "CANDIDATE_LIMIT_EXCEEDED",
                "approved proposal produced too many review candidates",
            )
        if any(not isinstance(candidate, Candidate) for candidate in candidates):
            raise PreparationFailure(
                "EXTRACTION_FAILED", "approved proposal extraction failed"
            )
        return candidates

    async def _create_review(
        self,
        source: ApprovedProposalSource,
        candidates: tuple[Candidate, ...],
        scope: ReviewScopeContext,
    ) -> CreateReviewRunResult:
        request = CreateReviewRunRequest(
            project_id=source.project_id,
            proposal_artifact_id=source.artifact_id,
            proposal_version_id=source.version_id,
            proposal_version=source.version,
            proposal_sha256=source.sha256,
            candidates=candidates,
        )
        try:
            result = await self.review_service.create_run(request, scope)
        except asyncio.CancelledError:
            raise
        except ReviewFailure as error:
            if error.code == "INVALID_PROPOSAL_STATE":
                raise PreparationFailure(
                    "PROPOSAL_CHANGED",
                    "approved proposal changed during preparation",
                ) from None
            raise PreparationFailure(
                "REVIEW_CREATE_FAILED",
                "review run could not be created",
                retryable=error.code == "REPOSITORY_FAILURE",
            ) from None
        except Exception:
            raise PreparationFailure(
                "REVIEW_CREATE_FAILED", "review run could not be created"
            ) from None
        if not isinstance(result, CreateReviewRunResult):
            raise PreparationFailure(
                "REVIEW_CREATE_FAILED", "review service returned invalid output"
            )
        return result

    @classmethod
    def _validate_request(
        cls, request: PreparationRequest, scope: ReviewScopeContext
    ) -> None:
        if not isinstance(scope, ReviewScopeContext):
            raise PreparationFailure(
                "AUTHORIZATION_REQUIRED", "server authorization scope is required"
            )
        for value in (
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            scope.actor_id,
        ):
            if not cls._canonical_uuid(value):
                raise PreparationFailure(
                    "AUTHORIZATION_REQUIRED", "server authorization scope is invalid"
                )
        if not isinstance(request, PreparationRequest) or not cls._canonical_uuid(
            request.project_id
        ):
            raise PreparationFailure("INVALID_INPUT", "project id is invalid")
        if (
            request.expected_proposal_version_id is not None
            and not cls._canonical_uuid(request.expected_proposal_version_id)
        ):
            raise PreparationFailure(
                "INVALID_INPUT", "expected proposal version id is invalid"
            )
        if (
            request.expected_proposal_sha256 is not None
            and not cls._canonical_hash(request.expected_proposal_sha256)
        ):
            raise PreparationFailure(
                "INVALID_INPUT", "expected proposal hash is invalid"
            )

    @classmethod
    def _validate_source(
        cls,
        source: ApprovedProposalSource,
        request: PreparationRequest,
        scope: ReviewScopeContext,
    ) -> None:
        if not isinstance(source, ApprovedProposalSource):
            raise PreparationFailure(
                "PROPOSAL_NOT_AVAILABLE", "approved proposal source is invalid"
            )
        identity_values = (
            source.organization_id,
            source.workspace_id,
            source.client_id,
            source.project_id,
            source.artifact_id,
            source.version_id,
        )
        if not all(cls._canonical_uuid(value) for value in identity_values):
            raise PreparationFailure(
                "PROPOSAL_NOT_AVAILABLE", "approved proposal source is invalid"
            )
        if (
            source.organization_id,
            source.workspace_id,
            source.client_id,
            source.project_id,
        ) != (
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            request.project_id,
        ):
            raise PreparationFailure(
                "PROPOSAL_NOT_AVAILABLE", "approved proposal is not available"
            )
        if (
            isinstance(source.version, bool)
            or not isinstance(source.version, int)
            or source.version < 1
            or isinstance(source.size_bytes, bool)
            or not isinstance(source.size_bytes, int)
            or not 1 <= source.size_bytes <= MAX_FILE_SIZE_BYTES
            or not cls._canonical_hash(source.sha256)
            or not isinstance(source.filename, str)
            or not source.filename.strip()
            or not isinstance(source.media_type, str)
            or not source.media_type.strip()
            or not isinstance(source.storage_key, str)
            or not source.storage_key
        ):
            raise PreparationFailure(
                "PROPOSAL_NOT_AVAILABLE", "approved proposal source is invalid"
            )
        if (
            request.expected_proposal_version_id is not None
            and request.expected_proposal_version_id != source.version_id
        ) or (
            request.expected_proposal_sha256 is not None
            and request.expected_proposal_sha256 != source.sha256
        ):
            raise PreparationFailure(
                "PROPOSAL_CHANGED", "approved proposal does not match the expected source"
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
    def _canonical_hash(value: object) -> bool:
        return isinstance(value, str) and _SHA256.fullmatch(value) is not None
