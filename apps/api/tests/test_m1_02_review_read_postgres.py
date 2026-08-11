import asyncio
import copy
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apps.api.marketops_review.postgres import (
    AsyncpgReviewRepository,
    ReviewPostgresError,
)
from apps.api.marketops_review.service import ReviewScopeContext


ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"
CLIENT_ID = "00000000-0000-4000-8000-000000000003"
PROJECT_ID = "00000000-0000-4000-8000-000000000004"
ACTOR_ID = "00000000-0000-4000-8000-000000000005"
OTHER_ACTOR_ID = "00000000-0000-4000-8000-000000000006"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000007"
VERSION_ID = "00000000-0000-4000-8000-000000000008"
RUN_ID = "00000000-0000-4000-8000-000000000009"
CANDIDATE_A = "00000000-0000-4000-8000-000000000010"
CANDIDATE_B = "00000000-0000-4000-8000-000000000011"
SNAPSHOT_1 = "00000000-0000-4000-8000-000000000012"
SNAPSHOT_2 = "00000000-0000-4000-8000-000000000013"
SNAPSHOT_3 = "00000000-0000-4000-8000-000000000014"
DECISION_2 = "00000000-0000-4000-8000-000000000015"
DECISION_3 = "00000000-0000-4000-8000-000000000016"
SOURCE_HASH = "a" * 64
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def run_row():
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
        "candidate_count": 2,
        "created_by": ACTOR_ID,
        "created_at": NOW,
    }


def candidate_row(candidate_id, ordinal, *, status, replacement=None):
    text = "Revise launch brief." if ordinal == 1 else "Approve media plan."
    return {
        "candidate_id": candidate_id,
        "candidate_ordinal": ordinal,
        "kind": "deliverable",
        "candidate_text": text,
        "classification": "fact",
        "confidence": 0.9,
        "source_version_id": VERSION_ID,
        "source_sha256": bytes.fromhex(SOURCE_HASH),
        "source_location": {
            "kind": "line_range",
            "startLine": ordinal,
            "endLine": ordinal,
        },
        "section_path": ["Deliverables"],
        "source_quote": text,
        "review_status": "pending",
        "item_ordinal": ordinal,
        "status": status,
        "replacement_text": replacement,
        "validated_source_version_id": VERSION_ID,
        "validated_source_sha256": bytes.fromhex(SOURCE_HASH),
    }


def decision_row(version, candidate_id, action, reason, replacement=None):
    return {
        "id": DECISION_2 if version == 2 else DECISION_3,
        "run_id": RUN_ID,
        "review_version": version,
        "candidate_id": candidate_id,
        "action": action,
        "reason": reason,
        "comment": f"comment-v{version}",
        "replacement_text": replacement,
        "actor_id": ACTOR_ID,
        "created_at": NOW,
    }


