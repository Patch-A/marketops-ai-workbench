"""Dependency-neutral M2-02 project-learning domain contract.

This module accepts only server-assembled project facts. It does not approve,
promote, retrieve, or reuse candidate knowledge beyond its source project.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

LEARNING_MODE = "deterministic-v2"
CAPSULE_STATUS = "ready"
KNOWLEDGE_SCOPE = "project"
KNOWLEDGE_STATUS = "candidate"
KNOWLEDGE_TYPES = frozenset({"observed_outcome", "observed_task_duration", "retrospective_finding"})
CLASSIFICATIONS = frozenset({"success_pattern", "failure_counterexample", "risk_check", "process_observation", "non_reusable_note"})
FEEDBACK_SOURCE_TYPES = frozenset({"artifact_version", "task_execution", "schedule_snapshot"})
EVIDENCE_SOURCE_TYPES = frozenset({"artifact_version", "task", "task_execution", "plan_version", "plan_approval", "schedule_snapshot", "outcome", "retrospective", "capsule"})
TERMINAL_TASK_STATUSES = frozenset({"completed", "cancelled"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

MAX_METRIC_LENGTH = 200
MAX_VALUE_LENGTH = 1000
MAX_UNIT_LENGTH = 80
MAX_FINDING_LENGTH = 4000
MAX_TASK_TITLE_LENGTH = 300
MAX_TASK_ID_LENGTH = 300
MAX_TASKS = 1000
MAX_OUTCOMES = 1000
MAX_RETROSPECTIVES = 1000
MAX_KNOWLEDGE_ITEMS = 3000


class LearningFailure(RuntimeError):
    """Stable, redacted service error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class LearningScopeContext:
    organization_id: str
    workspace_id: str
    client_id: str
    actor_id: str


@dataclass(frozen=True)
class FeedbackSourceReference:
    source_type: str
    source_id: str
    project_id: str
    binding_sha256: str


@dataclass(frozen=True)
class OutcomeInput:
    metric: str
    planned_value: str | None
    actual_value: str | None
    unit: str | None
    source: FeedbackSourceReference


@dataclass(frozen=True)
class OutcomeRecord:
    outcome_id: str
    metric: str
    planned_value: str | None
    actual_value: str | None
    unit: str | None
    source: FeedbackSourceReference
    classification: str = "outcome_observation"


@dataclass(frozen=True)
class RetrospectiveInput:
    finding: str
    classification: str
    reusable_candidate: bool
    evidence: tuple[FeedbackSourceReference, ...]


@dataclass(frozen=True)
class RetrospectiveRecord:
    retrospective_id: str
    finding: str
    classification: str
    reusable_candidate: bool
    evidence: tuple[FeedbackSourceReference, ...]


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    title: str
    planned_start: str | None
    planned_finish: str | None
    duration_workdays: int


@dataclass(frozen=True)
class TaskExecutionSnapshot:
    execution_update_id: str
    task_id: str
    status: str
    actual_start: str | None
    actual_finish: str | None
    blocker_reason: str | None
    execution_digest: str


@dataclass(frozen=True)
class KnowledgeEvidence:
    source_type: str
    source_id: str
    project_id: str
    binding_sha256: str


@dataclass(frozen=True)
class KnowledgeItem:
    knowledge_id: str
    ordinal: int
    type: str
    status: str
    classification: str
    content: str
    confidence: float
    evidence: tuple[KnowledgeEvidence, ...]
    content_sha256: str
    scope: str
    project_id: str


@dataclass(frozen=True)
class CapsuleInputs:
    project_id: str
    proposal_artifact_id: str
    proposal_version_id: str
    proposal_version: int
    proposal_sha256: str
    plan_id: str
    plan_version_id: str
    plan_version: int
    plan_digest: str
    approval_id: str
    approval_plan_id: str
    approved_plan_version_id: str
    approval_plan_digest: str
    approval_schedule_snapshot_id: str
    approval_schedule_digest: str
    schedule_snapshot_id: str
    schedule_plan_id: str
    schedule_plan_version_id: str
    schedule_plan_digest: str
    schedule_digest: str
    tasks: tuple[TaskSnapshot, ...]
    executions: tuple[TaskExecutionSnapshot, ...]
    outcomes: tuple[OutcomeRecord, ...]
    retrospectives: tuple[RetrospectiveRecord, ...]


