"""Thin FastAPI adapter for the project import service."""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hmac
import inspect
import json
import re
import tempfile
from contextlib import AbstractAsyncContextManager
from datetime import date
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from .service import (
    FilePayload,
    ImportFailure,
    ImportRequest,
    MAX_FILE_SIZE_BYTES,
    ProjectImportService,
    ScopeContext,
)
from ..marketops_review import (
    PreparationFailure,
    PreparationRequest,
    ReviewFailure,
    ReviewRequest,
    ReviewScopeContext,
)
from ..marketops_schedule import (
    CreatePlanRequest,
    ScheduleScopeContext,
    ScheduleServiceFailure,
)
from ..marketops_execution import (
    ExecutionFailure,
    ExecutionScopeContext,
    ExecutionUpdateRequest,
)


Authenticator = Callable[
    [str], ScopeContext | None | Awaitable[ScopeContext | None]
]

_EXPECTED_FIELDS = frozenset(
    {"projectName", "proposalVersion", "approvalConfirmed", "sourceFile", "proposalFile"}
)
_FILE_FIELDS = frozenset({"sourceFile", "proposalFile"})
_MAX_TEXT_FIELD_SIZE_BYTES = 4096
_MAX_MULTIPART_REQUEST_SIZE_BYTES = 2 * MAX_FILE_SIZE_BYTES + 64 * 1024
_MAX_REVIEW_JSON_BYTES = 16 * 1024
_MAX_SCHEDULE_JSON_BYTES = 128 * 1024
_CREATE_REVIEW_FIELDS = frozenset(
    {"expectedProposalVersionId", "expectedProposalSha256"}
)
_DECISION_REQUIRED_FIELDS = frozenset(
    {"expectedReviewVersion", "candidateId", "action", "reason"}
)
_DECISION_OPTIONAL_FIELDS = frozenset({"comment", "replacementText"})
_CREATE_PLAN_FIELDS = frozenset({"reviewRunId", "reviewVersion"})
_REVISE_PLAN_FIELDS = frozenset({"expectedPlanVersion", "taskUpdates"})
_CREATE_SCHEDULE_FIELDS = frozenset(
    {"expectedPlanVersion", "projectStart", "holidays"}
)
_APPROVE_PLAN_FIELDS = frozenset(
    {"expectedPlanVersion", "scheduleSnapshotId", "reason"}
)
_EXECUTION_REQUIRED_FIELDS = frozenset(
    {"expectedPlanVersion", "taskId", "expectedExecutionSequence", "status"}
)
_EXECUTION_OPTIONAL_FIELDS = frozenset(
    {"blockerReason", "actualStart", "actualFinish", "note"}
)
_TASK_UPDATE_FIELDS = frozenset({"taskId", "changes"})
_EDITABLE_TASK_FIELDS = frozenset(
    {
        "title",
        "durationWorkdays",
        "predecessors",
        "ownerRole",
        "plannedStart",
        "plannedFinish",
        "hardDeadline",
        "approvedBufferWorkdays",
        "isLocked",
        "status",
    }
)
_OPENAPI_CONTRACT = (
    Path(__file__).resolve().parents[1] / "openapi" / "project-import.openapi.yaml"
)
_STATUS_BY_CODE = {
    "INVALID_INPUT": 400,
    "AUTHORIZATION_REQUIRED": 403,
    "IDEMPOTENCY_CONFLICT": 409,
    "PAYLOAD_TOO_LARGE": 413,
    "UNSUPPORTED_FORMAT": 415,
    "INVALID_MEDIA_TYPE": 415,
    "INVALID_DOCUMENT": 422,
    "APPROVAL_REQUIRED": 422,
    "OBJECT_WRITE_FAILED": 500,
    "OBJECT_INTEGRITY_MISMATCH": 500,
    "INVALID_SERVER_ID": 500,
    "INVALID_SERVER_CLOCK": 500,
    "DATABASE_WRITE_FAILED": 500,
    "PROPOSAL_NOT_AVAILABLE": 422,
    "PROPOSAL_CHANGED": 409,
    "SOURCE_READ_FAILED": 503,
    "SOURCE_INTEGRITY_FAILED": 422,
    "PARSER_FAILED": 422,
    "PARSER_LIMIT_EXCEEDED": 422,
    "PARSER_WARNING": 422,
    "EXTRACTION_FAILED": 422,
    "NO_CANDIDATES": 422,
    "CANDIDATE_LIMIT_EXCEEDED": 422,
    "REVIEW_CREATE_FAILED": 500,
    "REPOSITORY_FAILURE": 503,
    "REVIEW_NOT_FOUND": 404,
    "CANDIDATE_NOT_FOUND": 404,
    "REVIEW_CONFLICT": 409,
    "REVIEW_INCOMPLETE": 422,
    "PLAN_NOT_FOUND": 404,
    "PLAN_CONFLICT": 409,
    "PLAN_ALREADY_APPROVED": 409,
    "SCHEDULE_NOT_FOUND": 404,
    "SCHEDULE_CONFLICT": 409,
    "SCHEDULE_NOT_READY": 422,
    "APPROVED_PROPOSAL_NOT_FOUND": 422,
    "SOURCE_CONFLICT": 409,
    "WBS_VALIDATION_FAILED": 422,
    "EXECUTION_CONFLICT": 409,
    "PLAN_NOT_APPROVED": 422,
    "TASK_NOT_FOUND": 404,
    "EXECUTION_WRITE_FAILED": 503,
    "EXECUTION_READ_FAILED": 503,
    "INTERNAL_FAILURE": 500,
}
_PUBLIC_MESSAGES = {
    "INVALID_INPUT": "The import request is malformed or incomplete.",
    "AUTHORIZATION_REQUIRED": "A valid server authorization scope is required.",
    "PAYLOAD_TOO_LARGE": "The request payload exceeds its allowed size.",
    "UNSUPPORTED_FORMAT": "An uploaded file format is unsupported.",
    "INVALID_MEDIA_TYPE": "An uploaded file media type is unsupported.",
    "INVALID_DOCUMENT": "An uploaded document is invalid.",
    "APPROVAL_REQUIRED": "Explicit proposal approval is required.",
    "OBJECT_WRITE_FAILED": "The import could not retain its immutable files.",
    "OBJECT_INTEGRITY_MISMATCH": "Retained file integrity verification failed.",
    "IDEMPOTENCY_CONFLICT": "The idempotency key belongs to a different import.",
    "INVALID_SERVER_ID": "The server could not allocate import identifiers.",
    "INVALID_SERVER_CLOCK": "The server clock is invalid.",
    "DATABASE_WRITE_FAILED": "The project transaction failed.",
    "PROJECT_NOT_FOUND": "The requested project was not found.",
    "PROPOSAL_NOT_AVAILABLE": "The approved proposal is not available.",
    "PROPOSAL_CHANGED": "The approved proposal differs from the expected source.",
    "SOURCE_READ_FAILED": "The approved proposal is temporarily unavailable.",
    "SOURCE_INTEGRITY_FAILED": "The approved proposal failed integrity verification.",
    "PARSER_FAILED": "The approved proposal could not be parsed.",
    "PARSER_LIMIT_EXCEEDED": "The approved proposal exceeds parser limits.",
    "PARSER_WARNING": "The approved proposal contains unsupported content.",
    "EXTRACTION_FAILED": "Review candidates could not be extracted.",
    "NO_CANDIDATES": "The approved proposal produced no review candidates.",
    "CANDIDATE_LIMIT_EXCEEDED": "The approved proposal produced too many candidates.",
    "REVIEW_CREATE_FAILED": "The review run could not be created.",
    "REPOSITORY_FAILURE": "The requested server operation is temporarily unavailable.",
    "REVIEW_NOT_FOUND": "The requested review run was not found.",
    "CANDIDATE_NOT_FOUND": "The requested candidate was not found.",
    "REVIEW_CONFLICT": "The review version is stale and must be refreshed.",
    "REVIEW_INCOMPLETE": "Every extracted candidate needs a human review decision.",
    "PLAN_NOT_FOUND": "The requested WBS plan was not found.",
    "PLAN_CONFLICT": "The WBS plan changed and must be refreshed.",
    "PLAN_ALREADY_APPROVED": "The WBS version already has a different approval.",
    "SCHEDULE_NOT_FOUND": "The requested schedule snapshot was not found.",
    "SCHEDULE_CONFLICT": "The schedule snapshot does not match the current WBS version.",
    "SCHEDULE_NOT_READY": "Schedule risks must be resolved before approval.",
    "APPROVED_PROPOSAL_NOT_FOUND": "The approved proposal is not available.",
    "SOURCE_CONFLICT": "The review source no longer matches the approved proposal.",
    "WBS_VALIDATION_FAILED": "The WBS or schedule request is invalid.",
    "EXECUTION_CONFLICT": "The task execution state changed and must be refreshed.",
    "PLAN_NOT_APPROVED": "The selected WBS version is not approved for execution.",
    "TASK_NOT_FOUND": "The requested WBS task was not found.",
    "EXECUTION_WRITE_FAILED": "The execution update could not be persisted.",
    "EXECUTION_READ_FAILED": "The execution state is temporarily unavailable.",
    "INTERNAL_FAILURE": "The server could not allocate schedule state.",
}

_STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/project-import.js": "project-import.js",
    "/review-workbench.js": "review-workbench.js",
    "/schedule-workbench.js": "schedule-workbench.js",
    "/execution-workbench.js": "execution-workbench.js",
    "/styles.css": "styles.css",
}


class StaticBearerAuthenticator:
    """Injectable single-deployment authentication for the M1-01 runtime slice."""

    def __init__(
        self,
        token: str,
        scope: ScopeContext,
        *,
        basic_username: str | None = None,
    ):
        self._token = token
        self._scope = scope
        self._basic_username = basic_username

    async def __call__(self, token: str) -> ScopeContext | None:
        if not self._token or not hmac.compare_digest(token, self._token):
            return None
        return self._scope

    async def authenticate_basic(
        self, username: str, password: str
    ) -> ScopeContext | None:
        if (
            not self._basic_username
            or not hmac.compare_digest(username, self._basic_username)
            or not self._token
            or not hmac.compare_digest(password, self._token)
        ):
            return None
        return self._scope


class _FileSizeLimitedMultipartParser(MultiPartParser):
    """Reject oversized file parts before Starlette writes them to its spool."""

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_size = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self._current_file_size += end - start
            if self._current_file_size > MAX_FILE_SIZE_BYTES:
                raise _PayloadTooLarge
        super().on_part_data(data, start, end)


