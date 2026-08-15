from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

from apps.api.marketops_retrieval import (
    ParsedBlock,
    RetrievalFailure,
    RetrievalScopeContext,
    SourceIdentity,
    build_source_index,
    search_source_indexes,
    validate_citation,
    withdraw_source_index,
)


class RetrievalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = RetrievalScopeContext(*(str(uuid4()) for _ in range(4)))
        self.project_id = str(uuid4())
        self.version_id = str(uuid4())
        self.source_sha = "a" * 64
        self.source = SourceIdentity(
            organization_id=self.scope.organization_id,
            workspace_id=self.scope.workspace_id,
            client_id=self.scope.client_id,
            project_id=self.project_id,
            artifact_version_id=self.version_id,
            source_sha256=self.source_sha,
            parser_version="marketops-parser-v1",
        )

    def build_index(self):
        return build_source_index(
            self.source,
            (
                ParsedBlock(
                    "The approved launch requires legal review before media booking.",
                    {"sectionPath": ["Launch"], "lineStart": 4, "lineEnd": 4},
                ),
                ParsedBlock(
                    "新品发布必须在媒体预订前完成法务审核。",
                    {"sectionPath": ["发布"], "lineStart": 8, "lineEnd": 8},
                ),
            ),
            self.scope,
        )

    def test_index_identity_chunks_and_digest_are_deterministic(self):
        first = self.build_index()
        second = self.build_index()
        self.assertEqual(first, second)
        self.assertEqual(first.status, "ready")
        self.assertEqual(len(first.chunks), 2)
        self.assertEqual([chunk.ordinal for chunk in first.chunks], [0, 1])
        self.assertTrue(all(len(chunk.content_sha256) == 64 for chunk in first.chunks))
        self.assertEqual(first.chunks[0].location["characterStart"], 0)

    def test_long_blocks_split_with_bounded_text_and_locations(self):
        index = build_source_index(
            self.source,
            (ParsedBlock("word " * 900, {"page": 1}),),
            self.scope,
        )
        self.assertGreater(len(index.chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 2000 for chunk in index.chunks))
        self.assertEqual(index.chunks[0].location["blockOrdinal"], 0)
        self.assertLess(
            index.chunks[0].location["characterStart"],
            index.chunks[0].location["characterEnd"],
        )

    def test_english_and_chinese_search_are_deterministic_and_cited(self):
        index = self.build_index()
        hashes = {self.version_id: self.source_sha}
        english = search_source_indexes(
            (index,), project_id=self.project_id, query="legal review media booking",
            scope=self.scope, current_source_hashes=hashes,
        )
        replay = search_source_indexes(
            (index,), project_id=self.project_id, query="legal review media booking",
            scope=self.scope, current_source_hashes=hashes,
        )
        chinese = search_source_indexes(
            (index,), project_id=self.project_id, query="法务审核 媒体预订",
            scope=self.scope, current_source_hashes=hashes,
        )
        self.assertEqual(english, replay)
        self.assertEqual(english.retrieval_mode, "lexical_ngram")
        self.assertEqual(english.scored_candidate_count, 2)
        self.assertEqual(english.result_count, 1)
        self.assertEqual(chinese.result_count, 1)
        validate_citation(english.results[0].citation, index)
        self.assertNotIn(self.scope.workspace_id, english.results[0].excerpt)

    def test_scope_contamination_fails_before_scoring(self):
        index = self.build_index()
        contaminated_source = replace(index.source, workspace_id=str(uuid4()))
        contaminated = replace(index, source=contaminated_source)
        with patch(
            "apps.api.marketops_retrieval.service._lexical_scores"
        ) as scorer, self.assertRaises(RetrievalFailure) as raised:
            search_source_indexes(
                (contaminated,), project_id=self.project_id, query="legal review",
                scope=self.scope, current_source_hashes={self.version_id: self.source_sha},
            )
        self.assertEqual(raised.exception.code, "SCOPE_CONTAMINATION")
        scorer.assert_not_called()

    def test_stale_source_fails_before_scoring(self):
        index = self.build_index()
        with patch(
            "apps.api.marketops_retrieval.service._lexical_scores"
        ) as scorer, self.assertRaises(RetrievalFailure) as raised:
            search_source_indexes(
                (index,), project_id=self.project_id, query="legal review",
                scope=self.scope, current_source_hashes={self.version_id: "b" * 64},
            )
        self.assertEqual(raised.exception.code, "SOURCE_STALE")
        scorer.assert_not_called()

    def test_withdrawal_removes_text_and_invalidates_old_citation(self):
        index = self.build_index()
        result = search_source_indexes(
            (index,), project_id=self.project_id, query="legal review",
            scope=self.scope, current_source_hashes={self.version_id: self.source_sha},
        )
        withdrawn = withdraw_source_index(index, self.scope)
        self.assertEqual(withdrawn.status, "withdrawn")
        self.assertEqual(withdrawn.chunks, ())
        self.assertEqual(withdraw_source_index(withdrawn, self.scope), withdrawn)
        with self.assertRaises(RetrievalFailure) as raised:
            validate_citation(result.results[0].citation, withdrawn)
        self.assertEqual(raised.exception.code, "CITATION_STALE")

    def test_content_drift_invalidates_citation(self):
        index = self.build_index()
        result = search_source_indexes(
            (index,), project_id=self.project_id, query="legal review",
            scope=self.scope, current_source_hashes={self.version_id: self.source_sha},
        )
        changed_chunk = replace(index.chunks[0], text="changed source text")
        changed = replace(index, chunks=(changed_chunk, *index.chunks[1:]))
        with self.assertRaises(RetrievalFailure) as raised:
            validate_citation(result.results[0].citation, changed)
        self.assertEqual(raised.exception.code, "CITATION_STALE")

    def test_invalid_inputs_are_sanitized(self):
        cases = (
            (replace(self.source, source_sha256="not-a-hash"), "INVALID_SOURCE"),
            (replace(self.source, workspace_id=str(uuid4())), "SOURCE_NOT_FOUND"),
        )
        for source, code in cases:
            with self.subTest(code=code), self.assertRaises(RetrievalFailure) as raised:
                build_source_index(
                    source,
                    (ParsedBlock("private source text", {"page": 1}),),
                    self.scope,
                )
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("private source text", str(raised.exception))
        with self.assertRaises(RetrievalFailure) as raised:
            search_source_indexes(
                (), project_id=self.project_id, query=" ", scope=self.scope,
                current_source_hashes={},
            )
        self.assertEqual(raised.exception.code, "INVALID_QUERY")


if __name__ == "__main__":
    unittest.main()
