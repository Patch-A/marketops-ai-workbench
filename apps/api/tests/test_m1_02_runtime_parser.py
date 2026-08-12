from __future__ import annotations

import hashlib
import io
import unittest
import warnings
from zipfile import ZIP_DEFLATED, ZipFile

from apps.api.marketops_extract import DeterministicExtractor
from apps.api.marketops_extract.parser import (
    DOCX_MEDIA_TYPE,
    MAX_BLOCK_TEXT_CHARS,
    MAX_DOCX_XML_BYTES,
    MAX_PARSER_BLOCKS,
    MAX_PARSER_TABLE_CELLS,
    MAX_PARSER_WARNINGS,
    MAX_TABLE_COLUMNS,
    MAX_TEXT_LINES,
    RuntimeParseFailure,
    RuntimeProposalParser,
)


VERSION_ID = "00000000-0000-4000-8000-000000000201"


def docx_payload(
    *,
    extra_inline: str = "",
    extra_body: str = "",
    extra_entries: tuple[tuple[str, str | bytes], ...] = (),
) -> bytes:
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Deliverables</w:t></w:r></w:p>
    <w:p><w:r><w:t>Registration page</w:t>{extra_inline}</w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Milestones</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:p><w:r><w:t>Milestone</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>Venue locked</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
    {extra_body}
    <w:sectPr/>
  </w:body>
</w:document>"""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>
</w:styles>"""
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as package:
        package.writestr("word/document.xml", document)
        package.writestr("word/styles.xml", styles)
        for name, content in extra_entries:
            package.writestr(name, content)
    return output.getvalue()