def create_app(
    *,
    import_service: ProjectImportService | None = None,
    authenticator: Authenticator | None = None,
    project_reader: Any | None = None,
    review_preparation: Any | None = None,
    review_service: Any | None = None,
    schedule_service: Any | None = None,
    execution_service: Any | None = None,
    static_root: Path | None = None,
    request_id_factory: Callable[[], Any] = uuid4,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
) -> FastAPI:
    app = FastAPI(title="MarketOps Project Import API", lifespan=lifespan)
    reviewed_openapi = _load_openapi_contract()
    app.openapi_schema = copy.deepcopy(reviewed_openapi)

    def runtime_openapi() -> dict[str, Any]:
        return copy.deepcopy(reviewed_openapi)

    app.openapi = runtime_openapi  # type: ignore[method-assign]
    if import_service is not None:
        app.state.import_service = import_service
    if authenticator is not None:
        app.state.authenticator = authenticator
    if project_reader is not None:
        app.state.project_reader = project_reader
    if review_preparation is not None:
        app.state.review_preparation = review_preparation
    if review_service is not None:
        app.state.review_service = review_service
    if schedule_service is not None:
        app.state.schedule_service = schedule_service
    if execution_service is not None:
        app.state.execution_service = execution_service

    @app.post("/v1/project-imports", status_code=201)
    async def import_project(request: Request) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error

        form: FormData | None = None
        temp_paths: list[Path] = []
        try:
            idempotency_key = _single_header(request, "idempotency-key")
            content_type = _single_header(request, "content-type")
            if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
                return _failure("INVALID_INPUT", request_id, status=400)
            try:
                parser = _FileSizeLimitedMultipartParser(
                    request.headers,
                    _limited_request_stream(request),
                    max_files=len(_FILE_FIELDS),
                    max_fields=len(_EXPECTED_FIELDS - _FILE_FIELDS),
                    max_part_size=_MAX_TEXT_FIELD_SIZE_BYTES,
                )
                form = await parser.parse()
            except _PayloadTooLarge:
                raise
            except (MultiPartException, ValueError):
                return _failure("INVALID_INPUT", request_id, status=400)

            parsed = _parse_form(form)
            source_path = await _stream_upload(parsed["sourceFile"])
            temp_paths.append(source_path)
            proposal_path = await _stream_upload(parsed["proposalFile"])
            temp_paths.append(proposal_path)
            service = getattr(request.app.state, "import_service", None)
            if service is None:
                return _failure("DATABASE_WRITE_FAILED", request_id, status=500)
            result = await service.import_project(
                ImportRequest(
                    idempotency_key=idempotency_key,
                    project_name=parsed["projectName"],
                    proposal_version=_positive_integer(parsed["proposalVersion"]),
                    approval_confirmed=_confirmed(parsed["approvalConfirmed"]),
                    source=FilePayload(
                        filename=parsed["sourceFile"].filename or "",
                        media_type=parsed["sourceFile"].content_type or "",
                        source_path=source_path,
                    ),
                    proposal=FilePayload(
                        filename=parsed["proposalFile"].filename or "",
                        media_type=parsed["proposalFile"].content_type or "",
                        source_path=proposal_path,
                    ),
                ),
                scope_or_error,
            )
            response = JSONResponse(
                status_code=201,
                content={
                    "projectId": result.project_id,
                    "sourceArtifactId": result.source_artifact_id,
                    "sourceVersionId": result.source_version_id,
                    "proposalArtifactId": result.proposal_artifact_id,
                    "proposalVersionId": result.proposal_version_id,
                    "manifestSha256": result.manifest_sha256,
                    "replayed": result.replayed,
                },
            )
            response.headers["Location"] = f"/v1/projects/{result.project_id}"
            response.headers["Cache-Control"] = "no-store"
            return response
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ImportFailure as error:
            return _failure(
                error.code,
                request_id,
                status=_STATUS_BY_CODE.get(error.code, 500),
                retryable=error.retryable,
            )
        except Exception:
            return _failure("DATABASE_WRITE_FAILED", request_id, status=500)
        finally:
            for path in temp_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if form is not None:
                closed = form.close()
                if inspect.isawaitable(closed):
                    await closed

    @app.get("/v1/projects")
    async def list_projects(request: Request) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        try:
            limit = _project_list_limit(request)
            reader = getattr(request.app.state, "project_reader", None)
            if reader is None:
                return _failure("DATABASE_WRITE_FAILED", request_id, status=500)
            projects = await reader.list_projects(scope_or_error, limit=limit)
            return _json_no_store(
                {
                    "projects": [
                        {
                            "projectId": project.project_id,
                            "name": project.name,
                            "status": project.status,
                            "approvedProposalVersion": project.approved_proposal_version,
                            "createdAt": project.created_at.isoformat(),
                        }
                        for project in projects
                    ]
                }
            )
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except Exception:
            return _failure(
                "DATABASE_WRITE_FAILED", request_id, status=500, retryable=True
            )

    @app.get("/v1/projects/{project_id}")
    async def get_project(project_id: str, request: Request) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_id = _canonical_project_id(project_id)
        if canonical_id is None:
            return _failure("PROJECT_NOT_FOUND", request_id, status=404)
        try:
            reader = getattr(request.app.state, "project_reader", None)
            if reader is None:
                return _failure("DATABASE_WRITE_FAILED", request_id, status=500)
            project = await reader.get_project(scope_or_error, canonical_id)
            if project is None:
                return _failure("PROJECT_NOT_FOUND", request_id, status=404)
            return _json_no_store(
                {
                    "projectId": project.project_id,
                    "name": project.name,
                    "status": project.status,
                    "createdAt": project.created_at.isoformat(),
                    "sourceFile": {
                        "artifactId": project.source_file.artifact_id,
                        "versionId": project.source_file.version_id,
                        "filename": project.source_file.filename,
                        "mediaType": project.source_file.media_type,
                        "sizeBytes": project.source_file.size_bytes,
                    },
                    "approvedProposal": {
                        "artifactId": project.approved_proposal.artifact_id,
                        "versionId": project.approved_proposal.version_id,
                        "filename": project.approved_proposal.filename,
                        "mediaType": project.approved_proposal.media_type,
                        "sizeBytes": project.approved_proposal.size_bytes,
                        "sha256": project.approved_proposal.sha256,
                        "proposalVersion": project.approved_proposal.proposal_version,
                        "approvalStatus": project.approved_proposal.approval_status,
                        "approvedAt": project.approved_proposal.approved_at.isoformat(),
                    },
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return _failure(
                "DATABASE_WRITE_FAILED", request_id, status=500, retryable=True
            )

    @app.post("/v1/projects/{project_id}/extraction-runs", status_code=201)
    async def create_extraction_run(
        project_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_id = _canonical_review_uuid(project_id)
        if canonical_id is None:
            return _failure("REVIEW_NOT_FOUND", request_id, status=404)
        try:
            idempotency_key = _single_header(request, "idempotency-key").strip()
            if not 8 <= len(idempotency_key) <= 200:
                raise _MalformedRequest
            body = await _read_strict_json(request)
            _require_exact_fields(body, _CREATE_REVIEW_FIELDS)
            version_id = _canonical_review_uuid(
                body.get("expectedProposalVersionId")
            )
            proposal_hash = body.get("expectedProposalSha256")
            if (
                version_id is None
                or not isinstance(proposal_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", proposal_hash) is None
            ):
                raise _MalformedRequest
            review_service = getattr(request.app.state, "review_service", None)
            if review_service is None:
                return _failure(
                    "REPOSITORY_FAILURE", request_id, status=503, retryable=True
                )
            review_scope = _review_scope(scope_or_error)
            replay = await review_service.replay_run_request(
                canonical_id,
                idempotency_key,
                version_id,
                proposal_hash,
                review_scope,
            )
            if replay is not None:
                response = _json_no_store(
                    _create_review_result_json(replay), status_code=201
                )
                response.headers["Location"] = (
                    f"/v1/projects/{canonical_id}/extraction-runs/"
                    f"{replay.run.run_id}"
                )
                return response
            service = getattr(request.app.state, "review_preparation", None)
            if service is None:
                return _failure("REPOSITORY_FAILURE", request_id, status=503, retryable=True)
            result = await service.prepare_and_create_run(
                PreparationRequest(
                    project_id=canonical_id,
                    expected_proposal_version_id=version_id,
                    expected_proposal_sha256=proposal_hash,
                    idempotency_key=idempotency_key,
                ),
                _review_scope(scope_or_error),
            )
            response = _json_no_store(
                {
                    **_create_review_result_json(result.review_result),
                },
                status_code=201,
            )
            response.headers["Location"] = (
                f"/v1/projects/{canonical_id}/extraction-runs/"
                f"{result.review_result.run.run_id}"
            )
            return response
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except PreparationFailure as error:
            return _failure(
                error.code,
                request_id,
                status=_STATUS_BY_CODE.get(error.code, 500),
                retryable=error.retryable,
            )
        except ReviewFailure as error:
            return _review_failure(error, request_id)
        except Exception:
            return _failure("REPOSITORY_FAILURE", request_id, status=503, retryable=True)

    @app.get("/v1/projects/{project_id}/extraction-runs")
    async def list_extraction_runs(
        project_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_id = _canonical_review_uuid(project_id)
        if canonical_id is None:
            return _failure("REVIEW_NOT_FOUND", request_id, status=404)
        try:
            limit = _project_list_limit(request)
            service = getattr(request.app.state, "review_service", None)
            if service is None:
                return _failure("REPOSITORY_FAILURE", request_id, status=503, retryable=True)
            summaries = await service.list_runs(
                canonical_id, _review_scope(scope_or_error), limit=limit
            )
            return _json_no_store(
                {"runs": [_review_summary_json(item) for item in summaries]}
            )
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ReviewFailure as error:
            return _review_failure(error, request_id)
        except Exception:
            return _failure("REPOSITORY_FAILURE", request_id, status=503, retryable=True)

    @app.get("/v1/projects/{project_id}/extraction-runs/{run_id}")
    async def get_extraction_run(
        project_id: str, run_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_run = _canonical_review_uuid(run_id)
        if canonical_project is None or canonical_run is None:
            return _failure("REVIEW_NOT_FOUND", request_id, status=404)
        try:
            review_version = _review_version_query(request)
            service = getattr(request.app.state, "review_service", None)
            if service is None:
                return _failure("REPOSITORY_FAILURE", request_id, status=503, retryable=True)
            result = await service.read_review(
                canonical_project,
                canonical_run,
                _review_scope(scope_or_error),
                review_version=review_version,
            )
            return _json_no_store(_review_model_json(result))
        except asyncio.CancelledError:
            raise
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ReviewFailure as error:
            return _review_failure(error, request_id)
        except Exception:
            return _failure("REPOSITORY_FAILURE", request_id, status=503, retryable=True)

    @app.post(
        "/v1/projects/{project_id}/extraction-runs/{run_id}/decisions",
        status_code=201,
    )
    async def decide_review_candidate(
        project_id: str, run_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_run = _canonical_review_uuid(run_id)
        if canonical_project is None or canonical_run is None:
            return _failure("REVIEW_NOT_FOUND", request_id, status=404)
        try:
            body = await _read_strict_json(request)
            _require_decision_fields(body)
            candidate_id = _canonical_review_uuid(body.get("candidateId"))
            if candidate_id is None:
                raise _MalformedRequest
            service = getattr(request.app.state, "review_service", None)
            if service is None:
                return _failure("REPOSITORY_FAILURE", request_id, status=503, retryable=True)
            result = await service.review_candidate(
                canonical_project,
                canonical_run,
                ReviewRequest(
                    expected_review_version=body["expectedReviewVersion"],
                    candidate_id=candidate_id,
                    action=body["action"],
                    reason=body["reason"],
                    comment=body.get("comment"),
                    replacement_text=body.get("replacementText"),
                ),
                _review_scope(scope_or_error),
            )
            response = _json_no_store(
                {
                    "runId": result.run_id,
                    "reviewVersion": result.snapshot.version,
                    "decision": _decision_json(result.decision),
                },
                status_code=201,
            )
            response.headers["Location"] = (
                f"/v1/projects/{canonical_project}/extraction-runs/"
                f"{canonical_run}?reviewVersion={result.snapshot.version}"
            )
            return response
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ReviewFailure as error:
            return _review_failure(error, request_id)
        except Exception:
            return _failure("REPOSITORY_FAILURE", request_id, status=503, retryable=True)

    @app.post("/v1/projects/{project_id}/wbs-plans", status_code=201)
    async def create_wbs_plan(project_id: str, request: Request) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        if canonical_project is None:
            return _failure("PLAN_NOT_FOUND", request_id, status=404)
        try:
            body = await _read_strict_json(
                request, max_bytes=_MAX_SCHEDULE_JSON_BYTES
            )
            _require_exact_fields(body, _CREATE_PLAN_FIELDS)
            run_id = _canonical_review_uuid(body.get("reviewRunId"))
            review_version = _positive_json_integer(body.get("reviewVersion"))
            if run_id is None:
                raise _MalformedRequest
            service = getattr(request.app.state, "schedule_service", None)
            if service is None:
                return _failure(
                    "REPOSITORY_FAILURE", request_id, status=503, retryable=True
                )
            result = await service.create_plan(
                CreatePlanRequest(canonical_project, run_id, review_version),
                _schedule_scope(scope_or_error),
            )
            response = _json_no_store(
                {
                    "plan": _plan_model_json(result.plan),
                    "replayed": bool(result.replayed),
                },
                status_code=201,
            )
            response.headers["Location"] = (
                f"/v1/projects/{canonical_project}/wbs-plans/"
                f"{result.plan.plan.plan_id}?planVersion={result.plan.version.version}"
            )
            return response
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ScheduleServiceFailure as error:
            return _schedule_failure(error, request_id)
        except Exception:
            return _failure(
                "REPOSITORY_FAILURE", request_id, status=503, retryable=True
            )

    @app.get("/v1/projects/{project_id}/wbs-plans/{plan_id}")
    async def get_wbs_plan(
        project_id: str, plan_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_plan = _canonical_review_uuid(plan_id)
        if canonical_project is None or canonical_plan is None:
            return _failure("PLAN_NOT_FOUND", request_id, status=404)
        try:
            plan_version = _plan_version_query(request)
            service = getattr(request.app.state, "schedule_service", None)
            if service is None:
                return _failure(
                    "REPOSITORY_FAILURE", request_id, status=503, retryable=True
                )
            result = await service.read_plan(
                canonical_project,
                canonical_plan,
                _schedule_scope(scope_or_error),
                plan_version=plan_version,
            )
            return _json_no_store(_plan_model_json(result))
        except asyncio.CancelledError:
            raise
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ScheduleServiceFailure as error:
            return _schedule_failure(error, request_id)
        except Exception:
            return _failure(
                "REPOSITORY_FAILURE", request_id, status=503, retryable=True
            )

    @app.post(
        "/v1/projects/{project_id}/wbs-plans/{plan_id}/revisions",
        status_code=201,
    )
    async def revise_wbs_plan(
        project_id: str, plan_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_plan = _canonical_review_uuid(plan_id)
        if canonical_project is None or canonical_plan is None:
            return _failure("PLAN_NOT_FOUND", request_id, status=404)
        try:
            body = await _read_strict_json(
                request, max_bytes=_MAX_SCHEDULE_JSON_BYTES
            )
            _require_exact_fields(body, _REVISE_PLAN_FIELDS)
            expected = _positive_json_integer(body.get("expectedPlanVersion"))
            updates = _task_updates(body.get("taskUpdates"))
            service = getattr(request.app.state, "schedule_service", None)
            if service is None:
                return _failure(
                    "REPOSITORY_FAILURE", request_id, status=503, retryable=True
                )
            result = await service.revise_plan(
                canonical_project,
                canonical_plan,
                expected,
                updates,
                _schedule_scope(scope_or_error),
            )
            response = _json_no_store(_plan_model_json(result), status_code=201)
            response.headers["Location"] = (
                f"/v1/projects/{canonical_project}/wbs-plans/{canonical_plan}"
                f"?planVersion={result.version.version}"
            )
            return response
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ScheduleServiceFailure as error:
            return _schedule_failure(error, request_id)
        except Exception:
            return _failure(
                "REPOSITORY_FAILURE", request_id, status=503, retryable=True
            )

    @app.post(
        "/v1/projects/{project_id}/wbs-plans/{plan_id}/schedule-snapshots",
        status_code=201,
    )
    async def create_schedule_snapshot(
        project_id: str, plan_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_plan = _canonical_review_uuid(plan_id)
        if canonical_project is None or canonical_plan is None:
            return _failure("PLAN_NOT_FOUND", request_id, status=404)
        try:
            body = await _read_strict_json(
                request, max_bytes=_MAX_SCHEDULE_JSON_BYTES
            )
            _require_exact_fields(body, _CREATE_SCHEDULE_FIELDS)
            expected = _positive_json_integer(body.get("expectedPlanVersion"))
            project_start = body.get("projectStart")
            holidays = body.get("holidays")
            if (
                not isinstance(project_start, str)
                or not project_start
                or not isinstance(holidays, list)
                or any(not isinstance(item, str) or not item for item in holidays)
                or len(holidays) != len(set(holidays))
            ):
                raise _MalformedRequest
            service = getattr(request.app.state, "schedule_service", None)
            if service is None:
                return _failure(
                    "REPOSITORY_FAILURE", request_id, status=503, retryable=True
                )
            result = await service.calculate_plan_schedule(
                canonical_project,
                canonical_plan,
                expected,
                project_start,
                tuple(holidays),
                _schedule_scope(scope_or_error),
            )
            response = _json_no_store(
                {
                    "snapshot": _schedule_snapshot_json(result.snapshot),
                    "replayed": bool(result.replayed),
                },
                status_code=201,
            )
            response.headers["Location"] = (
                f"/v1/projects/{canonical_project}/wbs-plans/{canonical_plan}"
                f"/schedule-snapshots/{result.snapshot.snapshot_id}"
            )
            return response
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ScheduleServiceFailure as error:
            return _schedule_failure(error, request_id)
        except Exception:
            return _failure(
                "REPOSITORY_FAILURE", request_id, status=503, retryable=True
            )

    @app.post(
        "/v1/projects/{project_id}/wbs-plans/{plan_id}/approvals",
        status_code=201,
    )
    async def approve_wbs_plan(
        project_id: str, plan_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_plan = _canonical_review_uuid(plan_id)
        if canonical_project is None or canonical_plan is None:
            return _failure("PLAN_NOT_FOUND", request_id, status=404)
        try:
            body = await _read_strict_json(
                request, max_bytes=_MAX_SCHEDULE_JSON_BYTES
            )
            _require_exact_fields(body, _APPROVE_PLAN_FIELDS)
            expected = _positive_json_integer(body.get("expectedPlanVersion"))
            snapshot_id = _canonical_review_uuid(body.get("scheduleSnapshotId"))
            reason = body.get("reason")
            if (
                snapshot_id is None
                or not isinstance(reason, str)
                or not reason.strip()
                or len(reason.strip()) > 1000
            ):
                raise _MalformedRequest
            service = getattr(request.app.state, "schedule_service", None)
            if service is None:
                return _failure(
                    "REPOSITORY_FAILURE", request_id, status=503, retryable=True
                )
            result = await service.approve_plan(
                canonical_project,
                canonical_plan,
                expected,
                snapshot_id,
                reason,
                _schedule_scope(scope_or_error),
            )
            response = _json_no_store(
                {
                    "approval": _plan_approval_json(result.approval),
                    "replayed": bool(result.replayed),
                },
                status_code=201,
            )
            response.headers["Location"] = (
                f"/v1/projects/{canonical_project}/wbs-plans/{canonical_plan}"
                f"/approvals?planVersion={result.approval.plan_version}"
            )
            return response
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ScheduleServiceFailure as error:
            return _schedule_failure(error, request_id)
        except Exception:
            return _failure(
                "REPOSITORY_FAILURE", request_id, status=503, retryable=True
            )

    @app.get("/v1/projects/{project_id}/wbs-plans/{plan_id}/approvals")
    async def get_wbs_plan_approval(
        project_id: str, plan_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_plan = _canonical_review_uuid(plan_id)
        if canonical_project is None or canonical_plan is None:
            return _failure("PLAN_NOT_FOUND", request_id, status=404)
        try:
            plan_version = _plan_version_query(request)
            service = getattr(request.app.state, "schedule_service", None)
            if service is None:
                return _failure(
                    "REPOSITORY_FAILURE", request_id, status=503, retryable=True
                )
            approval = await service.read_plan_approval(
                canonical_project,
                canonical_plan,
                _schedule_scope(scope_or_error),
                plan_version=plan_version,
            )
            return _json_no_store(
                {
                    "approval": (
                        _plan_approval_json(approval)
                        if approval is not None
                        else None
                    )
                }
            )
        except asyncio.CancelledError:
            raise
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ScheduleServiceFailure as error:
            return _schedule_failure(error, request_id)
        except Exception:
            return _failure(
                "REPOSITORY_FAILURE", request_id, status=503, retryable=True
            )

    @app.get("/v1/projects/{project_id}/wbs-plans/{plan_id}/execution")
    async def get_execution_state(
        project_id: str, plan_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_plan = _canonical_review_uuid(plan_id)
        if canonical_project is None or canonical_plan is None:
            return _failure("PLAN_NOT_FOUND", request_id, status=404)
        try:
            plan_version = _plan_version_query(request)
            plan, states = await _read_execution_rows(
                request,
                canonical_project,
                canonical_plan,
                plan_version,
                scope_or_error,
            )
            return _json_no_store(_execution_read_json(plan, states))
        except asyncio.CancelledError:
            raise
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ScheduleServiceFailure as error:
            return _schedule_failure(error, request_id)
        except ExecutionFailure as error:
            return _execution_failure(error, request_id)
        except Exception:
            return _failure(
                "EXECUTION_READ_FAILED", request_id, status=503, retryable=True
            )

    @app.post(
        "/v1/projects/{project_id}/wbs-plans/{plan_id}/execution-updates",
        status_code=201,
    )
    async def update_execution_state(
        project_id: str, plan_id: str, request: Request
    ) -> JSONResponse:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_plan = _canonical_review_uuid(plan_id)
        if canonical_project is None or canonical_plan is None:
            return _failure("PLAN_NOT_FOUND", request_id, status=404)
        try:
            body = await _read_strict_json(
                request, max_bytes=_MAX_SCHEDULE_JSON_BYTES
            )
            _require_execution_fields(body)
            expected_plan_version = _positive_json_integer(
                body.get("expectedPlanVersion")
            )
            expected_sequence = _nonnegative_json_integer(
                body.get("expectedExecutionSequence")
            )
            schedule_service = getattr(request.app.state, "schedule_service", None)
            execution_service = getattr(request.app.state, "execution_service", None)
            if schedule_service is None or execution_service is None:
                return _failure(
                    "EXECUTION_WRITE_FAILED", request_id, status=503, retryable=True
                )
            plan = await schedule_service.read_plan(
                canonical_project,
                canonical_plan,
                _schedule_scope(scope_or_error),
                plan_version=expected_plan_version,
            )
            if plan.version.version != max(plan.available_versions):
                raise ExecutionFailure(
                    "EXECUTION_CONFLICT", "historical WBS versions are read-only"
                )
            approval = await schedule_service.read_plan_approval(
                canonical_project,
                canonical_plan,
                _schedule_scope(scope_or_error),
                plan_version=expected_plan_version,
            )
            if approval is None:
                raise ExecutionFailure(
                    "PLAN_NOT_APPROVED", "WBS plan version is not approved"
                )
            result = await execution_service.update_task(
                ExecutionUpdateRequest(
                    project_id=canonical_project,
                    plan_id=canonical_plan,
                    plan_version_id=plan.version.version_id,
                    task_id=body["taskId"],
                    expected_sequence=expected_sequence,
                    status=body["status"],
                    blocker_reason=body.get("blockerReason"),
                    actual_start=body.get("actualStart"),
                    actual_finish=body.get("actualFinish"),
                    note=body.get("note"),
                ),
                _execution_scope(scope_or_error),
            )
            response = _json_no_store(
                {
                    "update": _execution_state_json(result.update),
                    "replayed": bool(result.replayed),
                },
                status_code=201,
            )
            response.headers["Location"] = (
                f"/v1/projects/{canonical_project}/wbs-plans/{canonical_plan}"
                f"/execution?planVersion={expected_plan_version}"
            )
            return response
        except asyncio.CancelledError:
            raise
        except _PayloadTooLarge:
            return _failure("PAYLOAD_TOO_LARGE", request_id, status=413)
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ScheduleServiceFailure as error:
            return _schedule_failure(error, request_id)
        except ExecutionFailure as error:
            return _execution_failure(error, request_id)
        except Exception:
            return _failure(
                "EXECUTION_WRITE_FAILED", request_id, status=503, retryable=True
            )

    async def download_execution_export(
        project_id: str, plan_id: str, request: Request, export_format: str
    ) -> Response:
        request_id = _request_id(request_id_factory)
        scope_or_error = await _authenticate(request, request_id)
        if isinstance(scope_or_error, JSONResponse):
            return scope_or_error
        canonical_project = _canonical_review_uuid(project_id)
        canonical_plan = _canonical_review_uuid(plan_id)
        if canonical_project is None or canonical_plan is None:
            return _failure("PLAN_NOT_FOUND", request_id, status=404)
        try:
            plan_version = _required_plan_version_query(request)
            plan, states = await _read_execution_rows(
                request,
                canonical_project,
                canonical_plan,
                plan_version,
                scope_or_error,
            )
            execution_service = request.app.state.execution_service
            rows = _execution_export_rows(plan, states)
            if export_format == "csv":
                content = execution_service.export_csv(rows)
                media_type = "text/csv; charset=utf-8"
            else:
                content = execution_service.export_xlsx(rows)
                media_type = (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                )
            response = Response(content=content, media_type=media_type)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Disposition"] = (
                f'attachment; filename="marketops-execution-v{plan.version.version}.{export_format}"'
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except asyncio.CancelledError:
            raise
        except _MalformedRequest:
            return _failure("INVALID_INPUT", request_id, status=400)
        except ScheduleServiceFailure as error:
            return _schedule_failure(error, request_id)
        except ExecutionFailure as error:
            return _execution_failure(error, request_id)
        except Exception:
            return _failure(
                "EXECUTION_READ_FAILED", request_id, status=503, retryable=True
            )

    @app.get("/v1/projects/{project_id}/wbs-plans/{plan_id}/exports/execution.csv")
    async def download_execution_csv(
        project_id: str, plan_id: str, request: Request
    ) -> Response:
        return await download_execution_export(project_id, plan_id, request, "csv")

    @app.get("/v1/projects/{project_id}/wbs-plans/{plan_id}/exports/execution.xlsx")
    async def download_execution_xlsx(
        project_id: str, plan_id: str, request: Request
    ) -> Response:
        return await download_execution_export(project_id, plan_id, request, "xlsx")

    if static_root is not None:
        root = static_root.resolve(strict=True)

        async def static_asset(request: Request) -> Any:
            request_id = _request_id(request_id_factory)
            scope_or_error = await _authenticate(
                request, request_id, prefer_basic_challenge=True
            )
            if isinstance(scope_or_error, JSONResponse):
                return scope_or_error
            filename = _STATIC_FILES.get(request.url.path)
            if filename is None:
                return _failure("PROJECT_NOT_FOUND", request_id, status=404)
            response = FileResponse(root / filename)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
            return response

        for path in _STATIC_FILES:
            app.add_api_route(
                path,
                static_asset,
                methods=["GET"],
                include_in_schema=False,
            )

    return app


def _load_openapi_contract() -> dict[str, Any]:
    try:
        document = json.loads(_OPENAPI_CONTRACT.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("the reviewed OpenAPI contract is unavailable") from error
    if document.get("openapi") != "3.1.0":
        raise RuntimeError("the reviewed OpenAPI contract is invalid")
    return document


async def _limited_request_stream(request: Request) -> AsyncIterator[bytes]:
    observed = 0
    async for chunk in request.stream():
        observed += len(chunk)
        if observed > _MAX_MULTIPART_REQUEST_SIZE_BYTES:
            raise _PayloadTooLarge
        yield chunk


async def _authenticate(
    request: Request,
    request_id: str,
    *,
    prefer_basic_challenge: bool = False,
) -> ScopeContext | JSONResponse:
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        return _authorization_failure(request_id, basic=prefer_basic_challenge)
    scheme, separator, token = values[0].partition(" ")
    normalized_scheme = scheme.lower()
    if (
        not separator
        or normalized_scheme not in {"bearer", "basic"}
        or not token.strip()
    ):
        return _authorization_failure(
            request_id, basic=prefer_basic_challenge or normalized_scheme == "basic"
        )
    authenticator = getattr(request.app.state, "authenticator", None)
    if authenticator is None:
        return _authorization_failure(request_id, basic=prefer_basic_challenge)
    try:
        if normalized_scheme == "basic":
            basic_authenticator = getattr(authenticator, "authenticate_basic", None)
            if not callable(basic_authenticator):
                return _authorization_failure(request_id, basic=True)
            credentials = _decode_basic_credentials(token.strip())
            if credentials is None:
                return _authorization_failure(request_id, basic=True)
            scope = basic_authenticator(*credentials)
        else:
            scope = authenticator(token.strip())
        if inspect.isawaitable(scope):
            scope = await scope
    except asyncio.CancelledError:
        raise
    except Exception:
        return _authorization_failure(
            request_id, basic=normalized_scheme == "basic"
        )
    if scope is None:
        return _authorization_failure(
            request_id, basic=normalized_scheme == "basic"
        )
    canonical_scope = _canonical_scope(scope)
    if canonical_scope is None:
        return _failure("AUTHORIZATION_REQUIRED", request_id, status=403)
    return canonical_scope


def _canonical_scope(scope: Any) -> ScopeContext | None:
    if not isinstance(scope, ScopeContext):
        return None
    try:
        identifiers = tuple(
            UUID(value)
            for value in (
                scope.organization_id,
                scope.workspace_id,
                scope.client_id,
                scope.actor_id,
            )
        )
    except (AttributeError, TypeError, ValueError):
        return None
    if any(identifier.version is None for identifier in identifiers):
        return None
    return ScopeContext(*(str(identifier) for identifier in identifiers))


def _parse_form(form: FormData) -> dict[str, Any]:
    values: dict[str, list[Any]] = {}
    for name, value in form.multi_items():
        values.setdefault(name, []).append(value)
    if set(values) != _EXPECTED_FIELDS or any(len(items) != 1 for items in values.values()):
        raise _MalformedRequest
    parsed = {name: items[0] for name, items in values.items()}
    for name in _EXPECTED_FIELDS - _FILE_FIELDS:
        if not isinstance(parsed[name], str):
            raise _MalformedRequest
    for name in _FILE_FIELDS:
        if not isinstance(parsed[name], UploadFile):
            raise _MalformedRequest
    return parsed


async def _stream_upload(upload: UploadFile) -> Path:
    handle = tempfile.NamedTemporaryFile(mode="wb", prefix="marketops-import-", delete=False)
    path = Path(handle.name)
    size = 0
    try:
        with handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE_BYTES:
                    raise _PayloadTooLarge
                handle.write(chunk)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _single_header(request: Request, name: str) -> str:
    values = request.headers.getlist(name)
    if len(values) != 1:
        raise _MalformedRequest
    return values[0]


def _positive_integer(value: str) -> int:
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise _MalformedRequest
    number = int(value)
    if number < 1:
        raise _MalformedRequest
    return number


def _confirmed(value: str) -> bool:
    if value.lower() != "true":
        raise _MalformedRequest
    return True


def _decode_basic_credentials(value: str) -> tuple[str, str] | None:
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator or not username or not password:
        return None
    return username, password


def _project_list_limit(request: Request) -> int:
    values: dict[str, list[str]] = {}
    for name, value in request.query_params.multi_items():
        values.setdefault(name, []).append(value)
    if not values:
        return 20
    if set(values) != {"limit"} or len(values["limit"]) != 1:
        raise _MalformedRequest
    value = values["limit"][0]
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise _MalformedRequest
    limit = int(value)
    if not 1 <= limit <= 100:
        raise _MalformedRequest
    return limit


def _canonical_project_id(value: str) -> str | None:
    try:
        return str(UUID(value))
    except (TypeError, ValueError):
        return None


def _canonical_review_uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = str(UUID(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed == value else None


def _review_scope(scope: ScopeContext) -> ReviewScopeContext:
    return ReviewScopeContext(
        scope.organization_id,
        scope.workspace_id,
        scope.client_id,
        scope.actor_id,
    )


def _schedule_scope(scope: ScopeContext) -> ScheduleScopeContext:
    return ScheduleScopeContext(
        scope.organization_id,
        scope.workspace_id,
        scope.client_id,
        scope.actor_id,
    )


def _execution_scope(scope: ScopeContext) -> ExecutionScopeContext:
    return ExecutionScopeContext(
        scope.organization_id,
        scope.workspace_id,
        scope.client_id,
        scope.actor_id,
    )


async def _read_execution_rows(
    request: Request,
    project_id: str,
    plan_id: str,
    plan_version: int | None,
    scope: ScopeContext,
) -> tuple[Any, Mapping[str, Any]]:
    schedule_service = getattr(request.app.state, "schedule_service", None)
    execution_service = getattr(request.app.state, "execution_service", None)
    if schedule_service is None or execution_service is None:
        raise ExecutionFailure(
            "EXECUTION_READ_FAILED", "execution services are unavailable"
        )
    schedule_scope = _schedule_scope(scope)
    plan = await schedule_service.read_plan(
        project_id, plan_id, schedule_scope, plan_version=plan_version
    )
    approval = await schedule_service.read_plan_approval(
        project_id,
        plan_id,
        schedule_scope,
        plan_version=plan.version.version,
    )
    if approval is None:
        raise ExecutionFailure(
            "PLAN_NOT_APPROVED", "WBS plan version is not approved"
        )
    states = await execution_service.read_states(
        project_id, plan.version.version_id, _execution_scope(scope)
    )
    return plan, states


async def _read_strict_json(
    request: Request, *, max_bytes: int = _MAX_REVIEW_JSON_BYTES
) -> dict[str, Any]:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
        raise _MalformedRequest
    observed = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        observed += len(chunk)
        if observed > max_bytes:
            raise _PayloadTooLarge
        chunks.append(chunk)
    try:
        raw = b"".join(chunks).decode("utf-8")
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise _MalformedRequest from None
    if not isinstance(value, dict):
        raise _MalformedRequest
    return value


def _reject_duplicate_json_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _require_exact_fields(value: dict[str, Any], expected: frozenset[str]) -> None:
    if frozenset(value) != expected:
        raise _MalformedRequest


def _positive_json_integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _MalformedRequest
    return value


def _nonnegative_json_integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _MalformedRequest
    return value


def _require_execution_fields(value: dict[str, Any]) -> None:
    keys = frozenset(value)
    if not _EXECUTION_REQUIRED_FIELDS.issubset(keys) or not keys.issubset(
        _EXECUTION_REQUIRED_FIELDS | _EXECUTION_OPTIONAL_FIELDS
    ):
        raise _MalformedRequest
    if (
        not isinstance(value.get("taskId"), str)
        or not value["taskId"]
        or len(value["taskId"]) > 300
        or not isinstance(value.get("status"), str)
        or any(
            key in value and value[key] is not None and not isinstance(value[key], str)
            for key in _EXECUTION_OPTIONAL_FIELDS
        )
        or any(
            key in value
            and value[key] is not None
            and not _is_full_date(value[key])
            for key in ("actualStart", "actualFinish")
        )
    ):
        raise _MalformedRequest


def _is_full_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _task_updates(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value or len(value) > 1000:
        raise _MalformedRequest
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or frozenset(item) != _TASK_UPDATE_FIELDS:
            raise _MalformedRequest
        task_id = item.get("taskId")
        changes = item.get("changes")
        if (
            not isinstance(task_id, str)
            or not task_id
            or len(task_id) > 300
            or task_id in seen
            or not isinstance(changes, dict)
            or not changes
            or not frozenset(changes).issubset(_EDITABLE_TASK_FIELDS)
        ):
            raise _MalformedRequest
        seen.add(task_id)
        result.append({"taskId": task_id, "changes": changes})
    return tuple(result)


def _require_decision_fields(value: dict[str, Any]) -> None:
    keys = frozenset(value)
    if not _DECISION_REQUIRED_FIELDS.issubset(keys) or not keys.issubset(
        _DECISION_REQUIRED_FIELDS | _DECISION_OPTIONAL_FIELDS
    ):
        raise _MalformedRequest
    expected_version = value.get("expectedReviewVersion")
    if (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 1
        or not isinstance(value.get("action"), str)
        or not isinstance(value.get("reason"), str)
        or ("comment" in value and value["comment"] is not None and not isinstance(value["comment"], str))
        or ("replacementText" in value and value["replacementText"] is not None and not isinstance(value["replacementText"], str))
    ):
        raise _MalformedRequest


def _review_version_query(request: Request) -> int | None:
    values: dict[str, list[str]] = {}
    for name, value in request.query_params.multi_items():
        values.setdefault(name, []).append(value)
    if not values:
        return None
    if set(values) != {"reviewVersion"} or len(values["reviewVersion"]) != 1:
        raise _MalformedRequest
    raw = values["reviewVersion"][0]
    if (
        not raw
        or not raw.isascii()
        or not raw.isdecimal()
        or (len(raw) > 1 and raw.startswith("0"))
        or int(raw) < 1
    ):
        raise _MalformedRequest
    return int(raw)


def _plan_version_query(request: Request) -> int | None:
    values: dict[str, list[str]] = {}
    for name, value in request.query_params.multi_items():
        values.setdefault(name, []).append(value)
    if not values:
        return None
    if set(values) != {"planVersion"} or len(values["planVersion"]) != 1:
        raise _MalformedRequest
    return _positive_integer(values["planVersion"][0])


def _required_plan_version_query(request: Request) -> int:
    version = _plan_version_query(request)
    if version is None:
        raise _MalformedRequest
    return version


def _review_summary_json(summary: Any) -> dict[str, Any]:
    return _review_run_json(summary.run, summary.latest_review_version)


def _create_review_result_json(result: Any) -> dict[str, Any]:
    return {
        "runId": result.run.run_id,
        "reviewVersion": result.snapshot.version,
        "candidateCount": result.run.candidate_count,
        "proposalVersionId": result.run.proposal_version_id,
        "proposalSha256": result.run.proposal_sha256,
        "replayed": bool(result.replayed),
        "createdAt": result.run.created_at.isoformat(),
    }


def _review_run_json(run: Any, latest_review_version: int) -> dict[str, Any]:
    return {
        "runId": run.run_id,
        "proposalVersionId": run.proposal_version_id,
        "proposalSha256": run.proposal_sha256,
        "candidateCount": run.candidate_count,
        "latestReviewVersion": latest_review_version,
        "createdAt": run.created_at.isoformat(),
    }


def _decision_json(decision: Any) -> dict[str, Any]:
    return {
        "decisionId": decision.decision_id,
        "reviewVersion": decision.review_version,
        "candidateId": decision.candidate_id,
        "action": decision.action,
        "reason": decision.reason,
        "comment": decision.comment,
        "replacementText": decision.replacement_text,
        "actorId": decision.actor_id,
        "createdAt": decision.created_at.isoformat(),
    }


def _review_model_json(model: Any) -> dict[str, Any]:
    return {
        "run": _review_run_json(
            model.run, model.available_review_versions[-1]
        ),
        "selectedReviewVersion": model.snapshot.version,
        "availableReviewVersions": list(model.available_review_versions),
        "selectedDecision": (
            _decision_json(model.selected_decision)
            if model.selected_decision is not None
            else None
        ),
        "candidates": [
            {
                "ordinal": view.ordinal,
                **view.candidate.as_dict(),
                "review": {
                    "status": view.status,
                    "replacementText": view.replacement_text,
                    "lastDecision": (
                        _decision_json(view.last_decision)
                        if view.last_decision is not None
                        else None
                    ),
                },
            }
            for view in model.candidates
        ],
    }


def _review_failure(error: ReviewFailure, request_id: str) -> JSONResponse:
    return _failure(
        error.code,
        request_id,
        status=_STATUS_BY_CODE.get(error.code, 500),
        retryable=error.code == "REPOSITORY_FAILURE",
    )


def _plan_model_json(model: Any) -> dict[str, Any]:
    plan = model.plan
    version = model.version
    payload = version.payload
    return {
        "planId": plan.plan_id,
        "projectId": plan.project_id,
        "proposalVersionId": plan.proposal_version_id,
        "proposalSha256": plan.proposal_sha256,
        "sourceReviewRunId": plan.source_review_run_id,
        "sourceReviewSnapshotId": plan.source_review_snapshot_id,
        "sourceReviewVersion": plan.source_review_version,
        "selectedPlanVersion": version.version,
        "availablePlanVersions": list(model.available_versions),
        "status": version.status,
        "planDigest": version.digest,
        "createdAt": version.created_at.isoformat(),
        "tasks": [_wbs_task_json(item) for item in payload["tasks"]],
        "controls": [_wbs_control_json(item) for item in payload["controls"]],
    }


def _wbs_task_json(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "taskId": item["taskId"],
        "candidateId": item["candidateId"],
        "kind": item["kind"],
        "classification": item["classification"],
        "sourceText": item["sourceText"],
        "title": item["title"],
        "sourceCitation": item["sourceCitation"],
        "reviewStatus": item["reviewStatus"],
        "durationWorkdays": item["durationWorkdays"],
        "predecessors": list(item["predecessors"]),
        "ownerRole": item["ownerRole"],
        "plannedStart": item["plannedStart"],
        "plannedFinish": item["plannedFinish"],
        "hardDeadline": item["hardDeadline"],
        "approvedBufferWorkdays": item["approvedBufferWorkdays"],
        "isLocked": item["isLocked"],
        "status": item["status"],
    }


def _wbs_control_json(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidateId": item["candidateId"],
        "kind": item["kind"],
        "classification": item["classification"],
        "sourceText": item["sourceText"],
        "text": item["text"],
        "sourceCitation": item["sourceCitation"],
        "reviewStatus": item["reviewStatus"],
    }


def _schedule_snapshot_json(snapshot: Any) -> dict[str, Any]:
    payload = snapshot.payload
    return {
        "snapshotId": snapshot.snapshot_id,
        "planId": snapshot.plan_id,
        "planVersion": snapshot.plan_version,
        "status": snapshot.status,
        "projectStart": snapshot.project_start,
        "holidays": list(snapshot.holidays),
        "planDigest": snapshot.plan_digest,
        "scheduleDigest": snapshot.schedule_digest,
        "createdAt": snapshot.created_at.isoformat(),
        "topologicalOrder": list(payload["topologicalOrder"]),
        "tasks": list(payload["tasks"]),
        "conflicts": list(payload["conflicts"]),
        "deadlineMisses": list(payload["deadlineMisses"]),
        "sourceDateDrift": list(payload["sourceDateDrift"]),
    }


def _plan_approval_json(approval: Any) -> dict[str, Any]:
    return {
        "approvalId": approval.approval_id,
        "planId": approval.plan_id,
        "planVersion": approval.plan_version,
        "scheduleSnapshotId": approval.schedule_snapshot_id,
        "planDigest": approval.plan_digest,
        "scheduleDigest": approval.schedule_digest,
        "reason": approval.reason,
        "approvedAt": approval.approved_at.isoformat(),
    }


def _execution_state_json(state: Any) -> dict[str, Any]:
    return {
        "taskId": state.task_id,
        "sequenceNo": state.sequence_no,
        "status": state.status,
        "blockerReason": state.blocker_reason,
        "actualStart": state.actual_start,
        "actualFinish": state.actual_finish,
        "note": state.note,
        "updatedAt": state.updated_at.isoformat(),
    }


def _execution_task_json(task: Mapping[str, Any], state: Any | None) -> dict[str, Any]:
    return {
        "taskId": task["taskId"],
        "title": task["title"],
        "ownerRole": task["ownerRole"],
        "plannedStart": task["plannedStart"],
        "plannedFinish": task["plannedFinish"],
        "status": state.status if state is not None else task["status"],
        "blockerReason": state.blocker_reason if state is not None else None,
        "actualStart": state.actual_start if state is not None else None,
        "actualFinish": state.actual_finish if state is not None else None,
        "note": state.note if state is not None else None,
        "sequenceNo": state.sequence_no if state is not None else 0,
        "updatedAt": state.updated_at.isoformat() if state is not None else None,
    }


def _execution_read_json(plan: Any, states: Mapping[str, Any]) -> dict[str, Any]:
    tasks = plan.version.payload["tasks"]
    return {
        "projectId": plan.plan.project_id,
        "planId": plan.plan.plan_id,
        "planVersion": plan.version.version,
        "editable": plan.version.version == max(plan.available_versions),
        "tasks": [
            _execution_task_json(task, states.get(task["taskId"])) for task in tasks
        ],
    }


def _execution_export_rows(
    plan: Any, states: Mapping[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in plan.version.payload["tasks"]:
        state = states.get(task["taskId"])
        rows.append(
            {
                "taskId": task["taskId"],
                "title": task["title"],
                "status": state.status if state is not None else task["status"],
                "blockerReason": (
                    state.blocker_reason if state is not None else None
                ),
                "plannedStart": task["plannedStart"],
                "plannedFinish": task["plannedFinish"],
                "actualStart": state.actual_start if state is not None else None,
                "actualFinish": state.actual_finish if state is not None else None,
                "ownerRole": task["ownerRole"],
                "sourceCandidateId": task["candidateId"],
            }
        )
    return rows


def _execution_failure(error: ExecutionFailure, request_id: str) -> JSONResponse:
    code = error.code if error.code in _PUBLIC_MESSAGES else "INTERNAL_FAILURE"
    return _failure(
        code,
        request_id,
        status=_STATUS_BY_CODE.get(code, 500),
        retryable=code in {"EXECUTION_WRITE_FAILED", "EXECUTION_READ_FAILED"},
    )


def _schedule_failure(
    error: ScheduleServiceFailure, request_id: str
) -> JSONResponse:
    code = error.code
    if code not in _PUBLIC_MESSAGES:
        code = "WBS_VALIDATION_FAILED" if code.islower() else "INTERNAL_FAILURE"
    return _failure(
        code,
        request_id,
        status=_STATUS_BY_CODE.get(code, 500),
        retryable=code == "REPOSITORY_FAILURE",
    )


def _request_id(factory: Callable[[], Any]) -> str:
    try:
        value = str(factory())
    except Exception:
        value = str(uuid4())
    return value if value else str(uuid4())


def _authorization_failure(request_id: str, *, basic: bool = False) -> JSONResponse:
    response = _failure("AUTHORIZATION_REQUIRED", request_id, status=401)
    response.headers["WWW-Authenticate"] = (
        'Basic realm="MarketOps", charset="UTF-8"' if basic else "Bearer"
    )
    return response


def _json_no_store(content: Any, *, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["Cache-Control"] = "no-store"
    return response


def _failure(
    code: str,
    request_id: str,
    *,
    status: int,
    retryable: bool = False,
) -> JSONResponse:
    public_code = code if code in _PUBLIC_MESSAGES else "DATABASE_WRITE_FAILED"
    response = JSONResponse(
        status_code=status if public_code == code else 500,
        content={
            "code": public_code,
            "message": _PUBLIC_MESSAGES[public_code],
            "retryable": bool(retryable),
            "requestId": request_id,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


class _MalformedRequest(ValueError):
    pass


class _PayloadTooLarge(ValueError):
    pass
