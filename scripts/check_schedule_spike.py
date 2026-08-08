#!/usr/bin/env python3
"""Acceptance and freshness check for the M0-03 scheduling spike."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
from pathlib import Path

from schedule_spike import ScheduleFailure, load_scenarios, load_tasks, run_scenario, schedule, topological_order


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "validation" / "fixtures" / "synthetic-b2b-event-001" / "schedule-scenarios.json"
RESULT_PATH = ROOT / "validation" / "results" / "m0-03-schedule.json"
SOURCE_PATH = ROOT / "validation" / "fixtures" / "synthetic-b2b-event-001" / "schedule-baseline.csv"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def task(result, task_id):
    return next(item for item in result["tasks"] if item["taskId"] == task_id)


def error_code(action, expected):
    try:
        action()
    except ScheduleFailure as error:
        require(error.code == expected, f"expected {expected}, got {error.code}")
        return error.code
    raise AssertionError(f"expected {expected}")


def malformed_tasks(mutator):
    with SOURCE_PATH.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        fieldnames = reader.fieldnames
    mutator(rows)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "schedule.csv"
        with path.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        return load_tasks(path)[0]


def scenario_summary(result):
    return {
        "scenarioId": result["scenarioId"],
        "projectFinish": result["projectFinish"],
        "criticalTaskIds": result["criticalTaskIds"],
        "criticalChains": result["criticalChains"],
        "conflicts": result["conflicts"],
        "deadlineMisses": result["deadlineMisses"],
        "sourceDateDriftTaskIds": [item["taskId"] for item in result["sourceDateDrift"]],
    }


def build_result():
    config = load_scenarios(CONFIG_PATH)
    baseline = run_scenario(ROOT, config, "baseline")
    extension = run_scenario(ROOT, config, "invitation-extension")
    locked_conflict = run_scenario(ROOT, config, "locked-date-conflict")
    holiday = run_scenario(ROOT, config, "weekday-holiday")

    require(len(baseline["tasks"]) == 15, "baseline task count changed")
    require(not baseline["conflicts"] and not baseline["deadlineMisses"], "baseline must be conflict-free")
    require(not baseline["sourceDateDrift"], "explicit baseline inputs must reproduce source dates")
    require(baseline["projectFinish"] == "2026-10-30", "baseline project finish changed")
    require(task(baseline, "TASK-040")["approvedBufferWorkdays"] == 2, "TASK-040 buffer changed")
    require(task(baseline, "TASK-100")["approvedBufferWorkdays"] == 7, "TASK-100 buffer changed")
    require(task(baseline, "TASK-120")["approvedBufferWorkdays"] == 3, "TASK-120 buffer changed")
    require(["TASK-010", "TASK-020", "TASK-030", "TASK-070", "TASK-110", "TASK-130", "TASK-140", "TASK-150"] in baseline["criticalChains"], "main critical chain changed")

    require(task(extension, "TASK-070")["calculatedFinish"] == "2026-10-23", "TASK-070 extension did not recalculate")
    require(task(extension, "TASK-110")["calculatedStart"] == "2026-10-26", "TASK-110 did not move")
    require(extension["projectFinish"] == "2026-11-06", "extension finish changed")
    require([item["taskId"] for item in extension["deadlineMisses"]] == ["TASK-110", "TASK-150"], "extension deadline misses changed")

    require(task(locked_conflict, "TASK-090")["calculatedStart"] == "2026-10-12", "locked TASK-090 moved")
    require(len(locked_conflict["conflicts"]) == 1, "locked conflict was not reported exactly once")
    require(locked_conflict["conflicts"][0]["code"] == "locked_dependency_conflict", "locked conflict code changed")
    require(locked_conflict["conflicts"][0]["requiredStart"] == "2026-10-15", "locked conflict required date changed")

    require(holiday["calendar"]["holidays"] == ["2026-09-08"], "holiday input changed")
    require(task(holiday, "TASK-030")["calculatedFinish"] == "2026-09-14", "holiday did not shift TASK-030")
    require(holiday["projectFinish"] == "2026-11-02", "holiday project finish changed")

    missing_tasks = malformed_tasks(lambda rows: rows[0].update({"predecessors": "TASK-404"}))
    cycle_tasks = malformed_tasks(lambda rows: rows[0].update({"predecessors": "TASK-150"}))
    failure_codes = [
        error_code(lambda: topological_order(missing_tasks), "missing_predecessor"),
        error_code(lambda: topological_order(cycle_tasks), "dependency_cycle"),
    ]
    base_tasks, source_hash = load_tasks(SOURCE_PATH)
    locked_override = {
        **config["defaults"], "id": "locked-duration-mismatch",
        "taskOverrides": {"TASK-090": {"durationWorkdays": 6}},
    }
    failure_codes.append(error_code(lambda: schedule(base_tasks, source_hash, locked_override), "locked_duration_mismatch"))

    source_sha = baseline["source"]["sha256"]
    require(all(run["source"]["sha256"] == source_sha for run in [extension, locked_conflict, holiday]), "scenario source hashes differ")
    return {
        "schemaVersion": 1,
        "spikeId": "M0-03",
        "source": baseline["source"],
        "scenarioConfig": {
            "path": CONFIG_PATH.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(),
        },
        "calendarSemantics": {
            "workweek": "monday-friday",
            "duration": "inclusive_workdays",
            "dependency": "finish_to_start",
            "buffer": "explicit_full_workdays_between_predecessor_finish_and_successor_start",
        },
        "scenarios": [scenario_summary(item) for item in [baseline, extension, locked_conflict, holiday]],
        "failureCodesVerified": failure_codes,
        "reportedIssueCodesVerified": ["locked_dependency_conflict", "hard_deadline_miss"],
    }


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = build_result()
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(serialized, encoding="utf-8", newline="\n")
        print(f"Wrote {RESULT_PATH.relative_to(ROOT)}")
    else:
        require(RESULT_PATH.exists(), "committed schedule result is missing; run with --write")
        require(RESULT_PATH.read_text(encoding="utf-8") == serialized, "schedule result is stale; run with --write")
        print("Scheduling spike passed: 4 scenarios, 3 rejected inputs, and 2 reported issue types.")


if __name__ == "__main__":
    main()