def unsafe_docx_payload(extra_name: str | None = None, *, duplicate=False) -> bytes:
    document = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Content</w:t></w:r></w:p></w:body></w:document>"""
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(output, "w", ZIP_DEFLATED) as package:
            package.writestr("word/document.xml", document)
            if duplicate:
                package.writestr("word/document.xml", document)
            if extra_name is not None:
                package.writestr(extra_name, b"unsafe")
    return output.getvalue()


class OversizedXmlArchive:
    class Entry:
        file_size = MAX_DOCX_XML_BYTES + 1

    def __init__(self):
        self.read_called = False

    def getinfo(self, _name):
        return self.Entry()

    def read(self, _name):
        self.read_called = True
        raise AssertionError("oversized XML must be rejected before archive.read")


class RuntimeProposalParserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.parser = RuntimeProposalParser()

    async def test_markdown_outputs_wp1_blocks_and_deterministic_candidates(self):
        payload = (
            b"# Deliverables\n- Registration page\n\n"
            b"# Constraints\n| Constraint | Owner |\n| --- | --- |\n"
            b"| Capacity is 360 | Operations |\n"
        )
        result = await self.parser.parse(
            payload=payload, filename="proposal.md", media_type="text/markdown"
        )

        self.assertEqual(result.source_sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(result.warnings, ())
        self.assertEqual(
            [block.kind for block in result.blocks],
            ["heading", "paragraph", "heading", "table"],
        )
        candidates = DeterministicExtractor().extract(
            proposal_version_id=VERSION_ID,
            proposal_sha256=result.source_sha256,
            blocks=result.blocks,
        )
        self.assertEqual(
            [(item.kind, item.text) for item in candidates],
            [
                ("deliverable", "Registration page"),
                ("constraint", "Capacity is 360"),
            ],
        )

    async def test_plain_text_recognizes_explicit_section_labels(self):
        payload = b"Deliverables:\n- Registration page\nAssumptions:\nAttendance holds\n"
        result = await self.parser.parse(
            payload=payload, filename="proposal.txt", media_type="text/plain"
        )
        candidates = DeterministicExtractor().extract(
            proposal_version_id=VERSION_ID,
            proposal_sha256=result.source_sha256,
            blocks=result.blocks,
        )
        self.assertEqual(
            [(item.kind, item.classification) for item in candidates],
            [("deliverable", "fact"), ("assumption", "hypothesis")],
        )

    async def test_docx_heading_paragraph_and_explicit_table_are_supported(self):
        payload = docx_payload()
        result = await self.parser.parse(
            payload=payload, filename="proposal.docx", media_type=DOCX_MEDIA_TYPE
        )

        self.assertEqual(result.warnings, ())
        self.assertEqual(
            [block.kind for block in result.blocks],
            ["heading", "paragraph", "heading", "table"],
        )
        candidates = DeterministicExtractor().extract(
            proposal_version_id=VERSION_ID,
            proposal_sha256=result.source_sha256,
            blocks=result.blocks,
        )
        self.assertEqual(
            [(item.kind, item.text) for item in candidates],
            [("deliverable", "Registration page"), ("milestone", "Venue locked")],
        )

    async def test_unsupported_content_is_a_warning_for_fail_closed_caller(self):
        result = await self.parser.parse(
            payload=b"# Deliverables\n```\nsecret\n```\n",
            filename="proposal.md",
            media_type="text/plain",
        )
        self.assertEqual(
            [warning.code for warning in result.warnings],
            ["unsupported_markdown_code_fence"],
        )
        self.assertTrue(all(block.kind in {"heading", "paragraph", "table"} for block in result.blocks))

    async def test_docx_visual_and_field_content_are_never_silently_dropped(self):
        cases = (
            (b"<w:drawing/>", "drawings_present"),
            (b"<w:instrText>PAGE</w:instrText>", "fields_present"),
            (b'<w:fldChar w:fldCharType="begin"/>', "fields_present"),
            (
                b"<m:oMath><m:r><m:t>x+y</m:t></m:r></m:oMath>",
                "office_math_present",
            ),
            (b"<m:oMathPara/>", "office_math_present"),
        )
        for xml, expected_warning in cases:
            payload = docx_payload(extra_inline=xml.decode("ascii"))
            with self.subTest(expected_warning=expected_warning):
                result = await self.parser.parse(
                    payload=payload,
                    filename="proposal.docx",
                    media_type=DOCX_MEDIA_TYPE,
                )
                self.assertIn(
                    expected_warning,
                    [warning.code for warning in result.warnings],
                )

        packaged = docx_payload(
            extra_entries=(("word/header1.xml", b"<header>content</header>"),)
        )
        result = await self.parser.parse(
            payload=packaged,
            filename="proposal.docx",
            media_type=DOCX_MEDIA_TYPE,
        )
        self.assertIn("headers_present", [warning.code for warning in result.warnings])

    async def test_supported_text_is_bounded_before_candidate_creation(self):
        cases = (
            (
                b"# Deliverables\n" + b"- item\n" * (MAX_PARSER_BLOCKS + 1),
                "proposal.md",
                "text/markdown",
            ),
            (b"line\n" * MAX_TEXT_LINES, "proposal.txt", "text/plain"),
            (
                b"> quote\n" * (MAX_PARSER_WARNINGS + 1),
                "proposal.md",
                "text/markdown",
            ),
        )
        for payload, filename, media_type in cases:
            with self.subTest(filename=filename), self.assertRaises(
                RuntimeParseFailure
            ) as raised:
                await self.parser.parse(
                    payload=payload,
                    filename=filename,
                    media_type=media_type,
                )
            self.assertEqual(raised.exception.code, "DOCUMENT_LIMIT_EXCEEDED")

    async def test_long_line_and_wide_markdown_table_fail_before_block_expansion(self):
        columns = [f"Column {index}" for index in range(MAX_TABLE_COLUMNS + 1)]
        wide_table = (
            "| " + " | ".join(columns) + " |\n"
            "| " + " | ".join("---" for _ in columns) + " |\n"
            "| " + " | ".join("value" for _ in columns) + " |\n"
        ).encode("utf-8")
        cases = (
            (b"x" * (MAX_BLOCK_TEXT_CHARS + 1), "proposal.txt", "text/plain"),
            (
                b"a" * 60000 + b"\n" + b"b" * 60000,
                "proposal.md",
                "text/markdown",
            ),
            (wide_table, "proposal.md", "text/markdown"),
        )

        for payload, filename, media_type in cases:
            with self.subTest(filename=filename), self.assertRaises(
                RuntimeParseFailure
            ) as raised:
                await self.parser.parse(
                    payload=payload,
                    filename=filename,
                    media_type=media_type,
                )
            self.assertEqual(raised.exception.code, "DOCUMENT_LIMIT_EXCEEDED")

    async def test_docx_rejects_zero_cell_row_expansion(self):
        empty_rows = "".join(
            "<w:tr><w:trPr><w:tblHeader/></w:trPr></w:tr>"
            for _ in range(MAX_PARSER_TABLE_CELLS + 1)
        )
        payload = docx_payload(extra_body=f"<w:tbl>{empty_rows}</w:tbl>")

        with self.assertRaises(RuntimeParseFailure) as raised:
            await self.parser.parse(
                payload=payload,
                filename="proposal.docx",
                media_type=DOCX_MEDIA_TYPE,
            )

        self.assertEqual(raised.exception.code, "DOCUMENT_LIMIT_EXCEEDED")

    async def test_docx_section_properties_do_not_consume_business_block_limit(self):
        extra_paragraphs = "".join(
            f"<w:p><w:r><w:t>Item {index}</w:t></w:r></w:p>"
            for index in range(MAX_PARSER_BLOCKS - 4)
        )
        payload = docx_payload(extra_body=extra_paragraphs)

        result = await self.parser.parse(
            payload=payload,
            filename="proposal.docx",
            media_type=DOCX_MEDIA_TYPE,
        )

        self.assertEqual(len(result.blocks), MAX_PARSER_BLOCKS)
        self.assertEqual(result.warnings, ())

    def test_docx_xml_parts_are_rejected_before_archive_read(self):
        for name in ("word/document.xml", "word/styles.xml"):
            archive = OversizedXmlArchive()
            with self.subTest(name=name), self.assertRaises(
                RuntimeParseFailure
            ) as raised:
                if name == "word/document.xml":
                    self.parser._read_xml(archive, name)
                else:
                    self.parser._load_docx_styles(archive, {name})
            self.assertEqual(raised.exception.code, "DOCUMENT_LIMIT_EXCEEDED")
            self.assertFalse(archive.read_called)

    async def test_format_encoding_and_invalid_docx_fail_with_sanitized_errors(self):
        cases = (
            (b"text", "proposal.pdf", "application/pdf", "UNSUPPORTED_FORMAT"),
            (b"\xff", "proposal.md", "text/markdown", "INVALID_ENCODING"),
            (b"not-a-zip", "proposal.docx", DOCX_MEDIA_TYPE, "INVALID_DOCUMENT"),
        )
        for payload, filename, media_type, code in cases:
            with self.subTest(code=code), self.assertRaises(RuntimeParseFailure) as raised:
                await self.parser.parse(
                    payload=payload, filename=filename, media_type=media_type
                )
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn(filename, str(raised.exception))

    async def test_docx_rejects_duplicate_absolute_and_ambiguous_entry_names(self):
        payloads = (
            unsafe_docx_payload(duplicate=True),
            unsafe_docx_payload("../outside.xml"),
            unsafe_docx_payload("/absolute.xml"),
            unsafe_docx_payload("C:/absolute.xml"),
        )
        for payload in payloads:
            with self.subTest(payload_hash=hashlib.sha256(payload).hexdigest()):
                with self.assertRaises(RuntimeParseFailure) as raised:
                    await self.parser.parse(
                        payload=payload,
                        filename="proposal.docx",
                        media_type=DOCX_MEDIA_TYPE,
                    )
                self.assertEqual(raised.exception.code, "INVALID_DOCUMENT")
        self.assertTrue(self.parser._unsafe_archive_name("word\\ambiguous.xml"))


if __name__ == "__main__":
    unittest.main()
