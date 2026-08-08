#!/usr/bin/env python3
"""Deterministic acceptance check for the M0-02 parser spike."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

from document_parser_spike import ParseFailure, parse_document


ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "validation" / "results" / "m0-02-document-parser.json"
FIXTURE_DIR = ROOT / "validation" / "fixtures" / "document-parser-spike-001"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def table_cell(table, row_number, column):
    row = next(row for row in table["rows"] if row["rowNumber"] == row_number)
    return next(cell for cell in row["cells"] if cell["column"] == column)


def summarize(relative_path):
    parsed = parse_document(ROOT / relative_path, display_path=relative_path)
    return parsed, {
        "path": relative_path,
        "format": parsed["source"]["format"],
        "sha256": parsed["source"]["sha256"],
        "blockCounts": dict(sorted(Counter(block["kind"] for block in parsed["blocks"]).items())),
        "warningCodes": sorted(warning["code"] for warning in parsed["warnings"]),
    }


def expected_failure(path, code):
    try:
        parse_document(path)
    except ParseFailure as error:
        require(error.code == code, f"expected {code}, got {error.code}")
        return code
    raise AssertionError(f"expected {code} for {path}")


def build_result():
    cases = []

    proposal, summary = summarize("validation/fixtures/synthetic-b2b-event-001/proposal.md")
    cases.append(summary)
    headings = [block for block in proposal["blocks"] if block["kind"] == "heading"]
    require(any(block["text"] == "1. 已确认输入" and block["location"]["startLine"] == 5 for block in headings), "Markdown heading coordinate changed")
    proposal_tables = [block for block in proposal["blocks"] if block["kind"] == "table"]
    require(proposal_tables[0]["sectionPath"] == ["凌川工业增长峰会方案", "1. 已确认输入"], "Markdown section path missing")
    proposal_cell = table_cell(proposal_tables[0], 9, "来源编号")
    require(proposal_cell["value"] == "SRC-01", "Markdown table cell extraction failed")
    summary["verifiedCoordinate"] = {
        "value": proposal_cell["value"],
        "sectionPath": proposal_tables[0]["sectionPath"],
        "location": proposal_cell["location"],
    }

    public_case, summary = summarize("validation/public-cases/dreamforce-2024.md")
    cases.append(summary)
    fact = next(block for block in public_case["blocks"] if "FACT-DF-03" in block.get("text", ""))
    require(fact["location"]["startLine"] > 1, "Public-case fact lacks a line coordinate")
    require("已确认的公开描述" in fact["sectionPath"], "Public-case fact lost its evidence section")
    summary["verifiedCoordinate"] = {
        "value": "FACT-DF-03",
        "sectionPath": fact["sectionPath"],
        "location": fact["location"],
    }

    schedule, summary = summarize("validation/fixtures/synthetic-b2b-event-001/schedule-baseline.csv")
    cases.append(summary)
    schedule_table = schedule["blocks"][0]
    require(len(schedule_table["rows"]) == 15, "CSV task count changed")
    locked = table_cell(schedule_table, 10, "task_id")
    require(locked["value"] == "TASK-090", "CSV row coordinate changed")
    require(locked["location"] == {"kind": "csv_cell", "row": 10, "columnIndex": 1, "columnName": "task_id"}, "CSV cell coordinate changed")
    summary["verifiedCoordinate"] = {
        "value": locked["value"],
        "sectionPath": schedule_table["sectionPath"],
        "location": locked["location"],
    }

    docx_path = "validation/fixtures/document-parser-spike-001/ai-event-brief.docx"
    docx, summary = summarize(docx_path)
    cases.append(summary)
    truth = json.loads((FIXTURE_DIR / "ground-truth.json").read_text(encoding="utf-8"))
    docx_headings = [block["text"] for block in docx["blocks"] if block["kind"] == "heading"]
    for expected in truth["expectedHeadings"]:
        require(expected in docx_headings, f"DOCX heading missing: {expected}")
    docx_tables = [block for block in docx["blocks"] if block["kind"] == "table"]
    require(len(docx_tables) == truth["expectedTableCount"], "DOCX table count changed")
    require(all(table["headerRowExplicit"] for table in docx_tables), "DOCX header-row semantics were not retained")
    source_cell = table_cell(docx_tables[0], 3, "编号")
    require(source_cell["value"] == truth["coordinateChecks"]["sourceId"], "DOCX source cell changed")
    require(source_cell["location"]["table"] == 1 and source_cell["location"]["row"] == 3 and source_cell["location"]["column"] == 1, "DOCX source coordinate changed")
    require(docx_tables[1]["sectionPath"] == ["3. 变更审批"], "DOCX table section path changed")
    change_cell = table_cell(docx_tables[1], 2, "变更编号")
    require(change_cell["value"] == truth["coordinateChecks"]["changeId"], "DOCX change cell changed")
    require(change_cell["location"]["table"] == 2 and change_cell["location"]["row"] == 2 and change_cell["location"]["column"] == 1, "DOCX change coordinate changed")
    require(not docx["warnings"], "Synthetic DOCX should parse without warnings")
    summary["verifiedCoordinate"] = {
        "value": change_cell["value"],
        "sectionPath": docx_tables[1]["sectionPath"],
        "location": change_cell["location"],
    }

    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        empty = temp / "empty.docx"
        empty.write_bytes(b"")
        unsupported = temp / "sample.pdf"
        unsupported.write_bytes(b"%PDF-invalid")
        corrupt = temp / "corrupt.docx"
        corrupt.write_bytes(b"not-a-zip")
        failure_codes = [
            expected_failure(empty, "empty_file"),
            expected_failure(unsupported, "unsupported_format"),
            expected_failure(corrupt, "invalid_docx"),
        ]

    return {
        "schemaVersion": 1,
        "spikeId": "M0-02",
        "cases": cases,
        "failureCodesVerified": failure_codes,
        "explicitlyUnsupported": [
            "pdf_and_ocr",
            "images_and_charts",
            "headers_footers_footnotes_endnotes",
            "comments_and_full_revision_semantics",
            "page_number_coordinates",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = build_result()
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RESULT_PATH.open("w", encoding="utf-8", newline="\n") as output:
            output.write(serialized)
        print(f"Wrote {RESULT_PATH.relative_to(ROOT)}")
    else:
        require(RESULT_PATH.exists(), "committed parser result is missing; run with --write")
        require(RESULT_PATH.read_text(encoding="utf-8") == serialized, "parser result is stale; run with --write")
        print(f"Document parser spike passed: {len(result['cases'])} cases and {len(result['failureCodesVerified'])} failure paths.")


if __name__ == "__main__":
    main()
