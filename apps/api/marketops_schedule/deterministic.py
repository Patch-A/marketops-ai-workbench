"""M1-03 contract slice: approved candidates to an editable deterministic plan.

This module has no HTTP, database, model, or browser dependency. It accepts
server-owned review facts and returns new plan/schedule values without mutating
the input objects.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, timedelta
from typing import Any, Mapping, Sequence


ACCEPTED_REVIEW_STATUSES = frozenset({"approve", "modify"})
REVIEW_STATUSES = frozenset({"pending", "approve", "modify", "reject"})
TASK_KINDS = frozenset({"deliverable", "milestone"})
CONTROL_KINDS = frozenset({"constraint", "assumption"})
EDITABLE_TASK_FIELDS = frozenset({
    "title", "durationWorkdays", "predecessors", "ownerRole", "plannedStart",
    "plannedFinish", "hardDeadline", "approvedBufferWorkdays", "isLocked", "status",
})


class ScheduleFailure(Exception):
    """A fail-closed validation error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _fail(code: str, message: str, **details: Any) -> None:
    raise ScheduleFailure(code, message, **details)


def _parse_date(value: str | None, field: str, task_id: str | None = None) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ScheduleFailure("invalid_date", f"{field} must be an ISO date.", field=field, taskId=task_id, value=value) from error


def _is_workday(value: date, holidays: set[date]) -> bool:
    return value.weekday() < 5 and value not in holidays


def _next_workday(value: date, holidays: set[date]) -> date:
    result = value
    while not _is_workday(result, holidays):
        result += timedelta(days=1)
    return result


def _shift_workdays(value: date, count: int, holidays: set[date]) -> date:
    if not _is_workday(value, holidays):
        _fail("non_workday_anchor", "Workday arithmetic needs a workday anchor.", value=value.isoformat())
    result = value
    direction = 1 if count >= 0 else -1
    remaining = abs(count)
    while remaining:
        result += timedelta(days=direction)
        if _is_workday(result, holidays):
            remaining -= 1
    return result


def _finish_from_start(start: date, duration: int, holidays: set[date]) -> date:
    return _shift_workdays(start, duration - 1, holidays)


