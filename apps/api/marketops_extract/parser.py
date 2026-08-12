"""Dependency-free runtime parser for approved proposal objects.

The parser accepts immutable bytes, never filesystem paths, and returns only block
shapes admitted by the M1-02 extraction contract. Unsupported constructs are
reported as warnings so the preparation layer can fail closed.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from apps.api.marketops_extract.contract import ExtractionContractError, ParserBlock
from apps.api.marketops_import.service import MAX_FILE_SIZE_BYTES


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = lambda name: f"{{{W_NS}}}{name}"
NS = {"w": W_NS, "m": M_NS}
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_TEXT_MEDIA_TYPES = frozenset({"text/markdown", "text/plain"})
_MAX_DOCX_ENTRIES = 2048
_MAX_EXPANDED_DOCX_BYTES = MAX_FILE_SIZE_BYTES * 4
MAX_PARSER_BLOCKS = 5000
MAX_PARSER_TABLE_CELLS = 5000
MAX_PARSER_WARNINGS = 100
MAX_TEXT_LINES = 20000
MAX_TABLE_COLUMNS = 256
MAX_BLOCK_TEXT_CHARS = 100000
MAX_DOCX_XML_BYTES = 10 * 1024 * 1024
_UNSUPPORTED_DOCX_CONTENT = (
    ("drawing", "drawings_present"),
    ("pict", "legacy_drawings_present"),
    ("object", "embedded_objects_present"),
    ("hyperlink", "hyperlinks_present"),
    ("fldSimple", "fields_present"),
    ("fldChar", "fields_present"),
    ("instrText", "fields_present"),
    ("sdt", "structured_document_tags_present"),
    ("footnoteReference", "footnote_references_present"),
    ("endnoteReference", "endnote_references_present"),
    ("commentReference", "comment_references_present"),
    ("sym", "symbols_present"),
)


class RuntimeParseFailure(RuntimeError):
    """Stable parser failure that never includes source content or a path."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ParserWarning:
    code: str


@dataclass(frozen=True)
class RuntimeParserResult:
    document_format: str
    size_bytes: int
    source_sha256: str
    blocks: tuple[ParserBlock, ...]
    warnings: tuple[ParserWarning, ...]


