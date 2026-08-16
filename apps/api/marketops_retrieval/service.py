"""Dependency-neutral indexing and retrieval rules for the M2-01 first slice."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5


PARSER_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TOKEN_PATTERN = re.compile(
    r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[^\W_]+",
    re.UNICODE,
)
CJK_PATTERN = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+$")
STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "before", "by",
        "for", "from", "has", "in", "is", "it", "of", "on", "or",
        "that", "the", "this", "to", "uses", "with",
    }
)
CHUNKER_VERSION = "deterministic-v1"
RETRIEVAL_MODE = "lexical_ngram"
MAX_BLOCKS = 5000
MAX_BLOCK_CHARACTERS = 100_000
MAX_INDEX_CHARACTERS = 2_000_000
MAX_CHUNK_CHARACTERS = 2000
MAX_LOCATION_BYTES = 4000
MAX_LOCATION_PROPERTIES = 16
MAX_QUERY_CHARACTERS = 1000
MAX_RESULTS = 20
MAX_EXCERPT_CHARACTERS = 600
MINIMUM_LEXICAL_COVERAGE = 0.6
LEXICAL_WEIGHT = 0.6
NGRAM_WEIGHT = 0.4
NGRAM_SIZE = 3


class RetrievalFailure(RuntimeError):
    """Stable retrieval failure that does not expose source or query content."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class RetrievalScopeContext:
    organization_id: str
    workspace_id: str
    client_id: str
    actor_id: str


@dataclass(frozen=True)
class SourceIdentity:
    organization_id: str
    workspace_id: str
    client_id: str
    project_id: str
    artifact_version_id: str
    source_sha256: str
    parser_version: str


@dataclass(frozen=True)
class ParsedBlock:
    text: str
    location: Mapping[str, Any]


@dataclass(frozen=True)
class SourceChunk:
    chunk_id: str
    index_id: str
    organization_id: str
    workspace_id: str
    client_id: str
    project_id: str
    artifact_version_id: str
    ordinal: int
    text: str
    content_sha256: str
    location: Mapping[str, Any]
    source_sha256: str
    parser_version: str
    chunker_version: str


@dataclass(frozen=True)
class SourceIndex:
    index_id: str
    source: SourceIdentity
    chunker_version: str
    status: str
    index_sha256: str
    chunks: tuple[SourceChunk, ...]


@dataclass(frozen=True)
class Citation:
    index_id: str
    chunk_id: str
    artifact_version_id: str
    source_sha256: str
    content_sha256: str
    location: Mapping[str, Any]
    parser_version: str
    chunker_version: str
    freshness: str = "fresh"


@dataclass(frozen=True)
class SearchHit:
    rank: int
    lexical_rank: int
    ngram_rank: int
    lexical_score: float
    ngram_score: float
    combined_score: float
    excerpt: str
    citation: Citation


@dataclass(frozen=True)
class SearchResult:
    retrieval_mode: str
    scored_candidate_count: int
    result_count: int
    run_sha256: str
    results: tuple[SearchHit, ...]


def build_source_index(
    source: SourceIdentity,
    blocks: Sequence[ParsedBlock],
    scope: RetrievalScopeContext,
) -> SourceIndex:
    _validate_scope(scope)
    _validate_source(source, scope)
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
        raise RetrievalFailure("INVALID_SOURCE", "parsed blocks are invalid")
    if not blocks or len(blocks) > MAX_BLOCKS:
        raise RetrievalFailure("INVALID_SOURCE", "parsed block count is invalid")

    index_id = str(
        uuid5(
            NAMESPACE_URL,
            "marketops:source-index:"
            f"{source.artifact_version_id}:{source.source_sha256}:"
            f"{source.parser_version}:{CHUNKER_VERSION}",
        )
    )
    chunks: list[SourceChunk] = []
    total_characters = 0
    for block_ordinal, block in enumerate(blocks):
        if not isinstance(block, ParsedBlock) or not isinstance(block.text, str):
            raise RetrievalFailure("INVALID_SOURCE", "parsed block is invalid")
        normalized = _normalize_text(block.text)
        if not normalized or len(normalized) > MAX_BLOCK_CHARACTERS:
            raise RetrievalFailure("INVALID_SOURCE", "parsed block text is invalid")
        location = _validate_location(block.location)
        total_characters += len(normalized)
        if total_characters > MAX_INDEX_CHARACTERS:
            raise RetrievalFailure("SOURCE_TOO_LARGE", "parsed source is too large")
        for start, end, text in _split_text(normalized):
            ordinal = len(chunks)
            content_sha256 = _sha256_text(text)
            chunk_id = str(
                uuid5(
                    NAMESPACE_URL,
                    "marketops:source-chunk:"
                    f"{index_id}:{ordinal}:{content_sha256}",
                )
            )
            chunk_location = dict(location)
            chunk_location.update(
                {
                    "blockOrdinal": block_ordinal,
                    "characterStart": start,
                    "characterEnd": end,
                }
            )
            chunk_location = dict(_validate_location(chunk_location))
            chunks.append(
                SourceChunk(
                    chunk_id=chunk_id,
                    index_id=index_id,
                    organization_id=source.organization_id,
                    workspace_id=source.workspace_id,
                    client_id=source.client_id,
                    project_id=source.project_id,
                    artifact_version_id=source.artifact_version_id,
                    ordinal=ordinal,
                    text=text,
                    content_sha256=content_sha256,
                    location=chunk_location,
                    source_sha256=source.source_sha256,
                    parser_version=source.parser_version,
                    chunker_version=CHUNKER_VERSION,
                )
            )
    if not chunks:
        raise RetrievalFailure("INVALID_SOURCE", "parsed source has no indexable text")
    digest_input = {
        "indexId": index_id,
        "sourceSha256": source.source_sha256,
        "parserVersion": source.parser_version,
        "chunkerVersion": CHUNKER_VERSION,
        "chunks": [
            {
                "chunkId": chunk.chunk_id,
                "ordinal": chunk.ordinal,
                "contentSha256": chunk.content_sha256,
                "location": chunk.location,
            }
            for chunk in chunks
        ],
    }
    return SourceIndex(
        index_id=index_id,
        source=source,
        chunker_version=CHUNKER_VERSION,
        status="ready",
        index_sha256=_canonical_sha256(digest_input),
        chunks=tuple(chunks),
    )


