import copy
import unittest

from apps.api.marketops_schedule import ScheduleFailure, bind_review_snapshot, build_wbs_draft, calculate_schedule, revise_wbs


def candidate(candidate_id, kind="deliverable", status="approve", text=None, review_version=2):
    replacement_text = "Edited title" if status == "modify" else None
    return {
        "candidateId": candidate_id,
        "kind": kind,
        "classification": "fact",
        "text": text or f"Candidate {candidate_id}",
        "sourceCitation": {"sourceVersionId": "proposal-v1", "sourceSha256": "a" * 64, "quote": f"Quote {candidate_id}", "location": {"kind": "line_range", "startLine": 1, "endLine": 1}},
        "review": {
            "status": status,
            "replacementText": replacement_text,
            "lastDecision": None if status == "pending" else {
                "decisionId": f"decision-{candidate_id}",
                "reviewVersion": review_version,
                "candidateId": candidate_id,
                "action": status,
                "reason": "Reviewed by a human",
                "comment": None,
                "replacementText": replacement_text,
                "actorId": "actor-1",
                "createdAt": "2026-08-14T00:00:00Z",
            },
        },
    }


def snapshot(candidates, version=4):
    return {
        "run": {
            "runId": "run-1",
            "proposalVersionId": "proposal-v1",
            "proposalSha256": "a" * 64,
            "latestReviewVersion": version,
        },
        "selectedReviewVersion": version,
        "candidates": candidates,
    }