class FakeTransaction:
    def __init__(self, connection, readonly):
        self.connection = connection
        self.readonly = readonly

    async def __aenter__(self):
        self.connection.transaction_modes.append(self.readonly)
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.transaction_modes = []
        self.cancel_on = None
        self.fail_on = None
        self.summary_rows = [
            {
                **run_row(),
                "actual_candidate_count": 2,
                "snapshot_count": 3,
                "min_review_version": 1,
                "latest_review_version": 3,
            }
        ]
        self.headers = [
            {
                "id": snapshot_id,
                "run_id": RUN_ID,
                "review_version": version,
                "created_by": ACTOR_ID,
                "created_at": NOW,
            }
            for version, snapshot_id in ((1, SNAPSHOT_1), (2, SNAPSHOT_2), (3, SNAPSHOT_3))
        ]
        self.candidates = {
            SNAPSHOT_1: [
                candidate_row(CANDIDATE_A, 1, status="pending"),
                candidate_row(CANDIDATE_B, 2, status="pending"),
            ],
            SNAPSHOT_2: [
                candidate_row(
                    CANDIDATE_A, 1, status="modify", replacement="Revised brief"
                ),
                candidate_row(CANDIDATE_B, 2, status="pending"),
            ],
            SNAPSHOT_3: [
                candidate_row(
                    CANDIDATE_A, 1, status="modify", replacement="Revised brief"
                ),
                candidate_row(CANDIDATE_B, 2, status="approve"),
            ],
        }
        self.decisions = [
            decision_row(
                2,
                CANDIDATE_A,
                "modify",
                "Clarify the launch promise",
                "Revised brief",
            ),
            decision_row(3, CANDIDATE_B, "approve", "Media plan is ready"),
        ]

    def transaction(self, *, readonly=False):
        return FakeTransaction(self, readonly)

    def _trigger(self, stage):
        if self.cancel_on == stage:
            raise asyncio.CancelledError
        if self.fail_on == stage:
            raise RuntimeError("postgres password=top-secret")

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        if "set_config('app.workspace_id'" in query:
            self._trigger("scope")
            return {"scope": True}
        if "FROM marketops.extraction_runs" in query:
            self._trigger("run")
            return copy.deepcopy(run_row()) if args[4] == ACTOR_ID else None
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        if "CROSS JOIN LATERAL" in query:
            self._trigger("list")
            return copy.deepcopy(self.summary_rows)
        if "FROM marketops.review_snapshots" in query:
            self._trigger("headers")
            return copy.deepcopy(self.headers)
        if "FROM marketops.extraction_candidates AS candidate" in query:
            self._trigger("candidates")
            return copy.deepcopy(self.candidates[args[1]])
        if "FROM marketops.review_decisions" in query:
            self._trigger("decisions")
            return copy.deepcopy(
                [row for row in self.decisions if row["review_version"] <= args[1]]
            )
        raise AssertionError(f"unexpected fetch query: {query}")


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class ReviewReadPostgresTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ReviewScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
        )
        self.connection = FakeConnection()
        self.repository = AsyncpgReviewRepository(FakePool(self.connection))

    async def test_list_and_latest_use_read_only_transactions_and_stable_query(self):
        summaries = await self.repository.list_review_runs(
            self.scope, PROJECT_ID, 25
        )
        result = await self.repository.get_review(
            self.scope, PROJECT_ID, RUN_ID, None
        )

        self.assertEqual(summaries[0].latest_review_version, 3)
        self.assertEqual(result.snapshot.version, 3)
        self.assertEqual(result.available_review_versions, (1, 2, 3))
        self.assertEqual(result.selected_decision.review_version, 3)
        self.assertEqual(result.candidates[0].last_decision.review_version, 2)
        self.assertEqual(
            result.candidates[0].last_decision.reason,
            "Clarify the launch promise",
        )
        self.assertEqual(result.candidates[1].last_decision.review_version, 3)
        self.assertEqual(self.connection.transaction_modes, [True, True])
        statements = [query.upper() for _, query, _ in self.connection.calls]
        self.assertTrue(all("FOR UPDATE" not in query for query in statements))
        self.assertTrue(all("INSERT " not in query for query in statements))
        list_sql = next(query for query in statements if "CROSS JOIN LATERAL" in query)
        self.assertIn("ORDER BY RUN.CREATED_AT DESC, RUN.ID DESC", list_sql)

    async def test_as_of_v2_keeps_prior_reason_and_never_reads_v3_decision(self):
        result = await self.repository.get_review(
            self.scope, PROJECT_ID, RUN_ID, 2
        )

        self.assertEqual(result.snapshot.version, 2)
        self.assertEqual(result.selected_decision.review_version, 2)
        self.assertEqual(result.candidates[0].last_decision.review_version, 2)
        self.assertIsNone(result.candidates[1].last_decision)
        decision_call = next(
            call for call in self.connection.calls if "review_decisions" in call[1]
        )
        self.assertEqual(decision_call[2], (RUN_ID, 2))
        self.assertIn("review_version <= $2", decision_call[1])

    async def test_top_level_semantic_table_candidate_can_have_empty_section_path(self):
        for rows in self.connection.candidates.values():
            for row in rows:
                row["section_path"] = []

        result = await self.repository.get_review(
            self.scope, PROJECT_ID, RUN_ID, None
        )

        self.assertTrue(
            all(view.candidate.source_citation.section_path == () for view in result.candidates)
        )

    async def test_missing_version_and_cross_actor_return_none(self):
        missing = await self.repository.get_review(
            self.scope, PROJECT_ID, RUN_ID, 99
        )
        other_scope = ReviewScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, OTHER_ACTOR_ID
        )
        cross_actor = await self.repository.get_review(
            other_scope, PROJECT_ID, RUN_ID, None
        )
        self.assertIsNone(missing)
        self.assertIsNone(cross_actor)
        run_query = next(
            query for _, query, _ in self.connection.calls if "created_by = $5" in query
        )
        self.assertIn("organization_id = $1", run_query)
        self.assertIn("workspace_id = $2", run_query)
        self.assertIn("client_id = $3", run_query)
        self.assertIn("project_id = $4", run_query)

    async def test_malformed_rows_fail_closed(self):
        cases = {
            "candidate count": lambda c: c.candidates[SNAPSHOT_3].pop(),
            "item ordinal": lambda c: c.candidates[SNAPSHOT_3][1].update(item_ordinal=1),
            "citation source": lambda c: c.candidates[SNAPSHOT_3][0].update(
                source_sha256=bytes.fromhex("b" * 64)
            ),
            "version gap": lambda c: c.headers[1].update(review_version=4),
            "decision gap": lambda c: c.decisions.pop(0),
            "pending decision": lambda c: c.candidates[SNAPSHOT_3][1].update(
                status="pending"
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                connection = FakeConnection()
                mutate(connection)
                repository = AsyncpgReviewRepository(FakePool(connection))
                with self.assertRaises(ReviewPostgresError):
                    await repository.get_review(
                        self.scope, PROJECT_ID, RUN_ID, None
                    )

    async def test_summary_integrity_failure_is_rejected(self):
        self.connection.summary_rows[0]["actual_candidate_count"] = 1
        with self.assertRaises(ReviewPostgresError):
            await self.repository.list_review_runs(self.scope, PROJECT_ID, 25)

        connection = FakeConnection()
        connection.summary_rows[0]["snapshot_count"] = 2
        repository = AsyncpgReviewRepository(FakePool(connection))
        with self.assertRaises(ReviewPostgresError):
            await repository.list_review_runs(self.scope, PROJECT_ID, 25)

    async def test_driver_errors_are_sanitized_and_cancellation_propagates(self):
        self.connection.fail_on = "decisions"
        with self.assertRaises(ReviewPostgresError) as raised:
            await self.repository.get_review(self.scope, PROJECT_ID, RUN_ID, None)
        self.assertNotIn("top-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

        connection = FakeConnection()
        connection.cancel_on = "headers"
        repository = AsyncpgReviewRepository(FakePool(connection))
        with self.assertRaises(asyncio.CancelledError):
            await repository.get_review(self.scope, PROJECT_ID, RUN_ID, None)


if __name__ == "__main__":
    unittest.main()