def search_source_indexes(
    indexes: Sequence[SourceIndex],
    *,
    project_id: str,
    query: str,
    scope: RetrievalScopeContext,
    current_source_hashes: Mapping[str, str],
    limit: int = 10,
) -> SearchResult:
    _validate_scope(scope)
    _uuid(project_id, "project")
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARACTERS:
        raise RetrievalFailure("INVALID_QUERY", "search query is invalid")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        raise RetrievalFailure("INVALID_QUERY", "search limit is invalid")

    chunks: list[SourceChunk] = []
    for index in indexes:
        _assert_authorized_index(index, project_id, scope)
        if index.status != "ready":
            raise RetrievalFailure("INDEX_NOT_READY", "source index is not searchable")
        current_hash = current_source_hashes.get(index.source.artifact_version_id)
        if current_hash != index.source.source_sha256:
            raise RetrievalFailure("SOURCE_STALE", "source index freshness check failed")
        _validate_index_integrity(index)
        chunks.extend(index.chunks)

    query_terms = _tokens(query)
    if not query_terms:
        raise RetrievalFailure("INVALID_QUERY", "search query has no indexable terms")
    lexical_scores = _lexical_scores(chunks, query_terms)
    query_ngrams = _ngrams(query, NGRAM_SIZE)
    unique_terms = set(query_terms)
    candidates: list[tuple[SourceChunk, float, float, float]] = []
    for chunk, lexical_score in zip(chunks, lexical_scores):
        document_terms = set(_tokens(chunk.text))
        coverage = len(unique_terms & document_terms) / len(unique_terms)
        ngram_score = _cosine(query_ngrams, _ngrams(chunk.text, NGRAM_SIZE))
        combined = LEXICAL_WEIGHT * lexical_score + NGRAM_WEIGHT * ngram_score
        if combined > 0 and coverage >= MINIMUM_LEXICAL_COVERAGE:
            candidates.append((chunk, lexical_score, ngram_score, combined))

    lexical_order = _rank_map(candidates, 1)
    ngram_order = _rank_map(candidates, 2)
    candidates.sort(key=lambda item: (-item[3], -item[1], -item[2], item[0].chunk_id))
    hits: list[SearchHit] = []
    for rank, (chunk, lexical_score, ngram_score, combined) in enumerate(
        candidates[:limit], 1
    ):
        citation = Citation(
            index_id=chunk.index_id,
            chunk_id=chunk.chunk_id,
            artifact_version_id=chunk.artifact_version_id,
            source_sha256=chunk.source_sha256,
            content_sha256=chunk.content_sha256,
            location=chunk.location,
            parser_version=chunk.parser_version,
            chunker_version=chunk.chunker_version,
        )
        hits.append(
            SearchHit(
                rank=rank,
                lexical_rank=lexical_order[chunk.chunk_id],
                ngram_rank=ngram_order[chunk.chunk_id],
                lexical_score=round(lexical_score, 12),
                ngram_score=round(ngram_score, 12),
                combined_score=round(combined, 12),
                excerpt=chunk.text[:MAX_EXCERPT_CHARACTERS],
                citation=citation,
            )
        )
    run_sha256 = _canonical_sha256(
        {
            "mode": RETRIEVAL_MODE,
            "projectId": project_id,
            "querySha256": _sha256_text(query.strip()),
            "limit": limit,
            "indexDigests": sorted(index.index_sha256 for index in indexes),
            "resultChunkIds": [hit.citation.chunk_id for hit in hits],
            "scores": [hit.combined_score for hit in hits],
        }
    )
    return SearchResult(
        retrieval_mode=RETRIEVAL_MODE,
        scored_candidate_count=len(chunks),
        result_count=len(hits),
        run_sha256=run_sha256,
        results=tuple(hits),
    )


