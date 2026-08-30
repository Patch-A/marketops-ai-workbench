from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

from apps.api.marketops_learning.approval import (
    ApprovalDecisionRequest,
    CandidateKnowledge,
    apply_approval_decision,
    approval_request_digest,
    authorize_citation,
    new_approval_state,
)
from apps.api.marketops_learning.service import LearningFailure, LearningScopeContext


def uid(value: int) -> str:
    return f"00000000-0000-4000-8000-{value:012d}"


SOURCE_SCOPE = LearningScopeContext(uid(1), uid(2), uid(3), uid(4))
OTHER_ACTOR_SCOPE = LearningScopeContext(uid(1), uid(2), uid(3), uid(5))
SOURCE_PROJECT = uid(6)
CLIENT_TARGET_PROJECT = uid(7)
OTHER_CLIENT_SCOPE = LearningScopeContext(uid(1), uid(2), uid(9), uid(10))


def candidate(**changes: object) -> CandidateKnowledge:
    content = changes.pop("content", "Use a venue handoff rehearsal.")
    value = CandidateKnowledge(
        knowledge_id=uid(20),
        source_project_id=SOURCE_PROJECT,
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        evidence_digest="a" * 64,
    )
    return replace(value, **changes)


def state():
    return new_approval_state(candidate(), SOURCE_SCOPE)


def approve(value=None):
    return apply_approval_decision(
        state() if value is None else value,
        ApprovalDecisionRequest("approve", 0 if value is None else value.version, "project"),
        SOURCE_SCOPE,
    )


