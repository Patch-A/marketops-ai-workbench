from __future__ import annotations

import unittest
from dataclasses import replace

from apps.api.marketops_learning.service import (
    CapsuleInputs,
    FeedbackSourceReference,
    LearningFailure,
    LearningScopeContext,
    OutcomeInput,
    RetrospectiveInput,
    TaskExecutionSnapshot,
    TaskSnapshot,
    build_project_capsule,
    normalize_outcome,
    normalize_retrospective,
)


def uid(value: int) -> str:
    return f"00000000-0000-4000-8000-{value:012d}"


SCOPE = LearningScopeContext(uid(1), uid(2), uid(3), uid(4))
PROJECT = uid(5)
SOURCE = FeedbackSourceReference("artifact_version", uid(6), PROJECT, "a" * 64)


def inputs(**changes: object) -> CapsuleInputs:
    outcome = normalize_outcome(
        OutcomeInput("qualified leads", None, "42", "count", SOURCE), SCOPE, uid(30)
    )
    retrospective = normalize_retrospective(
        RetrospectiveInput(
            "A rehearsal uncovered a missing venue handoff.",
            "process_observation",
            True,
            (SOURCE,),
        ),
        SCOPE,
        uid(31),
    )
    value = CapsuleInputs(
        project_id=PROJECT,
        proposal_artifact_id=uid(7),
        proposal_version_id=uid(8),
        proposal_version=1,
        proposal_sha256="b" * 64,
        plan_id=uid(9),
        plan_version_id=uid(10),
        plan_version=2,
        plan_digest="c" * 64,
        approval_id=uid(11),
        approval_plan_id=uid(9),
        approved_plan_version_id=uid(10),
        approval_plan_digest="c" * 64,
        approval_schedule_snapshot_id=uid(12),
        approval_schedule_digest="d" * 64,
        schedule_snapshot_id=uid(12),
        schedule_plan_id=uid(9),
        schedule_plan_version_id=uid(10),
        schedule_plan_digest="c" * 64,
        schedule_digest="d" * 64,
        tasks=(
            TaskSnapshot(uid(20), "Venue booking", "2026-08-01", "2026-08-02", 2),
            TaskSnapshot(uid(21), "Cancelled add-on", None, None, 1),
        ),
        executions=(
            TaskExecutionSnapshot(uid(22), uid(20), "completed", "2026-08-01", "2026-08-03", None, "e" * 64),
            TaskExecutionSnapshot(uid(23), uid(21), "cancelled", None, None, None, "f" * 64),
        ),
        outcomes=(outcome,),
        retrospectives=(retrospective,),
    )
    return replace(value, **changes)