def withdraw_source_index(
    index: SourceIndex, scope: RetrievalScopeContext
) -> SourceIndex:
    _validate_scope(scope)
    _assert_authorized_index(index, index.source.project_id, scope)
    if index.status == "withdrawn":
        return index
    if index.status != "ready":
        raise RetrievalFailure("INDEX_NOT_READY", "source index cannot be withdrawn")
    return replace(index, status="withdrawn", chunks=())


def validate_citation(citation: Citation, index: SourceIndex) -> None:
    if index.status != "ready":
        raise RetrievalFailure("CITATION_STALE", "citation source is not active")
    if (
        citation.index_id != index.index_id
        or citation.artifact_version_id != index.source.artifact_version_id
        or citation.source_sha256 != index.source.source_sha256
        or citation.parser_version != index.source.parser_version
        or citation.chunker_version != index.chunker_version
    ):
        raise RetrievalFailure("CITATION_STALE", "citation source identity changed")
    chunk = next(
        (candidate for candidate in index.chunks if candidate.chunk_id == citation.chunk_id),
        None,
    )
    if chunk is None or (
        chunk.content_sha256 != citation.content_sha256
        or chunk.location != citation.location
        or _sha256_text(chunk.text) != chunk.content_sha256
    ):
        raise RetrievalFailure("CITATION_STALE", "citation content changed")


def _validate_scope(scope: RetrievalScopeContext) -> None:
    if not isinstance(scope, RetrievalScopeContext) or any(
        not _uuid_like(value)
        for value in (
            scope.organization_id,
            scope.workspace_id,
            scope.client_id,
            scope.actor_id,
        )
    ):
        raise RetrievalFailure("INVALID_SCOPE", "retrieval scope is invalid")


def _validate_source(source: SourceIdentity, scope: RetrievalScopeContext) -> None:
    if not isinstance(source, SourceIdentity) or any(
        not _uuid_like(value)
        for value in (
            source.organization_id,
            source.workspace_id,
            source.client_id,
            source.project_id,
            source.artifact_version_id,
        )
    ):
        raise RetrievalFailure("INVALID_SOURCE", "source identity is invalid")
    if (
        source.organization_id != scope.organization_id
        or source.workspace_id != scope.workspace_id
        or source.client_id != scope.client_id
    ):
        raise RetrievalFailure("SOURCE_NOT_FOUND", "source version was not found")
    if not SHA256_PATTERN.fullmatch(source.source_sha256):
        raise RetrievalFailure("INVALID_SOURCE", "source hash is invalid")
    if not PARSER_VERSION_PATTERN.fullmatch(source.parser_version):
        raise RetrievalFailure("INVALID_SOURCE", "parser version is invalid")


def _assert_authorized_index(
    index: SourceIndex, project_id: str, scope: RetrievalScopeContext
) -> None:
    if not isinstance(index, SourceIndex) or (
        index.source.organization_id != scope.organization_id
        or index.source.workspace_id != scope.workspace_id
        or index.source.client_id != scope.client_id
        or index.source.project_id != project_id
    ):
        raise RetrievalFailure(
            "SCOPE_CONTAMINATION", "retrieval candidates violated scope boundaries"
        )
    for chunk in index.chunks:
        if (
            chunk.index_id != index.index_id
            or chunk.organization_id != scope.organization_id
            or chunk.workspace_id != scope.workspace_id
            or chunk.client_id != scope.client_id
            or chunk.project_id != project_id
            or chunk.artifact_version_id != index.source.artifact_version_id
        ):
            raise RetrievalFailure(
                "SCOPE_CONTAMINATION", "retrieval candidates violated scope boundaries"
            )