class ApprovalServiceTests(unittest.TestCase):
    def test_approval_is_project_scoped_and_candidate_is_unchanged(self) -> None:
        initial = state()
        approved = approve(initial)

        self.assertEqual(initial.version, 0)
        self.assertEqual(approved.version, 1)
        self.assertEqual(approved.current.status, "approved")
        self.assertEqual(approved.current.effective_scope, "project")
        self.assertEqual(approved.current.content, initial.candidate.content)
        self.assertEqual(approved.candidate, initial.candidate)

    def test_initial_approval_cannot_broaden_scope_or_supply_revision_fields(self) -> None:
        for request in (
            ApprovalDecisionRequest("approve", 0, "client"),
            ApprovalDecisionRequest("approve", 0, "project", "changed"),
            ApprovalDecisionRequest("approve", 0, "project", reason="unneeded"),
        ):
            with self.subTest(request=request), self.assertRaisesRegex(LearningFailure, "scope|not allowed"):
                apply_approval_decision(state(), request, SOURCE_SCOPE)

    def test_revised_approval_creates_new_rendering_with_reason(self) -> None:
        revised = apply_approval_decision(
            state(),
            ApprovalDecisionRequest("revise", 0, "project", "Use a signed venue handoff rehearsal.", "Clarified the control."),
            SOURCE_SCOPE,
        )

        self.assertEqual(revised.current.status, "approved")
        self.assertEqual(revised.current.content, "Use a signed venue handoff rehearsal.")
        self.assertNotEqual(revised.current.content_sha256, revised.candidate.content_sha256)
        self.assertEqual(revised.candidate.content, "Use a venue handoff rehearsal.")

    def test_revisions_require_a_changed_bounded_rendering_and_reason(self) -> None:
        approved = approve()
        for request in (
            ApprovalDecisionRequest("revise", 1, "project", approved.current.content, "same"),
            ApprovalDecisionRequest("revise", 1, "project", "changed", None),
            ApprovalDecisionRequest("revise", 1, "client", "changed", "wrong scope"),
            ApprovalDecisionRequest("revise", 1, "project", "x" * 4001, "too long"),
        ):
            with self.subTest(request=request), self.assertRaises(LearningFailure):
                apply_approval_decision(approved, request, SOURCE_SCOPE)

    def test_elevation_is_explicit_and_only_supports_project_to_client(self) -> None:
        approved = approve()
        client = apply_approval_decision(
            approved, ApprovalDecisionRequest("elevate", 1, "client", reason="Approved for this client."), SOURCE_SCOPE
        )

        self.assertEqual([item.effective_scope for item in client.decisions], ["project", "client"])
        for request in (
            ApprovalDecisionRequest("elevate", 1, "workspace", reason="skip"),
            ApprovalDecisionRequest("elevate", 1, "project", reason="same"),
            ApprovalDecisionRequest("elevate", 1, "client", "new content", "content forbidden"),
            ApprovalDecisionRequest("elevate", 2, "workspace", reason="unsupported"),
        ):
            subject = client if request.expected_version == 2 else approved
            with self.subTest(request=request), self.assertRaises(LearningFailure):
                apply_approval_decision(subject, request, SOURCE_SCOPE)

    def test_rejection_and_revocation_are_terminal_and_reasoned(self) -> None:
        rejected = apply_approval_decision(
            state(), ApprovalDecisionRequest("reject", 0, reason="Evidence is insufficient."), SOURCE_SCOPE
        )
        revoked = apply_approval_decision(
            approve(), ApprovalDecisionRequest("revoke", 1, reason="No longer applicable."), SOURCE_SCOPE
        )

        self.assertEqual(rejected.current.status, "rejected")
        self.assertEqual(revoked.current.status, "revoked")
        for terminal in (rejected, revoked):
            with self.subTest(status=terminal.current.status), self.assertRaisesRegex(LearningFailure, "terminal"):
                apply_approval_decision(
                    terminal, ApprovalDecisionRequest("approve", terminal.version, "project"), SOURCE_SCOPE
                )
        with self.assertRaisesRegex(LearningFailure, "reason"):
            apply_approval_decision(state(), ApprovalDecisionRequest("reject", 0), SOURCE_SCOPE)

    def test_stale_version_and_cross_source_tenant_fail_closed(self) -> None:
        approved = approve()
        with self.assertRaisesRegex(LearningFailure, "stale"):
            apply_approval_decision(approved, ApprovalDecisionRequest("revoke", 0, reason="stale"), SOURCE_SCOPE)
        with self.assertRaisesRegex(LearningFailure, "source tenant"):
            apply_approval_decision(
                state(), ApprovalDecisionRequest("approve", 0, "project"), OTHER_CLIENT_SCOPE
            )

    def test_replay_and_request_digests_are_deterministic(self) -> None:
        request = ApprovalDecisionRequest("approve", 0, "project")
        first = apply_approval_decision(state(), request, SOURCE_SCOPE)
        second = apply_approval_decision(state(), request, SOURCE_SCOPE)

        self.assertEqual(first, second)
        self.assertEqual(first.replay_digest, second.replay_digest)
        self.assertEqual(first.current.request_digest, approval_request_digest(state(), request, SOURCE_SCOPE))
        self.assertNotEqual(
            approval_request_digest(state(), request, SOURCE_SCOPE),
            approval_request_digest(state(), request, OTHER_ACTOR_SCOPE),
        )

    def test_project_scope_only_authorizes_the_source_project(self) -> None:
        approved = approve()
        citation = authorize_citation(approved, SOURCE_PROJECT, SOURCE_SCOPE, "Apply the approved handoff control.")

        self.assertEqual(citation.promotion_version, 1)
        self.assertEqual(citation.effective_scope, "project")
        with self.assertRaisesRegex(LearningFailure, "project knowledge"):
            authorize_citation(approved, CLIENT_TARGET_PROJECT, SOURCE_SCOPE, "Cross-project reuse is not approved.")

    def test_client_scope_authorizes_only_the_same_client(self) -> None:
        client = apply_approval_decision(
            approve(), ApprovalDecisionRequest("elevate", 1, "client", reason="Client convention."), SOURCE_SCOPE
        )
        citation = authorize_citation(client, CLIENT_TARGET_PROJECT, SOURCE_SCOPE, "Apply the client convention.")

        self.assertEqual(citation.effective_scope, "client")
        with self.assertRaisesRegex(LearningFailure, "client knowledge"):
            authorize_citation(client, CLIENT_TARGET_PROJECT, OTHER_CLIENT_SCOPE, "Cross-client use.")

    def test_candidate_rejected_or_revoked_knowledge_is_never_citable(self) -> None:
        for value in (
            state(),
            apply_approval_decision(state(), ApprovalDecisionRequest("reject", 0, reason="Insufficient evidence."), SOURCE_SCOPE),
            apply_approval_decision(approve(), ApprovalDecisionRequest("revoke", 1, reason="Invalidated."), SOURCE_SCOPE),
        ):
            with self.subTest(version=value.version), self.assertRaisesRegex(LearningFailure, "not approved"):
                authorize_citation(value, SOURCE_PROJECT, SOURCE_SCOPE, "Attempted use.")

    def test_candidate_hash_and_scope_context_are_server_derived_and_validated(self) -> None:
        with self.assertRaisesRegex(LearningFailure, "does not match"):
            new_approval_state(replace(candidate(), content_sha256="b" * 64), SOURCE_SCOPE)
        with self.assertRaisesRegex(LearningFailure, "scope identity"):
            new_approval_state(candidate(), replace(SOURCE_SCOPE, actor_id="not-a-uuid"))


if __name__ == "__main__":
    unittest.main()
