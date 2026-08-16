#!/usr/bin/env python3
"""Mutation-check the public M2-01 retrieval OpenAPI surface."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "apps" / "api" / "openapi" / "project-import.openapi.yaml"
INDEX_ROUTE = "/v1/projects/{projectId}/source-indexes"
DETAIL_ROUTE = INDEX_ROUTE + "/{indexId}"
SEARCH_ROUTE = "/v1/projects/{projectId}/retrieval-searches"
WITHDRAW_ROUTE = DETAIL_ROUTE + "/withdrawal"
FORBIDDEN_FIELDS = {
    "organizationId",
    "workspaceId",
    "clientId",
    "actorId",
    "storageKey",
    "parserVersion",
    "chunkerVersion",
    "sourceSha256",
    "chunks",
}
RETRIEVAL_ERROR_CODES = {
    "SOURCE_NOT_FOUND",
    "SOURCE_INTEGRITY_FAILED",
    "PARSER_FAILED",
    "PARSER_LIMIT_EXCEEDED",
    "PARSER_WARNING",
    "SOURCE_TOO_LARGE",
    "INVALID_SOURCE",
    "INVALID_QUERY",
    "INDEX_NOT_FOUND",
    "INDEX_CONFLICT",
    "INDEX_NOT_READY",
    "SOURCE_STALE",
    "CONTENT_STALE",
    "CITATION_STALE",
    "SCOPE_CONTAMINATION",
    "INDEX_WRITE_FAILED",
    "RETRIEVAL_WRITE_FAILED",
    "RETRIEVAL_READ_FAILED",
}


@dataclass(frozen=True)
class Guard:
    name: str
    check: Callable[[dict], bool]
    mutate: Callable[[dict], None]


def operation(document, route, method):
    return document.get("paths", {}).get(route, {}).get(method, {})


def schema(document, name):
    return document.get("components", {}).get("schemas", {}).get(name, {})


def request_schema(document, route):
    value = (
        operation(document, route, "post")
        .get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if "$ref" not in value:
        return value
    return schema(document, value["$ref"].rsplit("/", 1)[-1])


def response_schema(document, route, method, status):
    return (
        operation(document, route, method)
        .get("responses", {})
        .get(status, {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )


def security(document):
    return [{"deploymentActor": []}, {"browserDeploymentActor": []}]


def no_store(document, route, method, status):
    return (
        operation(document, route, method)
        .get("responses", {})
        .get(status, {})
        .get("headers", {})
        .get("Cache-Control")
        == {"required": True, "schema": {"type": "string", "const": "no-store"}}
    )


def error_codes(document):
    return set(
        schema(document, "ImportError")
        .get("properties", {})
        .get("code", {})
        .get("enum", [])
    )


def closed_schema(document, name, required):
    value = schema(document, name)
    return (
        value.get("type") == "object"
        and value.get("additionalProperties") is False
        and set(value.get("required", [])) == set(required)
        and set(value.get("properties", {})) == set(required)
    )


GUARDS = (
    Guard(
        "four authenticated retrieval operations",
        lambda d: all(
            operation(d, route, method).get("security") == security(d)
            for route, method in (
                (INDEX_ROUTE, "post"),
                (DETAIL_ROUTE, "get"),
                (SEARCH_ROUTE, "post"),
                (WITHDRAW_ROUTE, "post"),
            )
        ),
        lambda d: operation(d, SEARCH_ROUTE, "post").pop("security", None),
    ),
    Guard(
        "closed server-derived index request",
        lambda d: request_schema(d, INDEX_ROUTE).get("additionalProperties") is False
        and request_schema(d, INDEX_ROUTE).get("required") == ["artifactVersionId"]
        and set(request_schema(d, INDEX_ROUTE).get("properties", {}))
        == {"artifactVersionId"}
        and FORBIDDEN_FIELDS.isdisjoint(
            request_schema(d, INDEX_ROUTE).get("properties", {})
        ),
        lambda d: request_schema(d, INDEX_ROUTE)
        .setdefault("properties", {})
        .update(storageKey={"type": "string"}),
    ),
    Guard(
        "bounded body-only search request",
        lambda d: request_schema(d, SEARCH_ROUTE).get("additionalProperties") is False
        and set(request_schema(d, SEARCH_ROUTE).get("required", [])) == {"query"}
        and set(request_schema(d, SEARCH_ROUTE).get("properties", {}))
        == {"query", "limit"}
        and request_schema(d, SEARCH_ROUTE)["properties"]["query"].get("maxLength")
        == 1000
        and request_schema(d, SEARCH_ROUTE)["properties"]["limit"]
        == {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}
        and all(
            item.get("in") != "query"
            for item in operation(d, SEARCH_ROUTE, "post").get("parameters", [])
        )
        and FORBIDDEN_FIELDS.isdisjoint(
            request_schema(d, SEARCH_ROUTE).get("properties", {})
        ),
        lambda d: operation(d, SEARCH_ROUTE, "post")
        .setdefault("parameters", [])
        .append({"name": "query", "in": "query"}),
    ),
    Guard(
        "empty withdrawal command",
        lambda d: request_schema(d, WITHDRAW_ROUTE)
        == {"type": "object", "additionalProperties": False, "maxProperties": 0},
        lambda d: request_schema(d, WITHDRAW_ROUTE).update(additionalProperties=True),
    ),
    Guard(
        "index creation location and no-store response",
        lambda d: response_schema(d, INDEX_ROUTE, "post", "201")
        == {"$ref": "#/components/schemas/SourceIndexMutationResult"}
        and no_store(d, INDEX_ROUTE, "post", "201")
        and operation(d, INDEX_ROUTE, "post")
        .get("responses", {})
        .get("201", {})
        .get("headers", {})
        .get("Location", {})
        .get("required")
        is True,
        lambda d: operation(d, INDEX_ROUTE, "post")["responses"]["201"][
            "headers"
        ].pop("Location"),
    ),
    Guard(
        "read and withdrawal response contracts",
        lambda d: response_schema(d, DETAIL_ROUTE, "get", "200")
        == {"$ref": "#/components/schemas/SourceIndex"}
        and response_schema(d, WITHDRAW_ROUTE, "post", "200")
        == {"$ref": "#/components/schemas/SourceIndexMutationResult"}
        and no_store(d, DETAIL_ROUTE, "get", "200")
        and no_store(d, WITHDRAW_ROUTE, "post", "200"),
        lambda d: operation(d, WITHDRAW_ROUTE, "post")["responses"]["200"][
            "headers"
        ].pop("Cache-Control"),
    ),
    Guard(
        "closed source index schemas",
        lambda d: closed_schema(
            d,
            "SourceIndex",
            {
                "indexId",
                "artifactVersionId",
                "status",
                "sourceSha256",
                "parserVersion",
                "chunkerVersion",
                "indexSha256",
                "chunkCount",
            },
        )
        and closed_schema(
            d,
            "SourceIndexMutationResult",
            {
                "indexId",
                "artifactVersionId",
                "status",
                "sourceSha256",
                "parserVersion",
                "chunkerVersion",
                "indexSha256",
                "chunkCount",
                "replayed",
            },
        )
        and {"storageKey", "actorId", "workspaceId", "chunks"}.isdisjoint(
            schema(d, "SourceIndexMutationResult").get("properties", {})
        ),
        lambda d: schema(d, "SourceIndex").update(additionalProperties=True),
    ),
    Guard(
        "closed deterministic search result",
        lambda d: response_schema(d, SEARCH_ROUTE, "post", "200")
        == {"$ref": "#/components/schemas/RetrievalSearchResult"}
        and no_store(d, SEARCH_ROUTE, "post", "200")
        and closed_schema(
            d,
            "RetrievalSearchResult",
            {
                "retrievalMode",
                "scoredCandidateCount",
                "resultCount",
                "runSha256",
                "results",
            },
        )
        and schema(d, "RetrievalSearchResult")
        .get("properties", {})
        .get("retrievalMode")
        == {"type": "string", "const": "lexical_ngram"},
        lambda d: schema(d, "RetrievalSearchResult")["properties"][
            "retrievalMode"
        ].pop("const"),
    ),
    Guard(
        "fresh closed citation schema",
        lambda d: closed_schema(
            d,
            "RetrievalCitation",
            {
                "indexId",
                "chunkId",
                "artifactVersionId",
                "sourceSha256",
                "contentSha256",
                "location",
                "parserVersion",
                "chunkerVersion",
                "freshness",
            },
        )
        and schema(d, "RetrievalCitation")["properties"]["freshness"]
        == {"type": "string", "const": "fresh"}
        and schema(d, "RetrievalCitation")["properties"]["contentSha256"].get(
            "pattern"
        )
        == "^[a-f0-9]{64}$",
        lambda d: schema(d, "RetrievalCitation")["properties"].pop(
            "contentSha256"
        ),
    ),
    Guard(
        "closed ranked hit schema",
        lambda d: closed_schema(
            d,
            "RetrievalSearchHit",
            {
                "rank",
                "lexicalRank",
                "ngramRank",
                "lexicalScore",
                "ngramScore",
                "combinedScore",
                "excerpt",
                "citation",
            },
        )
        and schema(d, "RetrievalSearchHit")["properties"]["excerpt"].get(
            "maxLength"
        )
        == 600,
        lambda d: schema(d, "RetrievalSearchHit")["properties"]["excerpt"].pop(
            "maxLength"
        ),
    ),
    Guard(
        "retrieval error code coverage",
        lambda d: RETRIEVAL_ERROR_CODES.issubset(error_codes(d)),
        lambda d: schema(d, "ImportError")["properties"]["code"]["enum"].remove(
            "CONTENT_STALE"
        ),
    ),
)


def validate(document):
    return [guard.name for guard in GUARDS if not guard.check(document)]


def main() -> int:
    try:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"FAIL: M2-01 OpenAPI contract could not be read: {error}", file=sys.stderr)
        return 1
    failures = validate(document)
    for guard in GUARDS:
        mutated = copy.deepcopy(document)
        guard.mutate(mutated)
        if guard.name not in validate(mutated):
            failures.append(f"{guard.name}: checker accepted weakened contract")
    if failures:
        print("FAIL: M2-01 OpenAPI contract checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(
        f"PASS: M2-01 OpenAPI contract ({len(GUARDS)} guards, "
        f"{len(GUARDS)} mutations)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
