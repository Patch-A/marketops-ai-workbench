"""Dependency-free service contract for an atomic project import."""

from __future__ import annotations

import asyncio
import codecs
import csv
import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import AsyncContextManager, Callable, Protocol
from uuid import UUID
from xml.etree import ElementTree


SOURCE_EXTENSIONS = frozenset({"md", "markdown", "csv", "docx"})
PROPOSAL_EXTENSIONS = frozenset({"md", "markdown", "docx"})
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
HASH_CHUNK_SIZE = 1024 * 1024
MEDIA_TYPES = {
    "md": frozenset({"text/markdown", "text/plain"}),
    "markdown": frozenset({"text/markdown", "text/plain"}),
    "csv": frozenset({"text/csv", "application/csv", "text/plain"}),
    "docx": frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    ),
}


class ImportFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class FilePayload:
    filename: str
    media_type: str
    source_path: Path


@dataclass(frozen=True)
class ImportRequest:
    idempotency_key: str
    project_name: str
    proposal_version: int
    approval_confirmed: bool
    source: FilePayload
    proposal: FilePayload


@dataclass(frozen=True)
class ScopeContext:
    organization_id: str
    workspace_id: str
    client_id: str
    actor_id: str


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ProjectImportRecord:
    project_id: str
    organization_id: str
    workspace_id: str
    client_id: str
    idempotency_key: str
    manifest_sha256: str
    project_name: str
    status: str
    source_artifact_id: str
    source_version_id: str
    source: StoredObject
    source_filename: str
    source_media_type: str
    proposal_artifact_id: str
    proposal_version_id: str
    proposal: StoredObject
    proposal_filename: str
    proposal_media_type: str
    approved_proposal_version: int
    approval_status: str
    approved_by: str
    approved_at: datetime
    created_by: str


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    project_id: str
    actor_id: str
    action: str
    manifest_sha256: str
    source_version_id: str
    proposal_version_id: str
    approved_by: str
    approved_at: datetime


@dataclass(frozen=True)
class ImportResult:
    project_id: str
    source_artifact_id: str
    source_version_id: str
    proposal_artifact_id: str
    proposal_version_id: str
    manifest_sha256: str
    replayed: bool


@dataclass(frozen=True)
class ImportClaim:
    """Database-arbitrated result for one scoped idempotency key."""

    record: ProjectImportRecord
    created: bool


@dataclass(frozen=True)
class _ImportIdentifiers:
    project_id: str
    source_artifact_id: str
    source_version_id: str
    proposal_artifact_id: str
    proposal_version_id: str
    audit_event_id: str


class ObjectStore(Protocol):
    async def put_immutable(
        self,
        *,
        kind: str,
        storage_key: str,
        source_path: Path,
        size_bytes: int,
        sha256: str,
    ) -> StoredObject: ...


class ImportTransaction(Protocol):
    async def try_create_project_import(
        self, record: ProjectImportRecord
    ) -> ImportClaim:
        """Insert atomically, or return the record that won the unique-key race."""
        ...

    async def append_audit_event(self, event: AuditEvent) -> None: ...


class ImportRepository(Protocol):
    def transaction(
        self, scope: ScopeContext
    ) -> AsyncContextManager[ImportTransaction]: ...


