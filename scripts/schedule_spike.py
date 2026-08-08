#!/usr/bin/env python3
"""Deterministic workday scheduler for the M0-03 technical spike.

The engine treats source dates, confirmed constraints, and calculated dates as
separate records. It never moves a locked task to conceal a dependency conflict.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path


REQUIRED_COLUMNS = {
    "task_id", "deliverable_id", "task_name", "duration_workdays",
    "predecessors", "owner_role", "planned_start", "planned_finish",
    "hard_deadline", "is_locked", "status",
}


class ScheduleFailure(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class Task:
    task_id: str
    deliverable_id: str
    task_name: str
    duration: int
    predecessors: tuple[str, ...]
    owner_role: str
    planned_start: date
    planned_finish: date
    hard_deadline: date | None
    is_locked: bool
    status: str


def parse_date(value: str, field: str, task_id: str | None = None) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ScheduleFailure(
            "invalid_date", f"{field} must be an ISO date.",
            field=field, taskId=task_id, value=value,
        ) from error


def is_workday(value: date, holidays: set[date]) -> bool:
    return value.weekday() < 5 and value not in holidays


def next_workday(value: date, holidays: set[date]) -> date:
    candidate = value
    while not is_workday(candidate, holidays):
        candidate += timedelta(days=1)
    return candidate


def previous_workday(value: date, holidays: set[date]) -> date:
    candidate = value
    while not is_workday(candidate, holidays):
        candidate -= timedelta(days=1)
    return candidate


def shift_workdays(value: date, count: int, holidays: set[date]) -> date:
    if not is_workday(value, holidays):
        raise ScheduleFailure("non_workday_anchor", "Workday shift needs a workday anchor.", value=value.isoformat())
    candidate = value
    direction = 1 if count >= 0 else -1
    remaining = abs(count)
    while remaining:
        candidate += timedelta(days=direction)
        if is_workday(candidate, holidays):
            remaining -= 1
    return candidate


def finish_from_start(start: date, duration: int, holidays: set[date]) -> date:
    return shift_workdays(start, duration - 1, holidays)


def start_after(finish: date, buffer_workdays: int, holidays: set[date]) -> date:
    # One step enforces finish-to-start; additional steps are explicit idle days.
    return shift_workdays(finish, buffer_workdays + 1, holidays)


def predecessor_finish_before(start: date, buffer_workdays: int, holidays: set[date]) -> date:
    return shift_workdays(start, -(buffer_workdays + 1), holidays)


def load_tasks(path: Path) -> tuple[list[Task], str]:
    payload = path.read_bytes()
    source_hash = hashlib.sha256(payload).hexdigest()
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ScheduleFailure("invalid_encoding", "Schedule CSV must be UTF-8.") from error
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
        missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
        raise ScheduleFailure("missing_columns", "Schedule CSV lacks required columns.", missing=missing)

    tasks: list[Task] = []
    seen: set[str] = set()
    for row_number, row in enumerate(reader, start=2):
        task_id = row["task_id"].strip()
        if not task_id:
            raise ScheduleFailure("missing_task_id", "Task ID cannot be empty.", row=row_number)
        if task_id in seen:
            raise ScheduleFailure("duplicate_task_id", "Task IDs must be unique.", taskId=task_id)
        seen.add(task_id)
        try:
            duration = int(row["duration_workdays"])
        except ValueError as error:
            raise ScheduleFailure("invalid_duration", "Duration must be a positive integer.", taskId=task_id) from error
        if duration < 1:
            raise ScheduleFailure("invalid_duration", "Duration must be a positive integer.", taskId=task_id)
        locked_value = row["is_locked"].strip().lower()
        if locked_value not in {"true", "false"}:
            raise ScheduleFailure("invalid_boolean", "is_locked must be true or false.", taskId=task_id)
        planned_start = parse_date(row["planned_start"], "planned_start", task_id)
        planned_finish = parse_date(row["planned_finish"], "planned_finish", task_id)
        deadline = parse_date(row["hard_deadline"], "hard_deadline", task_id) if row["hard_deadline"].strip() else None
        tasks.append(Task(
            task_id=task_id, deliverable_id=row["deliverable_id"], task_name=row["task_name"],
            duration=duration, predecessors=tuple(item.strip() for item in row["predecessors"].split("|") if item.strip()),
            owner_role=row["owner_role"], planned_start=planned_start, planned_finish=planned_finish,
            hard_deadline=deadline, is_locked=locked_value == "true", status=row["status"],
        ))
    if not tasks:
        raise ScheduleFailure("empty_schedule", "Schedule must contain at least one task.")
    return tasks, source_hash


def topological_order(tasks: list[Task]) -> list[str]:
    by_id = {task.task_id: task for task in tasks}
    for task in tasks:
        missing = sorted(set(task.predecessors) - by_id.keys())
        if missing:
            raise ScheduleFailure("missing_predecessor", "A predecessor does not exist.", taskId=task.task_id, missing=missing)
    indegree = {task.task_id: len(task.predecessors) for task in tasks}
    successors = {task.task_id: [] for task in tasks}
    for task in tasks:
        for predecessor in task.predecessors:
            successors[predecessor].append(task.task_id)
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
        unresolved = sorted(task_id for task_id, count in indegree.items() if count > 0)
        raise ScheduleFailure("dependency_cycle", "Schedule dependencies contain a cycle.", taskIds=unresolved)
    return order


def apply_overrides(tasks: list[Task], overrides: dict) -> list[Task]:
    by_id = {task.task_id: task for task in tasks}
    unknown = sorted(set(overrides) - by_id.keys())
    if unknown:
        raise ScheduleFailure("unknown_override_task", "Scenario overrides an unknown task.", taskIds=unknown)
    result = []
    for task in tasks:
        values = overrides.get(task.task_id, {})
        allowed = {"durationWorkdays"}
        unsupported = sorted(set(values) - allowed)
        if unsupported:
            raise ScheduleFailure("unsupported_override", "Scenario contains an unsupported override.", taskId=task.task_id, fields=unsupported)
        duration = values.get("durationWorkdays", task.duration)
        if not isinstance(duration, int) or duration < 1:
            raise ScheduleFailure("invalid_duration", "Override duration must be a positive integer.", taskId=task.task_id)
        result.append(replace(task, duration=duration))
    return result


def schedule(tasks: list[Task], source_hash: str, scenario: dict) -> dict:
    scenario_id = scenario.get("id")
    if not scenario_id:
        raise ScheduleFailure("missing_scenario_id", "Scenario ID is required.")
    holidays = {parse_date(item, "holiday") for item in scenario.get("holidays", [])}
    project_start = parse_date(scenario["projectStart"], "projectStart")
    project_start = next_workday(project_start, holidays)
    tasks = apply_overrides(tasks, scenario.get("taskOverrides", {}))
    by_id = {task.task_id: task for task in tasks}
    order = topological_order(tasks)
    buffers = scenario.get("approvedBuffersWorkdays", {})
    unknown_buffers = sorted(set(buffers) - by_id.keys())
    if unknown_buffers:
        raise ScheduleFailure("unknown_buffer_task", "Buffer targets an unknown task.", taskIds=unknown_buffers)
    for task_id, value in buffers.items():
        if not isinstance(value, int) or value < 0:
            raise ScheduleFailure("invalid_buffer", "Approved buffers must be non-negative integers.", taskId=task_id)

    calculated: dict[str, dict] = {}
    conflicts: list[dict] = []
    deadline_misses: list[dict] = []
    source_drift: list[dict] = []
    for task_id in order:
        task = by_id[task_id]
        buffer_days = buffers.get(task_id, 0)
        if task.predecessors:
            driving_finish = max(calculated[item]["finishDateValue"] for item in task.predecessors)
            required_start = start_after(driving_finish, buffer_days, holidays)
        else:
            required_start = project_start

        if task.is_locked:
            start = task.planned_start
            finish = task.planned_finish
            if not is_workday(start, holidays) or not is_workday(finish, holidays):
                raise ScheduleFailure("locked_date_non_workday", "Locked dates must be workdays in this scenario.", taskId=task_id)
            expected_finish = finish_from_start(start, task.duration, holidays)
            if expected_finish != finish:
                raise ScheduleFailure("locked_duration_mismatch", "Locked dates do not match duration.", taskId=task_id, expectedFinish=expected_finish.isoformat())
            if required_start > start:
                conflicts.append({
                    "code": "locked_dependency_conflict", "taskId": task_id,
                    "lockedStart": start.isoformat(), "requiredStart": required_start.isoformat(),
                    "predecessors": list(task.predecessors),
                })
        else:
            start = required_start
            finish = finish_from_start(start, task.duration, holidays)

        if task.hard_deadline and finish > task.hard_deadline:
            deadline_misses.append({
                "code": "hard_deadline_miss", "taskId": task_id,
                "calculatedFinish": finish.isoformat(), "hardDeadline": task.hard_deadline.isoformat(),
            })
        if start != task.planned_start or finish != task.planned_finish:
            source_drift.append({
                "taskId": task_id, "sourceStart": task.planned_start.isoformat(),
                "sourceFinish": task.planned_finish.isoformat(), "calculatedStart": start.isoformat(),
                "calculatedFinish": finish.isoformat(),
            })
        calculated[task_id] = {
            "startDateValue": start, "finishDateValue": finish, "requiredStartValue": required_start,
            "bufferWorkdays": buffer_days,
        }

    successors = {task_id: [] for task_id in by_id}
    for task in tasks:
        for predecessor in task.predecessors:
            successors[predecessor].append(task.task_id)
    project_finish = max(item["finishDateValue"] for item in calculated.values())
    latest: dict[str, tuple[date, date]] = {}
    for task_id in reversed(order):
        task = by_id[task_id]
        if successors[task_id]:
            latest_finish = min(
                predecessor_finish_before(latest[successor][0], buffers.get(successor, 0), holidays)
                for successor in successors[task_id]
            )
        else:
            latest_finish = project_finish
        if task.hard_deadline:
            deadline_finish = previous_workday(task.hard_deadline, holidays)
            if deadline_finish < latest_finish:
                latest_finish = deadline_finish
        latest_start = shift_workdays(latest_finish, -(task.duration - 1), holidays)
        latest[task_id] = (latest_start, latest_finish)

    def workday_distance(start: date, end: date) -> int:
        if end < start:
            return -workday_distance(end, start)
        value, count = start, 0
        while value < end:
            value += timedelta(days=1)
            if is_workday(value, holidays):
                count += 1
        return count

    task_results = []
    for task_id in order:
        task = by_id[task_id]
        item = calculated[task_id]
        latest_start, latest_finish = latest[task_id]
        total_float = workday_distance(item["startDateValue"], latest_start)
        task_results.append({
            "taskId": task_id, "predecessors": list(task.predecessors),
            "durationWorkdays": task.duration, "approvedBufferWorkdays": item["bufferWorkdays"],
            "isLocked": task.is_locked, "sourcePlannedStart": task.planned_start.isoformat(),
            "sourcePlannedFinish": task.planned_finish.isoformat(),
            "calculatedStart": item["startDateValue"].isoformat(),
            "calculatedFinish": item["finishDateValue"].isoformat(),
            "latestStart": latest_start.isoformat(), "latestFinish": latest_finish.isoformat(),
            "totalFloatWorkdays": total_float, "isCritical": total_float <= 0,
            "hardDeadline": task.hard_deadline.isoformat() if task.hard_deadline else None,
        })

    critical_ids = {item["taskId"] for item in task_results if item["isCritical"]}
    critical_edges = []
    for successor_id in order:
        successor = by_id[successor_id]
        for predecessor_id in successor.predecessors:
            if predecessor_id not in critical_ids or successor_id not in critical_ids:
                continue
            expected_start = start_after(
                calculated[predecessor_id]["finishDateValue"], buffers.get(successor_id, 0), holidays,
            )
            if expected_start == calculated[successor_id]["startDateValue"]:
                critical_edges.append({"from": predecessor_id, "to": successor_id})

    critical_successors = {task_id: [] for task_id in critical_ids}
    incoming = {task_id: 0 for task_id in critical_ids}
    for edge in critical_edges:
        critical_successors[edge["from"]].append(edge["to"])
        incoming[edge["to"]] += 1
    critical_chains: list[list[str]] = []

    def extend_chain(chain: list[str]) -> None:
        next_items = sorted(critical_successors[chain[-1]])
        if not next_items:
            critical_chains.append(chain)
            return
        for next_id in next_items:
            extend_chain([*chain, next_id])

    for root_id in sorted(task_id for task_id, count in incoming.items() if count == 0):
        extend_chain([root_id])

    return {
        "scenarioId": scenario_id,
        "source": {"path": scenario["sourcePath"], "sha256": source_hash},
        "calendar": {"workweek": "monday-friday", "holidays": sorted(item.isoformat() for item in holidays)},
        "projectStart": project_start.isoformat(), "projectFinish": project_finish.isoformat(),
        "topologicalOrder": order, "tasks": task_results,
        "criticalTaskIds": [item["taskId"] for item in task_results if item["isCritical"]],
        "criticalEdges": critical_edges, "criticalChains": critical_chains,
        "conflicts": conflicts, "deadlineMisses": deadline_misses, "sourceDateDrift": source_drift,
    }


def load_scenarios(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScheduleFailure("invalid_scenario_file", "Scenario JSON could not be read.") from error


def run_scenario(root: Path, config: dict, scenario_id: str) -> dict:
    scenarios = {item["id"]: item for item in config.get("scenarios", [])}
    if scenario_id not in scenarios:
        raise ScheduleFailure("unknown_scenario", "Requested scenario does not exist.", scenarioId=scenario_id)
    scenario = {**config.get("defaults", {}), **scenarios[scenario_id]}
    source_path = root / scenario["sourcePath"]
    tasks, source_hash = load_tasks(source_path)
    return schedule(tasks, source_hash, scenario)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        result = run_scenario(root, load_scenarios(args.config), args.scenario)
    except ScheduleFailure as error:
        print(json.dumps({"error": {"code": error.code, "message": error.message, "details": error.details}}, ensure_ascii=False))
        raise SystemExit(2) from error
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
