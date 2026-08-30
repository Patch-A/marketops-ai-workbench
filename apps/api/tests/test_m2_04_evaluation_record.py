from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.check_m2_04_evaluation_record import DEFAULT_RECORD, validate


def opaque_ref(prefix: str, value: int) -> str:
    return f"{prefix}-{value:016x}"


class M204EvaluationRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = json.loads(Path(DEFAULT_RECORD).read_text(encoding="utf-8"))

    def test_template_preserves_the_metadata_only_boundary(self) -> None:
        self.assertEqual(validate(self.record), [])

    def completed_record(self) -> dict:
        record = copy.deepcopy(self.record)
        brief_sha = "a" * 64
        record.update({"status": "completed", "isSynthetic": False, "evaluationId": opaque_ref("eval", 1)})
        record["task"].update(
            {
                "authorizedDeidentifiedTaskRef": opaque_ref("task", 2),
                "targetProjectRef": opaque_ref("project", 3),
                "targetWorkspaceRef": opaque_ref("workspace", 4),
                "targetClientRef": opaque_ref("client", 5),
                "briefSha256": brief_sha,
                "authorizationRecorded": True,
                "removedCategories": ["customer-name", "brand-name", "commercial-terms"],
            }
        )
        record["evaluator"].update(
            {
                "evaluatorRef": opaque_ref("evaluator", 6),
                "role": "independent-evaluator",
                "independentOfImplementation": True,
                "evaluationDate": "2026-08-17T10:00:00+00:00",
                "consentToPublishDeidentified": True,
            }
        )
        record["preRegistration"]["recordedAt"] = "2026-08-17T09:00:00+00:00"
        record["conditions"]["conditionOrder"] = "control-first"
        record["conditions"]["control"].update(
            {
                "briefSha256": brief_sha,
                "outputRef": opaque_ref("output", 7),
                "outputSha256": "b" * 64,
            }
        )
        record["conditions"]["treatment"].update(
            {
                "citationSnapshotRef": opaque_ref("citation", 8),
                "citationPromotionVersion": 1,
                "citationEffectiveScope": "client",
                "citationSourceProjectRef": opaque_ref("project", 9),
                "citationSourceWorkspaceRef": opaque_ref("workspace", 4),
                "citationSourceClientRef": opaque_ref("client", 5),
                "citationTargetProjectRef": opaque_ref("project", 3),
                "citationWorkspaceRef": opaque_ref("workspace", 4),
                "citationClientRef": opaque_ref("client", 5),
                "citationContentSha256": "c" * 64,
                "briefSha256": brief_sha,
                "outputRef": opaque_ref("output", 10),
                "outputSha256": "d" * 64,
            }
        )
        record["rubric"] = {
            "seriousFactualOrPrivacyIssue": {"control": False, "treatment": False},
            "unsupportedClaims": {"control": 1, "treatment": 0},
            "usefulRetainedControls": {
                "control": [{"controlCode": "source-grounding-check", "adopted": False}],
                "treatment": [{"controlCode": "source-grounding-check", "adopted": True}],
            },
            "privacyLeakDetected": {"control": False, "treatment": False},
            "crossClientLeakDetected": {"control": False, "treatment": False},
            "citationActuallyAdopted": True,
            "evaluatorNotesRef": opaque_ref("notes", 11),
            "evaluatorNotesSha256": "e" * 64,
        }
        control_measure = {
            "factualErrorCount": 0,
            "unsupportedClaimCount": 1,
            "usefulRetainedControlCount": 0,
            "seriousFactualOrPrivacyIssue": False,
            "privacyLeakDetected": False,
            "crossClientLeakDetected": False,
            "citationActuallyAdopted": False,
        }
        treatment_measure = {
            **control_measure,
            "unsupportedClaimCount": 0,
            "usefulRetainedControlCount": 1,
            "citationActuallyAdopted": True,
        }
        record["result"].update(
            {
                "completedAt": "2026-08-17T11:00:00+00:00",
                "outcome": "passed",
                "observedMeasures": {"control": control_measure, "treatment": treatment_measure},
                "evaluatorOpinionCode": "citation-adopted",
                "evaluatorOpinionRef": opaque_ref("opinion", 12),
                "evaluatorOpinionSha256": "f" * 64,
                "limitations": [
                    "single-paired-run",
                    "non-blind-citation",
                    "metadata-only-record",
                    "no-commercial-inference",
                    "human-authorization-unverified-by-script",
                    "independent-evaluator-unverified-by-script",
                ],
            }
        )
        return record

    def test_completed_record_requires_real_metadata_and_hashes(self) -> None:
        self.assertEqual(validate(self.completed_record()), [])

    def test_control_cannot_receive_historical_knowledge(self) -> None:
        record = copy.deepcopy(self.record)
        record["conditions"]["control"]["historicalKnowledge"] = "approved-citation-only"
        self.assertIn("control condition must not receive historical knowledge", validate(record))

    def test_completed_record_rejects_free_text_and_unexpected_fields(self) -> None:
        record = self.completed_record()
        record["evaluationId"] = "customer-name-and-brief-content"
        record["result"]["comment"] = "The customer says this saved time."
        failures = validate(record)
        self.assertIn("evaluationId must be an opaque eval reference", failures)
        self.assertTrue(any("result must contain exactly" in failure for failure in failures))

    def test_completed_record_rejects_free_text_opinion_and_nonopaque_reference(self) -> None:
        record = self.completed_record()
        record["result"]["evaluatorOpinionCode"] = "customer said ROI improved"
        record["rubric"]["evaluatorNotesRef"] = "https://example.test/customer-brief"
        failures = validate(record)
        self.assertIn("result.evaluatorOpinionCode is invalid", failures)
        self.assertIn("rubric.evaluatorNotesRef must be an opaque notes reference", failures)

    def test_completed_record_requires_adoption_for_a_pass(self) -> None:
        record = self.completed_record()
        record["rubric"]["citationActuallyAdopted"] = False
        record["rubric"]["usefulRetainedControls"]["treatment"][0]["adopted"] = False
        record["result"]["observedMeasures"]["treatment"]["citationActuallyAdopted"] = False
        record["result"]["observedMeasures"]["treatment"]["usefulRetainedControlCount"] = 0
        failures = validate(record)
        self.assertIn("a passed record requires treatment citation adoption", failures)
        self.assertIn("a passed record requires at least one adopted treatment retained control", failures)

    def test_completed_record_rejects_wrong_time_order_and_naive_timestamps(self) -> None:
        record = self.completed_record()
        record["preRegistration"]["recordedAt"] = "2026-08-17T10:30:00+00:00"
        record["evaluator"]["evaluationDate"] = "2026-08-17T10:00:00"
        failures = validate(record)
        self.assertIn("evaluator.evaluationDate must be a timezone-aware ISO timestamp", failures)

        record = self.completed_record()
        record["preRegistration"]["recordedAt"] = "2026-08-17T10:30:00+00:00"
        failures = validate(record)
        self.assertIn("timestamps must satisfy pre-registration < evaluation date <= completion", failures)

    def test_completed_record_binds_client_scope_and_target_metadata(self) -> None:
        record = self.completed_record()
        record["conditions"]["treatment"]["citationEffectiveScope"] = "project"
        record["conditions"]["treatment"]["citationSourceProjectRef"] = record["task"]["targetProjectRef"]
        record["conditions"]["treatment"]["citationTargetProjectRef"] = opaque_ref("project", 13)
        record["conditions"]["treatment"]["citationSourceWorkspaceRef"] = opaque_ref("workspace", 14)
        record["conditions"]["treatment"]["citationSourceClientRef"] = opaque_ref("client", 15)
        record["conditions"]["treatment"]["citationClientRef"] = opaque_ref("client", 14)
        failures = validate(record)
        self.assertIn("a cross-project treatment citation must use client scope", failures)
        self.assertIn("treatment citation source and target projects must differ", failures)
        self.assertIn("treatment citation target project must match the registered target project", failures)
        self.assertIn("treatment citation source workspace must match the registered target workspace", failures)
        self.assertIn("treatment citation source client must match the registered target client", failures)
        self.assertIn("treatment citation client must match the registered target client", failures)

    def test_serious_privacy_or_cross_client_issue_cannot_be_marked_passed(self) -> None:
        record = self.completed_record()
        record["rubric"]["crossClientLeakDetected"]["treatment"] = True
        record["result"]["observedMeasures"]["treatment"]["crossClientLeakDetected"] = True
        failures = validate(record)
        self.assertIn("a serious factual, privacy, or cross-client issue requires failed or stopped outcome", failures)

    def test_completed_record_rejects_schema_bool_empty_removed_categories_and_missing_consent(self) -> None:
        record = self.completed_record()
        record["schemaVersion"] = True
        record["task"]["removedCategories"] = []
        record["evaluator"]["consentToPublishDeidentified"] = False
        failures = validate(record)
        self.assertIn("schemaVersion must be 1", failures)
        self.assertIn("task.removedCategories must not be empty", failures)
        self.assertIn("a completed record requires consent to publish deidentified metadata", failures)

    def test_malformed_types_return_validation_failures_instead_of_raising(self) -> None:
        malformed_values = [None, [], {}, True]
        for field, values in (
            ("status", malformed_values),
            ("conditions.conditionOrder", malformed_values),
            ("result.outcome", malformed_values),
            ("conditions.treatment.citationEffectiveScope", malformed_values),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    record = self.completed_record()
                    cursor = record
                    pieces = field.split(".")
                    for piece in pieces[:-1]:
                        cursor = cursor[piece]
                    cursor[pieces[-1]] = value
                    failures = validate(record)
                    self.assertIsInstance(failures, list)
                    self.assertGreater(len(failures), 0)

    def test_synthetic_record_cannot_be_completed_as_usefulness_evidence(self) -> None:
        record = self.completed_record()
        record["isSynthetic"] = True
        self.assertIn("a synthetic record cannot be completed usefulness evidence", validate(record))