class M103ScheduleDomainTests(unittest.TestCase):
    def setUp(self):
        self.proposal = {"versionId": "proposal-v1", "sha256": "a" * 64}

    def build(self, candidates, version=4):
        bound = bind_review_snapshot("project-1", "run-1", snapshot(candidates, version))
        return build_wbs_draft("project-1", self.proposal, bound)

    def test_only_approved_candidates_enter_wbs_and_controls_remain_cited(self):
        draft = self.build([
            candidate("approved"),
            candidate("modified", status="modify", text="Old title", review_version=3),
            candidate("pending", status="pending"), candidate("rejected", status="reject"), candidate("constraint", "constraint"),
        ])
        self.assertEqual([item["candidateId"] for item in draft["tasks"]], ["approved", "modified"])
        self.assertEqual(draft["tasks"][1]["title"], "Edited title")
        self.assertEqual(draft["tasks"][1]["sourceText"], "Old title")
        self.assertEqual(draft["controls"][0]["kind"], "constraint")
        self.assertEqual(draft["tasks"][0]["sourceCitation"]["sourceSha256"], "a" * 64)

    def test_missing_citation_and_missing_replacement_fail_closed(self):
        missing_citation = candidate("no-citation")
        missing_citation.pop("sourceCitation")
        with self.assertRaisesRegex(ScheduleFailure, "source citation") as context:
            self.build([missing_citation])
        self.assertEqual(context.exception.code, "missing_source_citation")
        with self.assertRaises(ScheduleFailure) as context:
            broken = candidate("modified", status="modify")
            broken["review"]["replacementText"] = None
            broken["review"]["lastDecision"]["replacementText"] = None
            self.build([broken])
        self.assertEqual(context.exception.code, "missing_replacement_text")
        with self.assertRaises(ScheduleFailure) as context:
            self.build([candidate("constraint", kind="constraint")])
        self.assertEqual(context.exception.code, "no_schedulable_candidates")

    def test_review_snapshot_and_citations_are_bound_to_the_approved_proposal(self):
        wrong_citation = candidate("wrong-citation")
        wrong_citation["sourceCitation"]["sourceVersionId"] = "proposal-other"
        with self.assertRaises(ScheduleFailure) as context:
            self.build([wrong_citation])
        self.assertEqual(context.exception.code, "source_citation_mismatch")

        wrong_run = snapshot([candidate("wrong-run")])
        wrong_run["run"]["proposalSha256"] = "b" * 64
        with self.assertRaises(ScheduleFailure) as context:
            bound = bind_review_snapshot("project-1", "run-1", wrong_run)
            build_wbs_draft("project-1", self.proposal, bound)
        self.assertEqual(context.exception.code, "review_proposal_mismatch")

        bound = bind_review_snapshot("project-1", "run-1", snapshot([candidate("wrong-project")]))
        with self.assertRaises(ScheduleFailure) as context:
            build_wbs_draft("project-other", self.proposal, bound)
        self.assertEqual(context.exception.code, "review_project_mismatch")

    def test_server_adapter_binds_actual_m1_02_http_shape_to_route_identity(self):
        raw = snapshot([candidate("http-shaped")])
        self.assertNotIn("projectId", raw["run"])
        self.assertNotIn("runId", raw["candidates"][0]["review"]["lastDecision"])
        with self.assertRaises(ScheduleFailure) as context:
            build_wbs_draft("project-1", self.proposal, raw)
        self.assertEqual(context.exception.code, "invalid_review_snapshot")

        bound = bind_review_snapshot("project-1", "run-1", raw)
        draft = build_wbs_draft("project-1", self.proposal, bound)
        self.assertEqual(draft["sourceReviewRunId"], "run-1")
        self.assertEqual(bound["run"]["projectId"], "project-1")
        self.assertEqual(bound["candidates"][0]["review"]["lastDecision"]["runId"], "run-1")
        self.assertNotIn("projectId", raw["run"])
        self.assertNotIn("runId", raw["candidates"][0]["review"]["lastDecision"])

        with self.assertRaises(ScheduleFailure) as context:
            bind_review_snapshot("project-1", "run-other", raw)
        self.assertEqual(context.exception.code, "review_route_mismatch")

    def test_approved_candidates_require_complete_matching_human_decisions(self):
        missing_decision = candidate("missing-decision")
        missing_decision["review"]["lastDecision"] = None
        with self.assertRaises(ScheduleFailure) as context:
            self.build([missing_decision])
        self.assertEqual(context.exception.code, "invalid_review_decision")

        wrong_run = candidate("wrong-decision-run")
        wrong_run["review"]["lastDecision"]["runId"] = "run-other"
        raw = snapshot([wrong_run])
        with self.assertRaises(ScheduleFailure) as context:
            bind_review_snapshot("project-1", "run-1", raw)
        self.assertEqual(context.exception.code, "review_decision_mismatch")

    def test_source_review_version_comes_from_the_selected_snapshot(self):
        draft = self.build([
            candidate("accepted", review_version=2),
            candidate("rejected-later", status="reject", review_version=4),
        ], version=4)
        self.assertEqual(draft["sourceReviewRunId"], "run-1")
        self.assertEqual(draft["sourceReviewVersion"], 4)

    def test_schedule_is_deterministic_and_does_not_mutate_plan(self):
        plan = self.build([candidate("a"), candidate("b")])
        plan["tasks"][1]["predecessors"] = [plan["tasks"][0]["taskId"]]
        plan["tasks"][0]["durationWorkdays"] = 2
        original = copy.deepcopy(plan)
        first = calculate_schedule(plan, "2026-08-28", ["2026-08-31"])
        second = calculate_schedule(plan, "2026-08-28", ["2026-08-31"])
        self.assertEqual(first, second)
        self.assertEqual(plan, original)
        self.assertEqual(first["tasks"][0]["calculatedStart"], "2026-08-28")
        self.assertEqual(first["tasks"][1]["calculatedStart"], "2026-09-02")
        self.assertEqual(first["sourceReviewRunId"], "run-1")
        self.assertEqual(first["status"], "ready")

    def test_revision_is_versioned_and_cannot_edit_review_owned_fields(self):
        plan = self.build([candidate("a"), candidate("b")])
        revised = revise_wbs(plan, 1, [{"taskId": "candidate:b", "changes": {"durationWorkdays": 3, "predecessors": ["candidate:a"], "ownerRole": "Campaign owner"}}])
        self.assertEqual(plan["planVersion"], 1)
        self.assertEqual(plan["tasks"][1]["durationWorkdays"], 1)
        self.assertEqual(revised["planVersion"], 2)
        self.assertEqual(revised["lastRevision"]["taskIds"], ["candidate:b"])
        self.assertEqual(revised["tasks"][1]["sourceCitation"], plan["tasks"][1]["sourceCitation"])
        with self.assertRaises(ScheduleFailure) as context:
            revise_wbs(revised, 1, [{"taskId": "candidate:b", "changes": {"title": "stale"}}])
        self.assertEqual(context.exception.code, "plan_version_conflict")
        with self.assertRaises(ScheduleFailure) as context:
            revise_wbs(plan, 1, [{"taskId": "candidate:b", "changes": {"sourceCitation": {}}}])
        self.assertEqual(context.exception.code, "immutable_task_field")
        with self.assertRaises(ScheduleFailure) as context:
            revise_wbs(plan, 1, [{"taskId": "candidate:b", "changes": {"isLocked": "false"}}])
        self.assertEqual(context.exception.code, "invalid_lock_flag")
        for bad_version in (True, 0, 1.5):
            with self.assertRaises(ScheduleFailure) as context:
                revise_wbs(plan, bad_version, [{"taskId": "candidate:b", "changes": {"title": "Edited"}}])
            self.assertEqual(context.exception.code, "invalid_expected_plan_version")
        malformed = copy.deepcopy(plan)
        malformed["tasks"] = [None]
        with self.assertRaises(ScheduleFailure) as context:
            revise_wbs(malformed, 1, [{"taskId": "candidate:b", "changes": {"title": "Edited"}}])
        self.assertEqual(context.exception.code, "invalid_task_id")

    def test_schedule_digest_changes_after_a_valid_revision(self):
        plan = self.build([candidate("a")])
        initial = calculate_schedule(plan, "2026-08-28")
        revised = revise_wbs(plan, 1, [{"taskId": "candidate:a", "changes": {"durationWorkdays": 2}}])
        updated = calculate_schedule(revised, "2026-08-28")
        self.assertNotEqual(initial["planDigest"], updated["planDigest"])
        self.assertNotEqual(initial["scheduleDigest"], updated["scheduleDigest"])
        self.assertEqual(updated["planVersion"], 2)

    def test_schedule_rejects_missing_plan_identity(self):
        plan = self.build([candidate("a")])
        del plan["proposal"]
        with self.assertRaises(ScheduleFailure) as context:
            calculate_schedule(plan, "2026-08-28")
        self.assertEqual(context.exception.code, "invalid_plan_identity")

        plan = self.build([candidate("a")])
        plan["tasks"][0]["sourceCitation"]["sourceSha256"] = "b" * 64
        with self.assertRaises(ScheduleFailure) as context:
            calculate_schedule(plan, "2026-08-28")
        self.assertEqual(context.exception.code, "source_citation_mismatch")

    def test_cycle_and_missing_predecessor_are_rejected(self):
        plan = self.build([candidate("a"), candidate("b")])
        plan["tasks"][0]["predecessors"] = [plan["tasks"][1]["taskId"]]
        plan["tasks"][1]["predecessors"] = [plan["tasks"][0]["taskId"]]
        with self.assertRaises(ScheduleFailure) as context:
            calculate_schedule(plan, "2026-08-28")
        self.assertEqual(context.exception.code, "dependency_cycle")
        plan["tasks"][1]["predecessors"] = ["candidate:missing"]
        with self.assertRaises(ScheduleFailure) as context:
            calculate_schedule(plan, "2026-08-28")
        self.assertEqual(context.exception.code, "missing_predecessor")

    def test_locked_conflict_and_deadline_are_needs_review_without_moving_lock(self):
        plan = self.build([candidate("a"), candidate("b")])
        plan["tasks"][0]["durationWorkdays"] = 3
        plan["tasks"][1]["predecessors"] = [plan["tasks"][0]["taskId"]]
        plan["tasks"][1].update({"isLocked": True, "plannedStart": "2026-08-28", "plannedFinish": "2026-08-28", "hardDeadline": "2026-08-27"})
        result = calculate_schedule(plan, "2026-08-28")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["tasks"][1]["calculatedStart"], "2026-08-28")
        self.assertTrue(result["conflicts"])
        self.assertTrue(result["deadlineMisses"])

    def test_empty_dates_and_non_boolean_lock_fail_with_stable_codes(self):
        plan = self.build([candidate("a")])
        with self.assertRaises(ScheduleFailure) as context:
            calculate_schedule(plan, "")
        self.assertEqual(context.exception.code, "invalid_date")
        with self.assertRaises(ScheduleFailure) as context:
            calculate_schedule(plan, "2026-08-28", [""])
        self.assertEqual(context.exception.code, "invalid_date")
        plan["tasks"][0]["isLocked"] = "false"
        with self.assertRaises(ScheduleFailure) as context:
            calculate_schedule(plan, "2026-08-28")
        self.assertEqual(context.exception.code, "invalid_lock_flag")
        plan["tasks"][0]["isLocked"] = False
        for bad_status in ([], {}):
            plan["tasks"][0]["status"] = bad_status
            with self.assertRaises(ScheduleFailure) as context:
                calculate_schedule(plan, "2026-08-28")
            self.assertEqual(context.exception.code, "invalid_task_status")


if __name__ == "__main__":
    unittest.main()
