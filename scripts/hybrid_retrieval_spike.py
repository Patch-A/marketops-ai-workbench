#!/usr/bin/env python3
"""Dependency-free deterministic hybrid retrieval for the M0-04 spike.

This module deliberately uses a character n-gram similarity proxy, not a
production embedding. Scope checks and hard filtering always happen before
either scoring path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
VISIBILITIES = {"project", "client", "workspace_approved"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(
    r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[^\W_]+",
    re.UNICODE,
)
CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+$")
LEXICAL_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "by", "for",
    "from", "has", "in", "is", "it", "of", "on", "or", "that", "the",
    "this", "to", "uses", "with",
}
ROOT = Path(__file__).resolve().parents[1]


class RetrievalFailure(Exception):
    """An observable fail-closed retrieval or citation error."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _failure(code: str, message: str, **details: Any) -> RetrievalFailure:
    return RetrievalFailure(code, message, **details)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path, error_code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _failure(
            error_code, "JSON input could not be read.", path=str(path),
        ) from error


def _required_string(record: dict[str, Any], field: str, code: str, **details: Any) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _failure(code, f"{field} must be a non-empty string.", field=field, **details)
    return value


def _optional_scope_id(record: dict[str, Any], field: str, code: str, **details: Any) -> str | None:
    if field not in record or record[field] is None:
        return None
    value = record[field]
    if not isinstance(value, str) or not value.strip():
        raise _failure(code, f"{field} must be null or a non-empty string.", field=field, **details)
    return value


