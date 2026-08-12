from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import unittest
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apps.api.marketops_extract.contract import ParserBlock, SourceLocation
from apps.api.marketops_extract.parser import (
    MAX_BLOCK_TEXT_CHARS,
    ParserWarning,
    RuntimeParserResult,
)
from apps.api.marketops_review.preparation import (
    ApprovedProposalPreparationService,
    ApprovedProposalSource,
    ApprovedProposalSourceReadError,
    AsyncpgApprovedProposalSourceReader,
    MAX_REVIEW_CANDIDATES,
    PreparationFailure,
    PreparationRequest,
)
from apps.api.marketops_review.service import (
    CreateReviewRunResult,
    ReviewFailure,
    ReviewRun,
    ReviewScopeContext,
    ReviewSnapshot,
    ReviewSnapshotItem,
)


ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"
CLIENT_ID = "00000000-0000-4000-8000-000000000003"
ACTOR_ID = "00000000-0000-4000-8000-000000000004"
PROJECT_ID = "00000000-0000-4000-8000-000000000101"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000201"
VERSION_ID = "00000000-0000-4000-8000-000000000202"
RUN_ID = "00000000-0000-4000-8000-000000000301"
SNAPSHOT_ID = "00000000-0000-4000-8000-000000000302"
PAYLOAD = b"# Deliverables\n- Registration page\n"
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


class AsyncValueContext:
    def __init__(self, value):
        self.value = value
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited += 1
        return False


class FakeSourceConnection:
    def __init__(self, row=None, failure=None):
        self.row = row
        self.failure = failure
        self.calls = []
        self.transaction_context = AsyncValueContext(None)

    def transaction(self):
        return self.transaction_context

    async def fetchrow(self, sql, *arguments):
        self.calls.append((sql, arguments))
        if len(self.calls) == 2 and self.failure is not None:
            raise self.failure
        return {} if len(self.calls) == 1 else self.row


class FakeSourcePool:
    def __init__(self, connection):
        self.acquire_context = AsyncValueContext(connection)

    def acquire(self):
        return self.acquire_context


class FakeSourceReader:
    def __init__(self, source, failure=None):
        self.source = source
        self.failure = failure
        self.calls = 0

    async def read_approved_proposal(self, scope, project_id):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.source


class FakeObjectStore:
    def __init__(self, payload=PAYLOAD, failure=None, cancel_guard=False):
        self.payload = payload
        self.failure = failure
        self.cancel_guard = cancel_guard
        self.reads = 0
        self.guards = 0

    @asynccontextmanager
    async def import_guard(self):
        self.guards += 1
        if self.cancel_guard:
            raise asyncio.CancelledError()
        yield

    async def read_verified_immutable(self, *, kind, stored):
        self.reads += 1
        if self.failure is not None:
            raise self.failure
        return self.payload


class FakeParser:
    def __init__(self, result=None, failure=None):
        self.result = result
        self.failure = failure

    async def parse(self, **unused):
        if self.failure is not None:
            raise self.failure
        return self.result


class ExplodingMapping(Mapping):
    def __getitem__(self, key):
        raise RuntimeError("untrusted parser mapping")

    def __iter__(self):
        return iter(("kind",))

    def __len__(self):
        return 1


class SpoofingFailureMapping(ExplodingMapping):
    def __getitem__(self, key):
        raise PreparationFailure(
            "SOURCE_READ_FAILED", "spoofed parser failure", retryable=True
        )


class SpoofingTuple(tuple):
    def __len__(self):
        raise PreparationFailure(
            "SOURCE_READ_FAILED", "spoofed tuple failure", retryable=True
        )


