from __future__ import annotations

import unittest

from apps.api.marketops_extract import ExtractionContractError, extract_candidates


VERSION_ID = "018f36f8-f56d-7a5a-98f6-16b13d9a4e41"
SOURCE_HASH = "bc" * 32


class DeterministicExtractorTests(unittest.TestCase):
    def extract(self, blocks):
        return extract_candidates(
            proposal_version_id=VERSION_ID,
            proposal_sha256=SOURCE_HASH,
            blocks=blocks,
        )

    def test_extracts_four_explicit_kinds_with_stable_ids_and_pending_review(self):
        blocks = [
            {
                "kind": "heading",
                "text": "Approved deliverables",
                "sectionPath": ["Approved deliverables"],
                "location": {"kind": "line_range", "startLine": 1, "endLine": 1},
            },
            {
                "kind": "paragraph",
                "text": "Registration page",
                "sectionPath": ["Approved deliverables"],
                "location": {"kind": "line_range", "startLine": 2, "endLine": 2},
            },
            {
                "kind": "paragraph",
                "text": "Venue locked",
                "sectionPath": ["Milestones"],
                "location": {"kind": "line_range", "startLine": 3, "endLine": 3},
            },
            {
                "kind": "paragraph",
                "text": "Capacity is 360",
                "sectionPath": ["Constraints"],
                "location": {"kind": "line_range", "startLine": 4, "endLine": 4},
            },
            {
                "kind": "paragraph",
                "text": "Targeted invitations improve attendance",
                "sectionPath": ["Assumptions"],
                "location": {"kind": "line_range", "startLine": 5, "endLine": 5},
            },
        ]
        first = self.extract(blocks)
        second = self.extract(blocks)
        self.assertEqual(first, second)
        self.assertEqual([item.kind for item in first], ["deliverable", "milestone", "constraint", "assumption"])
        self.assertEqual([item.classification for item in first], ["fact", "fact", "fact", "hypothesis"])
        self.assertTrue(all(item.review_status == "pending" for item in first))

    def test_unlabelled_paragraph_is_not_inferred(self):
        blocks = [{
            "kind": "paragraph",
            "text": "Launch landing page",
            "sectionPath": ["Overview"],
            "location": {"kind": "line_range", "startLine": 1, "endLine": 1},
        }]
        self.assertEqual(self.extract(blocks), ())

    def test_explicit_table_column_extracts_cell(self):
        blocks = [{
            "kind": "table",
            "text": "Event visual and registration page",
            "sectionPath": ["Approved plan"],
            "columnName": "Deliverable",
            "location": {"kind": "markdown_table_cell", "line": 9, "columnIndex": 2},
        }]
        candidate = self.extract(blocks)[0]
        self.assertEqual(candidate.kind, "deliverable")
        self.assertEqual(candidate.source_citation.location.as_dict()["columnIndex"], 2)

    def test_m0_parser_table_shape_extracts_only_named_semantic_column(self):
        blocks = [{
            "kind": "table",
            "columns": ["交付物编号", "交付物", "验收人"],
            "rows": [{
                "rowNumber": 9,
                "cells": [
                    {"column": "交付物编号", "value": "DEL-01", "location": {"kind": "markdown_table_cell", "line": 9, "columnIndex": 1}},
                    {"column": "交付物", "value": "活动视觉与报名页", "location": {"kind": "markdown_table_cell", "line": 9, "columnIndex": 2}},
                    {"column": "验收人", "value": "品牌负责人", "location": {"kind": "markdown_table_cell", "line": 9, "columnIndex": 3}},
                ],
            }],
            "sectionPath": ["已批准交付物"],
            "location": {"kind": "line_range", "startLine": 7, "endLine": 10},
        }]
        candidates = self.extract(blocks)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].text, "活动视觉与报名页")
        self.assertEqual(candidates[0].kind, "deliverable")

    def test_ambiguous_table_rejects_whole_batch(self):
        blocks = [
            {
                "kind": "paragraph",
                "text": "Registration page",
                "sectionPath": ["Deliverables"],
                "location": {"kind": "line_range", "startLine": 1, "endLine": 1},
            },
            {
                "kind": "table",
                "text": "Unknown cell",
                "sectionPath": ["Overview"],
                "location": {"kind": "markdown_table_cell", "line": 2, "columnIndex": 1},
            },
        ]
        with self.assertRaises(ExtractionContractError) as raised:
            self.extract(blocks)
        self.assertEqual(raised.exception.code, "AMBIGUOUS_TABLE")

    def test_conflicting_table_section_and_column_are_ambiguous(self):
        blocks = [{
            "kind": "table",
            "text": "Targeted invitations improve attendance",
            "sectionPath": ["Assumptions"],
            "columnName": "Deliverable",
            "location": {"kind": "markdown_table_cell", "line": 2, "columnIndex": 2},
        }]
        with self.assertRaises(ExtractionContractError) as raised:
            self.extract(blocks)
        self.assertEqual(raised.exception.code, "AMBIGUOUS_TABLE")

    def test_interleaved_location_families_cannot_hide_source_rewind(self):
        blocks = [
            {
                "kind": "paragraph", "text": "First constraint", "sectionPath": ["Constraints"],
                "location": {"kind": "line_range", "startLine": 1, "endLine": 1},
            },
            {
                "kind": "table", "text": "A deliverable", "columnName": "Deliverable",
                "sectionPath": ["Deliverables"],
                "location": {"kind": "markdown_table_cell", "line": 10, "columnIndex": 1},
            },
            {
                "kind": "paragraph", "text": "Rewound constraint", "sectionPath": ["Constraints"],
                "location": {"kind": "line_range", "startLine": 2, "endLine": 2},
            },
        ]
        with self.assertRaises(ExtractionContractError) as raised:
            self.extract(blocks)
        self.assertEqual(raised.exception.code, "NON_MONOTONIC_LOCATION")

    def test_table_cells_must_stay_inside_outer_range(self):
        blocks = [{
            "kind": "table", "columns": ["交付物"], "rows": [{
                "rowNumber": 1,
                "cells": [{"column": "交付物", "value": "Outside", "location": {"kind": "markdown_table_cell", "line": 99, "columnIndex": 1}}],
            }],
            "sectionPath": ["交付物"],
            "location": {"kind": "line_range", "startLine": 1, "endLine": 3},
        }]
        with self.assertRaises(ExtractionContractError) as raised:
            self.extract(blocks)
        self.assertEqual(raised.exception.code, "INVALID_LOCATION")

    def test_outer_table_range_high_water_mark_rejects_following_overlap(self):
        blocks = [
            {
                "kind": "table", "columns": ["Deliverable"], "rows": [{
                    "rowNumber": 9,
                    "cells": [{"column": "Deliverable", "value": "A", "location": {"kind": "markdown_table_cell", "line": 9, "columnIndex": 1}}],
                }],
                "sectionPath": ["Deliverables"],
                "location": {"kind": "line_range", "startLine": 7, "endLine": 10},
            },
            {
                "kind": "paragraph", "text": "Overlap", "sectionPath": ["Constraints"],
                "location": {"kind": "line_range", "startLine": 10, "endLine": 10},
            },
        ]
        with self.assertRaises(ExtractionContractError) as raised:
            self.extract(blocks)
        self.assertEqual(raised.exception.code, "NON_MONOTONIC_LOCATION")

    def test_outer_table_range_boundary_is_valid(self):
        blocks = [{
            "kind": "table", "columns": ["Deliverable"], "rows": [{
                "rowNumber": 9,
                "cells": [{"column": "Deliverable", "value": "A", "location": {"kind": "markdown_table_cell", "line": 9, "columnIndex": 1}}],
            }],
            "sectionPath": ["Deliverables"],
            "location": {"kind": "line_range", "startLine": 7, "endLine": 10},
        }, {
            "kind": "paragraph", "text": "Next", "sectionPath": ["Constraints"],
            "location": {"kind": "line_range", "startLine": 11, "endLine": 11},
        }]
        self.assertEqual(len(self.extract(blocks)), 2)

    def test_table_row_number_must_match_cell_coordinate(self):
        blocks = [{
            "kind": "table", "columns": ["Deliverable"], "rows": [{
                "rowNumber": 9,
                "cells": [{"column": "Deliverable", "value": "A", "location": {"kind": "markdown_table_cell", "line": 8, "columnIndex": 1}}],
            }],
            "sectionPath": ["Deliverables"],
            "location": {"kind": "line_range", "startLine": 7, "endLine": 10},
        }]
        with self.assertRaises(ExtractionContractError) as raised:
            self.extract(blocks)
        self.assertEqual(raised.exception.code, "INVALID_LOCATION")

    def test_table_column_position_must_match_header_position(self):
        blocks = [{
            "kind": "table", "columns": ["Deliverable"], "rows": [{
                "rowNumber": 9,
                "cells": [{"column": "Deliverable", "value": "A", "location": {"kind": "markdown_table_cell", "line": 9, "columnIndex": 99}}],
            }],
            "sectionPath": ["Deliverables"],
            "location": {"kind": "line_range", "startLine": 7, "endLine": 10},
        }]
        with self.assertRaises(ExtractionContractError) as raised:
            self.extract(blocks)
        self.assertEqual(raised.exception.code, "INVALID_LOCATION")

    def test_docx_cell_must_share_outer_part_and_table_identity(self):
        blocks = [{
            "kind": "table", "columns": ["Deliverable"], "rows": [{
                "rowNumber": 1,
                "cells": [{"column": "Deliverable", "value": "A", "location": {"kind": "docx_table_cell", "part": "word/header1.xml", "table": 1, "row": 1, "column": 1}}],
            }],
            "sectionPath": ["Deliverables"],
            "location": {"kind": "docx_table", "part": "word/document.xml", "bodyIndex": 1, "table": 1},
        }]
        with self.assertRaises(ExtractionContractError) as raised:
            self.extract(blocks)
        self.assertEqual(raised.exception.code, "INVALID_LOCATION")

    def test_docx_matching_coordinate_identity_is_valid(self):
        blocks = [{
            "kind": "table", "columns": ["Deliverable"], "rows": [{
                "rowNumber": 1,
                "cells": [{"column": "Deliverable", "value": "A", "location": {"kind": "docx_table_cell", "part": "word/document.xml", "table": 1, "row": 1, "column": 1}}],
            }],
            "sectionPath": ["Deliverables"],
            "location": {"kind": "docx_table", "part": "word/document.xml", "bodyIndex": 1, "table": 1},
        }]
        self.assertEqual(len(self.extract(blocks)), 1)

    def test_standalone_normalized_docx_cell_is_supported(self):
        blocks = [{
            "kind": "table", "text": "A", "columnName": "Deliverable",
            "sectionPath": ["Deliverables"],
            "location": {"kind": "docx_table_cell", "part": "word/document.xml", "table": 1, "row": 1, "column": 1},
        }]
        self.assertEqual(len(self.extract(blocks)), 1)

    def test_standalone_normalized_docx_cells_are_ordered_within_table(self):
        blocks = [{
            "kind": "table", "text": "A", "columnName": "Deliverable",
            "sectionPath": ["Deliverables"],
            "location": {"kind": "docx_table_cell", "part": "word/document.xml", "table": 1, "row": 1, "column": 1},
        }, {
            "kind": "table", "text": "B", "columnName": "Deliverable",
            "sectionPath": ["Deliverables"],
            "location": {"kind": "docx_table_cell", "part": "word/document.xml", "table": 1, "row": 2, "column": 1},
        }]
        self.assertEqual(len(self.extract(blocks)), 2)

    def test_multiple_semantic_columns_are_ambiguous(self):
        blocks = [{
            "kind": "table", "columns": ["Deliverable", "Deliverable description"], "rows": [{
                "rowNumber": 1,
                "cells": [
                    {"column": "Deliverable", "value": "A", "location": {"kind": "markdown_table_cell", "line": 1, "columnIndex": 1}},
                    {"column": "Deliverable description", "value": "B", "location": {"kind": "markdown_table_cell", "line": 1, "columnIndex": 2}},
                ],
            }],
            "sectionPath": ["Deliverables"],
            "location": {"kind": "line_range", "startLine": 1, "endLine": 2},
        }]
        with self.assertRaises(ExtractionContractError) as raised:
            self.extract(blocks)
        self.assertEqual(raised.exception.code, "AMBIGUOUS_TABLE")


if __name__ == "__main__":
    unittest.main()
