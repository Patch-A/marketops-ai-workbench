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
LIST_ROUTE = "/v1/projects"
DETAIL_ROUTE = "/v1/projects/{projectId}"
REVIEW_COLLECTION_ROUTE = "/v1/projects/{projectId}/extraction-runs"
REVIEW_DETAIL_ROUTE = "/v1/projects/{projectId}/extraction-runs/{runId}"
REVIEW_DECISION_ROUTE = REVIEW_DETAIL_ROUTE + "/decisions"
WBS_COLLECTION_ROUTE = "/v1/projects/{projectId}/wbs-plans"
WBS_DETAIL_ROUTE = WBS_COLLECTION_ROUTE + "/{planId}"
WBS_REVISION_ROUTE = WBS_DETAIL_ROUTE + "/revisions"
SCHEDULE_ROUTE = WBS_DETAIL_ROUTE + "/schedule-snapshots"
APPROVAL_ROUTE = WBS_DETAIL_ROUTE + "/approvals"
EXECUTION_ROUTE = WBS_DETAIL_ROUTE + "/execution"
EXECUTION_UPDATE_ROUTE = WBS_DETAIL_ROUTE + "/execution-updates"
EXECUTION_CSV_ROUTE = WBS_DETAIL_ROUTE + "/exports/execution.csv"
EXECUTION_XLSX_ROUTE = WBS_DETAIL_ROUTE + "/exports/execution.xlsx"
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
# Frozen against the public import, review, WBS, and schedule adapter codes.
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
        "PROJECT_NOT_FOUND",
        "PROPOSAL_NOT_AVAILABLE",
        "PROPOSAL_CHANGED",
        "SOURCE_READ_FAILED",
        "SOURCE_INTEGRITY_FAILED",
        "PARSER_FAILED",
        "PARSER_LIMIT_EXCEEDED",
        "PARSER_WARNING",
        "EXTRACTION_FAILED",
        "NO_CANDIDATES",
        "CANDIDATE_LIMIT_EXCEEDED",
        "REVIEW_CREATE_FAILED",
        "REPOSITORY_FAILURE",
        "REVIEW_NOT_FOUND",
        "CANDIDATE_NOT_FOUND",
        "REVIEW_CONFLICT",
        "REVIEW_INCOMPLETE",
        "PLAN_NOT_FOUND",
        "PLAN_CONFLICT",
        "PLAN_ALREADY_APPROVED",
        "SCHEDULE_NOT_FOUND",
        "SCHEDULE_CONFLICT",
        "SCHEDULE_NOT_READY",
        "APPROVED_PROPOSAL_NOT_FOUND",
        "SOURCE_CONFLICT",
        "WBS_VALIDATION_FAILED",
        "EXECUTION_CONFLICT",
        "PLAN_NOT_APPROVED",
        "TASK_NOT_FOUND",
        "EXECUTION_WRITE_FAILED",
        "EXECUTION_READ_FAILED",
        "INTERNAL_FAILURE",
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


def list_operation(document):
    return document.get("paths", {}).get(LIST_ROUTE, {}).get("get", {})


def detail_operation(document):
    return document.get("paths", {}).get(DETAIL_ROUTE, {}).get("get", {})


def review_create_operation(document):
    return document.get("paths", {}).get(REVIEW_COLLECTION_ROUTE, {}).get("post", {})


def review_list_operation(document):
    return document.get("paths", {}).get(REVIEW_COLLECTION_ROUTE, {}).get("get", {})


def review_detail_operation(document):
    return document.get("paths", {}).get(REVIEW_DETAIL_ROUTE, {}).get("get", {})


def review_decision_operation(document):
    return document.get("paths", {}).get(REVIEW_DECISION_ROUTE, {}).get("post", {})


def wbs_create_operation(document):
    return document.get("paths", {}).get(WBS_COLLECTION_ROUTE, {}).get("post", {})


def wbs_read_operation(document):
    return document.get("paths", {}).get(WBS_DETAIL_ROUTE, {}).get("get", {})


