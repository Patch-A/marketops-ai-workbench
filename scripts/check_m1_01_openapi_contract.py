#!/usr/bin/env python3
"""Validate the dependency-neutral M1-01 project import OpenAPI contract."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "apps" / "api" / "openapi" / "project-import.openapi.yaml"
ROUTE = "/v1/project-imports"
FORBIDDEN_SCOPE_FIELDS = {"organizationId", "workspaceId", "clientId", "actorId"}
REQUIRED_RESPONSES = {
    "201",
    "400",
    "401",
    "403",
    "409",
    "413",
    "415",
    "422",
    "500",
}
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
SOURCE_MEDIA_TYPES = (
    "text/markdown",
    "text/plain",
    "text/csv",
    "application/csv",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)
PROPOSAL_MEDIA_TYPES = (
    "text/markdown",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)
# Frozen against the ImportFailure codes emitted by marketops_import.service.
REQUIRED_ERROR_CODES = frozenset(
    {
        "INVALID_INPUT",
        "AUTHORIZATION_REQUIRED",
        "UNSUPPORTED_FORMAT",
        "INVALID_MEDIA_TYPE",
        "INVALID_DOCUMENT",
        "APPROVAL_REQUIRED",
        "PAYLOAD_TOO_LARGE",
        "OBJECT_WRITE_FAILED",
        "OBJECT_INTEGRITY_MISMATCH",
        "IDEMPOTENCY_CONFLICT",
        "INVALID_SERVER_ID",
        "INVALID_SERVER_CLOCK",
        "DATABASE_WRITE_FAILED",
    }
)
ERROR_CODE_GUARD = "stable error code enum"


@dataclass(frozen=True)
class Guard:
    name: str
    check: Callable[[dict], bool]
    mutate: Callable[[dict], None]


def operation(document):
    return document.get("paths", {}).get(ROUTE, {}).get("post", {})


def multipart_schema(document):
    return (
        operation(document)
        .get("requestBody", {})
        .get("content", {})
        .get("multipart/form-data", {})
        .get("schema", {})
    )


def response_schema(document):
    return (
        operation(document)
        .get("responses", {})
        .get("201", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )


def header(document):
    return next(
        (
            item
            for item in operation(document).get("parameters", [])
            if item.get("in") == "header" and item.get("name") == "Idempotency-Key"
        ),
        {},
    )


def pop_path(root, *path):
    current = root
    for key in path[:-1]:
        current = current.setdefault(key, {})
    current.pop(path[-1], None)


def request_properties(document):
    return multipart_schema(document).get("properties", {})


def multipart_media_types(document, field):
    value = (
        operation(document)
        .get("requestBody", {})
        .get("content", {})
        .get("multipart/form-data", {})
        .get("encoding", {})
        .get(field, {})
        .get("contentType")
    )
    if not isinstance(value, str):
        return ()
    return tuple(item.strip() for item in value.split(","))


def error_code_enum(document):
    return (
        document.get("components", {})
        .get("schemas", {})
        .get("ImportError", {})
        .get("properties", {})
        .get("code", {})
        .get("enum", [])
    )


GUARDS = (
    Guard(
        "OpenAPI 3.1 document",
        lambda d: d.get("openapi") == "3.1.0",
        lambda d: d.update(openapi="3.0.0"),
    ),
    Guard(
        "authenticated POST route",
        lambda d: operation(d).get("security") == [{"deploymentActor": []}],
        lambda d: operation(d).pop("security", None),
    ),
    Guard(
        "required idempotency header",
        lambda d: header(d).get("required") is True
        and header(d).get("schema", {}).get("type") == "string"
        and header(d).get("schema", {}).get("minLength", 0) >= 8,
        lambda d: header(d).update(required=False),
    ),
    Guard(
        "required multipart request",
        lambda d: operation(d).get("requestBody", {}).get("required") is True
        and multipart_schema(d).get("type") == "object"
        and set(multipart_schema(d).get("required", []))
        == {
            "projectName",
            "proposalVersion",
            "approvalConfirmed",
            "sourceFile",
            "proposalFile",
        },
        lambda d: multipart_schema(d).update(required=[]),
    ),
    Guard(
        "binary source and proposal files",
        lambda d: all(
            request_properties(d).get(name, {}).get("type") == "string"
            and request_properties(d).get(name, {}).get("format") == "binary"
            for name in ("sourceFile", "proposalFile")
        ),
        lambda d: request_properties(d).get("proposalFile", {}).pop("format", None),
    ),
    Guard(
        "exact source and proposal media types",
        lambda d: multipart_media_types(d, "sourceFile") == SOURCE_MEDIA_TYPES
        and multipart_media_types(d, "proposalFile") == PROPOSAL_MEDIA_TYPES,
        lambda d: operation(d)["requestBody"]["content"]["multipart/form-data"][
            "encoding"
        ]["sourceFile"].update(
            contentType=", ".join(
                item for item in SOURCE_MEDIA_TYPES if item != "application/csv"
            )
        ),
    ),
    Guard(
        "positive proposal version",
        lambda d: request_properties(d).get("proposalVersion", {}).get("type") == "integer"
        and request_properties(d).get("proposalVersion", {}).get("minimum") == 1,
        lambda d: request_properties(d).get("proposalVersion", {}).update(minimum=0),
    ),
    Guard(
        "explicit approval confirmation",
        lambda d: request_properties(d).get("approvalConfirmed")
        == {"type": "boolean", "const": True},
        lambda d: request_properties(d).get("approvalConfirmed", {}).update(
            const=False
        ),
    ),
    Guard(
        "authorization scope absent from request",
        lambda d: FORBIDDEN_SCOPE_FIELDS.isdisjoint(request_properties(d)),
        lambda d: request_properties(d).update(workspaceId={"type": "string"}),
    ),
    Guard(
        "complete stable responses",
        lambda d: REQUIRED_RESPONSES.issubset(operation(d).get("responses", {})),
        lambda d: operation(d).get("responses", {}).pop("409", None),
    ),
    Guard(
        "error responses use ImportError",
        lambda d: operation(d).get("responses", {}).get("401")
        == {"$ref": "#/components/responses/AuthenticationFailure"}
        and all(
            operation(d).get("responses", {}).get(status)
            == {"$ref": "#/components/responses/ImportFailure"}
            for status in REQUIRED_RESPONSES - {"201", "401"}
        ),
        lambda d: operation(d).get("responses", {}).update(
            {"403": {"description": "arbitrary response"}}
        ),
    ),
    Guard(
        "bearer authentication challenge",
        lambda d: d.get("components", {})
        .get("responses", {})
        .get("AuthenticationFailure", {})
        .get("headers", {})
        .get("WWW-Authenticate", {})
        == {
            "required": True,
            "schema": {"type": "string", "const": "Bearer"},
        }
        and d.get("components", {})
        .get("responses", {})
        .get("AuthenticationFailure", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
        == {"$ref": "#/components/schemas/ImportError"},
        lambda d: d.get("components", {})
        .get("responses", {})
        .get("AuthenticationFailure", {})
        .pop("headers", None),
    ),
    Guard(
        "traceable success response",
        lambda d: {
            "projectId",
            "sourceArtifactId",
            "sourceVersionId",
            "proposalArtifactId",
            "proposalVersionId",
            "manifestSha256",
            "replayed",
        }.issubset(response_schema(d).get("properties", {}))
        and {
            "projectId",
            "sourceArtifactId",
            "sourceVersionId",
            "proposalArtifactId",
            "proposalVersionId",
            "manifestSha256",
            "replayed",
        }.issubset(response_schema(d).get("required", []))
        and response_schema(d)
        .get("properties", {})
        .get("manifestSha256", {})
        .get("pattern")
        == "^[a-f0-9]{64}$",
        lambda d: response_schema(d).update(
            required=[
                item
                for item in response_schema(d).get("required", [])
                if item != "projectId"
            ]
        ),
    ),
    Guard(
        "file size limit",
        lambda d: all(
            request_properties(d).get(name, {}).get("x-max-size-bytes")
            == MAX_FILE_SIZE_BYTES
            for name in ("sourceFile", "proposalFile")
        ),
        lambda d: request_properties(d).get("sourceFile", {}).pop(
            "x-max-size-bytes", None
        ),
    ),
    Guard(
        "storage secrets excluded",
        lambda d: not {
            "storageKey",
            "storageUrl",
            "credential",
            "credentials",
            "secret",
        }.intersection(response_schema(d).get("properties", {})),
        lambda d: response_schema(d).setdefault("properties", {}).update(
            storageKey={"type": "string"}
        ),
    ),
    Guard(
        "stable error body",
        lambda d: {"code", "message", "retryable", "requestId"}.issubset(
            d.get("components", {})
            .get("schemas", {})
            .get("ImportError", {})
            .get("required", [])
        ),
        lambda d: d["components"]["schemas"]["ImportError"].update(required=[]),
    ),
    Guard(
        ERROR_CODE_GUARD,
        lambda d: isinstance(error_code_enum(d), list)
        and len(error_code_enum(d)) == len(REQUIRED_ERROR_CODES)
        and set(error_code_enum(d)) == REQUIRED_ERROR_CODES,
        lambda d: error_code_enum(d).append("UNSTABLE_ERROR_CODE"),
    ),
)


def validate(document):
    return [guard.name for guard in GUARDS if not guard.check(document)]


def mutation_failures(document):
    failures = []
    for guard in GUARDS:
        mutated = copy.deepcopy(document)
        guard.mutate(mutated)
        if guard.name not in validate(mutated):
            failures.append(f"{guard.name}: checker accepted weakened contract")
    return failures


def error_code_deletion_failures(document):
    failures = []
    for code in sorted(REQUIRED_ERROR_CODES):
        mutated = copy.deepcopy(document)
        codes = error_code_enum(mutated)
        if code not in codes:
            failures.append(f"{ERROR_CODE_GUARD}: baseline is missing {code}")
            continue
        codes.remove(code)
        if ERROR_CODE_GUARD not in validate(mutated):
            failures.append(
                f"{ERROR_CODE_GUARD}: checker accepted deletion of {code}"
            )
    return failures


def main():
    if not CONTRACT.is_file():
        print(f"FAIL: OpenAPI contract not found: {CONTRACT.relative_to(ROOT)}", file=sys.stderr)
        return 1
    try:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"FAIL: OpenAPI contract must be UTF-8 JSON-compatible YAML: {error}", file=sys.stderr)
        return 1

    failures = (
        validate(document)
        + mutation_failures(document)
        + error_code_deletion_failures(document)
    )
    if failures:
        print("FAIL: OpenAPI contract checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "PASS: M1-01 OpenAPI contract "
        f"({len(GUARDS)} guards, {len(GUARDS) + len(REQUIRED_ERROR_CODES)} mutations)"
    )
    print(
        "LIMITATION: static contract only; runtime equality and authentication "
        "behavior are separate HTTP adapter checks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