@dataclass(frozen=True)
class ProjectCapsule:
    capsule_id: str
    capsule_digest: str
    status: str
    payload: Mapping[str, Any]
    knowledge_items: tuple[KnowledgeItem, ...]


def normalize_outcome(input_value: OutcomeInput, scope: LearningScopeContext, outcome_id: str) -> OutcomeRecord:
    _validate_scope(scope)
    _uuid(outcome_id, "outcome id")
    if not isinstance(input_value, OutcomeInput):
        raise LearningFailure("INVALID_INPUT", "outcome input is invalid")
    planned = _optional_text(input_value.planned_value, "planned value", MAX_VALUE_LENGTH)
    actual = _optional_text(input_value.actual_value, "actual value", MAX_VALUE_LENGTH)
    if actual is None:
        raise LearningFailure("INVALID_INPUT", "outcome feedback needs an actual result")
    return OutcomeRecord(outcome_id, _required_text(input_value.metric, "metric", MAX_METRIC_LENGTH), planned, actual, _optional_text(input_value.unit, "unit", MAX_UNIT_LENGTH), _validate_feedback_source(input_value.source))


def normalize_retrospective(input_value: RetrospectiveInput, scope: LearningScopeContext, retrospective_id: str) -> RetrospectiveRecord:
    _validate_scope(scope)
    _uuid(retrospective_id, "retrospective id")
    if not isinstance(input_value, RetrospectiveInput):
        raise LearningFailure("INVALID_INPUT", "retrospective input is invalid")
    if input_value.classification not in CLASSIFICATIONS:
        raise LearningFailure("INVALID_INPUT", "retrospective classification is invalid")
    if not isinstance(input_value.reusable_candidate, bool):
        raise LearningFailure("INVALID_INPUT", "retrospective reuse choice is invalid")
    if input_value.classification == "non_reusable_note" and input_value.reusable_candidate:
        raise LearningFailure("INVALID_INPUT", "non-reusable note cannot create a candidate")
    evidence = _sorted_sources(_validate_feedback_sources(input_value.evidence))
    if not evidence:
        raise LearningFailure("INVALID_INPUT", "retrospective feedback needs evidence")
    return RetrospectiveRecord(retrospective_id, _required_text(input_value.finding, "finding", MAX_FINDING_LENGTH), input_value.classification, input_value.reusable_candidate, evidence)


def build_project_capsule(inputs: CapsuleInputs, scope: LearningScopeContext) -> ProjectCapsule:
    _validate_scope(scope)
    validated = _validate_inputs(inputs)
    task_view = _validated_task_view(validated)
    digest_material = _digest_material(validated, task_view)
    capsule_digest = _canonical_sha256(digest_material)
    capsule_id = str(uuid5(NAMESPACE_URL, f"marketops:project-capsule:{validated.project_id}:{capsule_digest}"))
    knowledge = _generate_knowledge(validated, task_view, capsule_id, capsule_digest)
    payload = dict(digest_material)
    payload.update({"capsuleId": capsule_id, "capsuleDigest": capsule_digest, "candidateKnowledgeTypes": sorted({item.type for item in knowledge})})
    return ProjectCapsule(capsule_id, capsule_digest, CAPSULE_STATUS, payload, knowledge)


