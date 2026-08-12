import asyncio
import dataclasses
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from apps.api.marketops_extract import Candidate, SourceCitation, SourceLocation
from apps.api.marketops_review import (
    ApprovedProposal,
    CreateReviewRunRequest,
    MAX_REVIEW_CANDIDATES,
    ReviewCandidateView,
    ReviewFailure,
    ReviewReadModel,
    ReviewRequest,
    ReviewRunRequestRecord,
    ReviewScopeContext,
    ReviewService,
)


PROJECT_ID = "00000000-0000-4000-8000-000000000101"
OTHER_PROJECT_ID = "00000000-0000-4000-8000-000000000106"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000102"
VERSION_ID = "00000000-0000-4000-8000-000000000103"
CANDIDATE_ONE = "00000000-0000-4000-8000-000000000104"
CANDIDATE_TWO = "00000000-0000-4000-8000-000000000105"
ORGANIZATION_ID = "00000000-0000-4000-8000-000000000111"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000112"
CLIENT_ID = "00000000-0000-4000-8000-000000000113"
ACTOR_ID = "00000000-0000-4000-8000-000000000114"
OTHER_WORKSPACE_ID = "00000000-0000-4000-8000-000000000115"
OTHER_CLIENT_ID = "00000000-0000-4000-8000-000000000116"
OTHER_ACTOR_ID = "00000000-0000-4000-8000-000000000117"
SOURCE_HASH = "a" * 64
NOW = datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)


def candidate(candidate_id, text, *, version_id=VERSION_ID, source_hash=SOURCE_HASH):
    return Candidate(
        candidate_id=candidate_id,
        kind="deliverable",
        text=text,
        classification="fact",
        confidence=0.9,
        source_citation=SourceCitation(
            source_version_id=version_id,
            source_sha256=source_hash,
            location=SourceLocation(
                kind="line_range",
                values=(("kind", "line_range"), ("startLine", 1), ("endLine", 1)),
            ),
            section_path=("Deliverables",),
            quote=text,
        ),
    )


class SequentialIds:
    def __init__(self):
        self.value = 1000

    def __call__(self):
        self.value += 1
        return str(UUID(int=self.value, version=4))


class FakeTransaction:
    def __init__(self, repository, scope, project_id):
        self.repository = repository
        self.scope = scope
        self.project_id = project_id
        self.runs = dict(repository.runs)
        self.candidates = dict(repository.candidates)
        self.snapshots = {
            run_id: list(values) for run_id, values in repository.snapshots.items()
        }
        self.decisions = list(repository.decisions)
        self.audit_events = list(repository.audit_events)
        self.run_requests = dict(repository.run_requests)

    def trigger(self, stage):
        if self.repository.cancel_stage == stage:
            raise asyncio.CancelledError
        if self.repository.fail_stage == stage:
            raise RuntimeError("database password=top-secret")

    async def get_approved_proposal_for_update(self, project_id):
        self.trigger("approved_proposal")
        self.repository.approved_lock_calls += 1
        if project_id != self.project_id:
            return None
        if self.repository.scope_leak:
            return next(
                (
                    proposal
                    for proposal in self.repository.approved.values()
                    if proposal.project_id == project_id
                ),
                None,
            )
        return self.repository.approved.get(
            (
                self.scope.organization_id,
                self.scope.workspace_id,
                self.scope.client_id,
                project_id,
            )
        )

    async def insert_run(self, run):
        self.trigger("run")
        self.runs[run.run_id] = run

    async def claim_run_request(self, request):
        self.trigger("run_request")
        key = (
            request.workspace_id,
            request.client_id,
            request.project_id,
            request.created_by,
            request.idempotency_key,
        )
        existing = self.run_requests.get(key)
        if existing is not None:
            return existing
        self.run_requests[key] = request
        return request

    async def insert_candidates(self, run_id, candidates):
        self.trigger("candidates")
        self.candidates[run_id] = candidates

    async def get_run(self, run_id):
        self.trigger("get_run")
        run = self.runs.get(run_id)
        if self.repository.scope_leak or run is None:
            return run
        if (
            run.organization_id,
            run.workspace_id,
            run.client_id,
            run.project_id,
        ) != (
            self.scope.organization_id,
            self.scope.workspace_id,
            self.scope.client_id,
            self.project_id,
        ):
            return None
        return run

    async def get_latest_snapshot_for_update(self, run_id):
        self.trigger("latest_snapshot")
        values = self.snapshots.get(run_id, [])
        return values[-1] if values else None

    async def get_snapshot(self, run_id, review_version):
        return next(
            (
                snapshot
                for snapshot in self.snapshots.get(run_id, ())
                if snapshot.version == review_version
            ),
            None,
        )

    async def insert_snapshot(self, snapshot):
        self.trigger("snapshot")
        self.snapshots.setdefault(snapshot.run_id, []).append(snapshot)

    async def append_decision(self, decision):
        self.trigger("decision")
        self.decisions.append(decision)

    async def append_audit_event(self, event):
        self.trigger("audit")
        self.audit_events.append(event)

    def commit(self):
        self.repository.runs = self.runs
        self.repository.candidates = self.candidates
        self.repository.snapshots = self.snapshots
        self.repository.decisions = self.decisions
        self.repository.audit_events = self.audit_events
        self.repository.run_requests = self.run_requests


