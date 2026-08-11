import asyncio
import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation
from apps.api.marketops_review.service import (
    ReviewCandidateView,
    ReviewDecision,
    ReviewFailure,
    ReviewReadModel,
    ReviewRun,
    ReviewRunSummary,
    ReviewScopeContext,
    ReviewService,
    ReviewSnapshot,
    ReviewSnapshotItem,
)


ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"
CLIENT_ID = "00000000-0000-4000-8000-000000000003"
PROJECT_ID = "00000000-0000-4000-8000-000000000004"
ACTOR_ID = "00000000-0000-4000-8000-000000000005"
OTHER_ACTOR_ID = "00000000-0000-4000-8000-000000000006"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000007"
VERSION_ID = "00000000-0000-4000-8000-000000000008"
OTHER_VERSION_ID = "00000000-0000-4000-8000-000000000009"
RUN_ID = "00000000-0000-4000-8000-000000000010"
OLDER_RUN_ID = "00000000-0000-4000-8000-000000000011"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000012"
SNAPSHOT_ONE_ID = "00000000-0000-4000-8000-000000000013"
SNAPSHOT_TWO_ID = "00000000-0000-4000-8000-000000000014"
DECISION_ID = "00000000-0000-4000-8000-000000000015"
SOURCE_HASH = "a" * 64
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def make_candidate(*, version_id=VERSION_ID):
    return Candidate(
        candidate_id=CANDIDATE_ID,
        kind="deliverable",
        text="Publish the reviewed execution brief.",
        classification="fact",
        confidence=0.9,
        source_citation=SourceCitation(
            source_version_id=version_id,
            source_sha256=SOURCE_HASH,
            location=SourceLocation.from_mapping(
                {"kind": "line_range", "startLine": 8, "endLine": 8}
            ),
            section_path=("Deliverables",),
            quote="Publish the reviewed execution brief.",
        ),
    )


def make_run(*, run_id=RUN_ID, actor_id=ACTOR_ID, created_at=NOW):
    return ReviewRun(
        run_id,
        ORGANIZATION_ID,
        WORKSPACE_ID,
        CLIENT_ID,
        PROJECT_ID,
        ARTIFACT_ID,
        VERSION_ID,
        1,
        SOURCE_HASH,
        1,
        actor_id,
        created_at,
    )


def make_model(version=2, *, candidate=None, decision=True):
    candidate = candidate or make_candidate()
    status = "pending" if version == 1 else "approve"
    snapshot = ReviewSnapshot(
        SNAPSHOT_ONE_ID if version == 1 else SNAPSHOT_TWO_ID,
        RUN_ID,
        version,
        (ReviewSnapshotItem(CANDIDATE_ID, status),),
        ACTOR_ID,
        NOW,
    )
    selected_decision = None
    if version > 1 and decision:
        selected_decision = ReviewDecision(
            DECISION_ID,
            RUN_ID,
            version,
            CANDIDATE_ID,
            "approve",
            "Ready",
            None,
            None,
            ACTOR_ID,
            NOW,
        )
    view = ReviewCandidateView(
        1,
        candidate,
        status,
        None,
        selected_decision if version > 1 else None,
    )
    return ReviewReadModel(
        make_run(),
        snapshot,
        (view,),
        (1, 2),
        selected_decision,
    )


class FakeReadRepository:
    def __init__(self):
        self.list_result = ()
        self.read_results = {}
        self.error = None
        self.cancel = False
        self.calls = []

    async def list_review_runs(self, scope, project_id, limit):
        self.calls.append(("list", scope, project_id, limit))
        if self.cancel:
            raise asyncio.CancelledError
        if self.error:
            raise self.error
        return self.list_result

    async def get_review(self, scope, project_id, run_id, review_version):
        self.calls.append(("read", scope, project_id, run_id, review_version))
        if self.cancel:
            raise asyncio.CancelledError
        if self.error:
            raise self.error
        return self.read_results.get(review_version)


class ReviewReadServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ReviewScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
        )
        self.repository = FakeReadRepository()
        self.service = ReviewService(
            repository=self.repository,
            id_factory=lambda: (_ for _ in ()).throw(AssertionError("id factory used")),
            clock=lambda: (_ for _ in ()).throw(AssertionError("clock used")),
        )

    async def test_lists_stable_descending_summaries_and_reads_latest_or_history(self):
        latest = make_model(2)
        historical = make_model(1)
        newer_run = make_run()
        older_run = make_run(
            run_id=OLDER_RUN_ID, created_at=NOW - timedelta(minutes=1)
        )
        self.repository.list_result = (
            ReviewRunSummary(newer_run, 2),
            ReviewRunSummary(older_run, 1),
        )
        self.repository.read_results = {None: latest, 1: historical}

        summaries = await self.service.list_runs(PROJECT_ID, self.scope, limit=2)
        selected_latest = await self.service.read_review(
            PROJECT_ID, RUN_ID, self.scope
        )
        selected_history = await self.service.read_review(
            PROJECT_ID, RUN_ID, self.scope, review_version=1
        )

        self.assertEqual([item.run.run_id for item in summaries], [RUN_ID, OLDER_RUN_ID])
        self.assertEqual(selected_latest.snapshot.version, 2)
        self.assertEqual(selected_latest.selected_decision.review_version, 2)
        self.assertEqual(selected_history.snapshot.version, 1)
        self.assertIsNone(selected_history.selected_decision)
        self.assertEqual(selected_latest.candidates[0].candidate.source_citation.quote,
                         "Publish the reviewed execution brief.")

    async def test_invalid_parameters_fail_before_repository(self):
        invalid_calls = [
            lambda: self.service.list_runs(PROJECT_ID, self.scope, limit=0),
            lambda: self.service.list_runs(PROJECT_ID, self.scope, limit=101),
            lambda: self.service.list_runs(PROJECT_ID, self.scope, limit=True),
            lambda: self.service.read_review(PROJECT_ID, RUN_ID, self.scope, review_version=0),
            lambda: self.service.read_review(PROJECT_ID, RUN_ID, self.scope, review_version=True),
            lambda: self.service.read_review(
                "AAAAAAAA-0000-4000-8000-000000000004", RUN_ID, self.scope
            ),
        ]
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(ReviewFailure) as raised:
                    await call()
                self.assertEqual(raised.exception.code, "INVALID_INPUT")
        self.assertEqual(self.repository.calls, [])

    async def test_absent_version_and_cross_actor_are_indistinguishable(self):
        self.repository.read_results[None] = None
        with self.assertRaises(ReviewFailure) as absent:
            await self.service.read_review(PROJECT_ID, RUN_ID, self.scope)
        leaked = dataclasses.replace(
            make_model(), run=make_run(actor_id=OTHER_ACTOR_ID)
        )
        self.repository.read_results[None] = leaked
        with self.assertRaises(ReviewFailure) as cross_actor:
            await self.service.read_review(PROJECT_ID, RUN_ID, self.scope)
        self.assertEqual(absent.exception.code, "REVIEW_NOT_FOUND")
        self.assertEqual(cross_actor.exception.code, "REVIEW_NOT_FOUND")
        self.assertEqual(str(absent.exception), str(cross_actor.exception))

    async def test_malformed_repository_models_fail_closed(self):
        valid = make_model()
        wrong_citation = dataclasses.replace(
            valid.candidates[0], candidate=make_candidate(version_id=OTHER_VERSION_ID)
        )
        cases = {
            "candidate count": dataclasses.replace(valid, candidates=()),
            "ordinal": dataclasses.replace(
                valid,
                candidates=(dataclasses.replace(valid.candidates[0], ordinal=2),),
            ),
            "version gap": dataclasses.replace(
                valid, available_review_versions=(1, 3)
            ),
            "citation": dataclasses.replace(valid, candidates=(wrong_citation,)),
            "missing decision": dataclasses.replace(valid, selected_decision=None),
            "inconsistent decision": dataclasses.replace(
                valid,
                selected_decision=dataclasses.replace(
                    valid.selected_decision, action="reject"
                ),
            ),
            "replacement": dataclasses.replace(
                valid,
                candidates=(
                    dataclasses.replace(
                        valid.candidates[0], status="modify", replacement_text=None
                    ),
                ),
            ),
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                self.repository.read_results[None] = value
                with self.assertRaises(ReviewFailure) as raised:
                    await self.service.read_review(PROJECT_ID, RUN_ID, self.scope)
                self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")

    async def test_bad_list_order_and_shape_fail_closed(self):
        older = make_run(run_id=OLDER_RUN_ID, created_at=NOW - timedelta(minutes=1))
        self.repository.list_result = (
            ReviewRunSummary(older, 1),
            ReviewRunSummary(make_run(), 2),
        )
        with self.assertRaises(ReviewFailure) as order_error:
            await self.service.list_runs(PROJECT_ID, self.scope)
        self.assertEqual(order_error.exception.code, "REPOSITORY_FAILURE")

        self.repository.list_result = [ReviewRunSummary(make_run(), 2)]
        with self.assertRaises(ReviewFailure) as shape_error:
            await self.service.list_runs(PROJECT_ID, self.scope)
        self.assertEqual(shape_error.exception.code, "REPOSITORY_FAILURE")

    async def test_driver_errors_are_sanitized_and_cancellation_propagates(self):
        self.repository.error = RuntimeError("postgres password=top-secret")
        with self.assertRaises(ReviewFailure) as raised:
            await self.service.read_review(PROJECT_ID, RUN_ID, self.scope)
        self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")
        self.assertNotIn("top-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

        self.repository.error = None
        self.repository.cancel = True
        with self.assertRaises(asyncio.CancelledError):
            await self.service.list_runs(PROJECT_ID, self.scope)
        with self.assertRaises(asyncio.CancelledError):
            await self.service.read_review(PROJECT_ID, RUN_ID, self.scope)


if __name__ == "__main__":
    unittest.main()