def _validate_inputs(inputs: CapsuleInputs) -> CapsuleInputs:
    if not isinstance(inputs, CapsuleInputs):
        raise LearningFailure("INVALID_INPUT", "capsule inputs are invalid")
    for identity in (inputs.project_id, inputs.proposal_artifact_id, inputs.proposal_version_id, inputs.plan_id, inputs.plan_version_id, inputs.approval_id, inputs.approval_plan_id, inputs.approved_plan_version_id, inputs.approval_schedule_snapshot_id, inputs.schedule_snapshot_id, inputs.schedule_plan_id, inputs.schedule_plan_version_id):
        _uuid(identity, "capsule identity")
    for value in (inputs.proposal_version, inputs.plan_version):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise LearningFailure("INVALID_INPUT", "capsule version is invalid")
    for digest in (inputs.proposal_sha256, inputs.plan_digest, inputs.approval_plan_digest, inputs.approval_schedule_digest, inputs.schedule_plan_digest, inputs.schedule_digest):
        _sha256(digest, "capsule digest")
    if (inputs.approval_plan_id != inputs.plan_id or inputs.approved_plan_version_id != inputs.plan_version_id or inputs.approval_plan_digest != inputs.plan_digest or inputs.approval_schedule_snapshot_id != inputs.schedule_snapshot_id or inputs.approval_schedule_digest != inputs.schedule_digest or inputs.schedule_plan_id != inputs.plan_id or inputs.schedule_plan_version_id != inputs.plan_version_id or inputs.schedule_plan_digest != inputs.plan_digest):
        raise LearningFailure("INVALID_INPUT", "approval and schedule do not bind the exact plan snapshot")
    if not isinstance(inputs.tasks, tuple) or not inputs.tasks or len(inputs.tasks) > MAX_TASKS:
        raise LearningFailure("INVALID_INPUT", "capsule task set is invalid")
    if not isinstance(inputs.executions, tuple) or len(inputs.executions) > MAX_TASKS:
        raise LearningFailure("INVALID_INPUT", "capsule execution set is invalid")
    if not isinstance(inputs.outcomes, tuple) or len(inputs.outcomes) > MAX_OUTCOMES:
        raise LearningFailure("INVALID_INPUT", "capsule outcomes are invalid")
    if not isinstance(inputs.retrospectives, tuple) or len(inputs.retrospectives) > MAX_RETROSPECTIVES:
        raise LearningFailure("INVALID_INPUT", "capsule retrospectives are invalid")
    if not inputs.outcomes and not inputs.retrospectives:
        raise LearningFailure("INVALID_INPUT", "capsule needs outcome or retrospective feedback")
    _validate_tasks(inputs.tasks, inputs.executions)
    _validate_feedback(inputs.project_id, inputs.outcomes, inputs.retrospectives)
    return inputs


def _validate_tasks(tasks: tuple[TaskSnapshot, ...], executions: tuple[TaskExecutionSnapshot, ...]) -> None:
    task_ids: set[str] = set()
    execution_update_ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, TaskSnapshot):
            raise LearningFailure("INVALID_INPUT", "task is invalid")
        _required_text(task.task_id, "task id", MAX_TASK_ID_LENGTH)
        if task.task_id in task_ids:
            raise LearningFailure("INVALID_INPUT", "task ids must be unique")
        task_ids.add(task.task_id)
        _required_text(task.title, "task title", MAX_TASK_TITLE_LENGTH)
        if isinstance(task.duration_workdays, bool) or not isinstance(task.duration_workdays, int) or task.duration_workdays < 1:
            raise LearningFailure("INVALID_INPUT", "task duration is invalid")
        _optional_date(task.planned_start, "planned start")
        _optional_date(task.planned_finish, "planned finish")
        if task.planned_start and task.planned_finish and task.planned_finish < task.planned_start:
            raise LearningFailure("INVALID_INPUT", "planned finish precedes planned start")
    execution_ids: set[str] = set()
    for execution in executions:
        if not isinstance(execution, TaskExecutionSnapshot):
            raise LearningFailure("INVALID_INPUT", "execution is invalid")
        _uuid(execution.execution_update_id, "execution update id")
        if execution.execution_update_id in execution_update_ids:
            raise LearningFailure("INVALID_INPUT", "execution update ids must be unique")
        execution_update_ids.add(execution.execution_update_id)
        _required_text(execution.task_id, "execution task id", MAX_TASK_ID_LENGTH)
        if execution.task_id not in task_ids or execution.task_id in execution_ids:
            raise LearningFailure("INVALID_INPUT", "execution task identity is invalid")
        execution_ids.add(execution.task_id)
        if execution.status not in TERMINAL_TASK_STATUSES:
            raise LearningFailure("INVALID_INPUT", "every task needs terminal execution state")
        _sha256(execution.execution_digest, "execution digest")
        _optional_date(execution.actual_start, "actual start")
        _optional_date(execution.actual_finish, "actual finish")
        if execution.actual_start and execution.actual_finish and execution.actual_finish < execution.actual_start:
            raise LearningFailure("INVALID_INPUT", "actual finish precedes actual start")
        if execution.status == "completed" and (not execution.actual_start or not execution.actual_finish):
            raise LearningFailure("INVALID_INPUT", "completed task needs actual start and finish")
        if execution.status == "cancelled" and execution.blocker_reason is not None:
            raise LearningFailure("INVALID_INPUT", "cancelled task cannot carry blocker reason")
    if execution_ids != task_ids:
        raise LearningFailure("INVALID_INPUT", "every task needs terminal execution state")