def _validate_index_integrity(index: SourceIndex) -> None:
    expected_index_id = str(
        uuid5(
            NAMESPACE_URL,
            "marketops:source-index:"
            f"{index.source.artifact_version_id}:{index.source.source_sha256}:"
            f"{index.source.parser_version}:{index.chunker_version}",
        )
    )
    if index.index_id != expected_index_id or not index.chunks:
        raise RetrievalFailure("CONTENT_STALE", "source index integrity check failed")
    for ordinal, chunk in enumerate(index.chunks):
        expected_content_sha256 = _sha256_text(chunk.text)
        expected_chunk_id = str(
            uuid5(
                NAMESPACE_URL,
                "marketops:source-chunk:"
                f"{index.index_id}:{ordinal}:{expected_content_sha256}",
            )
        )
        if (
            chunk.ordinal != ordinal
            or chunk.content_sha256 != expected_content_sha256
            or chunk.chunk_id != expected_chunk_id
        ):
            raise RetrievalFailure(
                "CONTENT_STALE", "source chunk integrity check failed"
            )
    expected_index_sha256 = _canonical_sha256(
        {
            "indexId": index.index_id,
            "sourceSha256": index.source.source_sha256,
            "parserVersion": index.source.parser_version,
            "chunkerVersion": index.chunker_version,
            "chunks": [
                {
                    "chunkId": chunk.chunk_id,
                    "ordinal": chunk.ordinal,
                    "contentSha256": chunk.content_sha256,
                    "location": chunk.location,
                }
                for chunk in index.chunks
            ],
        }
    )
    if index.index_sha256 != expected_index_sha256:
        raise RetrievalFailure("CONTENT_STALE", "source index integrity check failed")


def _validate_location(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise RetrievalFailure("INVALID_SOURCE", "source location is invalid")
    if len(value) > MAX_LOCATION_PROPERTIES:
        raise RetrievalFailure("INVALID_SOURCE", "source location has too many fields")
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise RetrievalFailure("INVALID_SOURCE", "source location is invalid") from None
    if len(encoded) > MAX_LOCATION_BYTES:
        raise RetrievalFailure("INVALID_SOURCE", "source location is too large")
    return json.loads(encoded.decode("utf-8"))


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    return normalized.strip()


def _split_text(text: str) -> list[tuple[int, int, str]]:
    pieces: list[tuple[int, int, str]] = []
    cursor = 0
    while cursor < len(text):
        end = min(cursor + MAX_CHUNK_CHARACTERS, len(text))
        if end < len(text):
            paragraph = text.rfind("\n\n", cursor + MAX_CHUNK_CHARACTERS // 2, end)
            whitespace = text.rfind(" ", cursor + MAX_CHUNK_CHARACTERS // 2, end)
            cut = max(paragraph + 2 if paragraph >= 0 else -1, whitespace + 1 if whitespace >= 0 else -1)
            if cut > cursor:
                end = cut
        raw = text[cursor:end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        if trailing > leading:
            start = cursor + leading
            stop = cursor + trailing
            pieces.append((start, stop, text[start:stop]))
        cursor = end
    return pieces


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_PATTERN.finditer(text):
        token = match.group(0).casefold()
        if token in STOP_WORDS:
            continue
        if CJK_PATTERN.fullmatch(token):
            tokens.extend(token)
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
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
    return Counter(
        padded[index : index + size]
        for index in range(len(padded) - size + 1)
    )


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    numerator = sum(count * right.get(term, 0) for term, count in left.items())
    if not numerator:
        return 0.0
    left_norm = math.sqrt(sum(count * count for count in left.values()))
    right_norm = math.sqrt(sum(count * count for count in right.values()))
    return numerator / (left_norm * right_norm)


def _lexical_scores(chunks: Sequence[SourceChunk], query_terms: list[str]) -> list[float]:
    documents = [Counter(_tokens(chunk.text)) for chunk in chunks]
    lengths = [sum(document.values()) for document in documents]
    average_length = sum(lengths) / len(lengths) if lengths else 1.0
    document_frequency = Counter(term for document in documents for term in document)
    query_frequency = Counter(query_terms)
    count = len(documents)
    scores: list[float] = []
    for document, length in zip(documents, lengths):
        raw = 0.0
        for term, query_count in query_frequency.items():
            frequency = document.get(term, 0)
            if not frequency:
                continue
            inverse = math.log(
                1.0
                + (count - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            denominator = frequency + 1.2 * (
                1.0 - 0.75 + 0.75 * length / average_length
            )
            raw += inverse * (frequency * 2.2 / denominator) * query_count
        scores.append(raw / (raw + 1.0) if raw else 0.0)
    return scores


def _rank_map(
    candidates: Sequence[tuple[SourceChunk, float, float, float]], score_index: int
) -> Mapping[str, int]:
    ordered = sorted(candidates, key=lambda item: (-item[score_index], item[0].chunk_id))
    return {item[0].chunk_id: rank for rank, item in enumerate(ordered, 1)}


def _uuid(value: str, label: str) -> str:
    if not _uuid_like(value):
        raise RetrievalFailure("INVALID_INPUT", f"{label} id is invalid")
    return value


def _uuid_like(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value.lower()
    except (ValueError, TypeError, AttributeError):
        return False


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
