#!/usr/bin/env python3
"""Mutation-check the public M2-02 project-learning OpenAPI surface."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "apps" / "api" / "openapi" / "project-import.openapi.yaml"
CAPSULES = "/v1/projects/{projectId}/capsules"
CAPSULE = CAPSULES + "/{capsuleId}"
KNOWLEDGE = "/v1/projects/{projectId}/knowledge"
KNOWLEDGE_ITEM = KNOWLEDGE + "/{knowledgeId}"
FORBIDDEN_REQUEST_FIELDS = {
    "organizationId", "workspaceId", "clientId", "actorId", "planId",
    "planVersionId", "approvalId", "scheduleSnapshotId", "sourceSha256",
    "bindingSha256", "candidateScope", "candidateStatus", "projectId",
}
LEARNING_CODES = {
    "CAPSULE_NOT_FOUND", "KNOWLEDGE_NOT_FOUND", "CAPSULE_NOT_READY",
    "LEARNING_WRITE_FAILED", "LEARNING_READ_FAILED",
}


@dataclass(frozen=True)
class Guard:
    name: str
    check: Callable[[dict], bool]
    mutate: Callable[[dict], None]


def operation(document: dict, route: str, method: str) -> dict:
    return document.get("paths", {}).get(route, {}).get(method, {})


def schema(document: dict, name: str) -> dict:
    return document.get("components", {}).get("schemas", {}).get(name, {})


def request_schema(document: dict) -> dict:
    value = operation(document, CAPSULES, "post").get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    return schema(document, value.get("$ref", "").rsplit("/", 1)[-1])


def security() -> list[dict[str, list[object]]]:
    return [{"deploymentActor": []}, {"browserDeploymentActor": []}]


def no_store(document: dict, route: str, method: str, status: str) -> bool:
    return operation(document, route, method).get("responses", {}).get(status, {}).get("headers", {}).get("Cache-Control") == {"required": True, "schema": {"type": "string", "const": "no-store"}}


def closed(document: dict, name: str, fields: set[str]) -> bool:
    value = schema(document, name)
    return value.get("type") == "object" and value.get("additionalProperties") is False and set(value.get("required", [])) == fields and set(value.get("properties", {})) == fields


GUARDS = (
    Guard(
        "five authenticated project-learning operations",
        lambda d: all(operation(d, route, method).get("security") == security() for route, method in ((CAPSULES, "post"), (CAPSULES, "get"), (CAPSULE, "get"), (KNOWLEDGE, "get"), (KNOWLEDGE_ITEM, "get"))),
        lambda d: operation(d, KNOWLEDGE_ITEM, "get").pop("security", None),
    ),
    Guard(
        "closed feedback-only finalization request",
        lambda d: closed(d, "CapsuleFinalizeRequest", {"outcomes", "retrospectives"}) and FORBIDDEN_REQUEST_FIELDS.isdisjoint(schema(d, "CapsuleFinalizeRequest").get("properties", {})) and closed(d, "FeedbackSource", {"sourceType", "sourceId"}),
        lambda d: schema(d, "CapsuleFinalizeRequest").setdefault("properties", {}).update(candidateScope={"type": "string"}),
    ),
    Guard(
        "candidate boundary stays project-scoped and pending",
        lambda d: closed(d, "CandidateKnowledge", {"knowledgeId", "ordinal", "scope", "type", "status", "classification", "content", "contentSha256", "confidence", "evidence"}) and schema(d, "CandidateKnowledge")["properties"]["scope"] == {"type": "string", "const": "project"} and schema(d, "CandidateKnowledge")["properties"]["status"] == {"type": "string", "const": "candidate"},
        lambda d: schema(d, "CandidateKnowledge")["properties"]["scope"].pop("const"),
    ),
    Guard(
        "capsule detail retains redacted server-derived bindings",
        lambda d: closed(d, "CapsuleBinding", {"proposal", "plan", "approval", "schedule"})
        and "binding" in schema(d, "ProjectCapsule").get("required", [])
        and schema(d, "ProjectCapsule").get("properties", {}).get("binding") == {"$ref": "#/components/schemas/CapsuleBinding"}
        and closed(d, "CapsuleProposalBinding", {"artifactId", "versionId", "version", "sha256"})
        and closed(d, "CapsulePlanBinding", {"planId", "versionId", "version", "digest"})
        and closed(d, "CapsuleApprovalBinding", {"approvalId"})
        and closed(d, "CapsuleScheduleBinding", {"snapshotId", "digest"}),
        lambda d: schema(d, "ProjectCapsule")["required"].remove("binding"),
    ),
    Guard(
        "responses retain no-store and finalization location",
        lambda d: all(no_store(d, route, method, "200") for route, method in ((CAPSULES, "get"), (CAPSULE, "get"), (KNOWLEDGE, "get"), (KNOWLEDGE_ITEM, "get"))) and no_store(d, CAPSULES, "post", "201") and operation(d, CAPSULES, "post")["responses"]["201"]["headers"].get("Location", {}).get("required") is True,
        lambda d: operation(d, CAPSULES, "post")["responses"]["201"]["headers"].pop("Location"),
    ),
    Guard(
        "learning error code coverage",
        lambda d: LEARNING_CODES.issubset(set(schema(d, "ImportError").get("properties", {}).get("code", {}).get("enum", []))),
        lambda d: schema(d, "ImportError")["properties"]["code"]["enum"].remove("CAPSULE_NOT_READY"),
    ),
)


def validate(document: dict) -> list[str]:
    return [guard.name for guard in GUARDS if not guard.check(document)]


def main() -> int:
    try:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"FAIL: M2-02 OpenAPI contract could not be read: {error}", file=sys.stderr)
        return 1
    failures = validate(document)
    for guard in GUARDS:
        mutated = copy.deepcopy(document)
        guard.mutate(mutated)
        if guard.name not in validate(mutated):
            failures.append(f"{guard.name}: checker accepted weakened contract")
    if failures:
        print("FAIL: M2-02 OpenAPI contract checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"PASS: M2-02 OpenAPI contract ({len(GUARDS)} guards, {len(GUARDS)} mutations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