class FakeReviewService:
    def __init__(self, failure=None):
        self.failure = failure
        self.requests = []

    async def create_run(self, request, scope):
        self.requests.append((request, scope))
        if self.failure is not None:
            raise self.failure
        now = datetime(2026, 8, 11, tzinfo=timezone.utc)
        run = ReviewRun(
            RUN_ID,
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            request.project_id,
            request.proposal_artifact_id,
            request.proposal_version_id,
            request.proposal_version,
            request.proposal_sha256,
            len(request.candidates),
            scope.actor_id,
            now,
        )
        snapshot = ReviewSnapshot(
            SNAPSHOT_ID,
            RUN_ID,
            1,
            tuple(ReviewSnapshotItem(item.candidate_id, "pending") for item in request.candidates),
            scope.actor_id,
            now,
        )
        return CreateReviewRunResult(run, snapshot)


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ReviewScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
        )
        self.source = ApprovedProposalSource(
            ORGANIZATION_ID,
            WORKSPACE_ID,
            CLIENT_ID,
            PROJECT_ID,
            ARTIFACT_ID,
            VERSION_ID,
            3,
            "approved.md",
            "text/markdown",
            len(PAYLOAD),
            "opaque-storage-key",
            SHA256,
        )

    def service(self, *, reader=None, store=None, parser=None, review=None):
        review = review or FakeReviewService()
        return (
            ApprovedProposalPreparationService(
                source_reader=reader or FakeSourceReader(self.source),
                object_store=store or FakeObjectStore(),
                review_service=review,
                parser=parser,
            ),
            review,
        )

    async def test_success_uses_only_server_source_and_deterministic_candidates(self):
        service, review = self.service()
        result = await service.prepare_and_create_run(
            PreparationRequest(PROJECT_ID, VERSION_ID, SHA256), self.scope
        )

        self.assertEqual(result.source, self.source)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].text, "Registration page")
        create_request, create_scope = review.requests[0]
        self.assertEqual(create_scope, self.scope)
        self.assertEqual(create_request.candidates, result.candidates)
        self.assertEqual(result.review_result.run.run_id, RUN_ID)
        request_fields = {field.name for field in dataclasses.fields(PreparationRequest)}
        self.assertTrue(
            request_fields.isdisjoint({"candidates", "blocks", "citations", "workspace_id", "actor_id"})
        )

    async def test_stale_expected_source_and_commit_time_source_change_fail(self):
        service, review = self.service()
        with self.assertRaises(PreparationFailure) as stale:
            await service.prepare_and_create_run(
                PreparationRequest(PROJECT_ID, VERSION_ID, "0" * 64), self.scope
            )
        self.assertEqual(stale.exception.code, "PROPOSAL_CHANGED")
        self.assertEqual(review.requests, [])

        changed = FakeReviewService(
            ReviewFailure("INVALID_PROPOSAL_STATE", "secret source row changed")
        )
        service, _ = self.service(review=changed)
        with self.assertRaises(PreparationFailure) as commit_change:
            await service.prepare_and_create_run(PreparationRequest(PROJECT_ID), self.scope)
        self.assertEqual(commit_change.exception.code, "PROPOSAL_CHANGED")
        self.assertNotIn("secret", str(commit_change.exception))

    async def test_warning_unknown_block_hash_change_and_zero_candidates_write_nothing(self):
        good_service, _ = self.service()
        parsed = await good_service.parser.parse(
            payload=PAYLOAD, filename="approved.md", media_type="text/markdown"
        )
        cases = (
            (
                RuntimeParserResult(
                    parsed.document_format,
                    parsed.size_bytes,
                    parsed.source_sha256,
                    parsed.blocks,
                    (ParserWarning("unsupported"),),
                ),
                "PARSER_WARNING",
            ),
            (
                RuntimeParserResult(
                    parsed.document_format,
                    parsed.size_bytes,
                    parsed.source_sha256,
                    (object(),),
                    (),
                ),
                "PARSER_FAILED",
            ),
            (
                RuntimeParserResult(
                    parsed.document_format,
                    parsed.size_bytes,
                    "0" * 64,
                    parsed.blocks,
                    (),
                ),
                "SOURCE_INTEGRITY_FAILED",
            ),
            (
                RuntimeParserResult(
                    parsed.document_format,
                    parsed.size_bytes,
                    parsed.source_sha256,
                    tuple(
                        dataclasses.replace(block, section_path=("Overview",))
                        for block in parsed.blocks
                    ),
                    (),
                ),
                "NO_CANDIDATES",
            ),
        )
        for parser_result, code in cases:
            with self.subTest(code=code):
                review = FakeReviewService()
                service, _ = self.service(
                    parser=FakeParser(result=parser_result), review=review
                )
                with self.assertRaises(PreparationFailure) as raised:
                    await service.prepare_and_create_run(
                        PreparationRequest(PROJECT_ID), self.scope
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(review.requests, [])

    async def test_candidate_batch_limit_fails_before_review_write(self):
        good_service, _ = self.service()
        parsed = await good_service.parser.parse(
            payload=PAYLOAD, filename="approved.md", media_type="text/markdown"
        )
        paragraph = parsed.blocks[1]
        blocks = (parsed.blocks[0],) + tuple(
            dataclasses.replace(
                paragraph,
                text=f"Deliverable {index}",
                location=SourceLocation.from_mapping(
                    {
                        "kind": "line_range",
                        "startLine": index + 2,
                        "endLine": index + 2,
                    }
                ),
            )
            for index in range(MAX_REVIEW_CANDIDATES + 1)
        )
        oversized = RuntimeParserResult(
            parsed.document_format,
            parsed.size_bytes,
            parsed.source_sha256,
            blocks,
            (),
        )
        review = FakeReviewService()
        service, _ = self.service(parser=FakeParser(result=oversized), review=review)

        with self.assertRaises(PreparationFailure) as raised:
            await service.prepare_and_create_run(
                PreparationRequest(PROJECT_ID), self.scope
            )

        self.assertEqual(raised.exception.code, "CANDIDATE_LIMIT_EXCEEDED")
        self.assertEqual(review.requests, [])

    async def test_injected_parser_cannot_bypass_block_or_cell_text_limits(self):
        good_service, _ = self.service()
        parsed = await good_service.parser.parse(
            payload=PAYLOAD, filename="approved.md", media_type="text/markdown"
        )
        oversized_text = "x" * (MAX_BLOCK_TEXT_CHARS + 1)
        oversized_block = dataclasses.replace(parsed.blocks[1], text=oversized_text)
        oversized_cell = ParserBlock.from_mapping(
            {
                "kind": "table",
                "columns": ["Deliverable"],
                "rows": [
                    {
                        "rowNumber": 2,
                        "cells": [
                            {
                                "column": "Deliverable",
                                "value": oversized_text,
                                "location": {
                                    "kind": "markdown_table_cell",
                                    "line": 2,
                                    "columnIndex": 1,
                                },
                            }
                        ],
                    }
                ],
                "headerRowExplicit": True,
                "sectionPath": ["Deliverables"],
                "location": {
                    "kind": "line_range",
                    "startLine": 1,
                    "endLine": 2,
                },
            }
        )

        for blocks in ((oversized_block,), (oversized_cell,)):
            with self.subTest(kind=blocks[0].kind):
                parser_result = RuntimeParserResult(
                    parsed.document_format,
                    parsed.size_bytes,
                    parsed.source_sha256,
                    blocks,
                    (),
                )
                review = FakeReviewService()
                service, _ = self.service(
                    parser=FakeParser(result=parser_result), review=review
                )
                with self.assertRaises(PreparationFailure) as raised:
                    await service.prepare_and_create_run(
                        PreparationRequest(PROJECT_ID), self.scope
                    )
                self.assertEqual(raised.exception.code, "PARSER_LIMIT_EXCEEDED")
                self.assertEqual(review.requests, [])

    async def test_injected_mapping_cannot_bypass_block_text_limit(self):
        good_service, _ = self.service()
        parsed = await good_service.parser.parse(
            payload=PAYLOAD, filename="approved.md", media_type="text/markdown"
        )
        oversized_mapping = {
            "kind": "paragraph",
            "text": "x" * (MAX_BLOCK_TEXT_CHARS + 1),
            "sectionPath": ["Deliverables"],
            "location": {
                "kind": "line_range",
                "startLine": 2,
                "endLine": 2,
            },
        }
        parser_result = RuntimeParserResult(
            parsed.document_format,
            parsed.size_bytes,
            parsed.source_sha256,
            (oversized_mapping,),
            (),
        )
        review = FakeReviewService()
        service, _ = self.service(
            parser=FakeParser(result=parser_result), review=review
        )

        with self.assertRaises(PreparationFailure) as raised:
            await service.prepare_and_create_run(
                PreparationRequest(PROJECT_ID), self.scope
            )

        self.assertEqual(raised.exception.code, "PARSER_LIMIT_EXCEEDED")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(review.requests, [])

    async def test_malformed_parser_block_is_not_reported_as_retryable_source_failure(self):
        good_service, _ = self.service()
        parsed = await good_service.parser.parse(
            payload=PAYLOAD, filename="approved.md", media_type="text/markdown"
        )
        malformed = dataclasses.replace(parsed.blocks[1], text=object())
        parser_result = dataclasses.replace(parsed, blocks=(malformed,))
        review = FakeReviewService()
        service, _ = self.service(
            parser=FakeParser(result=parser_result), review=review
        )

        with self.assertRaises(PreparationFailure) as raised:
            await service.prepare_and_create_run(
                PreparationRequest(PROJECT_ID), self.scope
            )

        self.assertEqual(raised.exception.code, "PARSER_FAILED")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(review.requests, [])

    async def test_parser_validation_exceptions_are_not_retryable_source_failures(self):
        good_service, _ = self.service()
        parsed = await good_service.parser.parse(
            payload=PAYLOAD, filename="approved.md", media_type="text/markdown"
        )
        inconsistent_location = SourceLocation(
            "line_range",
            (
                ("kind", "docx_table"),
                ("part", "word/document.xml"),
                ("bodyIndex", 1),
                ("table", 1),
            ),
        )
        inconsistent_block = dataclasses.replace(
            ParserBlock.from_mapping(
                {
                    "kind": "table",
                    "columns": ["Deliverable"],
                    "rows": [
                        {
                            "rowNumber": 2,
                            "cells": [
                                {
                                    "column": "Deliverable",
                                    "value": "Registration page",
                                    "location": {
                                        "kind": "markdown_table_cell",
                                        "line": 2,
                                        "columnIndex": 1,
                                    },
                                }
                            ],
                        }
                    ],
                    "headerRowExplicit": True,
                    "sectionPath": ["Deliverables"],
                    "location": {
                        "kind": "line_range",
                        "startLine": 1,
                        "endLine": 2,
                    },
                }
            ),
            location=inconsistent_location,
        )

        for block in (
            ExplodingMapping(),
            SpoofingFailureMapping(),
            inconsistent_block,
        ):
            with self.subTest(block_type=type(block).__name__):
                parser_result = dataclasses.replace(parsed, blocks=(block,))
                review = FakeReviewService()
                service, _ = self.service(
                    parser=FakeParser(result=parser_result), review=review
                )

                with self.assertRaises(PreparationFailure) as raised:
                    await service.prepare_and_create_run(
                        PreparationRequest(PROJECT_ID), self.scope
                    )

                self.assertEqual(raised.exception.code, "PARSER_FAILED")
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(review.requests, [])

        tuple_result = dataclasses.replace(
            parsed, blocks=SpoofingTuple((parsed.blocks[0],))
        )
        review = FakeReviewService()
        service, _ = self.service(
            parser=FakeParser(result=tuple_result), review=review
        )
        with self.assertRaises(PreparationFailure) as raised:
            await service.prepare_and_create_run(
                PreparationRequest(PROJECT_ID), self.scope
            )
        self.assertEqual(raised.exception.code, "PARSER_FAILED")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(review.requests, [])

    async def test_repository_object_parser_and_service_failures_are_sanitized(self):
        failures = (
            (
                {"reader": FakeSourceReader(self.source, RuntimeError("postgresql://secret"))},
                "SOURCE_READ_FAILED",
            ),
            (
                {"store": FakeObjectStore(failure=ValueError("C:/customer/proposal.md"))},
                "SOURCE_INTEGRITY_FAILED",
            ),
            (
                {"parser": FakeParser(failure=RuntimeError("customer proposal body"))},
                "PARSER_FAILED",
            ),
            (
                {"review": FakeReviewService(RuntimeError("SELECT secret FROM customer"))},
                "REVIEW_CREATE_FAILED",
            ),
        )
        for dependencies, code in failures:
            with self.subTest(code=code):
                service, review = self.service(**dependencies)
                with self.assertRaises(PreparationFailure) as raised:
                    await service.prepare_and_create_run(
                        PreparationRequest(PROJECT_ID), self.scope
                    )
                self.assertEqual(raised.exception.code, code)
                rendered = str(raised.exception).lower()
                self.assertNotIn("secret", rendered)
                self.assertNotIn("customer", rendered)
                if code != "REVIEW_CREATE_FAILED":
                    self.assertEqual(review.requests, [])

    async def test_cancellation_from_every_async_stage_propagates_without_later_calls(self):
        cases = (
            {"store": FakeObjectStore(cancel_guard=True)},
            {"reader": FakeSourceReader(self.source, asyncio.CancelledError())},
            {"store": FakeObjectStore(failure=asyncio.CancelledError())},
            {"parser": FakeParser(failure=asyncio.CancelledError())},
            {"review": FakeReviewService(asyncio.CancelledError())},
        )
        for dependencies in cases:
            with self.subTest(dependencies=tuple(dependencies)):
                service, _ = self.service(**dependencies)
                with self.assertRaises(asyncio.CancelledError):
                    await service.prepare_and_create_run(
                        PreparationRequest(PROJECT_ID), self.scope
                    )

    async def test_cross_scope_and_malformed_inputs_fail_before_object_read(self):
        leaked = dataclasses.replace(self.source, workspace_id=ACTOR_ID)
        store = FakeObjectStore()
        service, review = self.service(reader=FakeSourceReader(leaked), store=store)
        with self.assertRaises(PreparationFailure) as scope_failure:
            await service.prepare_and_create_run(PreparationRequest(PROJECT_ID), self.scope)
        self.assertEqual(scope_failure.exception.code, "PROPOSAL_NOT_AVAILABLE")
        self.assertEqual(store.reads, 0)
        self.assertEqual(review.requests, [])

        service, review = self.service()
        with self.assertRaises(PreparationFailure) as invalid:
            await service.prepare_and_create_run(PreparationRequest("not-a-uuid"), self.scope)
        self.assertEqual(invalid.exception.code, "INVALID_INPUT")
        self.assertEqual(review.requests, [])


class AsyncpgApprovedProposalSourceReaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ReviewScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
        )
        self.row = {
            "organization_id": ORGANIZATION_ID,
            "workspace_id": WORKSPACE_ID,
            "client_id": CLIENT_ID,
            "project_id": PROJECT_ID,
            "artifact_id": ARTIFACT_ID,
            "version_id": VERSION_ID,
            "proposal_version": 3,
            "original_filename": "approved.md",
            "media_type": "text/markdown",
            "byte_size": len(PAYLOAD),
            "storage_key": "opaque-storage-key",
            "sha256": bytes.fromhex(SHA256),
        }

    async def test_reads_current_approved_source_in_transaction_local_rls_scope(self):
        connection = FakeSourceConnection(self.row)
        pool = FakeSourcePool(connection)
        source = await AsyncpgApprovedProposalSourceReader(pool).read_approved_proposal(
            self.scope, PROJECT_ID
        )

        self.assertEqual(source.version_id, VERSION_ID)
        self.assertEqual(source.sha256, SHA256)
        self.assertEqual(pool.acquire_context.entered, 1)
        self.assertEqual(pool.acquire_context.exited, 1)
        self.assertEqual(connection.transaction_context.entered, 1)
        self.assertEqual(connection.transaction_context.exited, 1)
        scope_sql, scope_arguments = connection.calls[0]
        read_sql, read_arguments = connection.calls[1]
        self.assertIn("set_config('app.workspace_id'", scope_sql)
        self.assertEqual(
            scope_arguments,
            (WORKSPACE_ID, CLIENT_ID, PROJECT_ID, ACTOR_ID),
        )
        self.assertIn("project.approved_proposal_version_id", read_sql)
        self.assertIn("project.created_by = $5", read_sql)
        self.assertIn("version.approval_status = 'approved'", read_sql)
        self.assertNotIn("FOR UPDATE", read_sql)
        self.assertEqual(
            read_arguments,
            (ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, PROJECT_ID, ACTOR_ID),
        )

    async def test_missing_invalid_failure_and_cancellation_fail_closed(self):
        missing = await AsyncpgApprovedProposalSourceReader(
            FakeSourcePool(FakeSourceConnection(None))
        ).read_approved_proposal(self.scope, PROJECT_ID)
        self.assertIsNone(missing)

        invalid_row = dict(self.row)
        invalid_row["sha256"] = b"short"
        with self.assertRaises(ApprovedProposalSourceReadError) as invalid:
            await AsyncpgApprovedProposalSourceReader(
                FakeSourcePool(FakeSourceConnection(invalid_row))
            ).read_approved_proposal(self.scope, PROJECT_ID)
        self.assertNotIn("short", str(invalid.exception))

        with self.assertRaises(ApprovedProposalSourceReadError) as failed:
            await AsyncpgApprovedProposalSourceReader(
                FakeSourcePool(
                    FakeSourceConnection(failure=RuntimeError("postgresql://secret"))
                )
            ).read_approved_proposal(self.scope, PROJECT_ID)
        self.assertNotIn("secret", str(failed.exception))
        self.assertIsNone(failed.exception.__cause__)

        with self.assertRaises(asyncio.CancelledError):
            await AsyncpgApprovedProposalSourceReader(
                FakeSourcePool(
                    FakeSourceConnection(failure=asyncio.CancelledError())
                )
            ).read_approved_proposal(self.scope, PROJECT_ID)


if __name__ == "__main__":
    unittest.main()