class ProjectLearningServiceTests(unittest.TestCase):
    def test_builds_only_bounded_candidate_kinds_with_hashed_evidence(self) -> None:
        capsule = build_project_capsule(inputs(), SCOPE)

        self.assertEqual(capsule.status, "ready")
        self.assertEqual(
            [item.type for item in capsule.knowledge_items],
            ["observed_outcome", "observed_task_duration", "retrospective_finding"],
        )
        for item in capsule.knowledge_items:
            self.assertEqual(item.status, "candidate")
            self.assertEqual(item.scope, "project")
            self.assertEqual(item.project_id, PROJECT)
            self.assertTrue(item.evidence)
            self.assertTrue(all(len(evidence.binding_sha256) == 64 for evidence in item.evidence))
            self.assertIn("capsule", {evidence.source_type for evidence in item.evidence})

    def test_identical_input_is_deterministic_and_changed_feedback_is_new_snapshot(self) -> None:
        first = build_project_capsule(inputs(), SCOPE)
        replay = build_project_capsule(inputs(), SCOPE)
        changed = build_project_capsule(
            inputs(
                outcomes=(
                    normalize_outcome(
                        OutcomeInput("qualified leads", None, "43", "count", SOURCE), SCOPE, uid(30)
                    ),
                )
            ),
            SCOPE,
        )

        self.assertEqual(first, replay)
        self.assertNotEqual(first.capsule_digest, changed.capsule_digest)
        self.assertNotEqual(first.capsule_id, changed.capsule_id)

    def test_rejects_missing_or_non_terminal_execution_state(self) -> None:
        with self.assertRaisesRegex(LearningFailure, "terminal execution"):
            build_project_capsule(inputs(executions=inputs().executions[:1]), SCOPE)
        with self.assertRaisesRegex(LearningFailure, "terminal execution"):
            build_project_capsule(
                inputs(
                    executions=(
                        replace(inputs().executions[0], status="in_progress"),
                        inputs().executions[1],
                    )
                ),
                SCOPE,
            )

    def test_requires_feedback_and_exact_approved_schedule_binding(self) -> None:
        with self.assertRaisesRegex(LearningFailure, "feedback"):
            build_project_capsule(inputs(outcomes=(), retrospectives=()), SCOPE)
        with self.assertRaisesRegex(LearningFailure, "approval and schedule"):
            build_project_capsule(inputs(approved_plan_version_id=uid(99)), SCOPE)
        with self.assertRaisesRegex(LearningFailure, "digest"):
            build_project_capsule(inputs(schedule_digest="not-a-digest"), SCOPE)
        with self.assertRaisesRegex(LearningFailure, "approval and schedule"):
            build_project_capsule(inputs(approval_schedule_snapshot_id=uid(98)), SCOPE)

    def test_feedback_evidence_is_typed_hashed_and_same_project(self) -> None:
        with self.assertRaisesRegex(LearningFailure, "source"):
            normalize_outcome(
                OutcomeInput("leads", None, "1", None, "https://example.invalid"), SCOPE, uid(40)
            )
        foreign = replace(SOURCE, project_id=uid(88))
        with self.assertRaisesRegex(LearningFailure, "same project"):
            build_project_capsule(
                inputs(
                    outcomes=(
                        normalize_outcome(OutcomeInput("leads", None, "1", None, foreign), SCOPE, uid(40)),
                    )
                ),
                SCOPE,
            )

    def test_outcome_requires_actual_result_not_only_a_plan(self) -> None:
        with self.assertRaisesRegex(LearningFailure, "actual result"):
            normalize_outcome(
                OutcomeInput("leads", "20", None, "count", SOURCE), SCOPE, uid(42)
            )

    def test_rejects_incomplete_or_ambiguous_execution_facts(self) -> None:
        with self.assertRaisesRegex(LearningFailure, "actual start and finish"):
            build_project_capsule(
                inputs(executions=(
                    replace(inputs().executions[0], actual_start=None), inputs().executions[1]
                )),
                SCOPE,
            )
        with self.assertRaisesRegex(LearningFailure, "planned finish"):
            build_project_capsule(
                inputs(tasks=(
                    replace(inputs().tasks[0], planned_start="2026-08-03", planned_finish="2026-08-02"),
                    inputs().tasks[1],
                )),
                SCOPE,
            )
        with self.assertRaisesRegex(LearningFailure, "update ids"):
            build_project_capsule(
                inputs(executions=(
                    inputs().executions[0], replace(inputs().executions[1], execution_update_id=uid(22))
                )),
                SCOPE,
            )

    def test_evidence_order_does_not_change_the_capsule_digest(self) -> None:
        other = FeedbackSourceReference("schedule_snapshot", uid(43), PROJECT, "f" * 64)
        first = normalize_retrospective(
            RetrospectiveInput("Observed fact", "process_observation", True, (SOURCE, other)), SCOPE, uid(44)
        )
        reversed_order = normalize_retrospective(
            RetrospectiveInput("Observed fact", "process_observation", True, (other, SOURCE)), SCOPE, uid(44)
        )
        self.assertEqual(
            build_project_capsule(inputs(outcomes=(), retrospectives=(first,)), SCOPE).capsule_digest,
            build_project_capsule(inputs(outcomes=(), retrospectives=(reversed_order,)), SCOPE).capsule_digest,
        )
        self.assertEqual(first.evidence, reversed_order.evidence)

    def test_reordered_duplicate_findings_keep_candidate_evidence_and_ids(self) -> None:
        first = normalize_retrospective(
            RetrospectiveInput("Repeated fact", "process_observation", True, (SOURCE,)), SCOPE, uid(45)
        )
        second = normalize_retrospective(
            RetrospectiveInput("Repeated fact", "process_observation", True, (SOURCE,)), SCOPE, uid(46)
        )
        forward = build_project_capsule(inputs(outcomes=(), retrospectives=(first, second)), SCOPE)
        reverse = build_project_capsule(inputs(outcomes=(), retrospectives=(second, first)), SCOPE)
        self.assertEqual(forward, reverse)
        self.assertEqual(len({item.knowledge_id for item in forward.knowledge_items}), 3)

    def test_non_reusable_retrospective_does_not_create_a_candidate(self) -> None:
        record = normalize_retrospective(
            RetrospectiveInput("Private note", "non_reusable_note", False, (SOURCE,)),
            SCOPE,
            uid(41),
        )
        capsule = build_project_capsule(inputs(outcomes=(), retrospectives=(record,)), SCOPE)
        self.assertEqual(
            [item.type for item in capsule.knowledge_items], ["observed_task_duration"]
        )


if __name__ == "__main__":
    unittest.main()