class FakeRepository:
    def __init__(
        self,
        *,
        fail_stage=None,
        cancel_stage=None,
        scope_leak=False,
    ):
        self.fail_stage = fail_stage
        self.cancel_stage = cancel_stage
        self.scope_leak = scope_leak
        self.approved = {}
        self.runs = {}
        self.candidates = {}
        self.snapshots = {}
        self.decisions = []
        self.audit_events = []
        self.run_requests = {}
        self.approved_lock_calls = 0
        self.transactions = 0
        self.rollbacks = 0
        self.scopes = []
        self.lock = asyncio.Lock()

    def approve(self, proposal):
        self.approved[
            (
                proposal.organization_id,
                proposal.workspace_id,
                proposal.client_id,
                proposal.project_id,
            )
        ] = proposal

    async def get_run_request(self, scope, project_id, idempotency_key):
        if self.cancel_stage == "get_run_request":
            raise asyncio.CancelledError
        if self.fail_stage == "get_run_request":
            raise RuntimeError("database password=top-secret")
        return self.run_requests.get(
            (
                scope.workspace_id,
                scope.client_id,
                project_id,
                scope.actor_id,
                idempotency_key,
            )
        )

    async def get_review(self, scope, project_id, run_id, review_version):
        run = self.runs.get(run_id)
        if run is None or (
            run.organization_id,
            run.workspace_id,
            run.client_id,
            run.project_id,
            run.created_by,
        ) != (
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            project_id,
            scope.actor_id,
        ):
            return None
        snapshots = self.snapshots.get(run_id, ())
        selected_version = review_version if review_version is not None else snapshots[-1].version
        selected = next(
            (snapshot for snapshot in snapshots if snapshot.version == selected_version),
            None,
        )
        if selected is None:
            return None
        items = {item.candidate_id: item for item in selected.items}
        views = tuple(
            ReviewCandidateView(
                ordinal,
                value,
                items[value.candidate_id].status,
                items[value.candidate_id].replacement_text,
            )
            for ordinal, value in enumerate(self.candidates.get(run_id, ()), start=1)
        )
        return ReviewReadModel(
            run,
            selected,
            views,
            tuple(snapshot.version for snapshot in snapshots),
            None,
        )

    @asynccontextmanager
    async def transaction(self, scope, project_id):
        self.transactions += 1
        self.scopes.append((scope, project_id))
        async with self.lock:
            transaction = FakeTransaction(self, scope, project_id)
            try:
                yield transaction
                transaction.trigger("commit")
                transaction.commit()
            except BaseException:
                self.rollbacks += 1
                raise


class ReviewServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ReviewScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ACTOR_ID
        )
        self.other_scope = ReviewScopeContext(
            ORGANIZATION_ID,
            OTHER_WORKSPACE_ID,
            OTHER_CLIENT_ID,
            OTHER_ACTOR_ID,
        )
        self.candidates = (
            candidate(CANDIDATE_ONE, "Ship a cited review workflow."),
            candidate(CANDIDATE_TWO, "Retain immutable prior snapshots."),
        )

    def approved(self, scope=None, **changes):
        scope = scope or self.scope
        values = {
            "organization_id": scope.organization_id,
            "workspace_id": scope.workspace_id,
            "client_id": scope.client_id,
            "project_id": PROJECT_ID,
            "artifact_id": ARTIFACT_ID,
            "version_id": VERSION_ID,
            "version": 3,
            "sha256": SOURCE_HASH,
        }
        values.update(changes)
        return ApprovedProposal(**values)

    def create_request(self, **changes):
        values = {
            "project_id": PROJECT_ID,
            "proposal_artifact_id": ARTIFACT_ID,
            "proposal_version_id": VERSION_ID,
            "proposal_version": 3,
            "proposal_sha256": SOURCE_HASH,
            "candidates": self.candidates,
        }
        values.update(changes)
        return CreateReviewRunRequest(**values)

    def service(self, repository):
        return ReviewService(
            repository=repository,
            id_factory=SequentialIds(),
            clock=lambda: NOW,
        )

    async def create_run(self, repository=None, scope=None, request=None):
        repository = repository or FakeRepository()
        scope = scope or self.scope
        repository.approve(self.approved(scope))
        result = await self.service(repository).create_run(
            request or self.create_request(), scope
        )
        return repository, result

    def assert_empty_state(self, repository):
        self.assertEqual(repository.runs, {})
        self.assertEqual(repository.candidates, {})
        self.assertEqual(repository.snapshots, {})
        self.assertEqual(repository.decisions, [])
        self.assertEqual(repository.audit_events, [])
        self.assertEqual(repository.run_requests, {})

    async def test_create_run_persists_complete_pending_version_and_audit(self):
        repository, result = await self.create_run()

        self.assertEqual(result.run.candidate_count, 2)
        self.assertEqual(result.snapshot.version, 1)
        self.assertEqual(
            [(item.candidate_id, item.status) for item in result.snapshot.items],
            [(CANDIDATE_ONE, "pending"), (CANDIDATE_TWO, "pending")],
        )
        self.assertIs(repository.candidates[result.run.run_id], self.candidates)
        self.assertEqual(repository.snapshots[result.run.run_id], [result.snapshot])
        self.assertEqual(len(repository.audit_events), 1)
        self.assertEqual(repository.audit_events[0].action, "review_run_created")
        self.assertEqual(repository.scopes, [(self.scope, PROJECT_ID)])
        self.assertEqual(repository.approved_lock_calls, 1)

    async def test_same_idempotency_key_and_source_replays_existing_run(self):
        repository = FakeRepository()
        repository.approve(self.approved())
        service = self.service(repository)
        request = self.create_request(idempotency_key=" review-request-001 ")

        first = await service.create_run(request, self.scope)
        second = await service.create_run(request, self.scope)

        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(second.run, first.run)
        self.assertEqual(second.snapshot, first.snapshot)
        self.assertEqual(len(repository.run_requests), 1)
        self.assertEqual(len(repository.runs), 1)
        self.assertEqual(len(repository.audit_events), 1)

    async def test_same_idempotency_key_with_different_source_conflicts(self):
        repository = FakeRepository()
        repository.approve(self.approved())
        service = self.service(repository)
        first = await service.create_run(
            self.create_request(idempotency_key="review-request-002"), self.scope
        )
        different = ReviewRunRequestRecord(
            request_id=str(UUID(int=9001, version=4)),
            organization_id=self.scope.organization_id,
            workspace_id=self.scope.workspace_id,
            client_id=self.scope.client_id,
            project_id=PROJECT_ID,
            idempotency_key="review-request-002",
            expected_proposal_version_id=VERSION_ID,
            expected_proposal_sha256="b" * 64,
            run_id=str(UUID(int=9002, version=4)),
            created_by=self.scope.actor_id,
            created_at=NOW,
        )
        key = next(iter(repository.run_requests))
        repository.run_requests[key] = different

        with self.assertRaises(ReviewFailure) as raised:
            await service.create_run(
                self.create_request(idempotency_key="review-request-002"),
                self.scope,
            )

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(len(repository.runs), 1)
        self.assertEqual(next(iter(repository.runs)), first.run.run_id)

    async def test_idempotency_claim_failure_rolls_back_everything(self):
        repository = FakeRepository(fail_stage="run_request")
        repository.approve(self.approved())

        with self.assertRaises(ReviewFailure) as raised:
            await self.service(repository).create_run(
                self.create_request(idempotency_key="review-request-003"),
                self.scope,
            )

        self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")
        self.assert_empty_state(repository)

    async def test_persistent_replay_returns_original_version_one(self):
        repository = FakeRepository()
        repository.approve(self.approved())
        service = self.service(repository)
        created = await service.create_run(
            self.create_request(idempotency_key="review-request-replay"), self.scope
        )

        replay = await service.replay_run_request(
            PROJECT_ID,
            " review-request-replay ",
            VERSION_ID,
            SOURCE_HASH,
            self.scope,
        )

        self.assertIsNotNone(replay)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.run, created.run)
        self.assertEqual(replay.snapshot, created.snapshot)

    async def test_persistent_replay_detects_source_conflict(self):
        repository = FakeRepository()
        repository.approve(self.approved())
        service = self.service(repository)
        await service.create_run(
            self.create_request(idempotency_key="review-request-conflict"), self.scope
        )

        with self.assertRaises(ReviewFailure) as raised:
            await service.replay_run_request(
                PROJECT_ID,
                "review-request-conflict",
                VERSION_ID,
                "b" * 64,
                self.scope,
            )

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_CONFLICT")

    async def test_persistent_replay_fails_closed_for_malformed_or_inconsistent_record(self):
        repository = FakeRepository()
        repository.approve(self.approved())
        service = self.service(repository)
        created = await service.create_run(
            self.create_request(idempotency_key="review-request-corrupt"), self.scope
        )
        key = next(iter(repository.run_requests))
        valid = repository.run_requests[key]

        async def malformed(*_args):
            return dataclasses.replace(valid, request_id="not-a-uuid")

        repository.get_run_request = malformed
        with self.assertRaises(ReviewFailure) as malformed_error:
            await service.replay_run_request(
                PROJECT_ID,
                "review-request-corrupt",
                VERSION_ID,
                SOURCE_HASH,
                self.scope,
            )
        self.assertEqual(malformed_error.exception.code, "REPOSITORY_FAILURE")

        async def valid_record(*_args):
            return valid

        repository.get_run_request = valid_record
        repository.runs[created.run.run_id] = dataclasses.replace(
            created.run, proposal_sha256="b" * 64
        )
        repository.candidates[created.run.run_id] = tuple(
            dataclasses.replace(
                value,
                source_citation=dataclasses.replace(
                    value.source_citation, source_sha256="b" * 64
                ),
            )
            for value in self.candidates
        )
        with self.assertRaises(ReviewFailure) as inconsistent_error:
            await service.replay_run_request(
                PROJECT_ID,
                "review-request-corrupt",
                VERSION_ID,
                SOURCE_HASH,
                self.scope,
            )
        self.assertEqual(inconsistent_error.exception.code, "REPOSITORY_FAILURE")

    async def test_persistent_replay_propagates_cancellation_and_sanitizes_failure(self):
        cancelled = FakeRepository(cancel_stage="get_run_request")
        with self.assertRaises(asyncio.CancelledError):
            await self.service(cancelled).replay_run_request(
                PROJECT_ID,
                "review-request-cancel",
                VERSION_ID,
                SOURCE_HASH,
                self.scope,
            )

        failed = FakeRepository(fail_stage="get_run_request")
        with self.assertRaises(ReviewFailure) as raised:
            await self.service(failed).replay_run_request(
                PROJECT_ID,
                "review-request-failure",
                VERSION_ID,
                SOURCE_HASH,
                self.scope,
            )
        self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")
        self.assertNotIn("top-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    async def test_persistent_replay_is_isolated_by_actor_and_scope(self):
        repository = FakeRepository()
        repository.approve(self.approved())
        service = self.service(repository)
        await service.create_run(
            self.create_request(idempotency_key="review-request-isolation"), self.scope
        )
        other_actor = dataclasses.replace(self.scope, actor_id=OTHER_ACTOR_ID)

        self.assertIsNone(
            await service.replay_run_request(
                PROJECT_ID,
                "review-request-isolation",
                VERSION_ID,
                SOURCE_HASH,
                other_actor,
            )
        )
        self.assertIsNone(
            await service.replay_run_request(
                PROJECT_ID,
                "review-request-isolation",
                VERSION_ID,
                SOURCE_HASH,
                self.other_scope,
            )
        )

    async def test_all_domain_records_and_candidate_batch_are_immutable(self):
        repository, result = await self.create_run()
        request = ReviewRequest(1, CANDIDATE_ONE, "modify", "Clarify wording", replacement_text="Revised text")
        reviewed = await self.service(repository).review_candidate(
            PROJECT_ID, result.run.run_id, request, self.scope
        )

        for value, attribute in (
            (result.run, "project_id"),
            (result.snapshot, "version"),
            (result.snapshot.items[0], "status"),
            (reviewed.decision, "reason"),
            (repository.audit_events[-1], "action"),
            (repository.candidates[result.run.run_id][0], "text"),
        ):
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(value, attribute, "mutated")
        self.assertIsInstance(repository.candidates[result.run.run_id], tuple)
        self.assertEqual(
            repository.candidates[result.run.run_id][0].text,
            "Ship a cited review workflow.",
        )

    async def test_each_action_creates_a_full_snapshot_changing_only_one_candidate(self):
        repository, created = await self.create_run()
        service = self.service(repository)
        approved = await service.review_candidate(
            PROJECT_ID,
            created.run.run_id,
            ReviewRequest(1, CANDIDATE_ONE, "approve", "Ready"),
            self.scope,
        )
        modified = await service.review_candidate(
            PROJECT_ID,
            created.run.run_id,
            ReviewRequest(
                2,
                CANDIDATE_TWO,
                "modify",
                "Needs precision",
                comment="Reviewed against source",
                replacement_text="Retain every immutable prior snapshot.",
            ),
            self.scope,
        )
        rejected = await service.review_candidate(
            PROJECT_ID,
            created.run.run_id,
            ReviewRequest(3, CANDIDATE_ONE, "reject", "No longer in scope"),
            self.scope,
        )

        versions = repository.snapshots[created.run.run_id]
        self.assertEqual([snapshot.version for snapshot in versions], [1, 2, 3, 4])
        self.assertTrue(all(len(snapshot.items) == 2 for snapshot in versions))
        self.assertEqual([item.status for item in approved.snapshot.items], ["approve", "pending"])
        self.assertEqual([item.status for item in modified.snapshot.items], ["approve", "modify"])
        self.assertEqual([item.status for item in rejected.snapshot.items], ["reject", "modify"])
        self.assertEqual(
            modified.snapshot.items[1].replacement_text,
            "Retain every immutable prior snapshot.",
        )
        self.assertIsNone(rejected.snapshot.items[0].replacement_text)
        self.assertEqual(len(repository.decisions), 3)
        self.assertEqual(len(repository.audit_events), 4)
        self.assertEqual(
            repository.audit_events[-1].decision_id,
            repository.decisions[-1].decision_id,
        )

    async def test_reason_and_action_specific_replacement_validation_write_nothing(self):
        repository, created = await self.create_run()
        baseline = self.state_counts(repository, created.run.run_id)
        invalid = (
            ReviewRequest(1, CANDIDATE_ONE, "approve", ""),
            ReviewRequest(1, CANDIDATE_ONE, "reject", "  "),
            ReviewRequest(1, CANDIDATE_ONE, "modify", "Reason"),
            ReviewRequest(1, CANDIDATE_ONE, "modify", "Reason", replacement_text=" "),
            ReviewRequest(1, CANDIDATE_ONE, "reject", "Reason", replacement_text="extra"),
        )
        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(ReviewFailure) as raised:
                    await self.service(repository).review_candidate(
                        PROJECT_ID, created.run.run_id, request, self.scope
                    )
                self.assertEqual(raised.exception.code, "INVALID_INPUT")
                self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)

    def state_counts(self, repository, run_id):
        return (
            len(repository.snapshots.get(run_id, [])),
            len(repository.decisions),
            len(repository.audit_events),
        )

    async def test_source_citation_mismatch_fails_before_transaction(self):
        repository = FakeRepository()
        repository.approve(self.approved())
        mismatched = (
            candidate(
                CANDIDATE_ONE,
                "Wrong source.",
                source_hash="b" * 64,
            ),
        )
        with self.assertRaises(ReviewFailure) as raised:
            await self.service(repository).create_run(
                self.create_request(candidates=mismatched), self.scope
            )
        self.assertEqual(raised.exception.code, "SOURCE_CITATION_INVALID")
        self.assertEqual(repository.transactions, 0)
        self.assert_empty_state(repository)

    async def test_approved_proposal_identity_mismatch_rolls_back_without_partial_state(self):
        fields = {
            "artifact_id": "00000000-0000-4000-8000-000000000201",
            "version_id": "00000000-0000-4000-8000-000000000202",
            "version": 4,
            "sha256": "b" * 64,
        }
        for field, value in fields.items():
            with self.subTest(field=field):
                repository = FakeRepository()
                repository.approve(self.approved(**{field: value}))
                with self.assertRaises(ReviewFailure) as raised:
                    await self.service(repository).create_run(
                        self.create_request(), self.scope
                    )
                self.assertEqual(raised.exception.code, "INVALID_PROPOSAL_STATE")
                self.assert_empty_state(repository)
                self.assertEqual(repository.rollbacks, 1)

    async def test_unknown_candidate_creates_no_version_decision_or_audit(self):
        repository, created = await self.create_run()
        baseline = self.state_counts(repository, created.run.run_id)
        with self.assertRaises(ReviewFailure) as raised:
            await self.service(repository).review_candidate(
                PROJECT_ID,
                created.run.run_id,
                ReviewRequest(
                    1,
                    "00000000-0000-4000-8000-000000000299",
                    "approve",
                    "Looks good",
                ),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "CANDIDATE_NOT_FOUND")
        self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)

    async def test_repeating_the_same_review_state_creates_no_new_version(self):
        repository, created = await self.create_run()
        service = self.service(repository)
        reviewed = await service.review_candidate(
            PROJECT_ID,
            created.run.run_id,
            ReviewRequest(1, CANDIDATE_ONE, "approve", "Ready"),
            self.scope,
        )
        baseline = self.state_counts(repository, created.run.run_id)
        with self.assertRaises(ReviewFailure) as raised:
            await service.review_candidate(
                PROJECT_ID,
                created.run.run_id,
                ReviewRequest(2, CANDIDATE_ONE, "approve", "Still ready"),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "INVALID_INPUT")
        self.assertEqual(reviewed.snapshot.version, 2)
        self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)

    async def test_stale_review_version_returns_stable_conflict_and_writes_nothing(self):
        repository, created = await self.create_run()
        service = self.service(repository)
        await service.review_candidate(
            PROJECT_ID,
            created.run.run_id,
            ReviewRequest(1, CANDIDATE_ONE, "approve", "Ready"),
            self.scope,
        )
        baseline = self.state_counts(repository, created.run.run_id)
        with self.assertRaises(ReviewFailure) as raised:
            await service.review_candidate(
                PROJECT_ID,
                created.run.run_id,
                ReviewRequest(1, CANDIDATE_TWO, "reject", "Stale decision"),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "REVIEW_CONFLICT")
        self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)
        self.assertEqual(
            [snapshot.version for snapshot in repository.snapshots[created.run.run_id]],
            [1, 2],
        )

    async def test_concurrent_same_version_has_one_winner_and_one_conflict(self):
        repository, created = await self.create_run()
        service = self.service(repository)

        async def decide(action, reason):
            try:
                result = await service.review_candidate(
                    PROJECT_ID,
                    created.run.run_id,
                    ReviewRequest(1, CANDIDATE_ONE, action, reason),
                    self.scope,
                )
                return result.snapshot.version
            except ReviewFailure as failure:
                return failure.code

        results = await asyncio.gather(
            decide("approve", "Reviewer one"),
            decide("reject", "Reviewer two"),
        )
        self.assertCountEqual(results, [2, "REVIEW_CONFLICT"])
        self.assertEqual(
            [snapshot.version for snapshot in repository.snapshots[created.run.run_id]],
            [1, 2],
        )
        self.assertEqual(len(repository.decisions), 1)
        self.assertEqual(len(repository.audit_events), 2)

    async def test_create_failures_at_every_write_and_commit_roll_back_everything(self):
        for stage in ("run", "candidates", "snapshot", "audit", "commit"):
            with self.subTest(stage=stage):
                repository = FakeRepository(fail_stage=stage)
                repository.approve(self.approved())
                with self.assertRaises(ReviewFailure) as raised:
                    await self.service(repository).create_run(
                        self.create_request(), self.scope
                    )
                self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")
                self.assertNotIn("top-secret", str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assert_empty_state(repository)
                self.assertEqual(repository.rollbacks, 1)

    async def test_review_failures_at_every_write_and_commit_roll_back_new_state(self):
        for stage in ("snapshot", "decision", "audit", "commit"):
            with self.subTest(stage=stage):
                repository, created = await self.create_run()
                repository.fail_stage = stage
                baseline = self.state_counts(repository, created.run.run_id)
                with self.assertRaises(ReviewFailure) as raised:
                    await self.service(repository).review_candidate(
                        PROJECT_ID,
                        created.run.run_id,
                        ReviewRequest(1, CANDIDATE_ONE, "approve", "Ready"),
                        self.scope,
                    )
                self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")
                self.assertNotIn("top-secret", str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)

    async def test_create_and_review_cancellation_roll_back_and_propagate(self):
        for stage in ("run", "candidates", "snapshot", "audit", "commit"):
            with self.subTest(operation="create", stage=stage):
                repository = FakeRepository(cancel_stage=stage)
                repository.approve(self.approved())
                with self.assertRaises(asyncio.CancelledError):
                    await self.service(repository).create_run(
                        self.create_request(), self.scope
                    )
                self.assert_empty_state(repository)
                self.assertEqual(repository.rollbacks, 1)

        for stage in ("snapshot", "decision", "audit", "commit"):
            with self.subTest(operation="review", stage=stage):
                repository, created = await self.create_run()
                repository.cancel_stage = stage
                baseline = self.state_counts(repository, created.run.run_id)
                with self.assertRaises(asyncio.CancelledError):
                    await self.service(repository).review_candidate(
                        PROJECT_ID,
                        created.run.run_id,
                        ReviewRequest(1, CANDIDATE_ONE, "approve", "Ready"),
                        self.scope,
                    )
                self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)

    async def test_scope_leaks_are_rejected_at_the_service_boundary(self):
        leaked_proposal_repository = FakeRepository(scope_leak=True)
        leaked_proposal_repository.approve(self.approved(self.other_scope))
        with self.assertRaises(ReviewFailure) as create_error:
            await self.service(leaked_proposal_repository).create_run(
                self.create_request(), self.scope
            )
        self.assertEqual(create_error.exception.code, "INVALID_PROPOSAL_STATE")
        self.assert_empty_state(leaked_proposal_repository)

        repository, created = await self.create_run(scope=self.other_scope)
        repository.scope_leak = True
        baseline = self.state_counts(repository, created.run.run_id)
        with self.assertRaises(ReviewFailure) as leaked_error:
            await self.service(repository).review_candidate(
                PROJECT_ID,
                created.run.run_id,
                ReviewRequest(1, CANDIDATE_ONE, "approve", "Ready"),
                self.scope,
            )
        with self.assertRaises(ReviewFailure) as absent_error:
            await self.service(repository).review_candidate(
                PROJECT_ID,
                "00000000-0000-4000-8000-000000000399",
                ReviewRequest(1, CANDIDATE_ONE, "approve", "Ready"),
                self.scope,
            )
        self.assertEqual(leaked_error.exception.code, "REVIEW_NOT_FOUND")
        self.assertEqual(absent_error.exception.code, "REVIEW_NOT_FOUND")
        self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)

    async def test_same_client_other_project_cannot_review_run(self):
        repository, created = await self.create_run()
        baseline = self.state_counts(repository, created.run.run_id)
        with self.assertRaises(ReviewFailure) as raised:
            await self.service(repository).review_candidate(
                OTHER_PROJECT_ID,
                created.run.run_id,
                ReviewRequest(1, CANDIDATE_ONE, "approve", "Wrong project"),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "REVIEW_NOT_FOUND")
        self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)
        self.assertEqual(repository.scopes[-1], (self.scope, OTHER_PROJECT_ID))

    async def test_same_project_other_actor_cannot_review_run(self):
        repository, created = await self.create_run()
        baseline = self.state_counts(repository, created.run.run_id)
        other_actor = dataclasses.replace(self.scope, actor_id=OTHER_ACTOR_ID)

        with self.assertRaises(ReviewFailure) as raised:
            await self.service(repository).review_candidate(
                PROJECT_ID,
                created.run.run_id,
                ReviewRequest(1, CANDIDATE_ONE, "approve", "Wrong actor"),
                other_actor,
            )

        self.assertEqual(raised.exception.code, "REVIEW_NOT_FOUND")
        self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)

    async def test_invalid_actor_and_mutable_candidate_batch_fail_before_repository(self):
        repository = FakeRepository()
        repository.approve(self.approved())
        invalid_scope = ReviewScopeContext(
            ORGANIZATION_ID, WORKSPACE_ID, CLIENT_ID, ""
        )
        with self.assertRaises(ReviewFailure) as scope_error:
            await self.service(repository).create_run(
                self.create_request(), invalid_scope
            )
        self.assertEqual(scope_error.exception.code, "AUTHORIZATION_REQUIRED")
        self.assertEqual(repository.transactions, 0)

        with self.assertRaises(ReviewFailure) as batch_error:
            await self.service(repository).create_run(
                self.create_request(candidates=list(self.candidates)), self.scope
            )
        self.assertEqual(batch_error.exception.code, "INVALID_INPUT")
        self.assertEqual(repository.transactions, 0)

        with self.assertRaises(ReviewFailure) as empty_error:
            await self.service(repository).create_run(
                self.create_request(candidates=()), self.scope
            )
        self.assertEqual(empty_error.exception.code, "INVALID_INPUT")
        self.assertEqual(repository.transactions, 0)

        with self.assertRaises(ReviewFailure) as limit_error:
            await self.service(repository).create_run(
                self.create_request(
                    candidates=(self.candidates[0],) * (MAX_REVIEW_CANDIDATES + 1)
                ),
                self.scope,
            )
        self.assertEqual(limit_error.exception.code, "INVALID_INPUT")
        self.assertEqual(repository.transactions, 0)

    async def test_noncanonical_uuid_casing_fails_before_repository(self):
        repository = FakeRepository()
        repository.approve(self.approved())
        uppercase_project = "aaaaaaaa-0000-4000-8000-000000000101".upper()
        with self.assertRaises(ReviewFailure) as create_error:
            await self.service(repository).create_run(
                self.create_request(project_id=uppercase_project), self.scope
            )
        self.assertEqual(create_error.exception.code, "INVALID_INPUT")
        self.assertEqual(repository.transactions, 0)

        with self.assertRaises(ReviewFailure) as review_error:
            await self.service(repository).review_candidate(
                PROJECT_ID,
                uppercase_project,
                ReviewRequest(1, CANDIDATE_ONE, "approve", "Ready"),
                self.scope,
            )
        self.assertEqual(review_error.exception.code, "INVALID_INPUT")
        self.assertEqual(repository.transactions, 0)

    async def test_incomplete_repository_snapshot_fails_closed_without_writes(self):
        repository, created = await self.create_run()
        repository.snapshots[created.run.run_id][-1] = dataclasses.replace(
            created.snapshot,
            items=(created.snapshot.items[0],),
        )
        baseline = self.state_counts(repository, created.run.run_id)
        with self.assertRaises(ReviewFailure) as raised:
            await self.service(repository).review_candidate(
                PROJECT_ID,
                created.run.run_id,
                ReviewRequest(1, CANDIDATE_ONE, "approve", "Ready"),
                self.scope,
            )
        self.assertEqual(raised.exception.code, "REPOSITORY_FAILURE")
        self.assertEqual(self.state_counts(repository, created.run.run_id), baseline)

    def test_requests_cannot_supply_authorization_scope(self):
        create_fields = {field.name for field in dataclasses.fields(CreateReviewRunRequest)}
        review_fields = {field.name for field in dataclasses.fields(ReviewRequest)}
        forbidden = {"organization_id", "workspace_id", "client_id", "actor_id"}
        self.assertTrue(create_fields.isdisjoint(forbidden))
        self.assertTrue(review_fields.isdisjoint(forbidden))


if __name__ == "__main__":
    unittest.main()

