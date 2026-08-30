#!/usr/bin/env python3
"""Validate the metadata-only M2-04 paired-evaluation record contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORD = ROOT / "validation" / "templates" / "m2-04-paired-evaluation-record.json"

STATUS_CODES = {"planned", "running", "completed", "invalidated"}
OUTCOME_CODES = {"not-run", "passed", "failed", "stopped"}
FORBIDDEN_CLAIMS = {
    "demand",
    "roi",
    "time-savings",
    "repeat-use",
    "payment",
    "production-readiness",
}
CLAIM_BOUNDARY_CODES = {
    "no-demand-claim",
    "no-roi-claim",
    "no-time-savings-claim",
    "no-repeat-use-claim",
    "no-payment-claim",
    "no-production-readiness-claim",
}
REMOVED_CATEGORY_CODES = {
    "personal-name",
    "organization-name",
    "customer-name",
    "brand-name",
    "contact-details",
    "commercial-terms",
    "project-identifiers",
    "other-sensitive-content",
}
RETAINED_CONTROL_CODES = {
    "assumption-traceability-check",
    "citation-provenance-check",
    "constraint-check",
    "scope-boundary-check",
    "source-grounding-check",
}
LIMITATION_CODES = {
    "not-run",
    "single-paired-run",
    "non-blind-citation",
    "metadata-only-record",
    "no-commercial-inference",
    "human-authorization-unverified-by-script",
    "independent-evaluator-unverified-by-script",
}
REQUIRED_COMPLETED_LIMITATIONS = {
    "single-paired-run",
    "non-blind-citation",
    "metadata-only-record",
    "no-commercial-inference",
    "human-authorization-unverified-by-script",
    "independent-evaluator-unverified-by-script",
}
EVALUATOR_ROLE_CODES = {"not-assigned", "independent-evaluator"}
EVALUATOR_OPINION_CODES = {
    "not-run",
    "citation-adopted",
    "citation-not-adopted",
    "inconclusive",
    "safety-stop",
    "invalidated",
}
CITATION_REASON_CODES = {"paired-evaluation-approved-reuse"}
ADOPTION_CRITERIA = {"citation-adopted-plus-retained-control"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

TOP_LEVEL_KEYS = {
    "schemaVersion",
    "recordType",
    "status",
    "evaluationId",
    "isSynthetic",
    "task",
    "evaluator",
    "preRegistration",
    "conditions",
    "rubric",
    "result",
}
TASK_KEYS = {
    "authorizedDeidentifiedTaskRef",
    "targetProjectRef",
    "targetWorkspaceRef",
    "targetClientRef",
    "briefSha256",
    "authorizationRecorded",
    "deidentified",
    "removedCategories",
}
EVALUATOR_KEYS = {
    "evaluatorRef",
    "role",
    "independentOfImplementation",
    "evaluationDate",
    "consentToPublishDeidentified",
}
PREREGISTRATION_KEYS = {
    "recordedAt",
    "rubricVersion",
    "adoptionCriterion",
    "stopOnSeriousFactualOrPrivacyIssue",
    "prohibitedClaims",
}
CONDITIONS_KEYS = {"conditionOrder", "blind", "control", "treatment"}
CONTROL_KEYS = {"historicalKnowledge", "briefSha256", "outputRef", "outputSha256"}
TREATMENT_KEYS = {
    "historicalKnowledge",
    "citationSnapshotRef",
    "citationPromotionVersion",
    "citationEffectiveScope",
    "citationSourceProjectRef",
    "citationSourceWorkspaceRef",
    "citationSourceClientRef",
    "citationTargetProjectRef",
    "citationWorkspaceRef",
    "citationClientRef",
    "citationReasonCode",
    "citationContentSha256",
    "briefSha256",
    "outputRef",
    "outputSha256",
}
RUBRIC_KEYS = {
    "seriousFactualOrPrivacyIssue",
    "unsupportedClaims",
    "usefulRetainedControls",
    "privacyLeakDetected",
    "crossClientLeakDetected",
    "citationActuallyAdopted",
    "evaluatorNotesRef",
    "evaluatorNotesSha256",
}
RESULT_KEYS = {
    "completedAt",
    "outcome",
    "observedMeasures",
    "evaluatorOpinionCode",
    "evaluatorOpinionRef",
    "evaluatorOpinionSha256",
    "noCommercialInference",
    "claimBoundary",
    "limitations",
}
CONDITION_MEASURE_KEYS = {
    "factualErrorCount",
    "unsupportedClaimCount",
    "usefulRetainedControlCount",
    "seriousFactualOrPrivacyIssue",
    "privacyLeakDetected",
    "crossClientLeakDetected",
    "citationActuallyAdopted",
}


def expect_keys(value: dict[str, Any], expected: set[str], label: str, failures: list[str]) -> None:
    if set(value) != expected:
        failures.append(f"{label} must contain exactly {sorted(expected)}")


def object_value(value: Any, expected: set[str], label: str, failures: list[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        failures.append(f"{label} must be an object")
        return None
    expect_keys(value, expected, label, failures)
    return value


def code_value(value: Any, allowed: set[str], label: str, failures: list[str]) -> bool:
    if not isinstance(value, str) or value not in allowed:
        failures.append(f"{label} is invalid")
        return False
    return True


def coded_list(value: Any, allowed: set[str], label: str, failures: list[str], *, nonempty: bool = False) -> bool:
    if not isinstance(value, list):
        failures.append(f"{label} must be a code list")
        return False
    if nonempty and not value:
        failures.append(f"{label} must not be empty")
        return False
    if any(not isinstance(item, str) or item not in allowed for item in value):
        failures.append(f"{label} contains an invalid code")
        return False
    if len(value) != len(set(value)):
        failures.append(f"{label} must not contain duplicate codes")
        return False
    return True


def exact_code_set(value: Any, expected: set[str], label: str, failures: list[str]) -> bool:
    if not coded_list(value, expected, label, failures):
        return False
    if set(value) != expected:
        failures.append(f"{label} must preserve the full required code set")
        return False
    return True


def valid_sha256(value: Any, *, allow_pending: bool) -> bool:
    return isinstance(value, str) and (
        SHA256_RE.fullmatch(value) is not None
        or (allow_pending and value == "pending-sha256")
    )


def opaque_ref(value: Any, prefix: str, *, allow_pending: bool) -> bool:
    if not isinstance(value, str):
        return False
    if allow_pending and value == f"pending-{prefix}":
        return True
    return re.fullmatch(rf"{re.escape(prefix)}-[0-9a-f]{{16,64}}", value) is not None


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def validate_ref(value: Any, prefix: str, label: str, failures: list[str], *, allow_pending: bool) -> None:
    if not opaque_ref(value, prefix, allow_pending=allow_pending):
        suffix = " or pending placeholder" if allow_pending else ""
        failures.append(f"{label} must be an opaque {prefix} reference{suffix}")


def validate_digest(value: Any, label: str, failures: list[str], *, allow_pending: bool) -> None:
    if not valid_sha256(value, allow_pending=allow_pending):
        suffix = " or pending placeholder" if allow_pending else ""
        failures.append(f"{label} must be a SHA-256 digest{suffix}")


def validate_pair(value: Any, label: str, kind: type, failures: list[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {"control", "treatment"}:
        failures.append(f"{label} must contain control and treatment")
        return None
    if kind is int:
        valid = all(
            isinstance(value[condition], int)
            and not isinstance(value[condition], bool)
            and value[condition] >= 0
            for condition in ("control", "treatment")
        )
        description = "non-negative integers"
    else:
        valid = all(isinstance(value[condition], bool) for condition in ("control", "treatment"))
        description = "booleans"
    if not valid:
        failures.append(f"{label} values must be {description}")
        return None
    return value


def validate_retained_controls(value: Any, label: str, failures: list[str]) -> dict[str, list[dict[str, Any]]] | None:
    if not isinstance(value, dict) or set(value) != {"control", "treatment"}:
        failures.append(f"{label} must contain control and treatment")
        return None
    valid_controls: dict[str, list[dict[str, Any]]] = {}
    for condition in ("control", "treatment"):
        entries = value[condition]
        if not isinstance(entries, list):
            failures.append(f"{label}.{condition} must be a list")
            continue
        codes: list[str] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {"controlCode", "adopted"}:
                failures.append(f"{label}.{condition}[{index}] must contain controlCode and adopted")
                continue
            control_code = entry.get("controlCode")
            adopted = entry.get("adopted")
            if not isinstance(control_code, str) or control_code not in RETAINED_CONTROL_CODES or not isinstance(adopted, bool):
                failures.append(f"{label}.{condition}[{index}] is invalid")
                continue
            codes.append(control_code)
        if len(codes) != len(set(codes)):
            failures.append(f"{label}.{condition} must not duplicate controlCode values")
        if len(codes) == len(entries):
            valid_controls[condition] = entries
    return valid_controls if set(valid_controls) == {"control", "treatment"} else None


def validate_measure(value: Any, label: str, failures: list[str]) -> dict[str, Any] | None:
    measure = object_value(value, CONDITION_MEASURE_KEYS, label, failures)
    if measure is None:
        return None
    for key in ("factualErrorCount", "unsupportedClaimCount", "usefulRetainedControlCount"):
        item = measure.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            failures.append(f"{label}.{key} must be a non-negative integer")
    for key in (
        "seriousFactualOrPrivacyIssue",
        "privacyLeakDetected",
        "crossClientLeakDetected",
        "citationActuallyAdopted",
    ):
        if not isinstance(measure.get(key), bool):
            failures.append(f"{label}.{key} must be boolean")
    return measure


def validate_completed_rubric(rubric: dict[str, Any], failures: list[str]) -> dict[str, Any] | None:
    serious = validate_pair(rubric.get("seriousFactualOrPrivacyIssue"), "rubric.seriousFactualOrPrivacyIssue", bool, failures)
    unsupported = validate_pair(rubric.get("unsupportedClaims"), "rubric.unsupportedClaims", int, failures)
    retained = validate_retained_controls(rubric.get("usefulRetainedControls"), "rubric.usefulRetainedControls", failures)
    privacy = validate_pair(rubric.get("privacyLeakDetected"), "rubric.privacyLeakDetected", bool, failures)
    cross_client = validate_pair(rubric.get("crossClientLeakDetected"), "rubric.crossClientLeakDetected", bool, failures)
    if not isinstance(rubric.get("citationActuallyAdopted"), bool):
        failures.append("rubric.citationActuallyAdopted must be boolean")
    validate_ref(rubric.get("evaluatorNotesRef"), "notes", "rubric.evaluatorNotesRef", failures, allow_pending=False)
    validate_digest(rubric.get("evaluatorNotesSha256"), "rubric.evaluatorNotesSha256", failures, allow_pending=False)
    if None in (serious, unsupported, retained, privacy, cross_client):
        return None
    return {
        "serious": serious,
        "unsupported": unsupported,
        "retained": retained,
        "privacy": privacy,
        "cross_client": cross_client,
        "citation_adopted": rubric.get("citationActuallyAdopted"),
    }


def validate(record: Any) -> list[str]:
    failures: list[str] = []
    if not isinstance(record, dict):
        return ["record must be an object"]
    expect_keys(record, TOP_LEVEL_KEYS, "record", failures)
    if type(record.get("schemaVersion")) is not int or record.get("schemaVersion") != 1:
        failures.append("schemaVersion must be 1")
    if record.get("recordType") != "m2-04-paired-usefulness-evaluation":
        failures.append("recordType is invalid")
    status_is_valid = code_value(record.get("status"), STATUS_CODES, "status", failures)
    completed = record.get("status") == "completed"
    allow_pending = not completed
    if not isinstance(record.get("isSynthetic"), bool):
        failures.append("isSynthetic must be boolean")
    validate_ref(record.get("evaluationId"), "eval", "evaluationId", failures, allow_pending=allow_pending)

    task = object_value(record.get("task"), TASK_KEYS, "task", failures)
    if task is not None:
        validate_ref(task.get("authorizedDeidentifiedTaskRef"), "task", "task.authorizedDeidentifiedTaskRef", failures, allow_pending=allow_pending)
        validate_ref(task.get("targetProjectRef"), "project", "task.targetProjectRef", failures, allow_pending=allow_pending)
        validate_ref(task.get("targetWorkspaceRef"), "workspace", "task.targetWorkspaceRef", failures, allow_pending=allow_pending)
        validate_ref(task.get("targetClientRef"), "client", "task.targetClientRef", failures, allow_pending=allow_pending)
        validate_digest(task.get("briefSha256"), "task.briefSha256", failures, allow_pending=allow_pending)
        if not isinstance(task.get("authorizationRecorded"), bool):
            failures.append("task.authorizationRecorded must be boolean")
        if task.get("deidentified") is not True:
            failures.append("task must be deidentified")
        coded_list(task.get("removedCategories"), REMOVED_CATEGORY_CODES, "task.removedCategories", failures, nonempty=True)

    evaluator = object_value(record.get("evaluator"), EVALUATOR_KEYS, "evaluator", failures)
    if evaluator is not None:
        validate_ref(evaluator.get("evaluatorRef"), "evaluator", "evaluator.evaluatorRef", failures, allow_pending=allow_pending)
        code_value(evaluator.get("role"), EVALUATOR_ROLE_CODES, "evaluator.role", failures)
        if not isinstance(evaluator.get("independentOfImplementation"), bool):
            failures.append("evaluator.independentOfImplementation must be boolean")
        evaluation_date = evaluator.get("evaluationDate")
        if evaluation_date is not None and timestamp(evaluation_date) is None:
            failures.append("evaluator.evaluationDate must be a timezone-aware ISO timestamp")
        if not isinstance(evaluator.get("consentToPublishDeidentified"), bool):
            failures.append("evaluator.consentToPublishDeidentified must be boolean")

    preregistration = object_value(record.get("preRegistration"), PREREGISTRATION_KEYS, "preRegistration", failures)
    if preregistration is not None:
        recorded_at = preregistration.get("recordedAt")
        if recorded_at is not None and timestamp(recorded_at) is None:
            failures.append("preRegistration.recordedAt must be a timezone-aware ISO timestamp")
        if preregistration.get("rubricVersion") != "m2-04-v1":
            failures.append("preRegistration.rubricVersion is invalid")
        code_value(preregistration.get("adoptionCriterion"), ADOPTION_CRITERIA, "preRegistration.adoptionCriterion", failures)
        if preregistration.get("stopOnSeriousFactualOrPrivacyIssue") is not True:
            failures.append("serious factual or privacy issues must stop the evaluation")
        exact_code_set(preregistration.get("prohibitedClaims"), FORBIDDEN_CLAIMS, "preRegistration.prohibitedClaims", failures)

    conditions = object_value(record.get("conditions"), CONDITIONS_KEYS, "conditions", failures)
    control = treatment = None
    if conditions is not None:
        code_value(conditions.get("conditionOrder"), {"not-run", "control-first", "treatment-first"}, "conditions.conditionOrder", failures)
        if conditions.get("blind") is not False:
            failures.append("paired evaluation must record the visible citation as non-blind")
        control = object_value(conditions.get("control"), CONTROL_KEYS, "conditions.control", failures)
        treatment = object_value(conditions.get("treatment"), TREATMENT_KEYS, "conditions.treatment", failures)
    if control is not None:
        if control.get("historicalKnowledge") != "none":
            failures.append("control condition must not receive historical knowledge")
        validate_digest(control.get("briefSha256"), "conditions.control.briefSha256", failures, allow_pending=allow_pending)
        validate_ref(control.get("outputRef"), "output", "conditions.control.outputRef", failures, allow_pending=allow_pending)
        validate_digest(control.get("outputSha256"), "conditions.control.outputSha256", failures, allow_pending=allow_pending)
    if treatment is not None:
        if treatment.get("historicalKnowledge") != "approved-citation-only":
            failures.append("treatment condition must use approved citations only")
        validate_ref(treatment.get("citationSnapshotRef"), "citation", "conditions.treatment.citationSnapshotRef", failures, allow_pending=allow_pending)
        promotion = treatment.get("citationPromotionVersion")
        if promotion is not None and (not isinstance(promotion, int) or isinstance(promotion, bool) or promotion < 1):
            failures.append("conditions.treatment.citationPromotionVersion must be a positive integer or null")
        scope = treatment.get("citationEffectiveScope")
        if scope is not None and (not isinstance(scope, str) or scope not in {"project", "client"}):
            failures.append("conditions.treatment.citationEffectiveScope is invalid")
        validate_ref(treatment.get("citationSourceProjectRef"), "project", "conditions.treatment.citationSourceProjectRef", failures, allow_pending=allow_pending)
        validate_ref(treatment.get("citationSourceWorkspaceRef"), "workspace", "conditions.treatment.citationSourceWorkspaceRef", failures, allow_pending=allow_pending)
        validate_ref(treatment.get("citationSourceClientRef"), "client", "conditions.treatment.citationSourceClientRef", failures, allow_pending=allow_pending)
        validate_ref(treatment.get("citationTargetProjectRef"), "project", "conditions.treatment.citationTargetProjectRef", failures, allow_pending=allow_pending)
        validate_ref(treatment.get("citationWorkspaceRef"), "workspace", "conditions.treatment.citationWorkspaceRef", failures, allow_pending=allow_pending)
        validate_ref(treatment.get("citationClientRef"), "client", "conditions.treatment.citationClientRef", failures, allow_pending=allow_pending)
        code_value(treatment.get("citationReasonCode"), CITATION_REASON_CODES, "conditions.treatment.citationReasonCode", failures)
        validate_digest(treatment.get("citationContentSha256"), "conditions.treatment.citationContentSha256", failures, allow_pending=allow_pending)
        validate_digest(treatment.get("briefSha256"), "conditions.treatment.briefSha256", failures, allow_pending=allow_pending)
        validate_ref(treatment.get("outputRef"), "output", "conditions.treatment.outputRef", failures, allow_pending=allow_pending)
        validate_digest(treatment.get("outputSha256"), "conditions.treatment.outputSha256", failures, allow_pending=allow_pending)
    if task is not None and control is not None and treatment is not None:
        if control.get("briefSha256") != task.get("briefSha256") or treatment.get("briefSha256") != task.get("briefSha256"):
            failures.append("both conditions must use the registered deidentified brief")

    rubric = object_value(record.get("rubric"), RUBRIC_KEYS, "rubric", failures)
    if rubric is not None:
        validate_ref(rubric.get("evaluatorNotesRef"), "notes", "rubric.evaluatorNotesRef", failures, allow_pending=allow_pending)
        validate_digest(rubric.get("evaluatorNotesSha256"), "rubric.evaluatorNotesSha256", failures, allow_pending=allow_pending)

    result = object_value(record.get("result"), RESULT_KEYS, "result", failures)
    if result is not None:
        completed_at = result.get("completedAt")
        if completed_at is not None and timestamp(completed_at) is None:
            failures.append("result.completedAt must be a timezone-aware ISO timestamp")
        code_value(result.get("outcome"), OUTCOME_CODES, "result.outcome", failures)
        if result.get("observedMeasures") is not None and not isinstance(result.get("observedMeasures"), dict):
            failures.append("result.observedMeasures must be an object or null")
        code_value(result.get("evaluatorOpinionCode"), EVALUATOR_OPINION_CODES, "result.evaluatorOpinionCode", failures)
        validate_ref(result.get("evaluatorOpinionRef"), "opinion", "result.evaluatorOpinionRef", failures, allow_pending=allow_pending)
        validate_digest(result.get("evaluatorOpinionSha256"), "result.evaluatorOpinionSha256", failures, allow_pending=allow_pending)
        if result.get("noCommercialInference") is not True:
            failures.append("result must prohibit commercial inference")
        exact_code_set(result.get("claimBoundary"), CLAIM_BOUNDARY_CODES, "result.claimBoundary", failures)
        coded_list(result.get("limitations"), LIMITATION_CODES, "result.limitations", failures, nonempty=True)

    if not completed:
        if status_is_valid and result is not None and result.get("outcome") != "not-run":
            failures.append("only a completed record may have an outcome")
        if status_is_valid and result is not None and (result.get("completedAt") is not None or result.get("observedMeasures") is not None):
            failures.append("a non-completed record must not contain completed evaluation results")
        return failures

    if record.get("isSynthetic") is not False:
        failures.append("a synthetic record cannot be completed usefulness evidence")
    if task is not None and task.get("authorizationRecorded") is not True:
        failures.append("a completed record requires documented authorization")
    if evaluator is not None:
        if evaluator.get("independentOfImplementation") is not True or evaluator.get("role") != "independent-evaluator":
            failures.append("a completed record requires an independent evaluator")
        if evaluator.get("consentToPublishDeidentified") is not True:
            failures.append("a completed record requires consent to publish deidentified metadata")
        if timestamp(evaluator.get("evaluationDate")) is None:
            failures.append("a completed record requires an evaluator evaluation date")
    if preregistration is not None and timestamp(preregistration.get("recordedAt")) is None:
        failures.append("a completed record requires pre-registration")
    if conditions is not None and conditions.get("conditionOrder") == "not-run":
        failures.append("a completed record requires a registered condition order")
    if result is not None:
        if not isinstance(result.get("outcome"), str) or result.get("outcome") not in {"passed", "failed", "stopped"}:
            failures.append("a completed record must have passed, failed, or stopped outcome")
        if timestamp(result.get("completedAt")) is None:
            failures.append("a completed record requires a completion timestamp")
        limitations = result.get("limitations")
        if (
            not isinstance(limitations, list)
            or any(not isinstance(item, str) for item in limitations)
            or not REQUIRED_COMPLETED_LIMITATIONS.issubset(set(limitations))
        ):
            failures.append("a completed record must retain all required limitation codes")

    if task is not None and treatment is not None:
        if treatment.get("citationEffectiveScope") != "client":
            failures.append("a cross-project treatment citation must use client scope")
        if treatment.get("citationSourceProjectRef") == task.get("targetProjectRef"):
            failures.append("treatment citation source and target projects must differ")
        if treatment.get("citationSourceWorkspaceRef") != task.get("targetWorkspaceRef"):
            failures.append("treatment citation source workspace must match the registered target workspace")
        if treatment.get("citationSourceClientRef") != task.get("targetClientRef"):
            failures.append("treatment citation source client must match the registered target client")
        if treatment.get("citationTargetProjectRef") != task.get("targetProjectRef"):
            failures.append("treatment citation target project must match the registered target project")
        if treatment.get("citationWorkspaceRef") != task.get("targetWorkspaceRef"):
            failures.append("treatment citation workspace must match the registered target workspace")
        if treatment.get("citationClientRef") != task.get("targetClientRef"):
            failures.append("treatment citation client must match the registered target client")
        promotion = treatment.get("citationPromotionVersion")
        if not isinstance(promotion, int) or isinstance(promotion, bool) or promotion < 1:
            failures.append("completed treatment requires a positive citation promotion version")

    completed_rubric = validate_completed_rubric(rubric, failures) if rubric is not None else None
    measures: dict[str, dict[str, Any]] | None = None
    if result is not None:
        observed = result.get("observedMeasures")
        if not isinstance(observed, dict) or set(observed) != {"control", "treatment"}:
            failures.append("completed result requires observedMeasures for control and treatment")
        else:
            control_measure = validate_measure(observed.get("control"), "result.observedMeasures.control", failures)
            treatment_measure = validate_measure(observed.get("treatment"), "result.observedMeasures.treatment", failures)
            if control_measure is not None and treatment_measure is not None:
                measures = {"control": control_measure, "treatment": treatment_measure}

    if completed_rubric is not None and measures is not None:
        for condition in ("control", "treatment"):
            if measures[condition]["seriousFactualOrPrivacyIssue"] != completed_rubric["serious"][condition]:
                failures.append(f"{condition} serious-issue measure must match the rubric")
            if measures[condition]["privacyLeakDetected"] != completed_rubric["privacy"][condition]:
                failures.append(f"{condition} privacy-leak measure must match the rubric")
            if measures[condition]["crossClientLeakDetected"] != completed_rubric["cross_client"][condition]:
                failures.append(f"{condition} cross-client-leak measure must match the rubric")
            if measures[condition]["unsupportedClaimCount"] != completed_rubric["unsupported"][condition]:
                failures.append(f"{condition} unsupported-claim measure must match the rubric")
            adopted_controls = sum(1 for entry in completed_rubric["retained"][condition] if entry["adopted"])
            if measures[condition]["usefulRetainedControlCount"] != adopted_controls:
                failures.append(f"{condition} retained-control measure must match adopted controls")
        if measures["control"]["citationActuallyAdopted"] is not False:
            failures.append("control citation adoption must be false")
        if measures["treatment"]["citationActuallyAdopted"] != completed_rubric["citation_adopted"]:
            failures.append("treatment citation adoption measure must match the rubric")

        serious_or_leak = any(
            completed_rubric[key][condition]
            for key in ("serious", "privacy", "cross_client")
            for condition in ("control", "treatment")
        )
        if serious_or_leak and result is not None and result.get("outcome") not in {"failed", "stopped"}:
            failures.append("a serious factual, privacy, or cross-client issue requires failed or stopped outcome")

        if result is not None and result.get("outcome") == "passed":
            if completed_rubric["citation_adopted"] is not True:
                failures.append("a passed record requires treatment citation adoption")
            if not any(entry["adopted"] for entry in completed_rubric["retained"]["treatment"]):
                failures.append("a passed record requires at least one adopted treatment retained control")
            if result.get("evaluatorOpinionCode") != "citation-adopted":
                failures.append("a passed record requires the citation-adopted evaluator opinion code")

    if evaluator is not None and preregistration is not None and result is not None:
        preregistered_at = timestamp(preregistration.get("recordedAt"))
        evaluated_at = timestamp(evaluator.get("evaluationDate"))
        completed_at = timestamp(result.get("completedAt"))
        if preregistered_at is not None and evaluated_at is not None and completed_at is not None:
            if not (preregistered_at < evaluated_at <= completed_at):
                failures.append("timestamps must satisfy pre-registration < evaluation date <= completion")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("record", nargs="?", type=Path, default=DEFAULT_RECORD)
    args = parser.parse_args()
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"FAIL: could not read record: {error}", file=sys.stderr)
        return 1
    failures = validate(record)
    if failures:
        print("FAIL: M2-04 evaluation record checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"PASS: M2-04 evaluation record contract ({args.record})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
