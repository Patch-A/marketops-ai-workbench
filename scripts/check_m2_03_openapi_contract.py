#!/usr/bin/env python3
"""Mutation-check the M2-03 approval and controlled-reuse OpenAPI surface."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "apps" / "api" / "openapi" / "project-import.openapi.yaml"
DECISIONS = "/v1/projects/{projectId}/knowledge/{knowledgeId}/approval-decisions"
CITATION = "/v1/projects/{targetProjectId}/knowledge-sources/{sourceProjectId}/items/{knowledgeId}/citations"
CITATIONS = "/v1/projects/{projectId}/knowledge-citations"
SECURITY = [{"deploymentActor": []}, {"browserDeploymentActor": []}]


def operation(document: dict, path: str, method: str) -> dict:
    return document.get("paths", {}).get(path, {}).get(method, {})


def schema(document: dict, name: str) -> dict:
    return document.get("components", {}).get("schemas", {}).get(name, {})


def closed(document: dict, name: str, required: set[str]) -> bool:
    value = schema(document, name)
    return (
        value.get("type") == "object"
        and value.get("additionalProperties") is False
        and set(value.get("required", [])) == required
        and set(value.get("properties", {})) == required
    )


def no_store(document: dict, path: str, method: str, status: str) -> bool:
    return operation(document, path, method).get("responses", {}).get(status, {}).get(
        "headers", {}
    ).get("Cache-Control") == {
        "required": True,
        "schema": {"type": "string", "const": "no-store"},
    }


def validate(document: dict) -> list[str]:
    failures: list[str] = []
    for path, method, status in (
        (DECISIONS, "post", "201"),
        (DECISIONS, "get", "200"),
        (CITATION, "post", "201"),
        (CITATIONS, "get", "200"),
    ):
        if operation(document, path, method).get("security") != SECURITY:
            failures.append(f"{method} {path} lacks authenticated server scope")
        if not no_store(document, path, method, status):
            failures.append(f"{method} {path} lacks no-store response")
    decision = schema(document, "KnowledgeDecisionRequest")
    if not (
        decision.get("type") == "object"
        and decision.get("additionalProperties") is False
        and set(decision.get("required", [])) == {"action", "expectedVersion"}
        and set(decision.get("properties", {}))
        == {"action", "expectedVersion", "effectiveScope", "revisedContent", "reason"}
    ):
        failures.append("knowledge decision body is not closed")
    if not closed(document, "KnowledgeCitationRequest", {"reason"}):
        failures.append("knowledge citation body is not closed")
    if not closed(document, "KnowledgeCitationListResult", {"citations"}):
        failures.append("effective citation list result is not closed")
    if "clientId" in decision.get("properties", {}):
        failures.append("knowledge decision accepts client scope facts")
    return failures


def main() -> int:
    try:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"FAIL: M2-03 OpenAPI contract could not be read: {error}", file=sys.stderr)
        return 1
    failures = validate(document)
    mutations = (
        lambda value: operation(value, CITATIONS, "get").pop("security", None),
        lambda value: schema(value, "KnowledgeCitationRequest").setdefault("properties", {}).update(clientId={"type": "string"}),
        lambda value: schema(value, "KnowledgeCitationListResult").pop("additionalProperties", None),
    )
    for mutate in mutations:
        changed = copy.deepcopy(document)
        mutate(changed)
        if not validate(changed):
            failures.append("checker accepted a weakened M2-03 contract")
    if failures:
        print("FAIL: M2-03 OpenAPI contract checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("PASS: M2-03 OpenAPI contract (7 guards, 3 mutations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
