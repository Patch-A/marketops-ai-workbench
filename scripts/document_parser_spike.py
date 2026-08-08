#!/usr/bin/env python3
"""Dependency-free baseline parser for the M0-02 document contract.

This is deliberately a narrow spike, not the production ingestion service. It
parses the public validation formats while binding every location to an
immutable source hash.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import BadZipFile, ZipFile


SCHEMA_VERSION = 1
SUPPORTED_FORMATS = {".md": "markdown", ".csv": "csv", ".docx": "docx"}
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
W = lambda name: f"{{{W_NS}}}{name}"


class ParseFailure(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict:
        result = {"code": self.code, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result


def _source_record(path: Path, document_format: str, payload: bytes) -> dict:
    return {
        "path": path.as_posix(),
        "format": document_format,
        "sizeBytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _section_snapshot(section_stack: dict[int, str]) -> list[str]:
    return [section_stack[level] for level in sorted(section_stack)]


def _set_heading(section_stack: dict[int, str], level: int, text: str) -> None:
    for existing in list(section_stack):
        if existing >= level:
            del section_stack[existing]
    section_stack[level] = text


def _split_markdown_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    cells = []
    current = []
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def _is_markdown_separator(line: str) -> bool:
    cells = _split_markdown_row(line)
    return len(cells) > 0 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _markdown_cell(value: str, line_number: int, column_index: int, column_name: str) -> dict:
    return {
        "column": column_name,
        "value": value,
        "location": {
            "kind": "markdown_table_cell",
            "line": line_number,
            "columnIndex": column_index,
        },
    }


def _parse_markdown(path: Path, payload: bytes) -> tuple[list[dict], list[dict]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ParseFailure("invalid_encoding", "Markdown input must be UTF-8.", offset=error.start) from error

    lines = text.splitlines()
    blocks: list[dict] = []
    warnings: list[dict] = []
    sections: dict[int, str] = {}
    index = 0

    while index < len(lines):
        line = lines[index]
        line_number = index + 1
        if not line.strip():
            index += 1
            continue

        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            fence = fence_match.group(1)
            start = line_number
            content = [line]
            index += 1
            while index < len(lines):
                content.append(lines[index])
                if re.match(rf"^\s*{re.escape(fence[0])}{{{len(fence)},}}\s*$", lines[index]):
                    break
                index += 1
            if index >= len(lines):
                warnings.append({"code": "unclosed_markdown_fence", "location": {"startLine": start}})
            end = min(index + 1, len(lines))
            blocks.append({
                "kind": "code",
                "text": "\n".join(content),
                "sectionPath": _section_snapshot(sections),
                "location": {"kind": "line_range", "startLine": start, "endLine": end},
            })
            index += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            level = len(heading.group(1))
            heading_text = heading.group(2).strip()
            _set_heading(sections, level, heading_text)
            blocks.append({
                "kind": "heading",
                "level": level,
                "text": heading_text,
                "sectionPath": _section_snapshot(sections),
                "location": {"kind": "line_range", "startLine": line_number, "endLine": line_number},
            })
            index += 1
            continue

        if index + 1 < len(lines) and "|" in line and _is_markdown_separator(lines[index + 1]):
            headers = _split_markdown_row(line)
            start = line_number
            index += 2
            rows = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                values = _split_markdown_row(lines[index])
                values += [""] * max(0, len(headers) - len(values))
                row_line = index + 1
                rows.append({
                    "rowNumber": row_line,
                    "cells": [
                        _markdown_cell(values[column] if column < len(values) else "", row_line, column + 1, header)
                        for column, header in enumerate(headers)
                    ],
                })
                index += 1
            blocks.append({
                "kind": "table",
                "columns": headers,
                "rows": rows,
                "sectionPath": _section_snapshot(sections),
                "location": {"kind": "line_range", "startLine": start, "endLine": index},
            })
            continue

        start = line_number
        paragraph_lines = [line.strip()]
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip() or re.match(r"^(#{1,6})\s+", candidate) or re.match(r"^\s*(`{3,}|~{3,})", candidate):
                break
            if index + 1 < len(lines) and "|" in candidate and _is_markdown_separator(lines[index + 1]):
                break
            paragraph_lines.append(candidate.strip())
            index += 1
        blocks.append({
            "kind": "paragraph",
            "text": "\n".join(paragraph_lines),
            "sectionPath": _section_snapshot(sections),
            "location": {"kind": "line_range", "startLine": start, "endLine": index},
        })

    return blocks, warnings


def _parse_csv(path: Path, payload: bytes) -> tuple[list[dict], list[dict]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ParseFailure("invalid_encoding", "CSV input must be UTF-8.", offset=error.start) from error
    try:
        reader = csv.reader(io.StringIO(text, newline=""))
        records = list(reader)
    except csv.Error as error:
        raise ParseFailure("invalid_csv", "CSV input could not be parsed.", reason=str(error)) from error
    if not records or not records[0] or not any(cell.strip() for cell in records[0]):
        raise ParseFailure("empty_table", "CSV input must contain a header row.")

    headers = records[0]
    warnings = []
    if len(set(headers)) != len(headers):
        warnings.append({"code": "duplicate_csv_headers", "location": {"row": 1}})
    rows = []
    for row_number, values in enumerate(records[1:], start=2):
        if len(values) != len(headers):
            warnings.append({
                "code": "ragged_csv_row",
                "location": {"row": row_number},
                "expectedColumns": len(headers),
                "actualColumns": len(values),
            })
        values += [""] * max(0, len(headers) - len(values))
        rows.append({
            "rowNumber": row_number,
            "cells": [
                {
                    "column": header,
                    "value": values[column] if column < len(values) else "",
                    "location": {
                        "kind": "csv_cell",
                        "row": row_number,
                        "columnIndex": column + 1,
                        "columnName": header,
                    },
                }
                for column, header in enumerate(headers)
            ],
        })
    return [{
        "kind": "table",
        "columns": headers,
        "rows": rows,
        "sectionPath": [],
        "location": {"kind": "csv_range", "startRow": 1, "endRow": len(records)},
    }], warnings


def _paragraph_text(paragraph: ET.Element) -> str:
    parts = []
    for node in paragraph.iter():
        if node.tag == W("t"):
            parts.append(node.text or "")
        elif node.tag == W("tab"):
            parts.append("\t")
        elif node.tag in {W("br"), W("cr")}:
            parts.append("\n")
        elif node.tag == W("noBreakHyphen"):
            parts.append("-")
        elif node.tag == W("softHyphen"):
            parts.append("\u00ad")
    return "".join(parts).strip()


def _load_docx_styles(archive: ZipFile) -> dict[str, dict]:
    try:
        root = ET.fromstring(archive.read("word/styles.xml"))
    except KeyError:
        return {}
    styles = {}
    for style in root.findall("w:style", NS):
        style_id = style.get(W("styleId"))
        if not style_id:
            continue
        name = style.find("w:name", NS)
        outline = style.find("w:pPr/w:outlineLvl", NS)
        styles[style_id] = {
            "name": name.get(W("val")) if name is not None else style_id,
            "outlineLevel": int(outline.get(W("val"))) if outline is not None else None,
        }
    return styles


def _heading_level(style_id: str | None, style: dict | None) -> int | None:
    if style and style.get("outlineLevel") is not None:
        return style["outlineLevel"] + 1
    for value in (style_id or "", (style or {}).get("name", "")):
        match = re.search(r"heading\s*([1-9])", value, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _docx_cell(cell: ET.Element, table_index: int, row_index: int, column_index: int, column_name: str) -> dict:
    paragraphs = [_paragraph_text(paragraph) for paragraph in cell.findall(".//w:p", NS)]
    text = "\n".join(value for value in paragraphs if value)
    grid_span = cell.find("w:tcPr/w:gridSpan", NS)
    vertical_merge = cell.find("w:tcPr/w:vMerge", NS)
    result = {
        "column": column_name,
        "value": text,
        "location": {
            "kind": "docx_table_cell",
            "part": "word/document.xml",
            "table": table_index,
            "row": row_index,
            "column": column_index,
        },
    }
    if grid_span is not None:
        result["gridSpan"] = int(grid_span.get(W("val"), "1"))
    if vertical_merge is not None:
        result["verticalMerge"] = vertical_merge.get(W("val"), "continue")
    return result


def _parse_docx(path: Path, payload: bytes) -> tuple[list[dict], list[dict]]:
    try:
        archive = ZipFile(io.BytesIO(payload))
        document_xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError) as error:
        raise ParseFailure("invalid_docx", "DOCX input is not a readable WordprocessingML package.") from error

    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as error:
        raise ParseFailure("invalid_docx_xml", "word/document.xml is not valid XML.", reason=str(error)) from error

    styles = _load_docx_styles(archive)
    body = root.find("w:body", NS)
    if body is None:
        raise ParseFailure("invalid_docx_xml", "DOCX input has no document body.")

    warnings = []
    for tag, code in (("ins", "tracked_insertions_present"), ("del", "tracked_deletions_present"), ("txbxContent", "text_boxes_present")):
        if root.find(f".//w:{tag}", NS) is not None:
            warnings.append({"code": code, "location": {"part": "word/document.xml"}})

    blocks = []
    sections: dict[int, str] = {}
    paragraph_index = 0
    table_index = 0

    for body_index, child in enumerate(list(body), start=1):
        if child.tag == W("p"):
            paragraph_index += 1
            text = _paragraph_text(child)
            if not text:
                continue
            style_node = child.find("w:pPr/w:pStyle", NS)
            style_id = style_node.get(W("val")) if style_node is not None else None
            style = styles.get(style_id or "")
            level = _heading_level(style_id, style)
            kind = "heading" if level is not None else "paragraph"
            if level is not None:
                _set_heading(sections, level, text)
            block = {
                "kind": kind,
                "text": text,
                "sectionPath": _section_snapshot(sections),
                "location": {
                    "kind": "docx_paragraph",
                    "part": "word/document.xml",
                    "bodyIndex": body_index,
                    "paragraph": paragraph_index,
                },
            }
            if style_id:
                block["style"] = {"id": style_id, "name": (style or {}).get("name", style_id)}
            if level is not None:
                block["level"] = level
            blocks.append(block)
        elif child.tag == W("tbl"):
            table_index += 1
            table_rows = child.findall("w:tr", NS)
            if child.find(".//w:tbl", NS) is not None:
                warnings.append({"code": "nested_docx_table", "location": {"table": table_index}})
            header_is_explicit = bool(table_rows and table_rows[0].find("w:trPr/w:tblHeader", NS) is not None)
            raw_rows = []
            for row_index, row in enumerate(table_rows, start=1):
                cells = row.findall("w:tc", NS)
                raw_rows.append([
                    _docx_cell(cell, table_index, row_index, column_index, f"column_{column_index}")
                    for column_index, cell in enumerate(cells, start=1)
                ])
            columns = [cell["value"] for cell in raw_rows[0]] if header_is_explicit and raw_rows else []
            start_row = 2 if header_is_explicit else 1
            rows = []
            for row_index, cells in enumerate(raw_rows[start_row - 1 :], start=start_row):
                for column_index, cell in enumerate(cells):
                    cell["column"] = columns[column_index] if column_index < len(columns) else f"column_{column_index + 1}"
                rows.append({"rowNumber": row_index, "cells": cells})
            blocks.append({
                "kind": "table",
                "columns": columns,
                "headerRowExplicit": header_is_explicit,
                "rows": rows,
                "sectionPath": _section_snapshot(sections),
                "location": {
                    "kind": "docx_table",
                    "part": "word/document.xml",
                    "bodyIndex": body_index,
                    "table": table_index,
                },
            })
        elif child.tag != W("sectPr"):
            warnings.append({
                "code": "unsupported_docx_body_element",
                "element": child.tag.rsplit("}", 1)[-1],
                "location": {"part": "word/document.xml", "bodyIndex": body_index},
            })

    archive.close()
    return blocks, warnings


def parse_document(input_path: str | Path, *, display_path: str | None = None) -> dict:
    path = Path(input_path)
    if not path.exists() or not path.is_file():
        raise ParseFailure("file_not_found", "Input file does not exist.", path=str(path))
    payload = path.read_bytes()
    if not payload:
        raise ParseFailure("empty_file", "Input file is empty.", path=str(path))
    document_format = SUPPORTED_FORMATS.get(path.suffix.lower())
    if not document_format:
        raise ParseFailure(
            "unsupported_format",
            "Supported formats are Markdown, CSV, and DOCX.",
            extension=path.suffix.lower(),
        )

    parser = {"markdown": _parse_markdown, "csv": _parse_csv, "docx": _parse_docx}[document_format]
    blocks, warnings = parser(path, payload)
    source = _source_record(path, document_format, payload)
    if display_path is not None:
        source["path"] = display_path.replace("\\", "/")
    return {"schemaVersion": SCHEMA_VERSION, "source": source, "blocks": blocks, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse a validation document into traceable blocks.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", "-o", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        result = parse_document(args.input)
    except ParseFailure as error:
        print(json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2
    serialized = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="\n") as output:
            output.write(serialized)
    else:
        sys.stdout.write(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