def _validate_feedback(project_id: str, outcomes: tuple[OutcomeRecord, ...], retrospectives: tuple[RetrospectiveRecord, ...]) -> None:
    ids: set[str] = set()
    for outcome in outcomes:
        if not isinstance(outcome, OutcomeRecord):
            raise LearningFailure("INVALID_INPUT", "outcome is invalid")
        _uuid(outcome.outcome_id, "outcome id")
        if outcome.outcome_id in ids:
            raise LearningFailure("INVALID_INPUT", "feedback ids must be unique")
        ids.add(outcome.outcome_id)
        if outcome.classification != "outcome_observation":
            raise LearningFailure("INVALID_INPUT", "outcome classification is invalid")
        _required_text(outcome.metric, "metric", MAX_METRIC_LENGTH)
        _optional_text(outcome.planned_value, "planned value", MAX_VALUE_LENGTH)
        _optional_text(outcome.actual_value, "actual value", MAX_VALUE_LENGTH)
        _optional_text(outcome.unit, "unit", MAX_UNIT_LENGTH)
        if outcome.actual_value is None:
            raise LearningFailure("INVALID_INPUT", "outcome feedback needs an actual result")
        _validate_same_project_source(outcome.source, project_id)
    for retrospective in retrospectives:
        if not isinstance(retrospective, RetrospectiveRecord):
            raise LearningFailure("INVALID_INPUT", "retrospective is invalid")
        _uuid(retrospective.retrospective_id, "retrospective id")
        if retrospective.retrospective_id in ids:
            raise LearningFailure("INVALID_INPUT", "feedback ids must be unique")
        ids.add(retrospective.retrospective_id)
        _required_text(retrospective.finding, "finding", MAX_FINDING_LENGTH)
        if retrospective.classification not in CLASSIFICATIONS or not isinstance(retrospective.reusable_candidate, bool):
            raise LearningFailure("INVALID_INPUT", "retrospective classification is invalid")
        if retrospective.classification == "non_reusable_note" and retrospective.reusable_candidate:
            raise LearningFailure("INVALID_INPUT", "non-reusable note cannot create a candidate")
        evidence = _validate_feedback_sources(retrospective.evidence)
        if not evidence:
            raise LearningFailure("INVALID_INPUT", "retrospective feedback needs evidence")
        for source in evidence:
            _validate_same_project_source(source, project_id)


def _validated_task_view(inputs: CapsuleInputs) -> tuple[dict[str, Any], ...]:
    executions = {execution.task_id: execution for execution in inputs.executions}
    return tuple({"taskId": task.task_id, "title": task.title.strip(), "plannedStart": task.planned_start, "plannedFinish": task.planned_finish, "plannedWorkdays": task.duration_workdays, "executionUpdateId": executions[task.task_id].execution_update_id, "executionStatus": executions[task.task_id].status, "actualStart": executions[task.task_id].actual_start, "actualFinish": executions[task.task_id].actual_finish, "executionDigest": executions[task.task_id].execution_digest} for task in sorted(inputs.tasks, key=lambda item: item.task_id))