def wbs_revision_operation(document):
    return document.get("paths", {}).get(WBS_REVISION_ROUTE, {}).get("post", {})


def schedule_operation(document):
    return document.get("paths", {}).get(SCHEDULE_ROUTE, {}).get("post", {})


def approval_create_operation(document):
    return document.get("paths", {}).get(APPROVAL_ROUTE, {}).get("post", {})


def approval_read_operation(document):
    return document.get("paths", {}).get(APPROVAL_ROUTE, {}).get("get", {})


def execution_read_operation(document):
    return document.get("paths", {}).get(EXECUTION_ROUTE, {}).get("get", {})


def execution_update_operation(document):
    return document.get("paths", {}).get(EXECUTION_UPDATE_ROUTE, {}).get("post", {})


def execution_csv_operation(document):
    return document.get("paths", {}).get(EXECUTION_CSV_ROUTE, {}).get("get", {})


def execution_xlsx_operation(document):
    return document.get("paths", {}).get(EXECUTION_XLSX_ROUTE, {}).get("get", {})


def wbs_request_schema(document, operation_value):
    schema = json_request_schema(operation_value)
    if "$ref" not in schema:
        return schema
    prefix = "#/components/schemas/"
    name = schema["$ref"][len(prefix) :]
    return component_schema(document, name)


def wbs_operations(document):
    return (
        wbs_create_operation(document),
        wbs_read_operation(document),
        wbs_revision_operation(document),
        schedule_operation(document),
        approval_create_operation(document),
        approval_read_operation(document),
        execution_read_operation(document),
        execution_update_operation(document),
        execution_csv_operation(document),
        execution_xlsx_operation(document),
    )


def json_request_schema(operation_value):
    return (
        operation_value.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )


def component_schema(document, name):
    return document.get("components", {}).get("schemas", {}).get(name, {})


def response_content_schema(value):
    return (
        value.get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )


def security_options(document):
    return [
        {"deploymentActor": []},
        {"browserDeploymentActor": []},
    ]


def no_store_header(response):
    return response.get("headers", {}).get("Cache-Control") == {
        "required": True,
        "schema": {"type": "string", "const": "no-store"},
    }