def _validate_chunk(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _failure("invalid_chunk", "Each corpus chunk must be an object.", index=index)
    chunk = dict(value)
    chunk_id = _required_string(chunk, "chunkId", "invalid_chunk", index=index)
    _required_string(chunk, "workspaceId", "invalid_chunk", chunkId=chunk_id)
    missing_scope_fields = [field for field in ("clientId", "projectId") if field not in chunk]
    if missing_scope_fields:
        raise _failure(
            "invalid_chunk_scope", "Chunks must explicitly declare all scope fields.",
            chunkId=chunk_id, missing=missing_scope_fields,
        )
    client_id = _optional_scope_id(chunk, "clientId", "invalid_chunk", chunkId=chunk_id)
    project_id = _optional_scope_id(chunk, "projectId", "invalid_chunk", chunkId=chunk_id)
    visibility = chunk.get("visibility")
    if visibility not in VISIBILITIES:
        raise _failure(
            "invalid_chunk", "visibility is not allowed.",
            chunkId=chunk_id, visibility=visibility,
        )
    if visibility == "project" and (client_id is None or project_id is None):
        raise _failure(
            "invalid_chunk_scope",
            "Project-visible chunks require clientId and projectId.",
            chunkId=chunk_id,
        )
    if visibility == "client" and client_id is None:
        raise _failure(
            "invalid_chunk_scope", "Client-visible chunks require clientId.",
            chunkId=chunk_id,
        )
    _required_string(chunk, "sourcePath", "invalid_chunk", chunkId=chunk_id)
    source_hash = _required_string(
        chunk, "sourceVersionSha256", "invalid_chunk", chunkId=chunk_id,
    )
    if not SHA256_RE.fullmatch(source_hash):
        raise _failure(
            "invalid_chunk", "sourceVersionSha256 must be a lowercase SHA-256 digest.",
            chunkId=chunk_id, field="sourceVersionSha256",
        )
    location = chunk.get("location")
    if not isinstance(location, dict) or not location:
        raise _failure(
            "invalid_chunk", "location must be a non-empty object.", chunkId=chunk_id,
        )
    text_fields = [field for field in ("text", "content") if field in chunk]
    if len(text_fields) != 1:
        raise _failure(
            "invalid_chunk", "A chunk must contain exactly one of text or content.",
            chunkId=chunk_id,
        )
    text = chunk[text_fields[0]]
    if not isinstance(text, str) or not text.strip():
        raise _failure(
            "invalid_chunk", "Chunk text must be a non-empty string.", chunkId=chunk_id,
        )
    chunk["text"] = text
    chunk.pop("content", None)
    return chunk


def _validate_corpus_payload(payload: Any, allow_chunks: bool = False) -> dict[str, Any]:
    if allow_chunks and isinstance(payload, list):
        chunks_value = payload
    else:
        if not isinstance(payload, dict):
            raise _failure(
                "invalid_corpus_schema", "Corpus must be a schema-versioned object.",
            )
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            raise _failure(
                "invalid_corpus_schema", "Corpus schemaVersion must be 1.",
                schemaVersion=payload.get("schemaVersion"),
            )
        chunks_value = payload.get("chunks")
    if not isinstance(chunks_value, list) or not chunks_value:
        raise _failure("invalid_corpus_schema", "Corpus chunks must be a non-empty array.")
    chunks = [_validate_chunk(value, index) for index, value in enumerate(chunks_value)]
    chunk_ids = [chunk["chunkId"] for chunk in chunks]
    duplicate_ids = sorted(chunk_id for chunk_id, count in Counter(chunk_ids).items() if count > 1)
    if duplicate_ids:
        raise _failure(
            "duplicate_chunk_id", "Corpus chunk IDs must be unique.", chunkIds=duplicate_ids,
        )
    return {"schemaVersion": SCHEMA_VERSION, "chunks": chunks}


def load_corpus(path: str | Path) -> dict[str, Any]:
    """Load and strictly validate a fixed-schema corpus JSON file."""

    return _validate_corpus_payload(_read_json(Path(path), "invalid_corpus_json"))


def _validate_query(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _failure("invalid_query", "Query must be an object.")
    query = dict(value)
    workspace_id = query.get("workspaceId")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise _failure("WORKSPACE_REQUIRED", "workspaceId is required.", field="workspaceId")
    _required_string(query, "text", "invalid_query")
    client_id = _optional_scope_id(query, "clientId", "invalid_query")
    project_id = _optional_scope_id(query, "projectId", "invalid_query")
    include_approved = query.get("includeWorkspaceApproved", False)
    if not isinstance(include_approved, bool):
        raise _failure(
            "invalid_query", "includeWorkspaceApproved must be boolean.",
            field="includeWorkspaceApproved",
        )
    query["includeWorkspaceApproved"] = include_approved
    if project_id is not None and client_id is None:
        raise _failure(
            "CLIENT_REQUIRED_FOR_PROJECT_SCOPE", "A project query requires clientId.",
            projectId=project_id,
        )
    query_id = query.get("queryId")
    if query_id is not None and (not isinstance(query_id, str) or not query_id.strip()):
        raise _failure("invalid_query", "queryId must be a non-empty string when provided.")
    return query


def load_queries(path: str | Path) -> list[dict[str, Any]]:
    payload = _read_json(Path(path), "invalid_queries_json")
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA_VERSION:
        raise _failure(
            "invalid_queries_schema", "Queries must be a schema-versioned object.",
        )
    values = payload.get("queries")
    if not isinstance(values, list) or not values:
        raise _failure("invalid_queries_schema", "queries must be a non-empty array.")
    queries = []
    for value in values:
        if isinstance(value, dict) and "input" in value:
            query_id = value.get("id")
            query_input = value.get("input")
            if not isinstance(query_id, str) or not query_id or not isinstance(query_input, dict):
                raise _failure("invalid_queries_schema", "Wrapped query cases require id and input.")
            query = dict(query_input)
            query["queryId"] = query_id
            if "queryText" in query:
                query["text"] = query.pop("queryText")
            queries.append(query)
        else:
            if not isinstance(value, dict):
                raise _failure("invalid_queries_schema", "Each query must be an object.")
            queries.append(dict(value))
    ids = [query.get("queryId") for query in queries]
    if any(query_id is None for query_id in ids):
        raise _failure(
            "invalid_queries_schema", "Every query in a query file requires queryId.",
        )
    duplicates = sorted(query_id for query_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise _failure(
            "duplicate_query_id", "Query IDs must be unique.", queryIds=duplicates,
        )
    return queries


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0).casefold()
        if token in LEXICAL_STOP_WORDS:
            continue
        if CJK_RE.fullmatch(token):
            # CJK text commonly has no whitespace. Unigrams preserve partial
            # term recall; adjacent bigrams add enough order information for
            # deterministic lexical ranking without a dictionary dependency.
            tokens.extend(token)
            tokens.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            tokens.append(token)
    return tokens


def _ngrams(text: str, size: int) -> Counter[str]:
    normalized = " ".join(text.casefold().split())
    if not normalized:
        return Counter()
    padded = f" {normalized} "
    if len(padded) <= size:
        return Counter({padded: 1})
    return Counter(padded[index:index + size] for index in range(len(padded) - size + 1))


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    numerator = sum(count * right.get(term, 0) for term, count in left.items())
    left_norm = math.sqrt(sum(count * count for count in left.values()))
    right_norm = math.sqrt(sum(count * count for count in right.values()))
    return numerator / (left_norm * right_norm) if numerator else 0.0


def _scoped_chunks(chunks: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
    workspace_id = query["workspaceId"]
    workspace_chunks = [chunk for chunk in chunks if chunk["workspaceId"] == workspace_id]
    if not workspace_chunks:
        raise _failure("unknown_workspace", "Query workspace does not exist.", workspaceId=workspace_id)

    client_id = query.get("clientId")
    project_id = query.get("projectId")
    if client_id is not None:
        client_chunks = [
            chunk for chunk in workspace_chunks
            if chunk["visibility"] in {"client", "project"}
            and chunk.get("clientId") == client_id
        ]
        if not client_chunks:
            raise _failure(
                "unknown_client", "Query client does not exist in the workspace.",
                workspaceId=workspace_id, clientId=client_id,
            )
        if project_id is not None and not any(
            chunk["visibility"] == "project" and chunk.get("projectId") == project_id
            for chunk in client_chunks
        ):
            raise _failure(
                "unknown_project", "Query project does not exist for the client.",
                workspaceId=workspace_id, clientId=client_id, projectId=project_id,
            )

    scoped: list[dict[str, Any]] = []
    for chunk in workspace_chunks:
        visibility = chunk["visibility"]
        if visibility == "workspace_approved":
            if query["includeWorkspaceApproved"]:
                scoped.append(chunk)
            continue
        if client_id is None or chunk.get("clientId") != client_id:
            continue
        if visibility == "client" and project_id is None:
            scoped.append(chunk)
        elif project_id is None or chunk.get("projectId") == project_id:
            scoped.append(chunk)
    return scoped


def _positive_number(value: Any, field: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _failure("invalid_score_config", f"{field} must be a finite number.", field=field)
    if value < 0 or (not allow_zero and value == 0):
        raise _failure("invalid_score_config", f"{field} is outside its allowed range.", field=field)
    return float(value)


def _score_config(query: dict[str, Any]) -> tuple[float, float, int, float]:
    lexical_weight = _positive_number(query.get("lexicalWeight", 0.6), "lexicalWeight", True)
    semantic_weight = _positive_number(query.get("semanticWeight", 0.4), "semanticWeight", True)
    if lexical_weight + semantic_weight <= 0:
        raise _failure("invalid_score_config", "At least one score weight must be positive.")
    total = lexical_weight + semantic_weight
    ngram_size = query.get("semanticNgramSize", 3)
    if isinstance(ngram_size, bool) or not isinstance(ngram_size, int) or not 2 <= ngram_size <= 8:
        raise _failure(
            "invalid_score_config", "semanticNgramSize must be an integer from 2 through 8.",
            field="semanticNgramSize",
        )
    minimum_coverage = _positive_number(
        query.get("minimumLexicalCoverage", 0.6), "minimumLexicalCoverage", True,
    )
    if minimum_coverage > 1:
        raise _failure(
            "invalid_score_config", "minimumLexicalCoverage cannot exceed 1.",
            field="minimumLexicalCoverage",
        )
    return lexical_weight / total, semantic_weight / total, ngram_size, minimum_coverage


def _resolve_top_k(query: dict[str, Any], top_k: int | None) -> int:
    value = top_k if top_k is not None else query.get("topK", 10)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _failure("invalid_top_k", "topK must be a positive integer.", topK=value)
    return value


def _lexical_scores(chunks: list[dict[str, Any]], query_terms: list[str]) -> list[float]:
    documents = [Counter(_tokens(chunk["text"])) for chunk in chunks]
    lengths = [sum(document.values()) for document in documents]
    average_length = sum(lengths) / len(lengths) if lengths else 1.0
    document_frequency = Counter(
        term for document in documents for term in document
    )
    query_frequency = Counter(query_terms)
    count = len(documents)
    scores: list[float] = []
    for document, length in zip(documents, lengths):
        raw = 0.0
        for term, query_count in query_frequency.items():
            frequency = document.get(term, 0)
            if not frequency:
                continue
            inverse_frequency = math.log(1.0 + (count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = frequency + 1.2 * (1.0 - 0.75 + 0.75 * length / average_length)
            raw += inverse_frequency * (frequency * 2.2 / denominator) * query_count
        scores.append(raw / (raw + 1.0) if raw else 0.0)
    return scores


def retrieve(
    corpus_or_chunks: dict[str, Any] | list[dict[str, Any]],
    query: dict[str, Any],
    top_k: int | None = None,
) -> dict[str, Any]:
    """Retrieve cited results after fail-closed scope filtering."""

    corpus = _validate_corpus_payload(corpus_or_chunks, allow_chunks=True)
    normalized_query = _validate_query(query)
    limit = _resolve_top_k(normalized_query, top_k)
    lexical_weight, semantic_weight, ngram_size, minimum_coverage = _score_config(normalized_query)

    # Security boundary: no scoring data structure is built before this returns.
    scoped = _scoped_chunks(corpus["chunks"], normalized_query)
    query_terms = _tokens(normalized_query["text"])
    unique_query_terms = set(query_terms)
    query_ngrams = _ngrams(normalized_query["text"], ngram_size)
    lexical_scores = _lexical_scores(scoped, query_terms) if scoped else []

    ranked: list[dict[str, Any]] = []
    for chunk, lexical_score in zip(scoped, lexical_scores):
        document_terms = set(_tokens(chunk["text"]))
        lexical_coverage = (
            len(unique_query_terms & document_terms) / len(unique_query_terms)
            if unique_query_terms else 0.0
        )
        semantic_score = _cosine(query_ngrams, _ngrams(chunk["text"], ngram_size))
        hybrid_score = lexical_weight * lexical_score + semantic_weight * semantic_score
        if hybrid_score <= 0 or lexical_coverage < minimum_coverage:
            continue
        ranked.append({
            "chunkId": chunk["chunkId"],
            "workspaceId": chunk["workspaceId"],
            "clientId": chunk.get("clientId"),
            "projectId": chunk.get("projectId"),
            "visibility": chunk["visibility"],
            "sourcePath": chunk["sourcePath"],
            "sourceVersionSha256": chunk["sourceVersionSha256"],
            "location": chunk["location"],
            "excerpt": chunk["text"],
            "lexicalScore": round(lexical_score, 12),
            "semanticProxyScore": round(semantic_score, 12),
            "hybridScore": round(hybrid_score, 12),
        })
    ranked.sort(key=lambda item: (
        -item["hybridScore"], -item["lexicalScore"],
        -item["semanticProxyScore"], item["chunkId"],
    ))
    results = ranked[:limit]
    for rank, result in enumerate(results, start=1):
        result["rank"] = rank

    query_config = {
        "query": normalized_query,
        "resolvedTopK": limit,
        "resolvedLexicalWeight": lexical_weight,
        "resolvedSemanticWeight": semantic_weight,
        "resolvedSemanticNgramSize": ngram_size,
        "resolvedMinimumLexicalCoverage": minimum_coverage,
    }
    corpus_hash = _canonical_sha256(corpus)
    config_hash = _canonical_sha256(query_config)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "queryId": normalized_query.get("queryId"),
        "corpusSha256": corpus_hash,
        "queryConfigSha256": config_hash,
        "runSha256": _canonical_sha256({"corpusSha256": corpus_hash, "queryConfigSha256": config_hash}),
        "scoredCandidateCount": len(scoped),
        "resultCount": len(results),
        "results": results,
    }


def _result_items(results: Any) -> list[dict[str, Any]]:
    if isinstance(results, dict):
        results = results.get("results")
    if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
        raise _failure("invalid_results", "Results must be an array or a retrieval response.")
    return results


def validate_citations(results: Any, root_or_base: str | Path | None = None) -> dict[str, Any]:
    """Check citation completeness and source-file freshness without fail-open behavior."""

    base = Path(root_or_base).resolve() if root_or_base is not None else ROOT.resolve()
    errors: list[dict[str, Any]] = []
    items = _result_items(results)
    for result in items:
        chunk_id = result.get("chunkId")
        required = (
            "chunkId", "workspaceId", "clientId", "projectId",
            "visibility", "sourcePath", "sourceVersionSha256",
            "location", "excerpt", "lexicalScore",
            "semanticProxyScore", "hybridScore", "rank",
        )
        missing = [field for field in required if field not in result]
        if missing:
            errors.append({"code": "incomplete_citation", "chunkId": chunk_id, "missing": missing})
            continue
        if (
            result["visibility"] not in VISIBILITIES
            or not isinstance(result["location"], dict)
            or not result["location"]
            or not isinstance(result["sourcePath"], str)
            or not result["sourcePath"].strip()
            or not isinstance(result["sourceVersionSha256"], str)
            or not SHA256_RE.fullmatch(result["sourceVersionSha256"])
        ):
            errors.append({"code": "invalid_citation", "chunkId": chunk_id})
            continue
        source_path = Path(result["sourcePath"])
        resolved = source_path.resolve() if source_path.is_absolute() else (base / source_path).resolve()
        try:
            resolved.relative_to(base)
        except ValueError:
            errors.append({
                "code": "source_path_outside_base", "chunkId": chunk_id,
                "sourcePath": result["sourcePath"],
            })
            continue
        try:
            actual_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except OSError:
            errors.append({
                "code": "source_not_found", "chunkId": chunk_id,
                "sourcePath": result["sourcePath"],
            })
            continue
        if actual_hash != result["sourceVersionSha256"]:
            errors.append({
                "code": "source_hash_mismatch", "chunkId": chunk_id,
                "sourcePath": result["sourcePath"],
                "expectedSha256": result["sourceVersionSha256"],
                "actualSha256": actual_hash,
            })
    return {"valid": not errors, "checkedCitationCount": len(items), "errors": errors}


def _select_query(path: Path, query_id: str | None) -> dict[str, Any]:
    queries = load_queries(path)
    if query_id is None:
        if len(queries) != 1:
            raise _failure(
                "missing_query_id", "query-id is required when a file has multiple queries.",
                queryCount=len(queries),
            )
        return queries[0]
    for query in queries:
        if query["queryId"] == query_id:
            return query
    raise _failure("unknown_query", "Requested query does not exist.", queryId=query_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--query-file", required=True, type=Path)
    parser.add_argument("--query-id")
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        response = retrieve(
            load_corpus(args.corpus),
            _select_query(args.query_file, args.query_id),
            args.top_k,
        )
        citation_validation = validate_citations(response, args.source_root)
        response["citationValidation"] = citation_validation
        if not citation_validation["valid"]:
            first = citation_validation["errors"][0]
            raise _failure(
                first["code"], "Citation source is missing, unsafe, or stale.",
                citationValidation=citation_validation,
            )
    except RetrievalFailure as error:
        print(json.dumps(
            {"error": {"code": error.code, "message": error.message, "details": error.details}},
            ensure_ascii=False, indent=2 if args.pretty else None,
        ))
        raise SystemExit(2) from error
    print(json.dumps(response, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
