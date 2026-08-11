from __future__ import annotations

import hashlib
import io
import unittest
import warnings
from zipfile import ZIP_DEFLATED, ZipFile

from apps.api.marketops_extract import DeterministicExtractor
from apps.api.marketops_extract.parser import (
    DOCX_MEDIA_TYPE,
    RuntimeParseFailure,
    RuntimeProposalParser,
)


VERSION_ID = "00000000-0000-4000-8000-000000000201"


def docx_payload() -> bytes:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Deliverables</w:t></w:r></w:p>
    <w:p><w:r><w:t>Registration page</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Milestones</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:p><w:r><w:t>Milestone</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>Venue locked</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
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