def _start_after(finish: date, buffer_workdays: int, holidays: set[date]) -> date:
    return _shift_workdays(finish, buffer_workdays + 1, holidays)


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _citation(candidate: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    value = candidate.get("sourceCitation")
    if not isinstance(value, Mapping) or not value.get("sourceVersionId") or not value.get("sourceSha256") or not value.get("quote"):
        _fail("missing_source_citation", "Approved candidates must retain a source citation.", candidateId=candidate_id)
    return deepcopy(dict(value))


def _candidate_text(candidate: Mapping[str, Any], status: str, candidate_id: str) -> str:
    if status == "modify":
        value = candidate.get("review", {}).get("replacementText")
        if not isinstance(value, str) or not value.strip():
            _fail("missing_replacement_text", "Modified candidates need replacement text.", candidateId=candidate_id)
    else:
        value = candidate.get("text")
    if not isinstance(value, str) or not value.strip():
        _fail("empty_candidate_text", "Approved candidates need non-empty text.", candidateId=candidate_id)
    return value.strip()


def build_wbs_draft(project_id: str, proposal: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a new WBS draft from only human-approved review candidates."""
    if not project_id or not isinstance(proposal, Mapping) or not proposal.get("versionId") or not proposal.get("sha256"):
        _fail("invalid_proposal_identity", "A WBS draft needs the server-approved proposal identity.")

    tasks: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate.get("candidateId")
        if not isinstance(candidate_id, str) or not candidate_id:
            _fail("invalid_candidate_id", "Candidate IDs are required.")
        if candidate_id in seen:
            _fail("duplicate_candidate_id", "Candidate IDs must be unique.", candidateId=candidate_id)
        seen.add(candidate_id)
        review = candidate.get("review")
        status = review.get("status") if isinstance(review, Mapping) else "pending"
        if status not in REVIEW_STATUSES:
            _fail("unsupported_review_status", "Candidate review status is not supported.", candidateId=candidate_id, status=status)
        if status not in ACCEPTED_REVIEW_STATUSES:
            continue
        kind = candidate.get("kind")
        if kind not in TASK_KINDS | CONTROL_KINDS:
            _fail("unsupported_candidate_kind", "Candidate kind cannot enter a WBS draft.", candidateId=candidate_id, kind=kind)
        text = _candidate_text(candidate, status, candidate_id)
        item = {
            "candidateId": candidate_id,
            "kind": kind,
            "classification": candidate.get("classification"),
            "sourceText": candidate.get("text"),
            "text": text,
            "sourceCitation": _citation(candidate, candidate_id),
            "reviewStatus": status,
            "reviewVersion": review.get("lastDecision", {}).get("reviewVersion") if isinstance(review.get("lastDecision"), Mapping) else None,
        }
        if kind in CONTROL_KINDS:
            controls.append(item)
            continue
        tasks.append({
            **item,
            "taskId": f"candidate:{candidate_id}",
            "title": text,
            "durationWorkdays": 1,
            "predecessors": [],
            "ownerRole": "",
            "plannedStart": None,
            "plannedFinish": None,
            "hardDeadline": None,
            "approvedBufferWorkdays": 0,
            "isLocked": False,
            "status": "not_started",
        })

    if not tasks:
        _fail("no_schedulable_candidates", "A WBS draft needs an approved deliverable or milestone.")
    review_versions = [item["reviewVersion"] for item in [*tasks, *controls] if isinstance(item.get("reviewVersion"), int)]
    return {
        "schemaVersion": 1,
        "planVersion": 1,
        "status": "draft",
        "projectId": project_id,
        "proposal": {"versionId": proposal["versionId"], "sha256": proposal["sha256"]},
        "sourceReviewVersion": max(review_versions) if review_versions else None,
        "tasks": tasks,
        "controls": controls,
    }


def revise_wbs(plan: Mapping[str, Any], expected_plan_version: int, task_updates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return a new editable plan version while preserving review-owned fields."""
    current_version = plan.get("planVersion") if isinstance(plan, Mapping) else None
    if not isinstance(current_version, int) or isinstance(current_version, bool) or current_version < 1:
        _fail("invalid_plan_version", "The plan needs a positive planVersion.")
    if expected_plan_version != current_version:
        _fail("plan_version_conflict", "The plan version changed before this edit.", expectedPlanVersion=expected_plan_version, currentPlanVersion=current_version)
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        _fail("invalid_plan", "The plan tasks must be a list.")
    by_id = {task.get("taskId"): task for task in tasks if isinstance(task, Mapping)}
    result = deepcopy(dict(plan))
    result_by_id = {task["taskId"]: task for task in result["tasks"]}
    changed_ids: list[str] = []
    seen: set[str] = set()
    for update in task_updates:
        if not isinstance(update, Mapping):
            _fail("invalid_task_update", "Task updates must be objects.")
        task_id = update.get("taskId")
        changes = update.get("changes")
        if not isinstance(task_id, str) or task_id not in by_id:
            _fail("unknown_task", "A task update targets an unknown task.", taskId=task_id)
        if task_id in seen:
            _fail("duplicate_task_update", "A task may be updated once per plan revision.", taskId=task_id)
        seen.add(task_id)
        if not isinstance(changes, Mapping) or not changes:
            _fail("invalid_task_update", "Task changes must be a non-empty object.", taskId=task_id)
        unsupported = sorted(set(changes) - EDITABLE_TASK_FIELDS)
        if unsupported:
            _fail("immutable_task_field", "Review-owned task fields cannot be edited.", taskId=task_id, fields=unsupported)
        for field, value in changes.items():
            result_by_id[task_id][field] = deepcopy(value)
        changed_ids.append(task_id)
    if not changed_ids:
        _fail("empty_revision", "A plan revision needs at least one task update.")
    result["planVersion"] = current_version + 1
    result["status"] = "draft"
    result["lastRevision"] = {"fromVersion": current_version, "toVersion": current_version + 1, "taskIds": sorted(changed_ids)}
    return result


def _topological_order(tasks: Sequence[Mapping[str, Any]]) -> list[str]:
    task_ids = [task.get("taskId") if isinstance(task, Mapping) else None for task in tasks]
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        _fail("invalid_task_id", "Task IDs are required strings.")
    if len(set(task_ids)) != len(tasks):
        _fail("duplicate_task_id", "Task IDs must be unique.")
    by_id = {task["taskId"]: task for task in tasks}
    for task in tasks:
        predecessors = task.get("predecessors", [])
        if not isinstance(predecessors, list) or any(not isinstance(item, str) or not item for item in predecessors) or len(predecessors) != len(set(predecessors)):
            _fail("invalid_predecessors", "Predecessors must be a duplicate-free list.", taskId=task["taskId"])
        missing = sorted(set(predecessors) - by_id.keys())
        if missing:
            _fail("missing_predecessor", "A predecessor does not exist.", taskId=task["taskId"], missing=missing)
    indegree = {task_id: len(task.get("predecessors", [])) for task_id, task in by_id.items()}
    successors = {task_id: [] for task_id in by_id}
    for task in tasks:
        for predecessor in task.get("predecessors", []):
            successors[predecessor].append(task["taskId"])
    ready = sorted(task_id for task_id, count in indegree.items() if count == 0)
    order: list[str] = []
    while ready:
        task_id = ready.pop(0)
        order.append(task_id)
        for successor in sorted(successors[task_id]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort()
    if len(order) != len(tasks):
        _fail("dependency_cycle", "Schedule dependencies contain a cycle.", taskIds=sorted(set(by_id) - set(order)))
    return order


def calculate_schedule(plan: Mapping[str, Any], project_start: str, holidays: Sequence[str] = ()) -> dict[str, Any]:
    """Calculate a new schedule snapshot without mutating the editable plan."""
    plan_version = plan.get("planVersion") if isinstance(plan, Mapping) else None
    proposal = plan.get("proposal") if isinstance(plan, Mapping) else None
    if not isinstance(plan_version, int) or isinstance(plan_version, bool) or plan_version < 1:
        _fail("invalid_plan_version", "The plan needs a positive planVersion.")
    if not plan.get("projectId") or not isinstance(proposal, Mapping) or not proposal.get("versionId") or not proposal.get("sha256"):
        _fail("invalid_plan_identity", "The plan must retain project and approved proposal identity.")
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else None
    if not isinstance(tasks, list) or not tasks:
        _fail("empty_plan", "A schedule needs at least one WBS task.")
    start_value = _parse_date(project_start, "projectStart")
    assert start_value is not None
    holiday_values = {_parse_date(item, "holiday") for item in holidays}
    holiday_set = {item for item in holiday_values if item is not None}
    order = _topological_order(tasks)
    by_id = {task["taskId"]: task for task in tasks}
    calculated: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    deadline_misses: list[dict[str, Any]] = []
    source_drift: list[dict[str, Any]] = []

    for task_id in order:
        task = by_id[task_id]
        if not isinstance(task.get("title"), str) or not task["title"].strip():
            _fail("invalid_task_title", "WBS tasks need a non-empty title.", taskId=task_id)
        _citation(task, task_id)
        duration = task.get("durationWorkdays")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 1:
            _fail("invalid_duration", "Duration must be a positive integer.", taskId=task_id)
        buffer_days = task.get("approvedBufferWorkdays", 0)
        if not isinstance(buffer_days, int) or isinstance(buffer_days, bool) or buffer_days < 0:
            _fail("invalid_buffer", "Approved buffers must be non-negative integers.", taskId=task_id)
        predecessors = task.get("predecessors", [])
        required_start = _next_workday(start_value, holiday_set) if not predecessors else _start_after(max(calculated[item]["finishDate"] for item in predecessors), buffer_days, holiday_set)
        planned_start = _parse_date(task.get("plannedStart"), "plannedStart", task_id)
        planned_finish = _parse_date(task.get("plannedFinish"), "plannedFinish", task_id)
        if planned_finish is not None and planned_start is None:
            _fail("invalid_planned_dates", "plannedFinish requires plannedStart.", taskId=task_id)
        if task.get("isLocked"):
            if planned_start is None:
                _fail("locked_start_required", "Locked tasks need a plannedStart.", taskId=task_id)
            if not _is_workday(planned_start, holiday_set):
                _fail("locked_date_non_workday", "Locked task dates must be workdays.", taskId=task_id)
            start = planned_start
            finish = _finish_from_start(start, duration, holiday_set)
            if planned_finish is not None and planned_finish != finish:
                _fail("locked_duration_mismatch", "Locked dates do not match duration.", taskId=task_id, expectedFinish=finish.isoformat())
            if required_start > start:
                conflicts.append({"code": "locked_dependency_conflict", "taskId": task_id, "lockedStart": start.isoformat(), "requiredStart": required_start.isoformat(), "predecessors": list(predecessors)})
        else:
            start = required_start
            finish = _finish_from_start(start, duration, holiday_set)
        hard_deadline = _parse_date(task.get("hardDeadline"), "hardDeadline", task_id)
        if hard_deadline is not None and finish > hard_deadline:
            deadline_misses.append({"code": "hard_deadline_miss", "taskId": task_id, "calculatedFinish": finish.isoformat(), "hardDeadline": hard_deadline.isoformat()})
        if planned_start is not None and (planned_start != start or planned_finish != finish):
            source_drift.append({"taskId": task_id, "sourceStart": planned_start.isoformat(), "sourceFinish": planned_finish.isoformat() if planned_finish else None, "calculatedStart": start.isoformat(), "calculatedFinish": finish.isoformat()})
        calculated[task_id] = {"startDate": start, "finishDate": finish, "requiredStart": required_start}

    result_tasks = []
    for task_id in order:
        task = by_id[task_id]
        item = calculated[task_id]
        result_tasks.append({
            "taskId": task_id,
            "candidateId": task.get("candidateId"),
            "title": task.get("title"),
            "sourceCitation": deepcopy(task.get("sourceCitation")),
            "predecessors": list(task.get("predecessors", [])),
            "durationWorkdays": task["durationWorkdays"],
            "approvedBufferWorkdays": task.get("approvedBufferWorkdays", 0),
            "isLocked": bool(task.get("isLocked")),
            "calculatedStart": item["startDate"].isoformat(),
            "calculatedFinish": item["finishDate"].isoformat(),
            "requiredStart": item["requiredStart"].isoformat(),
            "hardDeadline": task.get("hardDeadline"),
        })
    output = {
        "schemaVersion": 1,
        "planVersion": plan_version,
        "sourceReviewVersion": plan.get("sourceReviewVersion"),
        "planDigest": _canonical_digest(plan),
        "projectStart": _next_workday(start_value, holiday_set).isoformat(),
        "calendar": {"workweek": "monday-friday", "holidays": sorted(item.isoformat() for item in holiday_set)},
        "topologicalOrder": order,
        "tasks": result_tasks,
        "conflicts": conflicts,
        "deadlineMisses": deadline_misses,
        "sourceDateDrift": source_drift,
        "status": "needs_review" if conflicts or deadline_misses else "ready",
    }
    output["scheduleDigest"] = _canonical_digest(output)
    return output
