import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation
from apps.api.marketops_review import (
    AsyncpgReviewRepository,
    CreateReviewRunRequest,
    ReviewFailure,
    ReviewPostgresError,
    ReviewRequest,
    ReviewScopeContext,
    ReviewService,
)


ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"
CLIENT_ID = "00000000-0000-4000-8000-000000000003"
PROJECT_ID = "00000000-0000-4000-8000-000000000004"
ACTOR_ID = "00000000-0000-4000-8000-000000000005"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000006"
VERSION_ID = "00000000-0000-4000-8000-000000000007"
RUN_ID = "00000000-0000-4000-8000-000000000008"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000009"
SNAPSHOT_ONE_ID = "00000000-0000-4000-8000-000000000010"
SNAPSHOT_TWO_ID = "00000000-0000-4000-8000-000000000011"
DECISION_ID = "00000000-0000-4000-8000-000000000012"
AUDIT_ID = "00000000-0000-4000-8000-000000000013"
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SOURCE_HASH = "a" * 64


def make_candidate():
    return Candidate(
        candidate_id=CANDIDATE_ID,
        kind="deliverable",
        text="Publish the reviewed execution brief.",
        classification="fact",
        confidence=0.9,
        source_citation=SourceCitation(
            source_version_id=VERSION_ID,
            source_sha256=SOURCE_HASH,
            location=SourceLocation.from_mapping(
                {"kind": "line_range", "startLine": 8, "endLine": 8}
            ),
            section_path=("Deliverables",),
            quote="Publish the reviewed execution brief.",
        ),
    )


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        self.connection.transactions += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.connection.commits += 1
        else:
            self.connection.rollbacks += 1
        return False


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0
        self.fail_write = False
        self.snapshot_version = 1

    def transaction(self):
        return FakeTransaction(self)

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        if "set_config('app.workspace_id'" in query:
            return {"scope": True}
        if "pg_advisory_xact_lock" in query:
            return {"locked": True}
        if "FROM marketops.projects AS project" in query:
            return {
                "organization_id": ORGANIZATION_ID,
                "workspace_id": WORKSPACE_ID,
                "client_id": CLIENT_ID,
                "project_id": PROJECT_ID,
                "artifact_id": ARTIFACT_ID,
                "version_id": VERSION_ID,
                "proposal_version": 1,
                "sha256": bytes.fromhex(SOURCE_HASH),
            }
        if "FROM marketops.extraction_runs" in query and "proposal_artifact_id" in query:
            return {
                "id": RUN_ID,
                "organization_id": ORGANIZATION_ID,
                "workspace_id": WORKSPACE_ID,
                "client_id": CLIENT_ID,
                "project_id": PROJECT_ID,
                "proposal_artifact_id": ARTIFACT_ID,
                "proposal_version_id": VERSION_ID,
                "proposal_version": 1,
                "proposal_sha256": bytes.fromhex(SOURCE_HASH),
                "candidate_count": 1,
                "created_by": ACTOR_ID,
                "created_at": NOW,
            }
        if "FROM marketops.extraction_runs" in query:
            return {"id": RUN_ID}
        if "FROM marketops.review_snapshots" in query:
            return {
                "id": SNAPSHOT_ONE_ID,
                "run_id": RUN_ID,
                "review_version": self.snapshot_version,
                "created_by": ACTOR_ID,
                "created_at": NOW,
            }
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        if "FROM marketops.review_snapshot_items" in query:
            return [
                {
                    "candidate_id": CANDIDATE_ID,
                    "status": "pending",
                    "replacement_text": None,
                }
            ]
        raise AssertionError(f"unexpected fetch query: {query}")

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        if self.fail_write:
            raise RuntimeError("postgres leaked top-secret DSN")
        return "INSERT 0 1"


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class SequenceIds:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class ReviewPostgresAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ReviewScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
        )

    def service(self, connection, ids):
        return ReviewService(
            repository=AsyncpgReviewRepository(FakePool(connection)),
            id_factory=SequenceIds(ids),
            clock=lambda: NOW,
        )

    async def test_create_run_sets_full_scope_locks_source_and_writes_one_transaction(self):
        connection = FakeConnection()
        service = self.service(connection, (RUN_ID, SNAPSHOT_ONE_ID, AUDIT_ID))
        result = await service.create_run(
            CreateReviewRunRequest(
                project_id=PROJECT_ID,
                proposal_artifact_id=ARTIFACT_ID,
                proposal_version_id=VERSION_ID,
                proposal_version=1,
                proposal_sha256=SOURCE_HASH,
                candidates=(make_candidate(),),
            ),
            self.scope,
        )

        self.assertEqual(result.run.run_id, RUN_ID)
        self.assertEqual((connection.transactions, connection.commits, connection.rollbacks), (1, 1, 0))
        statements = [query for _, query, _ in connection.calls]
        self.assertIn("set_config('app.project_id', $3, true)", statements[0])
        lock_index = next(i for i, query in enumerate(statements) if "pg_advisory_xact_lock" in query)
        source_index = next(i for i, query in enumerate(statements) if "FROM marketops.projects AS project" in query)
        self.assertLess(lock_index, source_index)
        self.assertEqual(sum("INSERT INTO marketops.extraction_candidates" in query for query in statements), 1)
        self.assertEqual(sum("INSERT INTO marketops.review_snapshot_items" in query for query in statements), 1)
        self.assertEqual(sum("INSERT INTO marketops.audit_events" in query for query in statements), 1)

    async def test_review_locks_run_before_reading_latest_snapshot(self):
        connection = FakeConnection()
        service = self.service(connection, (SNAPSHOT_TWO_ID, DECISION_ID, AUDIT_ID))
        result = await service.review_candidate(
            PROJECT_ID,
            RUN_ID,
            ReviewRequest(1, CANDIDATE_ID, "approve", "Ready"),
            self.scope,
        )

        self.assertEqual(result.snapshot.version, 2)
        statements = [query for _, query, _ in connection.calls]
        lock_index = next(i for i, query in enumerate(statements) if "review-run:" in str(connection.calls[i][2]))
        latest_index = next(i for i, query in enumerate(statements) if "ORDER BY review_version DESC" in query)
        self.assertLess(lock_index, latest_index)
        self.assertEqual(sum("INSERT INTO marketops.review_decisions" in query for query in statements), 1)
        self.assertEqual(sum("INSERT INTO marketops.audit_events" in query for query in statements), 1)

    async def test_write_error_is_sanitized_and_transaction_rolls_back(self):
        connection = FakeConnection()
        connection.fail_write = True
        service = self.service(connection, (RUN_ID, SNAPSHOT_ONE_ID, AUDIT_ID))
        with self.assertRaises(ReviewFailure) as raised:
            await service.create_run(
                CreateReviewRunRequest(
                    PROJECT_ID,
                    ARTIFACT_ID,
                    VERSION_ID,
                    1,
                    SOURCE_HASH,
                    (make_candidate(),),
                ),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")
        self.assertNotIn("top-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual((connection.commits, connection.rollbacks), (0, 1))

    async def test_transaction_rejects_scope_mismatched_run_before_sql_write(self):
        connection = FakeConnection()
        repository = AsyncpgReviewRepository(FakePool(connection))
        from apps.api.marketops_review import ReviewRun

        wrong = ReviewRun(
            RUN_ID,
            ORGANIZATION_ID,
            WORKSPACE_ID,
            CLIENT_ID,
            "00000000-0000-4000-8000-000000000099",
            ARTIFACT_ID,
            VERSION_ID,
            1,
            SOURCE_HASH,
            1,
            ACTOR_ID,
            NOW,
        )
        with self.assertRaises(ReviewPostgresError):
            async with repository.transaction(self.scope, PROJECT_ID) as transaction:
                await transaction.insert_run(wrong)
        self.assertFalse(any(kind == "execute" for kind, _, _ in connection.calls))
        self.assertEqual(connection.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