class ProjectImportService:
    def __init__(
        self,
        *,
        object_store: ObjectStore,
        repository: ImportRepository,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime],
    ):
        self.object_store = object_store
        self.repository = repository
        self.id_factory = id_factory
        self.clock = clock

    async def import_project(
        self, request: ImportRequest, scope: ScopeContext
    ) -> ImportResult:
        self._validate(request, scope)
        source_size, source_hash = self._inspect_file(
            request.source, SOURCE_EXTENSIONS, "source"
        )
        proposal_size, proposal_hash = self._inspect_file(
            request.proposal, PROPOSAL_EXTENSIONS, "proposal"
        )
        manifest_hash = self._manifest_hash(
            request, source_size, source_hash, proposal_size, proposal_hash
        )
        idempotency_key = request.idempotency_key.strip()
        idempotency_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        approved_at = self._capture_approved_at()
        identifiers = self._reserve_identifiers()

        source = await self._persist(
            "source",
            request.source,
            source_size,
            source_hash,
            scope.workspace_id,
            scope.client_id,
            idempotency_hash,
            manifest_hash,
        )
        proposal = await self._persist(
            "proposal",
            request.proposal,
            proposal_size,
            proposal_hash,
            scope.workspace_id,
            scope.client_id,
            idempotency_hash,
            manifest_hash,
        )

        try:
            async with self.repository.transaction(scope) as transaction:
                record = self._record(
                    request,
                    scope,
                    idempotency_key,
                    manifest_hash,
                    source,
                    proposal,
                    approved_at,
                    identifiers,
                )
                claim = await transaction.try_create_project_import(record)
                if (
                    not isinstance(claim, ImportClaim)
                    or type(claim.created) is not bool
                    or not isinstance(claim.record, ProjectImportRecord)
                    or (claim.created and claim.record != record)
                ):
                    raise RuntimeError(
                        "repository returned an invalid atomic import claim"
                    )
                claimed_record = claim.record
                if (
                    claimed_record.organization_id != scope.organization_id
                    or claimed_record.workspace_id != scope.workspace_id
                    or claimed_record.client_id != scope.client_id
                    or claimed_record.idempotency_key != idempotency_key
                ):
                    raise ImportFailure(
                        "IDEMPOTENCY_CONFLICT",
                        "the idempotency key is not available in this authorization scope",
                    )
                if claimed_record.manifest_sha256 != manifest_hash:
                    raise ImportFailure(
                        "IDEMPOTENCY_CONFLICT",
                        "the idempotency key already belongs to a different import manifest",
                    )
                if not claim.created:
                    return self._result(claimed_record, replayed=True)

                await transaction.append_audit_event(
                    AuditEvent(
                        event_id=identifiers.audit_event_id,
                        project_id=record.project_id,
                        actor_id=scope.actor_id,
                        action="project.imported_with_approved_proposal",
                        manifest_sha256=manifest_hash,
                        source_version_id=record.source_version_id,
                        proposal_version_id=record.proposal_version_id,
                        approved_by=scope.actor_id,
                        approved_at=approved_at,
                    )
                )
                return self._result(claimed_record, replayed=False)
        except asyncio.CancelledError:
            raise
        except ImportFailure:
            raise
        except Exception as error:
            raise ImportFailure(
                "DATABASE_WRITE_FAILED",
                "the project transaction failed after immutable objects were stored",
                retryable=True,
            ) from error

    def _record(
        self,
        request: ImportRequest,
        scope: ScopeContext,
        idempotency_key: str,
        manifest_hash: str,
        source: StoredObject,
        proposal: StoredObject,
        approved_at: datetime,
        identifiers: _ImportIdentifiers,
    ) -> ProjectImportRecord:
        return ProjectImportRecord(
            project_id=identifiers.project_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            client_id=scope.client_id,
            idempotency_key=idempotency_key,
            manifest_sha256=manifest_hash,
            project_name=request.project_name.strip(),
            status="planning",
            source_artifact_id=identifiers.source_artifact_id,
            source_version_id=identifiers.source_version_id,
            source=source,
            source_filename=request.source.filename.strip(),
            source_media_type=request.source.media_type.strip(),
            proposal_artifact_id=identifiers.proposal_artifact_id,
            proposal_version_id=identifiers.proposal_version_id,
            proposal=proposal,
            proposal_filename=request.proposal.filename.strip(),
            proposal_media_type=request.proposal.media_type.strip(),
            approved_proposal_version=request.proposal_version,
            approval_status="approved",
            approved_by=scope.actor_id,
            approved_at=approved_at,
            created_by=scope.actor_id,
        )

    async def _persist(
        self,
        kind: str,
        payload: FilePayload,
        size_bytes: int,
        sha256: str,
        workspace_id: str,
        client_id: str,
        idempotency_hash: str,
        manifest_hash: str,
    ) -> StoredObject:
        storage_key = (
            f"workspaces/{workspace_id}/clients/{client_id}/imports/"
            f"{idempotency_hash}/{manifest_hash}/{kind}/{sha256}"
        )
        try:
            stored = await self.object_store.put_immutable(
                kind=kind,
                storage_key=storage_key,
                source_path=payload.source_path,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise ImportFailure(
                "OBJECT_WRITE_FAILED", f"failed to retain the {kind} object", retryable=True
            ) from error
        if (
            stored.storage_key != storage_key
            or stored.size_bytes != size_bytes
            or stored.sha256 != sha256
        ):
            raise ImportFailure(
                "OBJECT_INTEGRITY_MISMATCH",
                f"retained {kind} object does not match the computed content hash",
            )
        return stored

    @staticmethod
    def _validate(request: ImportRequest, scope: ScopeContext) -> None:
        try:
            scope_ids = (
                UUID(scope.organization_id),
                UUID(scope.workspace_id),
                UUID(scope.client_id),
                UUID(scope.actor_id),
            )
        except (AttributeError, TypeError, ValueError):
            raise ImportFailure("AUTHORIZATION_REQUIRED", "server scope is incomplete")
        if any(identifier.version is None for identifier in scope_ids):
            raise ImportFailure("AUTHORIZATION_REQUIRED", "server scope is incomplete")
        if (
            not isinstance(request.idempotency_key, str)
            or not 8 <= len(request.idempotency_key.strip()) <= 200
            or not isinstance(request.project_name, str)
            or not 1 <= len(request.project_name.strip()) <= 300
        ):
            raise ImportFailure("INVALID_INPUT", "idempotency key and project name are required")
        if (
            isinstance(request.proposal_version, bool)
            or not isinstance(request.proposal_version, int)
            or request.proposal_version < 1
        ):
            raise ImportFailure("INVALID_INPUT", "proposal version must be a positive integer")
        if request.approval_confirmed is not True:
            raise ImportFailure(
                "APPROVAL_REQUIRED", "the authenticated actor must confirm approval"
            )

    @classmethod
    def _inspect_file(
        cls, payload: FilePayload, extensions: frozenset[str], label: str
    ) -> tuple[int, str]:
        filename = payload.filename.strip()
        extension = PurePath(filename).suffix.lower().lstrip(".")
        if not filename or extension not in extensions:
            raise ImportFailure("UNSUPPORTED_FORMAT", f"unsupported {label} file format")
        media_type = payload.media_type.strip().lower() if isinstance(payload.media_type, str) else ""
        if media_type not in MEDIA_TYPES[extension]:
            raise ImportFailure("INVALID_MEDIA_TYPE", f"unsupported {label} media type")
        path = payload.source_path
        if not isinstance(path, Path) or not path.is_file():
            raise ImportFailure("INVALID_INPUT", f"{label} file content is required")
        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise ImportFailure("INVALID_INPUT", f"{label} file content is required")
        if size_bytes > MAX_FILE_SIZE_BYTES:
            raise ImportFailure(
                "PAYLOAD_TOO_LARGE",
                f"{label} file exceeds the {MAX_FILE_SIZE_BYTES}-byte limit",
            )
        cls._validate_structure(path, extension, label)
        return size_bytes, cls._sha256(path)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_structure(path: Path, extension: str, label: str) -> None:
        try:
            if extension in {"md", "markdown"}:
                decoder = codecs.getincrementaldecoder("utf-8-sig")()
                has_text = False
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(HASH_CHUNK_SIZE), b""):
                        text = decoder.decode(chunk)
                        has_text = has_text or bool(text.strip())
                    final_text = decoder.decode(b"", final=True)
                    has_text = has_text or bool(final_text.strip())
                if not has_text:
                    raise ValueError("empty Markdown")
            elif extension == "csv":
                with path.open("r", encoding="utf-8-sig", newline="") as source:
                    rows = csv.reader(source)
                    header = next(rows, None)
                    for _ in rows:
                        pass
                if not header or not any(cell.strip() for cell in header):
                    raise ValueError("CSV header missing")
            elif extension == "docx":
                with zipfile.ZipFile(path) as package:
                    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
                    if not required.issubset(package.namelist()):
                        raise ValueError("DOCX package parts missing")
                    if any(item.flag_bits & 0x1 for item in package.infolist()):
                        raise ValueError("encrypted DOCX is unsupported")
                    if sum(item.file_size for item in package.infolist()) > MAX_FILE_SIZE_BYTES * 4:
                        raise ValueError("expanded DOCX exceeds safety limit")
                    root = ElementTree.parse(package.open("word/document.xml")).getroot()
                    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                    body = root.find(f"{namespace}body")
                    if body is None or not (
                        any((node.text or "").strip() for node in body.iter(f"{namespace}t"))
                        or body.find(f"{namespace}tbl") is not None
                    ):
                        raise ValueError("DOCX body has no supported content")
        except (UnicodeDecodeError, csv.Error, zipfile.BadZipFile, ElementTree.ParseError, ValueError) as error:
            raise ImportFailure(
                "INVALID_DOCUMENT", f"{label} file failed structural validation"
            ) from error

    @staticmethod
    def _manifest_hash(
        request: ImportRequest,
        source_size: int,
        source_hash: str,
        proposal_size: int,
        proposal_hash: str,
    ) -> str:
        manifest = {
            "projectName": request.project_name.strip(),
            "proposalVersion": request.proposal_version,
            "approvalConfirmed": request.approval_confirmed,
            "source": {
                "filename": request.source.filename.strip(),
                "mediaType": request.source.media_type.strip(),
                "sizeBytes": source_size,
                "sha256": source_hash,
            },
            "proposal": {
                "filename": request.proposal.filename.strip(),
                "mediaType": request.proposal.media_type.strip(),
                "sizeBytes": proposal_size,
                "sha256": proposal_hash,
            },
        }
        serialized = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _result(record: ProjectImportRecord, *, replayed: bool) -> ImportResult:
        return ImportResult(
            project_id=record.project_id,
            source_artifact_id=record.source_artifact_id,
            source_version_id=record.source_version_id,
            proposal_artifact_id=record.proposal_artifact_id,
            proposal_version_id=record.proposal_version_id,
            manifest_sha256=record.manifest_sha256,
            replayed=replayed,
        )

    def _capture_approved_at(self) -> datetime:
        try:
            approved_at = self.clock()
            is_aware = (
                isinstance(approved_at, datetime)
                and approved_at.tzinfo is not None
                and approved_at.utcoffset() is not None
            )
        except Exception as error:
            raise ImportFailure(
                "INVALID_SERVER_CLOCK", "approval time must be timezone-aware"
            ) from error
        if not is_aware:
            raise ImportFailure(
                "INVALID_SERVER_CLOCK", "approval time must be timezone-aware"
            )
        return approved_at

    def _reserve_identifiers(self) -> _ImportIdentifiers:
        values = tuple(self._next_uuid() for _ in range(6))
        if len(set(values)) != len(values):
            raise ImportFailure(
                "INVALID_SERVER_ID", "the server ID factory must return unique UUID values"
            )
        return _ImportIdentifiers(*values)

    def _next_uuid(self) -> str:
        try:
            value = UUID(str(self.id_factory()))
        except Exception as error:
            raise ImportFailure(
                "INVALID_SERVER_ID", "the server ID factory must return UUID values"
            ) from error
        if value.version is None:
            raise ImportFailure(
                "INVALID_SERVER_ID", "the server ID factory must return UUID values"
            )
        return str(value)
