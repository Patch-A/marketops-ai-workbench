"""Strict input/output contract for the M1-02 extraction slice.

This module deliberately contains no model or persistence dependency. Parser output is
validated as a complete batch before any candidate is constructed so callers never see
partial extraction results.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID, uuid5


HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
BLOCK_KINDS = frozenset({"heading", "paragraph", "table"})
CANDIDATE_KINDS = frozenset({"deliverable", "milestone", "constraint", "assumption"})
CLASSIFICATIONS = frozenset({"fact", "hypothesis"})
REVIEW_STATUSES = frozenset({"pending"})


class ExtractionContractError(ValueError):
    """Stable contract error; no candidate output is valid when raised."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class AmbiguousTableError(ExtractionContractError):
    """A table cell cannot be assigned to exactly one supported candidate kind."""

    def __init__(self, message: str = "table cell kind is ambiguous"):
        super().__init__("AMBIGUOUS_TABLE", message)


def _reject_unknown(mapping: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ExtractionContractError("UNKNOWN_FIELD", f"{label} contains unsupported fields")


def _uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ExtractionContractError("INVALID_UUID", f"{label} must be a UUID string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ExtractionContractError("INVALID_UUID", f"{label} is invalid") from None
    if str(parsed) != value.lower():
        raise ExtractionContractError("INVALID_UUID", f"{label} must use canonical UUID form")
    return str(parsed)


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX_SHA256.fullmatch(value):
        raise ExtractionContractError("INVALID_SHA256", f"{label} must be 64 hex characters")
    return value.lower()


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExtractionContractError("EMPTY_TEXT", f"{label} must be non-empty text")
    return value.strip()


@dataclass(frozen=True)
class SourceLocation:
    """A stable parser coordinate, retained verbatim in citations."""

    kind: str
    values: tuple[tuple[str, int | str], ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SourceLocation":
        if not isinstance(raw, Mapping):
            raise ExtractionContractError("INVALID_LOCATION", "location must be an object")
        location_kind = raw.get("kind")
        if location_kind == "line_range":
            allowed = frozenset({"kind", "startLine", "endLine"})
            required = ("startLine", "endLine")
        elif location_kind == "csv_range":
            allowed = frozenset({"kind", "startRow", "endRow"})
            required = ("startRow", "endRow")
        elif location_kind == "docx_paragraph":
            allowed = frozenset({"kind", "part", "bodyIndex", "paragraph"})
            required = ("part", "bodyIndex", "paragraph")
        elif location_kind == "docx_table":
            allowed = frozenset({"kind", "part", "bodyIndex", "table"})
            required = ("part", "bodyIndex", "table")
        elif location_kind == "markdown_table_cell":
            allowed = frozenset({"kind", "line", "columnIndex"})
            required = ("line", "columnIndex")
        elif location_kind == "csv_cell":
            allowed = frozenset({"kind", "row", "columnIndex", "columnName"})
            required = ("row", "columnIndex")
        elif location_kind == "docx_table_cell":
            allowed = frozenset({"kind", "part", "table", "row", "column"})
            required = ("part", "table", "row", "column")
        else:
            raise ExtractionContractError("INVALID_LOCATION", "unsupported location kind")
        _reject_unknown(raw, allowed, "location")
        if any(key not in raw for key in required):
            raise ExtractionContractError("INVALID_LOCATION", "location is missing required fields")
        values: dict[str, int | str] = {"kind": str(location_kind)}
        for key in required:
            value = raw[key]
            if key == "part" or key == "columnName":
                if not isinstance(value, str) or not value.strip():
                    raise ExtractionContractError("INVALID_LOCATION", f"{key} must be non-empty text")
                values[key] = value.strip()
            else:
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ExtractionContractError("INVALID_LOCATION", f"{key} must be a positive integer")
                values[key] = value
        if location_kind == "line_range" and values["endLine"] < values["startLine"]:
            raise ExtractionContractError("INVALID_LOCATION", "line range is reversed")
        if location_kind == "csv_range" and values["endRow"] < values["startRow"]:
            raise ExtractionContractError("INVALID_LOCATION", "CSV row range is reversed")
        return cls(location_kind, tuple(values.items()))

    def as_dict(self) -> dict[str, int | str]:
        return dict(self.values)

    def order_key(self) -> tuple[Any, ...]:
        raw = self.as_dict()
        if self.kind == "line_range":
            return (raw["startLine"], raw["endLine"], 0)
        if self.kind == "csv_range":
            return (raw["startRow"], raw["endRow"], 0)
        if self.kind in {"docx_paragraph", "docx_table"}:
            return (raw["bodyIndex"], raw.get("paragraph", raw.get("table", 0)), 0)
        if self.kind == "markdown_table_cell":
            return (raw["line"], raw["line"], raw["columnIndex"])
        if self.kind == "csv_cell":
            return (raw["row"], raw["row"], raw["columnIndex"])
        return (raw["table"], raw["row"], raw["column"])

    def identity(self) -> tuple[Any, ...]:
        return (self.kind, *self.values)


@dataclass(frozen=True)
class TableCell:
    column: str
    value: str
    location: SourceLocation

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TableCell":
        if not isinstance(raw, Mapping):
            raise ExtractionContractError("INVALID_TABLE", "table cell must be an object")
        _reject_unknown(raw, frozenset({"column", "value", "location"}), "table cell")
        column = _nonempty_text(raw.get("column"), "table column")
        value = _nonempty_text(raw.get("value"), "table cell value")
        location = SourceLocation.from_mapping(raw.get("location"))
        if location.kind not in {"markdown_table_cell", "csv_cell", "docx_table_cell"}:
            raise ExtractionContractError("INVALID_TABLE", "table cell requires a cell location")
        return cls(column, value, location)


@dataclass(frozen=True)
class ParserBlock:
    kind: str
    text: str | None
    section_path: tuple[str, ...]
    location: SourceLocation
    column_name: str | None = None
    cells: tuple[TableCell, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ParserBlock":
        if not isinstance(raw, Mapping):
            raise ExtractionContractError("INVALID_BLOCK", "parser block must be an object")
        if "kind" in raw and "type" in raw:
            raise ExtractionContractError("UNKNOWN_FIELD", "use either kind or type, not both")
        kind = raw.get("kind", raw.get("type"))
        if kind not in BLOCK_KINDS:
            raise ExtractionContractError("INVALID_BLOCK", "block kind must be heading, paragraph, or table")
        if kind == "table":
            allowed = frozenset(
                {
                    "kind",
                    "type",
                    "text",
                    "columnName",
                    "columns",
                    "rows",
                    "headerRowExplicit",
                    "sectionPath",
                    "location",
                }
            )
        else:
            allowed = frozenset({"kind", "type", "text", "sectionPath", "location", "level", "style"})
        _reject_unknown(raw, allowed, "parser block")
        section = raw.get("sectionPath")
        if not isinstance(section, list) or any(not isinstance(item, str) or not item.strip() for item in section):
            raise ExtractionContractError("INVALID_SECTION", "sectionPath must be a list of non-empty strings")
        location = SourceLocation.from_mapping(raw.get("location"))
        if kind == "table":
            text, column_name, cells = cls._table_content(raw)
            cls._validate_table_cells(location, cells)
        else:
            text = _nonempty_text(raw.get("text"), "block text")
            column_name = None
            cells = ()
            level = raw.get("level")
            if level is not None and (isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 6):
                raise ExtractionContractError("INVALID_BLOCK", "heading level must be between 1 and 6")
            style = raw.get("style")
            if style is not None:
                if not isinstance(style, Mapping):
                    raise ExtractionContractError("INVALID_BLOCK", "style must be an object")
                _reject_unknown(style, frozenset({"id", "name"}), "style")
                _nonempty_text(style.get("id"), "style id")
                _nonempty_text(style.get("name"), "style name")
        return cls(kind, text, tuple(item.strip() for item in section), location, column_name, cells)

    @staticmethod
    def _validate_table_cells(location: SourceLocation, cells: tuple[TableCell, ...]) -> None:
        if not cells:
            return
        outer = location.as_dict()
        for cell in cells:
            inner = cell.location.as_dict()
            if location.kind == "line_range":
                valid = inner["kind"] == "markdown_table_cell" and outer["startLine"] <= inner["line"] <= outer["endLine"]
            elif location.kind == "csv_range":
                valid = inner["kind"] == "csv_cell" and outer["startRow"] <= inner["row"] <= outer["endRow"]
            elif location.kind == "docx_table":
                valid = inner["kind"] == "docx_table_cell" and inner["table"] == outer["table"]
            else:
                valid = False
            if not valid:
                raise ExtractionContractError("INVALID_LOCATION", "table cell is outside the table range")

    @staticmethod
    def _table_content(raw: Mapping[str, Any]) -> tuple[str | None, str | None, tuple[TableCell, ...]]:
        """Accept a normalized cell block or the reviewed M0 parser table shape."""
        column_name = raw.get("columnName")
        if column_name is not None and (not isinstance(column_name, str) or not column_name.strip()):
            raise ExtractionContractError("INVALID_COLUMN", "columnName must be non-empty text")
        if "text" in raw or "columnName" in raw:
            if "rows" in raw or "columns" in raw:
                raise ExtractionContractError("INVALID_TABLE", "table block cannot mix normalized and row shapes")
            text = _nonempty_text(raw.get("text"), "table cell text")
            if column_name is None:
                raise AmbiguousTableError("normalized table cell requires an explicit semantic column")
            return text, column_name.strip(), ()
        columns = raw.get("columns")
        rows = raw.get("rows")
        if not isinstance(columns, list) or any(not isinstance(item, str) or not item.strip() for item in columns):
            raise ExtractionContractError("INVALID_TABLE", "columns must be non-empty strings")
        if len(set(item.strip() for item in columns)) != len(columns):
            raise AmbiguousTableError("table column names must be unique")
        if not isinstance(rows, list) or not rows:
            raise ExtractionContractError("INVALID_TABLE", "table rows must be a non-empty list")
        parsed_cells: list[TableCell] = []
        previous_row = 0
        for row in rows:
            if not isinstance(row, Mapping):
                raise ExtractionContractError("INVALID_TABLE", "table row must be an object")
            _reject_unknown(row, frozenset({"rowNumber", "cells"}), "table row")
            row_number = row.get("rowNumber")
            if isinstance(row_number, bool) or not isinstance(row_number, int) or row_number <= previous_row:
                raise ExtractionContractError("NON_MONOTONIC_LOCATION", "table rows are not in source order")
            previous_row = row_number
            cells = row.get("cells")
            if not isinstance(cells, list) or len(cells) != len(columns):
                raise AmbiguousTableError("table row does not map one-to-one to its columns")
            parsed_row = tuple(TableCell.from_mapping(cell) for cell in cells)
            if tuple(cell.column for cell in parsed_row) != tuple(item.strip() for item in columns):
                raise AmbiguousTableError("table cell columns do not match the table header")
            parsed_cells.extend(parsed_row)
        return None, None, tuple(parsed_cells)


@dataclass(frozen=True)
class SourceCitation:
    source_version_id: str
    source_sha256: str
    location: SourceLocation
    section_path: tuple[str, ...]
    quote: str

    def __post_init__(self) -> None:
        _uuid(self.source_version_id, "source citation version")
        _sha256(self.source_sha256, "source citation hash")
        if not isinstance(self.location, SourceLocation):
            raise ExtractionContractError("MISSING_CITATION", "citation location is required")
        if not isinstance(self.section_path, tuple) or any(not isinstance(item, str) or not item.strip() for item in self.section_path):
            raise ExtractionContractError("MISSING_CITATION", "citation section path is required")
        _nonempty_text(self.quote, "citation quote")

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceVersionId": self.source_version_id,
            "sourceSha256": self.source_sha256,
            "location": self.location.as_dict(),
            "sectionPath": list(self.section_path),
            "quote": self.quote,
        }


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    kind: str
    text: str
    classification: str
    confidence: float
    source_citation: SourceCitation
    review_status: str = "pending"

    def __post_init__(self) -> None:
        _uuid(self.candidate_id, "candidate id")
        if self.kind not in CANDIDATE_KINDS:
            raise ExtractionContractError("INVALID_CANDIDATE_KIND", "invalid candidate kind")
        _nonempty_text(self.text, "candidate text")
        if self.classification not in CLASSIFICATIONS:
            raise ExtractionContractError("INVALID_CLASSIFICATION", "invalid candidate classification")
        if self.review_status not in REVIEW_STATUSES:
            raise ExtractionContractError("INVALID_REVIEW_STATUS", "invalid review status")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)) or not 0 <= self.confidence <= 1:
            raise ExtractionContractError("INVALID_CONFIDENCE", "confidence must be between 0 and 1")
        if not isinstance(self.source_citation, SourceCitation):
            raise ExtractionContractError("MISSING_CITATION", "candidate source citation is required")

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "kind": self.kind,
            "text": self.text,
            "classification": self.classification,
            "confidence": self.confidence,
            "sourceCitation": self.source_citation.as_dict(),
            "review": {"status": self.review_status},
        }


def stable_candidate_id(
    source_version_id: str,
    kind: str,
    classification: str,
    text: str,
    citation: SourceCitation,
) -> str:
    """Return a UUID5 derived solely from immutable source identity and content."""
    payload = {
        "sourceVersionId": source_version_id,
        "kind": kind,
        "classification": classification,
        "text": text,
        "location": citation.location.as_dict(),
        "sectionPath": list(citation.section_path),
    }
    name = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(uuid5(UUID("8f9f4b3f-4f5c-5dc9-bf4a-93a5960f6b27"), name))