def component_response(document, name):
    return document.get("components", {}).get("responses", {}).get(name, {})


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
        lambda d: operation(d).get("security") == security_options(d),
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
        "deployment authentication schemes and challenge",
        lambda d: d.get("components", {})
        .get("securitySchemes", {})
        .get("deploymentActor", {})
        .get("scheme")
        == "bearer"
        and d.get("components", {})
        .get("securitySchemes", {})
        .get("browserDeploymentActor", {})
        .get("scheme")
        == "basic"
        and set(
            d.get("components", {})
            .get("responses", {})
            .get("AuthenticationFailure", {})
            .get("headers", {})
            .get("WWW-Authenticate", {})
            .get("schema", {})
            .get("enum", [])
        )
        == {"Bearer", 'Basic realm="MarketOps", charset="UTF-8"'}
        and d.get("components", {})
        .get("responses", {})
        .get("AuthenticationFailure", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
        == {"$ref": "#/components/schemas/ImportError"},
        lambda d: d.get("components", {})
        .get("securitySchemes", {})
        .pop("browserDeploymentActor", None),
    ),
    Guard(
        "traceable success location and no-store",
        lambda d: operation(d)
        .get("responses", {})
        .get("201", {})
        .get("headers", {})
        .get("Location", {})
        .get("required")
        is True
        and operation(d)
        .get("responses", {})
        .get("201", {})
        .get("headers", {})
        .get("Location", {})
        .get("schema", {})
        .get("pattern")
        == "^/v1/projects/[0-9a-f-]{36}$"
        and no_store_header(operation(d).get("responses", {}).get("201", {})),
        lambda d: operation(d)
        .get("responses", {})
        .get("201", {})
        .get("headers", {})
        .pop("Location", None),
    ),
    Guard(
        "scoped bounded project list route",
        lambda d: list_operation(d).get("security") == security_options(d)
        and next(
            (
                item
                for item in list_operation(d).get("parameters", [])
                if item.get("name") == "limit" and item.get("in") == "query"
            ),
            {},
        ).get("schema")
        == {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}
        and set(list_operation(d).get("responses", {}))
        == {"200", "400", "401", "403", "500"}
        and no_store_header(list_operation(d).get("responses", {}).get("200", {}))
        and response_content_schema(
            list_operation(d).get("responses", {}).get("200", {})
        ).get("properties", {}).get("projects", {}).get("items")
        == {"$ref": "#/components/schemas/ProjectSummary"},
        lambda d: list_operation(d).pop("security", None),
    ),
    Guard(
        "scoped indistinguishable project detail route",
        lambda d: detail_operation(d).get("security") == security_options(d)
        and next(
            (
                item
                for item in detail_operation(d).get("parameters", [])
                if item.get("name") == "projectId" and item.get("in") == "path"
            ),
            {},
        )
        == {
            "name": "projectId",
            "in": "path",
            "required": True,
            "schema": {"type": "string", "format": "uuid"},
        }
        and set(detail_operation(d).get("responses", {}))
        == {"200", "401", "403", "404", "500"}
        and no_store_header(detail_operation(d).get("responses", {}).get("200", {}))
        and response_content_schema(
            detail_operation(d).get("responses", {}).get("200", {})
        )
        == {"$ref": "#/components/schemas/ProjectDetail"}
        and detail_operation(d).get("responses", {}).get("404")
        == {"$ref": "#/components/responses/ImportFailure"},
        lambda d: detail_operation(d).get("responses", {}).pop("404", None),
    ),
    Guard(
        "closed project read schemas",
        lambda d: component_schema(d, "ProjectSummary").get("additionalProperties")
        is False
        and set(component_schema(d, "ProjectSummary").get("required", []))
        == {"projectId", "name", "status", "approvedProposalVersion", "createdAt"}
        and component_schema(d, "ProjectFile").get("additionalProperties") is False
        and set(component_schema(d, "ProjectFile").get("required", []))
        == {"artifactId", "versionId", "filename", "mediaType", "sizeBytes"}
        and component_schema(d, "ApprovedProposal").get("type") == "object"
        and component_schema(d, "ApprovedProposal").get("additionalProperties") is False
        and component_schema(d, "ApprovedProposal").get("properties", {}).get("sha256")
        == {"type": "string", "pattern": "^[a-f0-9]{64}$"}
        and set(component_schema(d, "ApprovedProposal").get("required", []))
        == {
            "artifactId",
            "versionId",
            "filename",
            "mediaType",
            "sizeBytes",
            "sha256",
            "proposalVersion",
            "approvalStatus",
            "approvedAt",
        }
        and component_schema(d, "ProjectDetail").get("additionalProperties") is False
        and set(component_schema(d, "ProjectDetail").get("required", []))
        == {"projectId", "name", "status", "createdAt", "sourceFile", "approvedProposal"},
        lambda d: component_schema(d, "ProjectDetail").update(additionalProperties=True),
    ),
    Guard(
        "closed server-derived review create route",
        lambda d: review_create_operation(d).get("security") == security_options(d)
        and json_request_schema(review_create_operation(d)).get("additionalProperties") is False
        and set(json_request_schema(review_create_operation(d)).get("required", []))
        == {"expectedProposalVersionId", "expectedProposalSha256"}
        and set(json_request_schema(review_create_operation(d)).get("properties", {}))
        == {"expectedProposalVersionId", "expectedProposalSha256"}
        and all(
            field not in json.dumps(json_request_schema(review_create_operation(d)))
            for field in (
                "candidate",
                "citation",
                "parserBlock",
                "storageKey",
                "organizationId",
                "workspaceId",
                "clientId",
                "actorId",
            )
        )
        and set(review_create_operation(d).get("responses", {}))
        == {"201", "400", "401", "403", "409", "413", "422", "500", "503"}
        and no_store_header(review_create_operation(d).get("responses", {}).get("201", {})),
        lambda d: json_request_schema(review_create_operation(d))
        .setdefault("properties", {})
        .update(candidates={"type": "array"}),
    ),
    Guard(
        "scoped review list and history routes",
        lambda d: review_list_operation(d).get("security") == security_options(d)
        and review_detail_operation(d).get("security") == security_options(d)
        and set(review_list_operation(d).get("responses", {}))
        == {"200", "400", "401", "403", "404", "503"}
        and set(review_detail_operation(d).get("responses", {}))
        == {"200", "400", "401", "403", "404", "503"}
        and no_store_header(review_list_operation(d).get("responses", {}).get("200", {}))
        and no_store_header(review_detail_operation(d).get("responses", {}).get("200", {}))
        and response_content_schema(
            review_detail_operation(d).get("responses", {}).get("200", {})
        ) == {"$ref": "#/components/schemas/ReviewDetail"},
        lambda d: review_detail_operation(d).pop("security", None),
    ),
    Guard(
        "closed conflict-safe review decision route",
        lambda d: review_decision_operation(d).get("security") == security_options(d)
        and json_request_schema(review_decision_operation(d)).get("additionalProperties") is False
        and set(json_request_schema(review_decision_operation(d)).get("required", []))
        == {"expectedReviewVersion", "candidateId", "action", "reason"}
        and set(json_request_schema(review_decision_operation(d)).get("properties", {}))
        == {
            "expectedReviewVersion",
            "candidateId",
            "action",
            "reason",
            "comment",
            "replacementText",
        }
        and json_request_schema(review_decision_operation(d))
        .get("properties", {})
        .get("action", {})
        .get("enum")
        == ["approve", "modify", "reject"]
        and set(review_decision_operation(d).get("responses", {}))
        == {"201", "400", "401", "403", "404", "409", "413", "500", "503"}
        and no_store_header(review_decision_operation(d).get("responses", {}).get("201", {})),
        lambda d: json_request_schema(review_decision_operation(d)).update(
            additionalProperties=True
        ),
    ),
    Guard(
        "closed review response schemas",
        lambda d: all(
            component_schema(d, name).get("additionalProperties") is False
            for name in (
                "ReviewRunSummary",
                "ReviewCreateResult",
                "ReviewDecision",
                "ReviewCandidate",
                "ReviewDetail",
                "ReviewDecisionResult",
            )
        )
        and component_schema(d, "ReviewDetail")
        .get("properties", {})
        .get("candidates", {})
        .get("items")
        == {"$ref": "#/components/schemas/ReviewCandidate"},
        lambda d: component_schema(d, "ReviewCandidate").update(
            additionalProperties=True
        ),
    ),
    Guard(
        "closed review source citation schema",
        lambda d: component_schema(d, "ReviewCandidate")
        .get("properties", {})
        .get("sourceCitation")
        == {"$ref": "#/components/schemas/SourceCitation"}
        and component_schema(d, "SourceCitation").get("type") == "object"
        and component_schema(d, "SourceCitation").get("additionalProperties") is False
        and set(component_schema(d, "SourceCitation").get("required", []))
        == {"sourceVersionId", "sourceSha256", "location", "sectionPath", "quote"}
        and set(component_schema(d, "SourceCitation").get("properties", {}))
        == {"sourceVersionId", "sourceSha256", "location", "sectionPath", "quote"}
        and component_schema(d, "SourceCitation")
        .get("properties", {})
        .get("location")
        == {"$ref": "#/components/schemas/SourceLocation"},
        lambda d: component_schema(d, "SourceCitation").update(
            additionalProperties=True
        ),
    ),
    Guard(
        "closed review source location variants",
        lambda d: isinstance(component_schema(d, "SourceLocation").get("oneOf"), list)
        and len(component_schema(d, "SourceLocation")["oneOf"]) == 7
        and {
            variant.get("properties", {}).get("kind", {}).get("const")
            for variant in component_schema(d, "SourceLocation")["oneOf"]
        }
        == {
            "line_range",
            "csv_range",
            "docx_paragraph",
            "docx_table",
            "markdown_table_cell",
            "csv_cell",
            "docx_table_cell",
        }
        and all(
            variant.get("type") == "object"
            and variant.get("additionalProperties") is False
            and set(variant.get("required", []))
            == set(variant.get("properties", {}))
            for variant in component_schema(d, "SourceLocation")["oneOf"]
        ),
        lambda d: component_schema(d, "SourceLocation")["oneOf"][0].update(
            additionalProperties=True
        ),
    ),
    Guard(
        "project responses exclude server secrets",
        lambda d: all(
            f'"{field}"' not in json.dumps(
                {
                    name: component_schema(d, name)
                    for name in (
                        "ProjectSummary",
                        "ProjectFile",
                        "ApprovedProposal",
                        "ProjectDetail",
                    )
                }
            )
            for field in (
                "storageKey",
                "storageUrl",
                "idempotencyKey",
                "credential",
                "credentials",
                "secret",
                "dsn",
            )
        ),
        lambda d: component_schema(d, "ProjectFile")
        .setdefault("properties", {})
        .update(storageKey={"type": "string"}),
    ),
    Guard(
        "authentication challenge",
        lambda d: d.get("components", {})
        .get("responses", {})
        .get("AuthenticationFailure", {})
        .get("headers", {})
        .get("WWW-Authenticate", {})
        .get("required")
        is True
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
        "all failure responses are no-store",
        lambda d: no_store_header(component_response(d, "AuthenticationFailure"))
        and no_store_header(component_response(d, "ImportFailure")),
        lambda d: component_response(d, "ImportFailure")
        .get("headers", {})
        .pop("Cache-Control", None),
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
        "scoped WBS and schedule routes",
        lambda d: set(
            (
                WBS_COLLECTION_ROUTE,
                WBS_DETAIL_ROUTE,
                WBS_REVISION_ROUTE,
                SCHEDULE_ROUTE,
                APPROVAL_ROUTE,
                EXECUTION_ROUTE,
                EXECUTION_UPDATE_ROUTE,
                EXECUTION_CSV_ROUTE,
                EXECUTION_XLSX_ROUTE,
            )
        ).issubset(d.get("paths", {}))
        and all(
            operation_value.get("security") == security_options(d)
            for operation_value in wbs_operations(d)
        ),
        lambda d: d.get("paths", {}).pop(WBS_REVISION_ROUTE, None),
    ),
    Guard(
        "closed WBS create request",
        lambda d: wbs_create_operation(d).get("security") == security_options(d)
        and wbs_request_schema(d, wbs_create_operation(d)).get("type") == "object"
        and wbs_request_schema(d, wbs_create_operation(d)).get("additionalProperties")
        is False
        and set(wbs_request_schema(d, wbs_create_operation(d)).get("required", []))
        == {"reviewRunId", "reviewVersion"}
        and set(wbs_request_schema(d, wbs_create_operation(d)).get("properties", {}))
        == {"reviewRunId", "reviewVersion"}
        and FORBIDDEN_SCOPE_FIELDS.isdisjoint(
            wbs_request_schema(d, wbs_create_operation(d)).get("properties", {})
        ),
        lambda d: wbs_request_schema(d, wbs_create_operation(d))
        .setdefault("properties", {})
        .update(actorId={"type": "string"}),
    ),
    Guard(
        "closed WBS revision request",
        lambda d: wbs_revision_operation(d).get("security") == security_options(d)
        and wbs_request_schema(d, wbs_revision_operation(d)).get("additionalProperties")
        is False
        and set(wbs_request_schema(d, wbs_revision_operation(d)).get("required", []))
        == {"expectedPlanVersion", "taskUpdates"}
        and wbs_request_schema(d, wbs_revision_operation(d))
        .get("properties", {})
        .get("taskUpdates", {})
        .get("items", {})
        .get("additionalProperties")
        is False,
        lambda d: wbs_request_schema(d, wbs_revision_operation(d)).update(
            additionalProperties=True
        ),
    ),
    Guard(
        "closed WBS task changes",
        lambda d: (
            component_schema(d, "WbsTaskChanges").get("type") == "object"
            and component_schema(d, "WbsTaskChanges").get("additionalProperties") is False
            and component_schema(d, "WbsTaskChanges").get("minProperties") == 1
            and set(component_schema(d, "WbsTaskChanges").get("properties", {}))
            == {
                "title",
                "durationWorkdays",
                "predecessors",
                "ownerRole",
                "plannedStart",
                "plannedFinish",
                "hardDeadline",
                "approvedBufferWorkdays",
                "isLocked",
                "status",
            }
            and component_schema(d, "WbsTaskChanges")
            .get("properties", {})
            .get("status", {})
            .get("type")
            == "string"
        ),
        lambda d: component_schema(d, "WbsTaskChanges").update(
            additionalProperties=True
        ),
    ),
    Guard(
        "closed WBS revision items",
        lambda d: (
            component_schema(d, "WbsRevisionRequest")
            .get("properties", {})
            .get("taskUpdates", {})
            .get("items", {})
            .get("type")
            == "object"
            and component_schema(d, "WbsRevisionRequest")
            .get("properties", {})
            .get("taskUpdates", {})
            .get("items", {})
            .get("additionalProperties")
            is False
            and set(
                component_schema(d, "WbsRevisionRequest")
                .get("properties", {})
                .get("taskUpdates", {})
                .get("items", {})
                .get("required", [])
            )
            == {"taskId", "changes"}
        ),
        lambda d: component_schema(d, "WbsRevisionRequest")
        .get("properties", {})
        .get("taskUpdates", {})
        .get("items", {})
        .get("required", [])
        .remove("taskId"),
    ),
    Guard(
        "typed schedule holidays",
        lambda d: (
            component_schema(d, "ScheduleRequest")
            .get("properties", {})
            .get("holidays", {})
            .get("items", {})
            .get("type")
            == "string"
            and component_schema(d, "ScheduleRequest")
            .get("properties", {})
            .get("holidays", {})
            .get("items", {})
            .get("format")
            == "date"
        ),
        lambda d: component_schema(d, "ScheduleRequest")
        .get("properties", {})
        .get("holidays", {})
        .get("items", {})
        .update(type="integer"),
    ),
    Guard(
        "typed WBS task status",
        lambda d: (
            component_schema(d, "WbsTask")
            .get("properties", {})
            .get("status", {})
            .get("type")
            == "string"
            and set(
                component_schema(d, "WbsTask")
                .get("properties", {})
                .get("status", {})
                .get("enum", [])
            )
            == {"not_started", "in_progress", "blocked", "completed", "cancelled"}
        ),
        lambda d: component_schema(d, "WbsTask")
        .get("properties", {})
        .get("status", {})
        .update(type="integer"),
    ),
    Guard(
        "closed schedule request",
        lambda d: schedule_operation(d).get("security") == security_options(d)
        and wbs_request_schema(d, schedule_operation(d)).get("additionalProperties")
        is False
        and set(wbs_request_schema(d, schedule_operation(d)).get("required", []))
        == {"expectedPlanVersion", "projectStart", "holidays"}
        and set(wbs_request_schema(d, schedule_operation(d)).get("properties", {}))
        == {"expectedPlanVersion", "projectStart", "holidays"},
        lambda d: wbs_request_schema(d, schedule_operation(d)).update(
            additionalProperties=True
        ),
    ),
    Guard(
        "closed plan approval request",
        lambda d: approval_create_operation(d).get("security") == security_options(d)
        and wbs_request_schema(d, approval_create_operation(d)).get("type") == "object"
        and wbs_request_schema(d, approval_create_operation(d)).get("additionalProperties")
        is False
        and set(
            wbs_request_schema(d, approval_create_operation(d)).get("required", [])
        )
        == {"expectedPlanVersion", "scheduleSnapshotId", "reason"}
        and set(
            wbs_request_schema(d, approval_create_operation(d)).get("properties", {})
        )
        == {"expectedPlanVersion", "scheduleSnapshotId", "reason"}
        and FORBIDDEN_SCOPE_FIELDS.isdisjoint(
            wbs_request_schema(d, approval_create_operation(d)).get("properties", {})
        )
        and {
            "planDigest",
            "scheduleDigest",
            "approvedAt",
            "approvedBy",
        }.isdisjoint(
            wbs_request_schema(d, approval_create_operation(d)).get("properties", {})
        ),
        lambda d: wbs_request_schema(d, approval_create_operation(d))
        .setdefault("properties", {})
        .update(planDigest={"type": "string"}),
    ),
    Guard(
        "plan approval response and cache contract",
        lambda d: set(approval_create_operation(d).get("responses", {}))
        == {"201", "400", "401", "403", "404", "409", "413", "422", "500", "503"}
        and set(approval_read_operation(d).get("responses", {}))
        == {"200", "400", "401", "403", "404", "503"}
        and no_store_header(
            approval_create_operation(d).get("responses", {}).get("201", {})
        )
        and no_store_header(
            approval_read_operation(d).get("responses", {}).get("200", {})
        )
        and approval_create_operation(d)
        .get("responses", {})
        .get("201", {})
        .get("headers", {})
        .get("Location", {})
        .get("required")
        is True,
        lambda d: approval_create_operation(d).get("responses", {}).pop("409", None),
    ),
    Guard(
        "closed public plan approval schemas",
        lambda d: all(
            component_schema(d, name).get("additionalProperties") is False
            for name in (
                "PlanApprovalRequest",
                "PlanApproval",
                "PlanApprovalCreateResult",
                "PlanApprovalReadResult",
            )
        )
        and set(component_schema(d, "PlanApproval").get("required", []))
        == {
            "approvalId",
            "planId",
            "planVersion",
            "scheduleSnapshotId",
            "planDigest",
            "scheduleDigest",
            "reason",
            "approvedAt",
        }
        and {
            "organizationId",
            "workspaceId",
            "clientId",
            "actorId",
            "approvedBy",
            "credential",
        }.isdisjoint(component_schema(d, "PlanApproval").get("properties", {}))
        and approval_create_operation(d)
        .get("responses", {})
        .get("201", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
        == {"$ref": "#/components/schemas/PlanApprovalCreateResult"}
        and approval_read_operation(d)
        .get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
        == {"$ref": "#/components/schemas/PlanApprovalReadResult"},
        lambda d: component_schema(d, "PlanApproval").setdefault(
            "properties", {}
        ).update(approvedBy={"type": "string"}),
    ),
    Guard(
        "closed execution request and response schemas",
        lambda d: execution_update_operation(d).get("security") == security_options(d)
        and wbs_request_schema(d, execution_update_operation(d)).get("additionalProperties") is False
        and set(wbs_request_schema(d, execution_update_operation(d)).get("required", []))
        == {"expectedPlanVersion", "taskId", "expectedExecutionSequence", "status"}
        and set(wbs_request_schema(d, execution_update_operation(d)).get("properties", {}))
        == {"expectedPlanVersion", "taskId", "expectedExecutionSequence", "status", "blockerReason", "actualStart", "actualFinish", "note"}
        and FORBIDDEN_SCOPE_FIELDS.isdisjoint(
            wbs_request_schema(d, execution_update_operation(d)).get("properties", {})
        )
        and all(
            component_schema(d, name).get("additionalProperties") is False
            for name in ("ExecutionTask", "ExecutionReadResult", "ExecutionUpdateRequest", "ExecutionUpdate", "ExecutionUpdateResult")
        )
        and execution_read_operation(d).get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema")
        == {"$ref": "#/components/schemas/ExecutionReadResult"}
        and execution_update_operation(d).get("responses", {}).get("201", {}).get("content", {}).get("application/json", {}).get("schema")
        == {"$ref": "#/components/schemas/ExecutionUpdateResult"},
        lambda d: component_schema(d, "ExecutionUpdateRequest").update(additionalProperties=True),
    ),
    Guard(
        "execution route status and download contract",
        lambda d: set(execution_read_operation(d).get("responses", {}))
        == {"200", "400", "401", "403", "404", "422", "503"}
        and set(execution_update_operation(d).get("responses", {}))
        == {"201", "400", "401", "403", "404", "409", "413", "422", "503"}
        and no_store_header(execution_read_operation(d).get("responses", {}).get("200", {}))
        and no_store_header(execution_update_operation(d).get("responses", {}).get("201", {}))
        and execution_update_operation(d).get("responses", {}).get("201", {}).get("headers", {}).get("Location", {}).get("required") is True
        and "text/csv" in execution_csv_operation(d).get("responses", {}).get("200", {}).get("content", {})
        and "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in execution_xlsx_operation(d).get("responses", {}).get("200", {}).get("content", {}),
        lambda d: execution_update_operation(d).get("responses", {}).pop("409", None),
    ),
    Guard(
        "WBS and schedule response status and cache contract",
        lambda d: set(wbs_create_operation(d).get("responses", {}))
        == {"201", "400", "401", "403", "404", "409", "413", "422", "500", "503"}
        and set(wbs_read_operation(d).get("responses", {}))
        == {"200", "400", "401", "403", "404", "503"}
        and set(wbs_revision_operation(d).get("responses", {}))
        == {"201", "400", "401", "403", "404", "409", "413", "422", "500", "503"}
        and set(schedule_operation(d).get("responses", {}))
        == {"201", "400", "401", "403", "404", "409", "413", "422", "500", "503"}
        and no_store_header(wbs_create_operation(d).get("responses", {}).get("201", {}))
        and no_store_header(wbs_read_operation(d).get("responses", {}).get("200", {}))
        and no_store_header(wbs_revision_operation(d).get("responses", {}).get("201", {}))
        and no_store_header(schedule_operation(d).get("responses", {}).get("201", {}))
        and wbs_create_operation(d).get("responses", {}).get("201", {}).get("headers", {}).get("Location", {}).get("required") is True
        and schedule_operation(d).get("responses", {}).get("201", {}).get("headers", {}).get("Location", {}).get("required") is True,
        lambda d: schedule_operation(d).get("responses", {}).pop("503", None),
    ),
    Guard(
        "closed WBS and schedule response schemas",
        lambda d: all(
            component_schema(d, name).get("additionalProperties") is False
            for name in ("WbsTask", "WbsControl", "WbsPlan", "ScheduleSnapshot", "ScheduledTask")
        )
        and {"planId", "projectId", "selectedPlanVersion", "planDigest", "tasks", "controls"}.issubset(
            set(component_schema(d, "WbsPlan").get("required", []))
        )
        and {"snapshotId", "planId", "planVersion", "status", "scheduleDigest", "tasks"}.issubset(
            set(component_schema(d, "ScheduleSnapshot").get("required", []))
        )
        and wbs_create_operation(d).get("responses", {}).get("201", {}).get("content", {}).get("application/json", {}).get("schema")
        == {"$ref": "#/components/schemas/WbsPlanCreateResult"}
        and schedule_operation(d).get("responses", {}).get("201", {}).get("content", {}).get("application/json", {}).get("schema")
        == {"$ref": "#/components/schemas/ScheduleCreateResult"},
        lambda d: component_schema(d, "ScheduleSnapshot").get("required", []).remove(
            "scheduleDigest"
        ),
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
