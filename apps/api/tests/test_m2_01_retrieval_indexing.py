from __future__ import annotations

import asyncio
import hashlib
import unittest
from contextlib import asynccontextmanager

from apps.api.marketops_extract.parser import RuntimeProposalParser
from apps.api.marketops_import.service import StoredObject
from apps.api.marketops_retrieval import (
    ArtifactVersionSource,
    IndexSourceRequest,
    PersistIndexResult,
    RetrievalFailure,
    RetrievalScopeContext,
    SourceIndexingService,
)


IDS = {
    "organization": "00000000-0000-4000-8000-000000000101",
    "workspace": "00000000-0000-4000-8000-000000000102",
    "client": "00000000-0000-4000-8000-000000000103",
    "actor": "00000000-0000-4000-8000-000000000104",
    "project": "00000000-0000-4000-8000-000000000105",
    "artifact": "00000000-0000-4000-8000-000000000106",
    "version": "00000000-0000-4000-8000-000000000107",
}


class FakeReader:
    def __init__(self, source):
        self.source = source
        self.calls = []

    async def read_source(self, scope, project_id, artifact_version_id):
        self.calls.append((scope, project_id, artifact_version_id))
        return self.source


class FakeStore:
    def __init__(self, payload: bytes, *, failure=None):
        self.payload = payload
        self.failure = failure
        self.calls = []
        self.guard_entries = 0

    @asynccontextmanager
    async def import_guard(self):
        self.guard_entries += 1
        yield

    async def read_verified_immutable(self, *, kind: str, stored: StoredObject):
        self.calls.append((kind, stored))
        if self.failure is not None:
            raise self.failure
        return self.payload


class FakeRepository:
    def __init__(self, *, replayed=False):
        self.replayed = replayed
        self.calls = []

    async def persist_index(self, index, scope):
        self.calls.append((index, scope))
        return PersistIndexResult(index, self.replayed)


class CancellingParser:
    async def parse(self, **_kwargs):
        raise asyncio.CancelledError()


class RetrievalIndexingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.payload = (
            b"# Constraints\n"
            b"| Constraint | Value |\n"
            b"| --- | --- |\n"
            b"| Capacity | 360 |\n"
        )
        self.scope = RetrievalScopeContext(
            IDS["organization"], IDS["workspace"], IDS["client"], IDS["actor"]
        )
        self.source = ArtifactVersionSource(
            organization_id=IDS["organization"],
            workspace_id=IDS["workspace"],
            client_id=IDS["client"],
            project_id=IDS["project"],
            artifact_id=IDS["artifact"],
            artifact_kind="source",
            artifact_version_id=IDS["version"],
            filename="source.md",
            media_type="text/markdown",
            size_bytes=len(self.payload),
            storage_key="opaque-test-key",
            sha256=hashlib.sha256(self.payload).hexdigest(),
        )
        self.request = IndexSourceRequest(IDS["project"], IDS["version"])

    def service(self, *, source=None, store=None, repository=None, parser=None):
        return SourceIndexingService(
            source_reader=FakeReader(self.source if source is None else source),
            object_store=store or FakeStore(self.payload),
            repository=repository or FakeRepository(),
            parser=parser or RuntimeProposalParser(),
        )

    async def test_server_loads_verifies_parses_and_persists_selected_version(self):
        store = FakeStore(self.payload)
        repository = FakeRepository(replayed=True)
        result = await self.service(store=store, repository=repository).index_source(
            self.request, self.scope
        )

        self.assertTrue(result.replayed)
        self.assertEqual(result.document_format, "markdown")
        self.assertEqual(result.index.source.artifact_version_id, IDS["version"])
        self.assertEqual(result.index.source.source_sha256, self.source.sha256)
        self.assertEqual(result.index.source.parser_version, "marketops-parser-v1")
        self.assertEqual(store.guard_entries, 1)
        self.assertEqual(store.calls[0][0], "source")
        self.assertEqual(repository.calls[0][1], self.scope)
        texts = [chunk.text for chunk in result.index.chunks]
        self.assertEqual(
            texts,
            ["Constraints", "Constraint: Capacity", "Value: 360"],
        )
        self.assertEqual(
            result.index.chunks[1].location["kind"], "markdown_table_cell"
        )
        self.assertEqual(
            result.index.chunks[1].location["tableLocation"]["kind"], "line_range"
        )

    async def test_cross_scope_or_version_source_fails_before_object_read(self):
        store = FakeStore(self.payload)
        foreign = ArtifactVersionSource(
            **{
                **self.source.__dict__,
                "project_id": "00000000-0000-4000-8000-000000000999",
            }
        )
        with self.assertRaises(RetrievalFailure) as raised:
            await self.service(source=foreign, store=store).index_source(
                self.request, self.scope
            )
        self.assertEqual(raised.exception.code, "SOURCE_NOT_FOUND")
        self.assertEqual(store.calls, [])

    async def test_object_failure_is_sanitized_and_not_persisted(self):
        repository = FakeRepository()
        store = FakeStore(
            self.payload, failure=ValueError("C:/customer/private-source.md")
        )
        with self.assertRaises(RetrievalFailure) as raised:
            await self.service(store=store, repository=repository).index_source(
                self.request, self.scope
            )
        self.assertEqual(raised.exception.code, "SOURCE_INTEGRITY_FAILED")
        self.assertNotIn("customer", str(raised.exception))
        self.assertEqual(repository.calls, [])

    async def test_parser_warning_fails_closed_before_persistence(self):
        payload = b"# Context\n```\nignored\n```\n"
        source = ArtifactVersionSource(
            **{
                **self.source.__dict__,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        repository = FakeRepository()
        with self.assertRaises(RetrievalFailure) as raised:
            await self.service(
                source=source,
                store=FakeStore(payload),
                repository=repository,
            ).index_source(self.request, self.scope)
        self.assertEqual(raised.exception.code, "PARSER_WARNING")
        self.assertEqual(repository.calls, [])

    async def test_csv_import_is_not_misrepresented_as_runtime_parser_support(self):
        payload = b"name,value\ncapacity,360\n"
        source = ArtifactVersionSource(
            **{
                **self.source.__dict__,
                "filename": "source.csv",
                "media_type": "text/csv",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        repository = FakeRepository()
        with self.assertRaises(RetrievalFailure) as raised:
            await self.service(
                source=source,
                store=FakeStore(payload),
                repository=repository,
            ).index_source(self.request, self.scope)
        self.assertEqual(raised.exception.code, "UNSUPPORTED_FORMAT")
        self.assertEqual(repository.calls, [])

    async def test_cancellation_propagates_without_repository_write(self):
        repository = FakeRepository()
        with self.assertRaises(asyncio.CancelledError):
            await self.service(
                repository=repository, parser=CancellingParser()
            ).index_source(self.request, self.scope)
        self.assertEqual(repository.calls, [])


if __name__ == "__main__":
    unittest.main()
