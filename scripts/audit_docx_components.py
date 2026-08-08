#!/usr/bin/env python3
"""Component-level DOCX audit for environments without a renderer.

This audit checks the OOXML components that can be verified without opening
Word or LibreOffice. It is intentionally distinct from visual rendering QA.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import sys
from pathlib import Path
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W_NS, "pr": PKG_REL_NS, "ct": CT_NS}
W = lambda name: f"{{{W_NS}}}{name}"
PR = lambda name: f"{{{PKG_REL_NS}}}{name}"
CT = lambda name: f"{{{CT_NS}}}{name}"


class Audit:
    def __init__(self):
        self.checks = []
        self.errors = []
        self.warnings = []

    def pass_check(self, name, details=None):
        self.checks.append({"name": name, "status": "passed", "details": details or {}})

    def fail(self, name, message, details=None):
        self.checks.append({"name": name, "status": "failed", "message": message, "details": details or {}})
        self.errors.append({"check": name, "message": message, "details": details or {}})

    def warn(self, name, message, details=None):
        self.warnings.append({"check": name, "message": message, "details": details or {}})


def attr(node, local, namespace=W_NS):
    return node.get(f"{{{namespace}}}{local}") if node is not None else None


def direct_text(node):
    return "".join(child.text or "" for child in node.iter() if child.tag == W("t"))


def check_package(path: Path, audit: Audit):
    try:
        archive = ZipFile(path)
        bad = archive.testzip()
        if bad:
            audit.fail("zip_integrity", "CRC failure in DOCX package", {"entry": bad})
        else:
            audit.pass_check("zip_integrity", {"entries": len(archive.namelist())})
    except (BadZipFile, OSError) as error:
        audit.fail("zip_integrity", "DOCX is not a readable ZIP package", {"error": str(error)})
        return None, None

    required = {"[Content_Types].xml", "word/document.xml", "word/styles.xml", "word/settings.xml"}
    missing = sorted(required.difference(archive.namelist()))
    if missing:
        audit.fail("required_parts", "required OOXML parts are missing", {"missing": missing})
    else:
        audit.pass_check("required_parts", {"count": len(required)})

    roots = {}
    for name in archive.namelist():
        if not name.endswith(".xml") and not name.endswith(".rels"):
            continue
        try:
            roots[name] = ET.fromstring(archive.read(name))
        except ET.ParseError as error:
            audit.fail("xml_well_formed", "an OOXML part is not well formed", {"part": name, "error": str(error)})
    if not audit.errors or all(item["check"] != "xml_well_formed" for item in audit.errors):
        audit.pass_check("xml_well_formed", {"partsChecked": len(roots)})

    content_types = roots.get("[Content_Types].xml")
    document = roots.get("word/document.xml")
    styles = roots.get("word/styles.xml")
    settings = roots.get("word/settings.xml")
    if content_types is not None and document is not None:
        overrides = {item.get("PartName") for item in content_types.findall(f"{CT('Override')}")}
        audit.pass_check("content_types", {"documentOverridePresent": "/word/document.xml" in overrides}) if "/word/document.xml" in overrides else audit.fail("content_types", "document.xml override is missing")
    if settings is not None:
        audit.pass_check("settings_part", {"present": True})

    # Verify internal relationships resolve to package parts.
    relationship_errors = []
    external_relationships = []
    for name, root in roots.items():
        if not name.endswith(".rels"):
            continue
        if name.endswith("_rels/.rels"):
            source_part = ""
        elif "/_rels/" in name:
            source_part = name.replace("/_rels/", "/")[:-5]
        else:
            source_part = name[:-5]
        base = posixpath.dirname(source_part)
        for relationship in root.findall(f"{PR('Relationship')}"):
            target = relationship.get("Target", "")
            if relationship.get("TargetMode") == "External":
                external_relationships.append({"part": name, "target": target, "type": relationship.get("Type")})
                continue
            resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(base, target))
            if resolved not in archive.namelist():
                relationship_errors.append({"part": name, "target": target, "resolved": resolved})
    if relationship_errors:
        audit.fail("internal_relationships", "an internal relationship target is missing", {"items": relationship_errors})
    else:
        audit.pass_check("internal_relationships")
    if external_relationships:
        audit.fail("external_relationships", "public synthetic fixture must not load external resources", {"items": external_relationships})
    else:
        audit.pass_check("external_relationships", {"count": 0})

    prohibited_prefixes = ("word/activeX/", "word/embeddings/", "word/ctrlProps/")
    prohibited_names = {"word/vbaProject.bin"}
    active_parts = sorted(
        name for name in archive.namelist()
        if name in prohibited_names or name.startswith(prohibited_prefixes)
    )
    if active_parts:
        audit.fail("active_content", "macros, ActiveX, or embedded objects are not allowed in the public fixture", {"parts": active_parts})
    else:
        audit.pass_check("active_content", {"parts": 0})

    return archive, {"document": document, "styles": styles, "settings": settings}


def check_page_and_styles(parts, audit: Audit):
    document = parts["document"]
    styles = parts["styles"]
    sections = document.findall(".//w:sectPr", NS) if document is not None else []
    if len(sections) != 1:
        audit.fail("sections", "synthetic fixture must contain exactly one section", {"count": len(sections)})
    else:
        section = sections[0]
        page = section.find("w:pgSz", NS)
        margins = section.find("w:pgMar", NS)
        expected_page = {"w": "12240", "h": "15840"}
        expected_margins = {"top": "1440", "right": "1440", "bottom": "1440", "left": "1440"}
        actual_page = {key: attr(page, key) for key in ("w", "h")}
        actual_margins = {key: attr(margins, key) for key in expected_margins}
        if actual_page != expected_page or actual_margins != expected_margins:
            audit.fail("page_geometry", "page size or margins do not match the fixture contract", {"page": actual_page, "margins": actual_margins})
        else:
            audit.pass_check("page_geometry", {"page": actual_page, "margins": actual_margins})

    required_styles = {
        "Normal": {"name": "Normal", "font": "Calibri", "size": "22", "color": "000000", "before": "0", "after": "120", "line": "264", "lineRule": "auto"},
        "Heading1": {"name": "heading 1", "font": "Calibri", "size": "32", "color": "2E74B5", "before": "320", "after": "160", "line": "264", "lineRule": "auto"},
        "Heading2": {"name": "heading 2", "font": "Calibri", "size": "26", "color": "2E74B5", "before": "240", "after": "120", "line": "264", "lineRule": "auto"},
        "Heading3": {"name": "heading 3", "font": "Calibri", "size": "24", "color": "1F4D78", "before": "160", "after": "80", "line": "264", "lineRule": "auto"},
    }
    actual = {}
    if styles is not None:
        for style in styles.findall("w:style", NS):
            style_id = attr(style, "styleId")
            name = style.find("w:name", NS)
            if style_id in required_styles:
                fonts = style.find("w:rPr/w:rFonts", NS)
                size = style.find("w:rPr/w:sz", NS)
                color = style.find("w:rPr/w:color", NS)
                spacing = style.find("w:pPr/w:spacing", NS)
                actual[style_id] = {
                    "name": name.get(W("val")) if name is not None else None,
                    "font": attr(fonts, "ascii"),
                    "size": attr(size, "val"),
                    "color": attr(color, "val"),
                    "before": attr(spacing, "before"),
                    "after": attr(spacing, "after"),
                    "line": attr(spacing, "line"),
                    "lineRule": attr(spacing, "lineRule"),
                }
    if actual != required_styles:
        audit.fail("required_styles", "fixture styles are missing or renamed", {"actual": actual})
    else:
        audit.pass_check("required_styles", {"styles": actual})


def check_headings_tables_accessibility(parts, audit: Audit):
    document = parts["document"]
    styles = parts["styles"]
    style_levels = {}
    if styles is not None:
        for style in styles.findall("w:style", NS):
            style_id = attr(style, "styleId")
            outline = style.find("w:pPr/w:outlineLvl", NS)
            if style_id and outline is not None:
                style_levels[style_id] = int(attr(outline, "val")) + 1
    levels = []
    for paragraph in document.findall(".//w:body/w:p", NS):
        style = paragraph.find("w:pPr/w:pStyle", NS)
        level = style_levels.get(attr(style, "val")) if style is not None else None
        if level is not None:
            levels.append(level)
    skipped = [(previous, current) for previous, current in zip(levels, levels[1:]) if current > previous + 1]
    if skipped:
        audit.fail("heading_hierarchy", "heading levels skip a level", {"sequence": levels, "skips": skipped})
    else:
        audit.pass_check("heading_hierarchy", {"sequence": levels})

    tables = document.findall(".//w:body/w:tbl", NS)
    table_issues = []
    for index, table in enumerate(tables, start=1):
        rows = table.findall("w:tr", NS)
        if not rows or rows[0].find("w:trPr/w:tblHeader", NS) is None:
            table_issues.append({"table": index, "issue": "missing_explicit_header_row"})
        for row_number, row in enumerate(rows, start=1):
            if row.find("w:trPr/w:trHeight", NS) is not None:
                table_issues.append({"table": index, "row": row_number, "issue": "fixed_row_height"})
    if table_issues:
        audit.fail("table_accessibility_and_rows", "table component contract failed", {"items": table_issues})
    else:
        audit.pass_check("table_accessibility_and_rows", {"tables": len(tables), "headerRows": len(tables)})

    geometry_issues = []
    geometry_summary = []
    expected_margins = {"top": "80", "bottom": "80", "start": "120", "end": "120"}
    for index, table in enumerate(tables, start=1):
        table_width = table.find("w:tblPr/w:tblW", NS)
        table_indent = table.find("w:tblPr/w:tblInd", NS)
        table_layout = table.find("w:tblPr/w:tblLayout", NS)
        grid = [int(attr(column, "w")) for column in table.findall("w:tblGrid/w:gridCol", NS)]
        width = int(attr(table_width, "w") or 0)
        summary = {"table": index, "width": width, "indent": int(attr(table_indent, "w") or 0), "grid": grid}
        geometry_summary.append(summary)
        if attr(table_width, "type") != "dxa" or width != 9360:
            geometry_issues.append({"table": index, "issue": "table_width", "actual": {"type": attr(table_width, "type"), "width": width}})
        if attr(table_indent, "type") != "dxa" or attr(table_indent, "w") != "120":
            geometry_issues.append({"table": index, "issue": "table_indent", "actual": {"type": attr(table_indent, "type"), "width": attr(table_indent, "w")}})
        if attr(table_layout, "type") != "fixed":
            geometry_issues.append({"table": index, "issue": "table_layout", "actual": attr(table_layout, "type")})
        if sum(grid) != width:
            geometry_issues.append({"table": index, "issue": "grid_sum", "grid": grid, "width": width})
        for row_index, row in enumerate(table.findall("w:tr", NS), start=1):
            cell_widths = []
            for column_index, cell in enumerate(row.findall("w:tc", NS), start=1):
                cell_width = cell.find("w:tcPr/w:tcW", NS)
                cell_widths.append(int(attr(cell_width, "w") or 0))
                margins = cell.find("w:tcPr/w:tcMar", NS)
                actual_margins = {
                    side: attr(margins.find(f"w:{side}", NS), "w") if margins is not None else None
                    for side in expected_margins
                }
                if actual_margins != expected_margins:
                    geometry_issues.append({"table": index, "row": row_index, "column": column_index, "issue": "cell_margins", "actual": actual_margins})
            if cell_widths != grid:
                geometry_issues.append({"table": index, "row": row_index, "issue": "cell_widths", "actual": cell_widths, "grid": grid})
    if geometry_issues:
        audit.fail("table_geometry", "table geometry does not match the fixture contract", {"items": geometry_issues})
    else:
        audit.pass_check("table_geometry", {"tables": geometry_summary, "cellMargins": expected_margins})

    direct_runs = document.findall(".//w:body//w:r/w:rPr", NS)
    direct_paragraphs = []
    for paragraph in document.findall(".//w:body//w:p", NS):
        properties = paragraph.find("w:pPr", NS)
        if properties is not None and any(child.tag != W("pStyle") for child in list(properties)):
            direct_paragraphs.append(direct_text(paragraph)[:80])
    if direct_runs or direct_paragraphs:
        audit.fail(
            "direct_formatting",
            "fixture content must be driven by named styles",
            {"runProperties": len(direct_runs), "paragraphs": direct_paragraphs},
        )
    else:
        audit.pass_check("direct_formatting", {"runProperties": 0, "paragraphProperties": 0})

    drawings = document.findall(".//w:drawing", NS)
    missing_alt = []
    for index, drawing in enumerate(drawings, start=1):
        doc_pr = next((node for node in drawing.iter() if node.tag.endswith("}docPr")), None)
        if doc_pr is None or not (doc_pr.get("descr") or doc_pr.get("name")):
            missing_alt.append(index)
    if missing_alt:
        audit.fail("image_alt_text", "images lack an accessible description", {"indexes": missing_alt})
    else:
        audit.pass_check("image_alt_text", {"images": len(drawings)})

    fields = document.findall(".//w:fldSimple", NS) + document.findall(".//w:instrText", NS)
    if fields:
        audit.fail("dynamic_fields", "dynamic Word fields are not allowed in the deterministic fixture", {"count": len(fields)})
    else:
        audit.pass_check("dynamic_fields", {"count": 0})

    revisions = document.findall(".//w:ins", NS) + document.findall(".//w:del", NS)
    comments = (
        document.findall(".//w:commentRangeStart", NS)
        + document.findall(".//w:commentRangeEnd", NS)
        + document.findall(".//w:commentReference", NS)
    )
    if revisions or comments:
        audit.fail("review_markup", "tracked changes or comments are not allowed in the deterministic fixture", {"revisions": len(revisions), "commentMarkers": len(comments)})
    else:
        audit.pass_check("review_markup", {"revisions": 0, "commentMarkers": 0})

    text = direct_text(document)
    if "\ufffd" in text or not text.strip():
        audit.fail("text_content", "document text is empty or contains replacement characters")
    else:
        audit.pass_check("text_content", {"characters": len(text)})


def check_metadata(archive, audit: Audit):
    core = None
    try:
        core = ET.fromstring(archive.read("docProps/core.xml"))
    except (KeyError, ET.ParseError):
        pass
    if core is None:
        audit.fail("core_metadata", "core properties are missing or invalid")
        return
    creator = next((node.text for node in core.iter() if node.tag.endswith("}creator")), None)
    if creator != "MarketOps AI Workbench":
        audit.fail("core_metadata", "fixture creator is unexpected", {"creator": creator})
    else:
        audit.pass_check("core_metadata", {"creator": creator})
    custom_present = "docProps/custom.xml" in archive.namelist()
    if custom_present:
        audit.fail("custom_metadata", "custom properties must not be present in the public fixture")
    else:
        audit.pass_check("custom_metadata", {"present": False})


def audit_docx(path: Path) -> dict:
    audit = Audit()
    archive, parts = check_package(path, audit)
    if archive is not None and parts is not None:
        check_page_and_styles(parts, audit)
        check_headings_tables_accessibility(parts, audit)
        check_metadata(archive, audit)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        archive.close()
    else:
        sha = None
    return {
        "schemaVersion": 1,
        "auditId": "M0-02-DOCX-COMPONENTS",
        "path": path.as_posix(),
        "sha256": sha,
        "structuralStatus": "passed" if not audit.errors else "failed",
        "visualStatus": "not_run_renderer_unavailable",
        "checks": audit.checks,
        "errors": audit.errors,
        "warnings": audit.warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--output", type=Path)
    mode.add_argument("--check", type=Path)
    args = parser.parse_args()
    result = audit_docx(args.input)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not args.check.exists():
            print(f"Committed component audit is missing: {args.check}", file=sys.stderr)
            return 2
        if args.check.read_text(encoding="utf-8") != serialized:
            print(f"Component audit is stale; regenerate {args.check}", file=sys.stderr)
            return 2
        print(f"DOCX component audit passed and matches {args.check}")
    elif args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8", newline="\n")
    else:
        print(serialized, end="")
    return 0 if result["structuralStatus"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