class RuntimeProposalParser:
    """Parse the narrow approved-proposal formats without third-party code."""

    async def parse(
        self, *, payload: bytes, filename: str, media_type: str
    ) -> RuntimeParserResult:
        await asyncio.sleep(0)
        return await asyncio.to_thread(
            self._parse_sync,
            payload=payload,
            filename=filename,
            media_type=media_type,
        )

    def _parse_sync(
        self, *, payload: bytes, filename: str, media_type: str
    ) -> RuntimeParserResult:
        if not isinstance(payload, bytes) or not payload:
            raise RuntimeParseFailure("INVALID_DOCUMENT", "proposal content is empty")
        if len(payload) > MAX_FILE_SIZE_BYTES:
            raise RuntimeParseFailure("INVALID_DOCUMENT", "proposal content is too large")
        document_format = self._document_format(filename, media_type)
        try:
            if document_format == "markdown":
                raw_blocks, warning_codes = self._parse_markdown(payload)
            elif document_format == "text":
                raw_blocks, warning_codes = self._parse_plain_text(payload)
            else:
                raw_blocks, warning_codes = self._parse_docx(payload)
            self._validate_output_limits(raw_blocks, warning_codes)
            blocks = tuple(ParserBlock.from_mapping(block) for block in raw_blocks)
        except RuntimeParseFailure:
            raise
        except ExtractionContractError:
            raise RuntimeParseFailure(
                "INVALID_PARSER_OUTPUT", "proposal parser produced invalid blocks"
            ) from None
        except Exception:
            raise RuntimeParseFailure(
                "PARSER_FAILED", "proposal parsing failed"
            ) from None
        return RuntimeParserResult(
            document_format=document_format,
            size_bytes=len(payload),
            source_sha256=hashlib.sha256(payload).hexdigest(),
            blocks=blocks,
            warnings=tuple(ParserWarning(code) for code in warning_codes),
        )

    @staticmethod
    def _document_format(filename: str, media_type: str) -> str:
        if not isinstance(filename, str) or not filename.strip():
            raise RuntimeParseFailure("UNSUPPORTED_FORMAT", "proposal filename is invalid")
        if not isinstance(media_type, str) or not media_type.strip():
            raise RuntimeParseFailure("UNSUPPORTED_FORMAT", "proposal media type is invalid")
        match = re.search(r"\.([A-Za-z0-9]+)$", filename.strip())
        extension = match.group(1).lower() if match else ""
        normalized_media = media_type.strip().lower()
        if extension in {"md", "markdown"} and normalized_media in _TEXT_MEDIA_TYPES:
            return "markdown"
        if extension == "txt" and normalized_media == "text/plain":
            return "text"
        if extension == "docx" and normalized_media == DOCX_MEDIA_TYPE:
            return "docx"
        raise RuntimeParseFailure(
            "UNSUPPORTED_FORMAT", "proposal format and media type are not supported"
        )

    @classmethod
    def _parse_markdown(cls, payload: bytes) -> tuple[list[dict], list[str]]:
        text = cls._decode_text(payload)
        cls._validate_text_line_count(text)
        lines = text.splitlines()
        blocks: list[dict] = []
        warnings: list[str] = []
        sections: dict[int, str] = {}
        table_cell_count = 0
        index = 0
        while index < len(lines):
            line = lines[index]
            line_number = index + 1
            if not line.strip():
                index += 1
                continue

            fence = re.match(r"^\s*(`{3,}|~{3,})", line)
            if fence:
                cls._append_warning(warnings, "unsupported_markdown_code_fence")
                marker = fence.group(1)
                index += 1
                closed = False
                while index < len(lines):
                    if re.match(
                        rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$",
                        lines[index],
                    ):
                        closed = True
                        index += 1
                        break
                    index += 1
                if not closed:
                    cls._append_warning(warnings, "unclosed_markdown_code_fence")
                continue

            heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if heading:
                level = len(heading.group(1))
                heading_text = heading.group(2).strip()
                cls._set_heading(sections, level, heading_text)
                cls._append_block(
                    blocks,
                    {
                        "kind": "heading",
                        "level": level,
                        "text": heading_text,
                        "sectionPath": cls._section_snapshot(sections),
                        "location": cls._line_location(line_number, line_number),
                    },
                )
                index += 1
                continue

            if (
                index + 1 < len(lines)
                and "|" in line
                and cls._is_markdown_separator(lines[index + 1])
            ):
                table, next_index, table_warnings, observed_cells = cls._markdown_table(
                    lines,
                    index,
                    sections,
                    MAX_PARSER_TABLE_CELLS - table_cell_count,
                )
                cls._extend_warnings(warnings, table_warnings)
                table_cell_count += observed_cells
                if table is not None:
                    cls._append_block(blocks, table)
                index = next_index
                continue

            list_item = re.match(r"^\s*(?:[-+*]|\d+[.)])\s+(.+?)\s*$", line)
            if list_item:
                cls._append_block(
                    blocks,
                    {
                        "kind": "paragraph",
                        "text": list_item.group(1).strip(),
                        "sectionPath": cls._section_snapshot(sections),
                        "location": cls._line_location(line_number, line_number),
                    },
                )
                index += 1
                continue

            if re.match(r"^\s*(?:>|<[^>]+>|!\[)", line):
                cls._append_warning(warnings, "unsupported_markdown_block")
                index += 1
                continue

            start = line_number
            paragraph_lines = [line.strip()]
            paragraph_chars = len(paragraph_lines[0])
            index += 1
            while index < len(lines):
                candidate = lines[index]
                if (
                    not candidate.strip()
                    or re.match(r"^(#{1,6})\s+", candidate)
                    or re.match(r"^\s*(`{3,}|~{3,})", candidate)
                    or re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", candidate)
                ):
                    break
                if (
                    index + 1 < len(lines)
                    and "|" in candidate
                    and cls._is_markdown_separator(lines[index + 1])
                ):
                    break
                normalized_line = candidate.strip()
                paragraph_chars += 1 + len(normalized_line)
                if paragraph_chars > MAX_BLOCK_TEXT_CHARS:
                    raise RuntimeParseFailure(
                        "DOCUMENT_LIMIT_EXCEEDED", "proposal paragraph is too long"
                    )
                paragraph_lines.append(normalized_line)
                index += 1
            cls._append_block(
                blocks,
                {
                    "kind": "paragraph",
                    "text": "\n".join(paragraph_lines),
                    "sectionPath": cls._section_snapshot(sections),
                    "location": cls._line_location(start, index),
                },
            )
        return blocks, warnings

    @classmethod
    def _markdown_table(
        cls,
        lines: list[str],
        start_index: int,
        sections: dict[int, str],
        remaining_cells: int,
    ) -> tuple[dict | None, int, list[str], int]:
        warnings: list[str] = []
        headers = cls._split_markdown_row(lines[start_index])
        if len(headers) > MAX_TABLE_COLUMNS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal table has too many columns"
            )
        index = start_index + 2
        rows: list[dict] = []
        observed_cells = 0
        valid = bool(headers) and all(headers) and len(set(headers)) == len(headers)
        if not valid:
            cls._append_warning(warnings, "invalid_markdown_table_header")
        while index < len(lines) and lines[index].strip() and "|" in lines[index]:
            values = cls._split_markdown_row(lines[index])
            observed_cells += len(values)
            if observed_cells > remaining_cells:
                raise RuntimeParseFailure(
                    "DOCUMENT_LIMIT_EXCEEDED", "proposal has too many table cells"
                )
            row_line = index + 1
            if len(values) != len(headers) or any(not value for value in values):
                valid = False
                cls._append_warning(warnings, "invalid_markdown_table_row")
            if len(values) == len(headers) and all(values):
                rows.append(
                    {
                        "rowNumber": row_line,
                        "cells": [
                            {
                                "column": header,
                                "value": values[column],
                                "location": {
                                    "kind": "markdown_table_cell",
                                    "line": row_line,
                                    "columnIndex": column + 1,
                                },
                            }
                            for column, header in enumerate(headers)
                        ],
                    }
                )
            index += 1
        if not rows:
            valid = False
            cls._append_warning(warnings, "empty_markdown_table")
        if not valid:
            return None, index, warnings, observed_cells
        return (
            {
                "kind": "table",
                "columns": headers,
                "rows": rows,
                "headerRowExplicit": True,
                "sectionPath": cls._section_snapshot(sections),
                "location": cls._line_location(start_index + 1, index),
            },
            index,
            warnings,
            observed_cells,
        )

    @classmethod
    def _parse_plain_text(cls, payload: bytes) -> tuple[list[dict], list[str]]:
        text = cls._decode_text(payload)
        cls._validate_text_line_count(text)
        blocks: list[dict] = []
        sections: dict[int, str] = {}
        for line_number, raw in enumerate(text.splitlines(), start=1):
            value = raw.strip()
            if not value:
                continue
            heading = cls._plain_heading(value)
            if heading is not None:
                cls._set_heading(sections, 1, heading)
                cls._append_block(
                    blocks,
                    {
                        "kind": "heading",
                        "level": 1,
                        "text": heading,
                        "sectionPath": cls._section_snapshot(sections),
                        "location": cls._line_location(line_number, line_number),
                    },
                )
                continue
            item = re.sub(r"^(?:[-+*]|\d+[.)])\s+", "", value).strip()
            cls._append_block(
                blocks,
                {
                    "kind": "paragraph",
                    "text": item,
                    "sectionPath": cls._section_snapshot(sections),
                    "location": cls._line_location(line_number, line_number),
                },
            )
        return blocks, []

    @staticmethod
    def _plain_heading(value: str) -> str | None:
        stripped = value.rstrip(":\uff1a").strip()
        normalized = stripped.casefold()
        exact = {
            "deliverable",
            "deliverables",
            "milestone",
            "milestones",
            "constraint",
            "constraints",
            "assumption",
            "assumptions",
            "\u4ea4\u4ed8\u7269",
            "\u91cc\u7a0b\u7891",
            "\u7ea6\u675f",
            "\u9650\u5236\u6761\u4ef6",
            "\u5047\u8bbe",
        }
        if normalized in exact:
            return stripped
        return None

    @classmethod
    def _parse_docx(cls, payload: bytes) -> tuple[list[dict], list[str]]:
        try:
            archive = ZipFile(io.BytesIO(payload))
        except BadZipFile:
            raise RuntimeParseFailure("INVALID_DOCUMENT", "proposal DOCX is invalid") from None
        with archive:
            entries = archive.infolist()
            entry_names = [entry.filename for entry in entries]
            names = set(entry_names)
            if "word/document.xml" not in names:
                raise RuntimeParseFailure("INVALID_DOCUMENT", "proposal DOCX is invalid")
            if (
                len(entries) > _MAX_DOCX_ENTRIES
                or len(names) != len(entry_names)
                or any(cls._unsafe_archive_name(name) for name in entry_names)
                or any(entry.flag_bits & 0x1 for entry in entries)
                or sum(entry.file_size for entry in entries) > _MAX_EXPANDED_DOCX_BYTES
            ):
                raise RuntimeParseFailure("INVALID_DOCUMENT", "proposal DOCX is unsafe")
            package_warnings = cls._docx_package_warnings(names)
            root = cls._read_xml(archive, "word/document.xml")
            styles = cls._load_docx_styles(archive, names)

        body = root.find("w:body", NS)
        if body is None:
            raise RuntimeParseFailure("INVALID_DOCUMENT", "proposal DOCX has no body")
        business_block_count = sum(
            child.tag in {W("p"), W("tbl")} for child in body
        )
        if business_block_count > MAX_PARSER_BLOCKS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal has too many document blocks"
            )
        warnings = package_warnings
        for tag, code in (
            ("ins", "tracked_insertions_present"),
            ("del", "tracked_deletions_present"),
            ("txbxContent", "text_boxes_present"),
        ):
            if root.find(f".//w:{tag}", NS) is not None:
                cls._append_warning(warnings, code)
        for tag, code in _UNSUPPORTED_DOCX_CONTENT:
            if root.find(f".//w:{tag}", NS) is not None and code not in warnings:
                cls._append_warning(warnings, code)
        if (
            root.find(".//m:oMath", NS) is not None
            or root.find(".//m:oMathPara", NS) is not None
        ):
            cls._append_warning(warnings, "office_math_present")

        blocks: list[dict] = []
        sections: dict[int, str] = {}
        paragraph_index = 0
        table_index = 0
        table_cell_count = 0
        for body_index, child in enumerate(list(body), start=1):
            if child.tag == W("p"):
                paragraph_index += 1
                text = cls._paragraph_text(child)
                if not text:
                    continue
                if len(text) > MAX_BLOCK_TEXT_CHARS:
                    raise RuntimeParseFailure(
                        "DOCUMENT_LIMIT_EXCEEDED", "proposal paragraph is too long"
                    )
                style_node = child.find("w:pPr/w:pStyle", NS)
                style_id = style_node.get(W("val")) if style_node is not None else None
                style = styles.get(style_id or "")
                level = cls._heading_level(style_id, style)
                kind = "heading" if level is not None else "paragraph"
                if level is not None:
                    cls._set_heading(sections, level, text)
                block = {
                    "kind": kind,
                    "text": text,
                    "sectionPath": cls._section_snapshot(sections),
                    "location": {
                        "kind": "docx_paragraph",
                        "part": "word/document.xml",
                        "bodyIndex": body_index,
                        "paragraph": paragraph_index,
                    },
                }
                if style_id:
                    block["style"] = {
                        "id": style_id,
                        "name": (style or {}).get("name", style_id),
                    }
                if level is not None:
                    block["level"] = level
                cls._append_block(blocks, block)
            elif child.tag == W("tbl"):
                table_index += 1
                table, table_warnings, observed_cells = cls._docx_table(
                    child,
                    body_index,
                    table_index,
                    sections,
                    MAX_PARSER_TABLE_CELLS - table_cell_count,
                )
                cls._extend_warnings(warnings, table_warnings)
                table_cell_count += observed_cells
                if table is not None:
                    cls._append_block(blocks, table)
            elif child.tag != W("sectPr"):
                cls._append_warning(warnings, "unsupported_docx_body_element")
        if not blocks and not warnings:
            raise RuntimeParseFailure("INVALID_DOCUMENT", "proposal DOCX has no content")
        return blocks, warnings

    @staticmethod
    def _unsafe_archive_name(name: str) -> bool:
        if not name or "\\" in name:
            return True
        path = PurePosixPath(name)
        return (
            path.is_absolute()
            or ".." in path.parts
            or re.match(r"^[A-Za-z]:/", name) is not None
        )

    @staticmethod
    def _docx_package_warnings(names: set[str]) -> list[str]:
        checks = (
            (lambda name: name.startswith("word/header") and name.endswith(".xml"), "headers_present"),
            (lambda name: name.startswith("word/footer") and name.endswith(".xml"), "footers_present"),
            (lambda name: name == "word/footnotes.xml", "footnotes_present"),
            (lambda name: name == "word/endnotes.xml", "endnotes_present"),
            (lambda name: name.startswith("word/comments"), "comments_present"),
            (lambda name: name.startswith("word/media/"), "media_present"),
            (lambda name: name.startswith("word/embeddings/"), "embedded_objects_present"),
            (lambda name: name.startswith("word/charts/"), "charts_present"),
            (lambda name: name.startswith("word/diagrams/"), "diagrams_present"),
            (lambda name: name.startswith("word/activeX/"), "active_content_present"),
        )
        return [code for predicate, code in checks if any(predicate(name) for name in names)]

    @classmethod
    def _docx_table(
        cls,
        table: ElementTree.Element,
        body_index: int,
        table_index: int,
        sections: dict[int, str],
        remaining_cells: int,
    ) -> tuple[dict | None, list[str], int]:
        warnings: list[str] = []
        if table.find(".//w:tbl", NS) is not None:
            return None, ["nested_docx_table"], 0
        table_rows = table.findall("w:tr", NS)
        if not table_rows or table_rows[0].find("w:trPr/w:tblHeader", NS) is None:
            return None, ["docx_table_header_not_explicit"], 0
        if len(table_rows) > MAX_PARSER_TABLE_CELLS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal table has too many rows"
            )
        raw_rows: list[list[str]] = []
        observed_cells = 0
        for row in table_rows:
            row_values: list[str] = []
            cells: list[ElementTree.Element] = []
            for cell in row.iterfind("w:tc", NS):
                cells.append(cell)
                if len(cells) > MAX_TABLE_COLUMNS:
                    raise RuntimeParseFailure(
                        "DOCUMENT_LIMIT_EXCEEDED", "proposal table has too many columns"
                    )
            if not cells:
                cls._append_warning(warnings, "invalid_docx_table_row")
                return None, warnings, observed_cells
            observed_cells += len(cells)
            if observed_cells > remaining_cells:
                raise RuntimeParseFailure(
                    "DOCUMENT_LIMIT_EXCEEDED", "proposal has too many table cells"
                )
            for cell in cells:
                if (
                    cell.find("w:tcPr/w:gridSpan", NS) is not None
                    or cell.find("w:tcPr/w:vMerge", NS) is not None
                ):
                    cls._append_warning(warnings, "merged_docx_table_cell")
                value = cls._docx_cell_text(cell)
                if len(value) > MAX_BLOCK_TEXT_CHARS:
                    raise RuntimeParseFailure(
                        "DOCUMENT_LIMIT_EXCEEDED", "proposal table cell is too long"
                    )
                row_values.append(value)
            raw_rows.append(row_values)
        headers = raw_rows[0]
        valid = bool(headers) and all(headers) and len(set(headers)) == len(headers)
        if not valid:
            cls._append_warning(warnings, "invalid_docx_table_header")
        rows: list[dict] = []
        for row_number, values in enumerate(raw_rows[1:], start=2):
            if len(values) != len(headers) or any(not value for value in values):
                valid = False
                cls._append_warning(warnings, "invalid_docx_table_row")
                continue
            rows.append(
                {
                    "rowNumber": row_number,
                    "cells": [
                        {
                            "column": header,
                            "value": values[column],
                            "location": {
                                "kind": "docx_table_cell",
                                "part": "word/document.xml",
                                "table": table_index,
                                "row": row_number,
                                "column": column + 1,
                            },
                        }
                        for column, header in enumerate(headers)
                    ],
                }
            )
        if not rows:
            valid = False
            cls._append_warning(warnings, "empty_docx_table")
        if warnings or not valid:
            return None, warnings, observed_cells
        return (
            {
                "kind": "table",
                "columns": headers,
                "rows": rows,
                "headerRowExplicit": True,
                "sectionPath": cls._section_snapshot(sections),
                "location": {
                    "kind": "docx_table",
                    "part": "word/document.xml",
                    "bodyIndex": body_index,
                    "table": table_index,
                },
            },
            warnings,
            observed_cells,
        )

    @staticmethod
    def _read_xml(archive: ZipFile, name: str) -> ElementTree.Element:
        try:
            if archive.getinfo(name).file_size > MAX_DOCX_XML_BYTES:
                raise RuntimeParseFailure(
                    "DOCUMENT_LIMIT_EXCEEDED", "proposal DOCX XML is too large"
                )
            payload = archive.read(name)
            if len(payload) > MAX_DOCX_XML_BYTES:
                raise RuntimeParseFailure(
                    "DOCUMENT_LIMIT_EXCEEDED", "proposal DOCX XML is too large"
                )
            return ElementTree.fromstring(payload)
        except RuntimeParseFailure:
            raise
        except (KeyError, ElementTree.ParseError, RuntimeError):
            raise RuntimeParseFailure("INVALID_DOCUMENT", "proposal DOCX XML is invalid") from None

    @classmethod
    def _load_docx_styles(
        cls, archive: ZipFile, names: set[str]
    ) -> dict[str, dict[str, str | int | None]]:
        if "word/styles.xml" not in names:
            return {}
        root = cls._read_xml(archive, "word/styles.xml")
        styles: dict[str, dict[str, str | int | None]] = {}
        for style in root.findall("w:style", NS):
            style_id = style.get(W("styleId"))
            if not style_id:
                continue
            name = style.find("w:name", NS)
            outline = style.find("w:pPr/w:outlineLvl", NS)
            try:
                outline_level = (
                    int(outline.get(W("val"))) if outline is not None else None
                )
            except (TypeError, ValueError):
                raise RuntimeParseFailure(
                    "INVALID_DOCUMENT", "proposal DOCX styles are invalid"
                ) from None
            styles[style_id] = {
                "name": name.get(W("val")) if name is not None else style_id,
                "outlineLevel": outline_level,
            }
        return styles

    @staticmethod
    def _heading_level(
        style_id: str | None, style: dict[str, str | int | None] | None
    ) -> int | None:
        if style and isinstance(style.get("outlineLevel"), int):
            level = int(style["outlineLevel"]) + 1
            return level if 1 <= level <= 6 else None
        for value in (style_id or "", str((style or {}).get("name", ""))):
            match = re.search(r"heading\s*([1-6])", value, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _paragraph_text(paragraph: ElementTree.Element) -> str:
        parts: list[str] = []
        text_chars = 0
        for node in paragraph.iter():
            part = ""
            if node.tag == W("t"):
                part = node.text or ""
            elif node.tag == W("tab"):
                part = "\t"
            elif node.tag in {W("br"), W("cr")}:
                part = "\n"
            elif node.tag == W("noBreakHyphen"):
                part = "-"
            elif node.tag == W("softHyphen"):
                part = "\u00ad"
            if part:
                text_chars += len(part)
                if text_chars > MAX_BLOCK_TEXT_CHARS:
                    raise RuntimeParseFailure(
                        "DOCUMENT_LIMIT_EXCEEDED", "proposal paragraph is too long"
                    )
                parts.append(part)
        return "".join(parts).strip()

    @classmethod
    def _docx_cell_text(cls, cell: ElementTree.Element) -> str:
        paragraphs: list[str] = []
        text_chars = 0
        for item in cell.findall("w:p", NS):
            value = cls._paragraph_text(item)
            if not value:
                continue
            text_chars += len(value) + (1 if paragraphs else 0)
            if text_chars > MAX_BLOCK_TEXT_CHARS:
                raise RuntimeParseFailure(
                    "DOCUMENT_LIMIT_EXCEEDED", "proposal table cell is too long"
                )
            paragraphs.append(value)
        return "\n".join(paragraphs)

    @staticmethod
    def _decode_text(payload: bytes) -> str:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise RuntimeParseFailure(
                "INVALID_ENCODING", "proposal text must be UTF-8"
            ) from None
        if not text.strip():
            raise RuntimeParseFailure("INVALID_DOCUMENT", "proposal text is empty")
        return text

    @staticmethod
    def _validate_text_line_count(text: str) -> None:
        line_count = 1
        line_length = 0
        index = 0
        while index < len(text):
            character = text[index]
            if character == "\r":
                if index + 1 < len(text) and text[index + 1] == "\n":
                    index += 1
                line_count += 1
                line_length = 0
            elif character in "\n\v\f\x1c\x1d\x1e\x85\u2028\u2029":
                line_count += 1
                line_length = 0
            else:
                line_length += 1
            if line_count > MAX_TEXT_LINES:
                raise RuntimeParseFailure(
                    "DOCUMENT_LIMIT_EXCEEDED", "proposal has too many text lines"
                )
            if line_length > MAX_BLOCK_TEXT_CHARS:
                raise RuntimeParseFailure(
                    "DOCUMENT_LIMIT_EXCEEDED", "proposal text line is too long"
                )
            index += 1

    @staticmethod
    def _validate_output_limits(blocks: list[dict], warnings: list[str]) -> None:
        if len(blocks) > MAX_PARSER_BLOCKS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal has too many parser blocks"
            )
        if len(warnings) > MAX_PARSER_WARNINGS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal has too many parser warnings"
            )
        table_cells = sum(
            len(row.get("cells", ()))
            for block in blocks
            if block.get("kind") == "table"
            for row in block.get("rows", ())
        )
        if table_cells > MAX_PARSER_TABLE_CELLS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal has too many table cells"
            )

    @staticmethod
    def _append_block(blocks: list[dict], block: dict) -> None:
        if len(blocks) >= MAX_PARSER_BLOCKS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal has too many parser blocks"
            )
        text = block.get("text")
        if isinstance(text, str) and len(text) > MAX_BLOCK_TEXT_CHARS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal block text is too long"
            )
        blocks.append(block)

    @staticmethod
    def _append_warning(warnings: list[str], code: str) -> None:
        if len(warnings) >= MAX_PARSER_WARNINGS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal has too many parser warnings"
            )
        warnings.append(code)

    @classmethod
    def _extend_warnings(cls, warnings: list[str], codes: Iterable[str]) -> None:
        for code in codes:
            cls._append_warning(warnings, code)

    @staticmethod
    def _section_snapshot(sections: dict[int, str]) -> list[str]:
        return [sections[level] for level in sorted(sections)]

    @staticmethod
    def _set_heading(sections: dict[int, str], level: int, text: str) -> None:
        for existing in tuple(sections):
            if existing >= level:
                del sections[existing]
        sections[level] = text

    @staticmethod
    def _line_location(start: int, end: int) -> dict[str, int | str]:
        return {"kind": "line_range", "startLine": start, "endLine": end}

    @classmethod
    def _split_markdown_row(cls, line: str) -> list[str]:
        value = line.strip()
        if value.startswith("|"):
            value = value[1:]
        if value.endswith("|") and not value.endswith("\\|"):
            value = value[:-1]
        cells: list[str] = []
        current: list[str] = []
        escaped = False
        for character in value:
            if escaped:
                current.append(character)
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "|":
                cells.append("".join(current).strip())
                if len(cells) > MAX_TABLE_COLUMNS:
                    raise RuntimeParseFailure(
                        "DOCUMENT_LIMIT_EXCEEDED", "proposal table has too many columns"
                    )
                current = []
            else:
                current.append(character)
        if escaped:
            current.append("\\")
        cells.append("".join(current).strip())
        if len(cells) > MAX_TABLE_COLUMNS:
            raise RuntimeParseFailure(
                "DOCUMENT_LIMIT_EXCEEDED", "proposal table has too many columns"
            )
        return cells

    @classmethod
    def _is_markdown_separator(cls, line: str) -> bool:
        cells = cls._split_markdown_row(line)
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def warning_codes(warnings: Iterable[ParserWarning]) -> tuple[str, ...]:
    """Return stable warning codes without exposing source locations or content."""
    return tuple(warning.code for warning in warnings)
