from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, AsyncContextManager, Mapping, Protocol, Sequence
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile


STATUSES = frozenset(
    {"not_started", "in_progress", "blocked", "completed", "cancelled"}
)


class ExecutionFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ExecutionScopeContext:
    organization_id: str
    workspace_id: str
    client_id: str
    actor_id: str


@dataclass(frozen=True)
class ExecutionUpdateRequest:
    project_id: str
    plan_id: str
    plan_version_id: str
    task_id: str
    expected_sequence: int
    status: str
    blocker_reason: str | None = None
    actual_start: str | None = None
    actual_finish: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class ExecutionState:
    task_id: str
    sequence_no: int
    status: str
    blocker_reason: str | None
    actual_start: str | None
    actual_finish: str | None
    note: str | None
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class ExecutionUpdateResult:
    update: ExecutionState
    replayed: bool = False


class ExecutionTransaction(Protocol):
    async def append_update(
        self,
        request: ExecutionUpdateRequest,
        scope: ExecutionScopeContext,
        updated_at: datetime,
    ) -> ExecutionUpdateResult: ...


class ExecutionRepository(Protocol):
    def transaction(
        self, scope: ExecutionScopeContext, project_id: str
    ) -> AsyncContextManager[ExecutionTransaction]: ...

    async def list_states(
        self,
        scope: ExecutionScopeContext,
        project_id: str,
        plan_version_id: str,
    ) -> Mapping[str, ExecutionState]: ...

    async def assert_approved_plan(
        self,
        scope: ExecutionScopeContext,
        project_id: str,
        plan_id: str,
        plan_version_id: str,
    ) -> None: ...


class ExecutionService:
    def __init__(self, repository: ExecutionRepository, *, clock):
        self.repository = repository
        self.clock = clock

    async def update_task(
        self, request: ExecutionUpdateRequest, scope: ExecutionScopeContext
    ) -> ExecutionUpdateResult:
        self._validate_scope(scope)
        self._validate_request(request)
        await self.repository.assert_approved_plan(
            scope, request.project_id, request.plan_id, request.plan_version_id
        )
        try:
            async with self.repository.transaction(
                scope, request.project_id
            ) as transaction:
                return await transaction.append_update(request, scope, self.clock())
        except ExecutionFailure:
            raise
        except Exception:
            raise ExecutionFailure(
                "EXECUTION_WRITE_FAILED",
                "execution update could not be persisted",
            ) from None

    async def read_states(
        self,
        project_id: str,
        plan_version_id: str,
        scope: ExecutionScopeContext,
    ) -> Mapping[str, ExecutionState]:
        self._validate_scope(scope)
        if not _uuid_like(project_id) or not _uuid_like(plan_version_id):
            raise ExecutionFailure("INVALID_INPUT", "execution identity is invalid")
        return await self.repository.list_states(
            scope, project_id, plan_version_id
        )

    @staticmethod
    def export_csv(rows: Sequence[Mapping[str, Any]]) -> bytes:
        fields = (
            "taskId",
            "title",
            "status",
            "blockerReason",
            "plannedStart",
            "plannedFinish",
            "actualStart",
            "actualFinish",
            "ownerRole",
            "sourceCandidateId",
        )
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: _export_value(row.get(field))
                    for field in fields
                }
            )
        return output.getvalue().encode("utf-8")

    @staticmethod
    def export_xlsx(rows: Sequence[Mapping[str, Any]]) -> bytes:
        fields = (
            "taskId",
            "title",
            "status",
            "blockerReason",
            "plannedStart",
            "plannedFinish",
            "actualStart",
            "actualFinish",
            "ownerRole",
            "sourceCandidateId",
        )
        sheet_rows = [fields] + [
            tuple(_export_value(row.get(field)) for field in fields)
            for row in rows
        ]
        xml_rows = []
        for row_number, values in enumerate(sheet_rows, 1):
            cells = []
            for column, value in enumerate(values):
                reference = f"{chr(65 + column)}{row_number}"
                escaped = _xml_escape(value)
                cells.append(
                    f'<c r="{reference}" t="inlineStr"><is><t>{escaped}</t></is></c>'
                )
            xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
        sheet = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
        )
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        )
        root_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        )
        workbook = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Execution" sheetId="1" r:id="rId1"/></sheets></workbook>'
        )
        workbook_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        )
        output = io.BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", root_rels)
            archive.writestr("xl/workbook.xml", workbook)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        return output.getvalue()

    @staticmethod
    def _validate_scope(scope: ExecutionScopeContext) -> None:
        if any(
            not _uuid_like(value)
            for value in (
                scope.organization_id,
                scope.workspace_id,
                scope.client_id,
                scope.actor_id,
            )
        ):
            raise ExecutionFailure("INVALID_SCOPE", "execution scope is invalid")

    @staticmethod
    def _validate_request(request: ExecutionUpdateRequest) -> None:
        if (
            any(
                not _uuid_like(value)
                for value in (
                    request.project_id,
                    request.plan_id,
                    request.plan_version_id,
                )
            )
            or not isinstance(request.task_id, str)
            or not request.task_id.strip()
        ):
            raise ExecutionFailure("INVALID_INPUT", "execution identity is invalid")
        if (
            isinstance(request.expected_sequence, bool)
            or not isinstance(request.expected_sequence, int)
            or request.expected_sequence < 0
        ):
            raise ExecutionFailure("INVALID_INPUT", "expected sequence is invalid")
        if request.status not in STATUSES:
            raise ExecutionFailure("INVALID_INPUT", "execution status is invalid")
        if request.status == "blocked" and (
            not request.blocker_reason
            or not request.blocker_reason.strip()
            or len(request.blocker_reason.strip()) > 2000
        ):
            raise ExecutionFailure(
                "INVALID_INPUT", "blocked tasks need a concise blocker reason"
            )
        if request.status != "blocked" and request.blocker_reason is not None:
            raise ExecutionFailure(
                "INVALID_INPUT", "only blocked tasks may retain a blocker reason"
            )
        if request.status == "in_progress" and request.actual_start is None:
            raise ExecutionFailure(
                "INVALID_INPUT", "in-progress tasks need an actual start"
            )
        if request.status == "completed" and (
            request.actual_start is None or request.actual_finish is None
        ):
            raise ExecutionFailure(
                "INVALID_INPUT", "completed tasks need actual start and finish dates"
            )
        if request.note is not None and len(request.note.strip()) > 4000:
            raise ExecutionFailure("INVALID_INPUT", "execution note is too long")
        for value in (request.actual_start, request.actual_finish):
            if value is not None:
                try:
                    parsed = date.fromisoformat(value)
                except (TypeError, ValueError):
                    raise ExecutionFailure(
                        "INVALID_INPUT", "actual dates must use YYYY-MM-DD"
                    ) from None
                if parsed.isoformat() != value:
                    raise ExecutionFailure(
                        "INVALID_INPUT", "actual dates must use YYYY-MM-DD"
                    )
        if (
            request.actual_start
            and request.actual_finish
            and request.actual_finish < request.actual_start
        ):
            raise ExecutionFailure(
                "INVALID_INPUT", "actual finish cannot precede actual start"
            )


def _uuid_like(value: Any) -> bool:
    try:
        UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _export_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _xml_escape(value: Any) -> str:
    text = "".join(
        character
        for character in str(value)
        if character in ("\t", "\n", "\r") or ord(character) >= 32
    )
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
