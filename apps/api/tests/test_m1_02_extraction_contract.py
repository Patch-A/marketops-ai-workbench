from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from apps.api.marketops_extract import Candidate, ExtractionContractError, extract_candidates


VERSION_ID = "018f36f8-f56d-7a5a-98f6-16b13d9a4e41"
SOURCE_HASH = "a" * 64


def paragraph(line: int = 2, **changes):
    block = {
        "kind": "paragraph",
        "text": "Venue capacity is limited to 360 people.",
        "sectionPath": ["Project constraints"],
        "location": {"kind": "line_range", "startLine": line, "endLine": line},
    }
    block.update(changes)
    return block


class ExtractionContractTests(unittest.TestCase):
    def assert_code(self, code, **kwargs):
        with self.assertRaises(ExtractionContractError) as raised:
            extract_candidates(**kwargs)
        self.assertEqual(raised.exception.code, code)

    def valid_input(self, **changes):
        values = {
            "proposal_version_id": VERSION_ID,
            "proposal_sha256": SOURCE_HASH,
            "blocks": [paragraph()],
        }
        values.update(changes)
        return values

    def test_candidate_is_immutable_and_has_complete_citation(self):
        candidate = extract_candidates(**self.valid_input())[0]
        self.assertEqual(candidate.kind, "constraint")
        self.assertEqual(candidate.classification, "fact")
        self.assertEqual(candidate.review_status, "pending")
        self.assertEqual(candidate.source_citation.source_version_id, VERSION_ID)
        self.assertEqual(candidate.source_citation.source_sha256, SOURCE_HASH)
        self.assertEqual(candidate.source_citation.quote, paragraph()["text"])
        self.assertEqual(candidate.source_citation.section_path, ("Project constraints",))
        self.assertEqual(candidate.as_dict()["sourceCitation"]["sectionPath"], ["Project constraints"])
        self.assertEqual(candidate.source_citation.location.as_dict()["startLine"], 2)
        with self.assertRaises(FrozenInstanceError):
            candidate.text = "changed"

    def test_invalid_uuid_and_hash_fail_closed(self):
        self.assert_code("INVALID_UUID", **self.valid_input(proposal_version_id="nope"))
        self.assert_code("INVALID_SHA256", **self.valid_input(proposal_sha256="123"))

    def test_candidate_rejects_unknown_kind_and_missing_citation(self):
        valid = extract_candidates(**self.valid_input())[0]
        with self.assertRaises(ExtractionContractError) as unknown:
            Candidate(valid.candidate_id, "unknown", valid.text, "fact", 1.0, valid.source_citation)
        self.assertEqual(unknown.exception.code, "INVALID_CANDIDATE_KIND")
        with self.assertRaises(ExtractionContractError) as missing:
            Candidate(valid.candidate_id, "constraint", valid.text, "fact", 1.0, None)
        self.assertEqual(missing.exception.code, "MISSING_CITATION")

    def test_unknown_fields_are_rejected_at_block_and_location_levels(self):
        self.assert_code("UNKNOWN_FIELD", **self.valid_input(blocks=[paragraph(extra=True)]))
        invalid = paragraph()
        invalid["location"]["postgresql://user:secret@db/customer"] = 1
        with self.assertRaises(ExtractionContractError) as raised:
            extract_candidates(**self.valid_input(blocks=[invalid]))
        self.assertEqual(raised.exception.code, "UNKNOWN_FIELD")
        self.assertNotIn("secret", str(raised.exception))

    def test_empty_text_and_unknown_block_kind_are_rejected(self):
        self.assert_code("EMPTY_TEXT", **self.valid_input(blocks=[paragraph(text="  ")]))
        self.assert_code("INVALID_BLOCK", **self.valid_input(blocks=[paragraph(kind="image")]))

    def test_invalid_duplicate_and_non_monotonic_locations_are_rejected(self):
        reversed_range = paragraph()
        reversed_range["location"] = {"kind": "line_range", "startLine": 4, "endLine": 3}
        self.assert_code("INVALID_LOCATION", **self.valid_input(blocks=[reversed_range]))
        self.assert_code("DUPLICATE_LOCATION", **self.valid_input(blocks=[paragraph(), paragraph()]))
        self.assert_code(
            "NON_MONOTONIC_LOCATION",
            **self.valid_input(blocks=[paragraph(3), paragraph(2)]),
        )

    def test_overlapping_ranges_are_rejected_but_adjacent_ranges_are_valid(self):
        overlapping = [paragraph(1)]
        overlapping[0]["location"] = {"kind": "line_range", "startLine": 1, "endLine": 10}
        overlapping.append(paragraph(2))
        self.assert_code("NON_MONOTONIC_LOCATION", **self.valid_input(blocks=overlapping))

        adjacent = [paragraph(1)]
        adjacent[0]["location"] = {"kind": "line_range", "startLine": 1, "endLine": 10}
        adjacent.append(paragraph(11))
        self.assertEqual(len(extract_candidates(**self.valid_input(blocks=adjacent))), 2)

    def test_failure_does_not_expose_partial_candidates(self):
        with self.assertRaises(ExtractionContractError) as raised:
            extract_candidates(**self.valid_input(blocks=[paragraph(2), paragraph(1, text="")]))
        self.assertFalse(hasattr(raised.exception, "candidates"))


if __name__ == "__main__":
    unittest.main()