def _digest_material(inputs: CapsuleInputs, tasks: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    return {"mode": LEARNING_MODE, "projectId": inputs.project_id, "brief": {"artifactId": inputs.proposal_artifact_id, "versionId": inputs.proposal_version_id, "version": inputs.proposal_version, "sha256": inputs.proposal_sha256}, "plan": {"planId": inputs.plan_id, "versionId": inputs.plan_version_id, "version": inputs.plan_version, "sha256": inputs.plan_digest, "approval": {"id": inputs.approval_id, "planId": inputs.approval_plan_id, "planVersionId": inputs.approved_plan_version_id, "planSha256": inputs.approval_plan_digest, "scheduleSnapshotId": inputs.approval_schedule_snapshot_id, "scheduleSha256": inputs.approval_schedule_digest}, "schedule": {"id": inputs.schedule_snapshot_id, "planId": inputs.schedule_plan_id, "planVersionId": inputs.schedule_plan_version_id, "planSha256": inputs.schedule_plan_digest, "scheduleSha256": inputs.schedule_digest}}, "tasks": list(tasks), "outcomes": [{"outcomeId": item.outcome_id, "metric": item.metric, "plannedValue": item.planned_value, "actualValue": item.actual_value, "unit": item.unit, "source": _source_dict(item.source)} for item in sorted(inputs.outcomes, key=lambda item: item.outcome_id)], "retrospectives": [{"retrospectiveId": item.retrospective_id, "finding": item.finding, "classification": item.classification, "reusableCandidate": item.reusable_candidate, "evidence": [_source_dict(source) for source in _sorted_sources(item.evidence)]} for item in sorted(inputs.retrospectives, key=lambda item: item.retrospective_id)]}


def _generate_knowledge(inputs: CapsuleInputs, tasks: tuple[dict[str, Any], ...], capsule_id: str, capsule_digest: str) -> tuple[KnowledgeItem, ...]:
    drafts: list[tuple[str, str, str, tuple[KnowledgeEvidence, ...]]] = []
    capsule = KnowledgeEvidence("capsule", capsule_id, inputs.project_id, capsule_digest)
    plan = KnowledgeEvidence("plan_version", inputs.plan_version_id, inputs.project_id, inputs.plan_digest)
    approval = KnowledgeEvidence("plan_approval", inputs.approval_id, inputs.project_id, _approval_binding_digest(inputs))
    schedule = KnowledgeEvidence("schedule_snapshot", inputs.schedule_snapshot_id, inputs.project_id, inputs.schedule_digest)
    for task in tasks:
        if task["executionStatus"] == "completed" and task["actualStart"] and task["actualFinish"]:
            days = _calendar_days(task["actualStart"], task["actualFinish"])
            content = f"Task {task['taskId']} was observed from {task['actualStart']} to {task['actualFinish']} ({days} calendar days)."
            evidence = (KnowledgeEvidence("task", task["taskId"], inputs.project_id, _canonical_sha256(task)), KnowledgeEvidence("task_execution", task["executionUpdateId"], inputs.project_id, task["executionDigest"]), plan, approval, schedule, capsule)
            drafts.append(("observed_task_duration", "observed_duration", content, evidence))
    for outcome in inputs.outcomes:
        value = outcome.actual_value if outcome.actual_value is not None else outcome.planned_value
        content = f"Outcome observation {outcome.metric}: {value}."
        evidence = (_knowledge_evidence_from_source(outcome.source), KnowledgeEvidence("outcome", outcome.outcome_id, inputs.project_id, _canonical_sha256({"metric": outcome.metric, "plannedValue": outcome.planned_value, "actualValue": outcome.actual_value, "unit": outcome.unit, "source": _source_dict(outcome.source)})), capsule)
        drafts.append(("observed_outcome", "outcome_observation", content, evidence))
    for retrospective in inputs.retrospectives:
        if retrospective.reusable_candidate:
            evidence = tuple(_knowledge_evidence_from_source(source) for source in _sorted_sources(retrospective.evidence)) + (KnowledgeEvidence("retrospective", retrospective.retrospective_id, inputs.project_id, _canonical_sha256({"finding": retrospective.finding, "classification": retrospective.classification, "reusableCandidate": retrospective.reusable_candidate, "evidence": [_source_dict(source) for source in _sorted_sources(retrospective.evidence)]})), capsule)
            drafts.append(("retrospective_finding", retrospective.classification, retrospective.finding, evidence))
    drafts.sort(key=lambda item: (item[0], item[2], _evidence_digest(item[3])))
    if len(drafts) > MAX_KNOWLEDGE_ITEMS:
        raise LearningFailure("INVALID_INPUT", "candidate knowledge is too large")
    return tuple(KnowledgeItem(str(uuid5(NAMESPACE_URL, f"marketops:knowledge:{capsule_id}:{ordinal}:{_sha256_text(content)}:{_evidence_digest(evidence)}")), ordinal, kind, KNOWLEDGE_STATUS, classification, content, 1.0, evidence, _sha256_text(content), KNOWLEDGE_SCOPE, inputs.project_id) for ordinal, (kind, classification, content, evidence) in enumerate(drafts, 1))


def _knowledge_evidence_from_source(source: FeedbackSourceReference) -> KnowledgeEvidence:
    return KnowledgeEvidence(source.source_type, source.source_id, source.project_id, source.binding_sha256)


def _validate_feedback_source(value: Any) -> FeedbackSourceReference:
    if not isinstance(value, FeedbackSourceReference) or value.source_type not in FEEDBACK_SOURCE_TYPES:
        raise LearningFailure("INVALID_INPUT", "feedback source is invalid")
    _uuid(value.source_id, "feedback source id")
    _uuid(value.project_id, "feedback source project id")
    _sha256(value.binding_sha256, "feedback source hash")
    return value


def _validate_feedback_sources(value: Any) -> tuple[FeedbackSourceReference, ...]:
    if not isinstance(value, tuple) or len(value) > 100:
        raise LearningFailure("INVALID_INPUT", "feedback sources are invalid")
    return tuple(_validate_feedback_source(item) for item in value)


def _sorted_sources(value: tuple[FeedbackSourceReference, ...]) -> tuple[FeedbackSourceReference, ...]:
    return tuple(sorted(value, key=lambda item: (item.source_type, item.source_id, item.binding_sha256)))


def _validate_same_project_source(source: FeedbackSourceReference, project_id: str) -> None:
    _validate_feedback_source(source)
    if source.project_id != project_id:
        raise LearningFailure("INVALID_INPUT", "feedback source is not in the same project")


def _validate_scope(scope: LearningScopeContext) -> None:
    if not isinstance(scope, LearningScopeContext):
        raise LearningFailure("INVALID_SCOPE", "learning scope is invalid")
    for identity in (scope.organization_id, scope.workspace_id, scope.client_id, scope.actor_id):
        _uuid(identity, "scope identity", "INVALID_SCOPE")


def _uuid(value: Any, label: str, code: str = "INVALID_INPUT") -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value.lower():
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise LearningFailure(code, f"{label} is invalid") from None
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise LearningFailure("INVALID_INPUT", f"{label} is invalid")
    return value


def _required_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise LearningFailure("INVALID_INPUT", f"{label} is invalid")
    return value.strip()


def _optional_text(value: Any, label: str, maximum: int) -> str | None:
    return None if value is None else _required_text(value, label, maximum)


def _optional_date(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise LearningFailure("INVALID_INPUT", f"{label} must use YYYY-MM-DD")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise LearningFailure("INVALID_INPUT", f"{label} must use YYYY-MM-DD") from None


def _calendar_days(start: str, finish: str) -> int:
    return (date.fromisoformat(finish) - date.fromisoformat(start)).days + 1


def _source_dict(source: FeedbackSourceReference) -> dict[str, str]:
    return {"sourceType": source.source_type, "sourceId": source.source_id, "projectId": source.project_id, "bindingSha256": source.binding_sha256}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _evidence_digest(evidence: tuple[KnowledgeEvidence, ...]) -> str:
    return _canonical_sha256([{"sourceType": item.source_type, "sourceId": item.source_id, "projectId": item.project_id, "bindingSha256": item.binding_sha256} for item in evidence])


def _approval_binding_digest(inputs: CapsuleInputs) -> str:
    return _canonical_sha256({"approvalId": inputs.approval_id, "planId": inputs.approval_plan_id, "planVersionId": inputs.approved_plan_version_id, "planSha256": inputs.approval_plan_digest, "scheduleSnapshotId": inputs.approval_schedule_snapshot_id, "scheduleSha256": inputs.approval_schedule_digest})


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
