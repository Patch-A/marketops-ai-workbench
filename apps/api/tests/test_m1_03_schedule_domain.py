import copy
import unittest

from apps.api.marketops_schedule import ScheduleFailure, build_wbs_draft, calculate_schedule, revise_wbs


def candidate(candidate_id, kind="deliverable", status="approve", text=None):
    return {
        "candidateId": candidate_id,
        "kind": kind,
        "classification": "fact",
        "text": text or f"Candidate {candidate_id}",
        "sourceCitation": {"sourceVersionId": "proposal-v1", "sourceSha256": "a" * 64, "quote": f"Quote {candidate_id}", "location": {"kind": "line_range", "startLine": 1, "endLine": 1}},
        "review": {"status": status, "lastDecision": {"reviewVersion": 2}},
    }


class M103ScheduleDomainTests(unittest.TestCase):
    def setUp(self):
        self.proposal = {"versionId": "proposal-v1", "sha256": "a" * 64}

    def test_only_approved_candidates_enter_wbs_and_controls_remain_cited(self):
        draft = build_wbs_draft("project-1", self.proposal, [
            candidate("approved"),
            {**candidate("modified", text="Old title"), "review": {"status": "modify", "replacementText": "Edited title", "lastDecision": {"reviewVersion": 3}}},
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
            build_wbs_draft("project-1", self.proposal, [missing_citation])
        self.assertEqual(context.exception.code, "missing_source_citation")
        with self.assertRaises(ScheduleFailure) as context:
            build_wbs_draft("project-1", self.proposal, [{**candidate("modified"), "review": {"status": "modify"}}])
        self.assertEqual(context.exception.code, "missing_replacement_text")
        with self.assertRaises(ScheduleFailure) as context:
            build_wbs_draft("project-1", self.proposal, [candidate("constraint", kind="constraint")])
        self.assertEqual(context.exception.code, "no_schedulable_candidates")

    def test_schedule_is_deterministic_and_does_not_mutate_plan(self):
        plan = build_wbs_draft("project-1", self.proposal, [candidate("a"), candidate("b")])
        plan["tasks"][1]["predecessors"] = [plan["tasks"][0]["taskId"]]
        plan["tasks"][0]["durationWorkdays"] = 2
        original = copy.deepcopy(plan)
        first = calculate_schedule(plan, "2026-08-28", ["2026-08-31"])
        second = calculate_schedule(plan, "2026-08-28", ["2026-08-31"])
        self.assertEqual(first, second)
        self.assertEqual(plan, original)
        self.assertEqual(first["tasks"][0]["calculatedStart"], "2026-08-28")
        self.assertEqual(first["tasks"][1]["calculatedStart"], "2026-09-02")
        self.assertEqual(first["status"], "ready")

    def test_revision_is_versioned_and_cannot_edit_review_owned_fields(self):
        plan = build_wbs_draft("project-1", self.proposal, [candidate("a"), candidate("b")])
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

    def test_schedule_digest_changes_after_a_valid_revision(self):
        plan = build_wbs_draft("project-1", self.proposal, [candidate("a")])
        initial = calculate_schedule(plan, "2026-08-28")
        revised = revise_wbs(plan, 1, [{"taskId": "candidate:a", "changes": {"durationWorkdays": 2}}])
        updated = calculate_schedule(revised, "2026-08-28")
        self.assertNotEqual(initial["planDigest"], updated["planDigest"])
        self.assertNotEqual(initial["scheduleDigest"], updated["scheduleDigest"])
        self.assertEqual(updated["planVersion"], 2)

    def test_schedule_rejects_missing_plan_identity(self):
        plan = build_wbs_draft("project-1", self.proposal, [candidate("a")])
        del plan["proposal"]
        with self.assertRaises(ScheduleFailure) as context:
            calculate_schedule(plan, "2026-08-28")
        self.assertEqual(context.exception.code, "invalid_plan_identity")

    def test_cycle_and_missing_predecessor_are_rejected(self):
        plan = build_wbs_draft("project-1", self.proposal, [candidate("a"), candidate("b")])
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
        plan = build_wbs_draft("project-1", self.proposal, [candidate("a"), candidate("b")])
        plan["tasks"][0]["durationWorkdays"] = 3
        plan["tasks"][1]["predecessors"] = [plan["tasks"][0]["taskId"]]
        plan["tasks"][1].update({"isLocked": True, "plannedStart": "2026-08-28", "plannedFinish": "2026-08-28", "hardDeadline": "2026-08-27"})
        result = calculate_schedule(plan, "2026-08-28")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["tasks"][1]["calculatedStart"], "2026-08-28")
        self.assertTrue(result["conflicts"])
        self.assertTrue(result["deadlineMisses"])


if __name__ == "__main__":
    unittest.main()
