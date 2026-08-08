#!/usr/bin/env python3
"""Independent black-box acceptance check for the M0-04 retrieval spike."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_PATH = ROOT / "scripts" / "hybrid_retrieval_spike.py"
FIXTURE_DIR = ROOT / "validation" / "fixtures" / "retrieval-spike-001"
CORPUS_PATH = FIXTURE_DIR / "corpus.json"
GOLDEN_PATH = FIXTURE_DIR / "golden-queries.json"
RESULT_PATH = ROOT / "validation" / "results" / "m0-04-hybrid-retrieval.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("m0_04_retrieval_under_test", IMPLEMENTATION_PATH)
    require(spec is not None and spec.loader is not None, "retrieval implementation is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("load_corpus", "retrieve", "validate_citations", "RetrievalFailure"):
        require(hasattr(module, name), f"public retrieval API is missing {name}")
    return module


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path.relative_to(ROOT)} must contain an object")
    return value


def adapt_query(case: dict[str, Any]) -> dict[str, Any]:
    """Map frozen fixture names into the explicitly accepted public query API."""

    value = case.get("input")
    require(isinstance(value, dict), f"fixture case {case.get('id')!r} lacks an input object")
    query = dict(value)
    query_id = case.get("id")
    require(isinstance(query_id, str) and query_id, "fixture query id must be non-empty")
    text = query.pop("queryText", None)
    require(isinstance(text, str) and text, f"fixture case {query_id} lacks queryText")
    query["queryId"] = query_id
    query["text"] = text
    return query


def failure_code(action: Callable[[], Any], expected: str) -> str:
    try:
        action()
    except MODULE.RetrievalFailure as error:
        require(error.code == expected, f"expected {expected}, got {error.code}")
        return error.code
    raise AssertionError(f"expected observable failure {expected}")


def result_ids(response: dict[str, Any]) -> list[str]:
    results = response.get("results")
    require(isinstance(results, list), "retrieval response lacks results array")
    return [item.get("chunkId") for item in results]


def require_citations(response: dict[str, Any], corpus_by_id: dict[str, dict[str, Any]]) -> None:
    required_fields = {
        "chunkId", "workspaceId", "clientId", "projectId", "visibility", "sourcePath",
        "sourceVersionSha256", "location", "excerpt", "lexicalScore", "semanticProxyScore",
        "hybridScore", "rank",
    }
    for index, result in enumerate(response["results"], start=1):
        missing = sorted(required_fields - result.keys())
        require(not missing, f"result {result.get('chunkId')!r} is missing fields: {missing}")
        require(result["rank"] == index, f"result {result['chunkId']} has unstable rank")
        source = corpus_by_id.get(result["chunkId"])
        require(source is not None, f"unknown result chunk {result['chunkId']}")
        for field in ("workspaceId", "clientId", "projectId", "visibility", "sourcePath", "sourceVersionSha256", "location"):
            require(result[field] == source.get(field), f"result {result['chunkId']} citation {field} differs from corpus")
        require(result["excerpt"] == source["text"], f"result {result['chunkId']} excerpt differs from corpus")
        for field in ("lexicalScore", "semanticProxyScore", "hybridScore"):
            require(isinstance(result[field], (int, float)) and not isinstance(result[field], bool), f"result {result['chunkId']} {field} is not numeric")


def check_oracle(case: dict[str, Any], corpus: dict[str, Any], corpus_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    oracle = case.get("oracle")
    require(isinstance(oracle, dict), f"fixture case {case.get('id')!r} lacks an oracle")
    query = adapt_query(case)
    status = oracle.get("expectedStatus")
    if status == "validation_error":
        expected = oracle.get("expectedErrorCode")
        actual = failure_code(lambda: MODULE.retrieve(corpus, query), str(expected))
        return {"queryId": query["queryId"], "status": "validation_error", "errorCode": actual}

    if status == "empty":
        try:
            response = MODULE.retrieve(corpus, query)
        except MODULE.RetrievalFailure as error:
            require(error.code in {"unknown_workspace", "unknown_client", "unknown_project"}, f"unexpected empty-path failure: {error.code}")
            return {"queryId": query["queryId"], "status": "empty", "errorCode": error.code}
        require(not response["results"], f"empty case {query['queryId']} returned {result_ids(response)}")
        return {"queryId": query["queryId"], "status": "empty", "errorCode": None}

    require(status == "ok", f"unsupported fixture status {status!r}")
    response = MODULE.retrieve(corpus, query)
    ids = result_ids(response)
    require_citations(response, corpus_by_id)
    required = set(oracle.get("requiredChunkIds", []))
    allowed = set(oracle.get("allowedChunkIds", []))
    forbidden = set(oracle.get("forbiddenChunkIds", []))
    require(required <= set(ids), f"{query['queryId']} missed required chunks {sorted(required - set(ids))}")
    require(set(ids) <= allowed, f"{query['queryId']} returned out-of-scope chunks {sorted(set(ids) - allowed)}")
    require(not (set(ids) & forbidden), f"{query['queryId']} returned forbidden chunks {sorted(set(ids) & forbidden)}")
    repeat_runs = oracle.get("repeatRuns", 1)
    baseline = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for _ in range(1, repeat_runs):
        require(json.dumps(MODULE.retrieve(corpus, query), ensure_ascii=False, sort_keys=True, separators=(",", ":")) == baseline, f"{query['queryId']} is not deterministic")
    expected_order = oracle.get("expectedOrderedChunkIds")
    if expected_order is not None:
        require(ids == expected_order, f"{query['queryId']} order changed: {ids}")
    return {"queryId": query["queryId"], "status": "ok", "resultChunkIds": ids}


def check_scope_precedes_scoring(corpus: dict[str, Any]) -> None:
    query = adapt_query(next(case for case in GOLDEN["queries"] if case["id"] == "cross-workspace-rare-decoy"))
    response = MODULE.retrieve(corpus, query)
    expected_scoped_count = sum(
        1
        for chunk in corpus["chunks"]
        if chunk["workspaceId"] == query["workspaceId"]
        and chunk.get("clientId") == query["clientId"]
        and chunk.get("projectId") == query["projectId"]
        and chunk["visibility"] == "project"
    )
    require(
        response["scoredCandidateCount"] == expected_scoped_count,
        "out-of-scope chunks entered the scoring candidate set",
    )


def check_stale_citation(corpus: dict[str, Any]) -> dict[str, Any]:
    case = next(case for case in GOLDEN["queries"] if case["id"] == "source-hash-freshness")
    response = MODULE.retrieve(corpus, adapt_query(case))
    require(MODULE.validate_citations(response, ROOT)["valid"], "fresh citation must validate")
    mutation = case["oracle"]["citationIntegrity"]["tamperInTemporaryCopy"]
    with tempfile.TemporaryDirectory() as directory:
        temp_root = Path(directory) / "repo"
        temp_root.mkdir()
        relative_source = Path(case["oracle"]["citationIntegrity"]["expectedSourcePath"])
        target = temp_root / relative_source
        target.parent.mkdir(parents=True)
        shutil.copy2(ROOT / relative_source, target)
        with target.open("a", encoding="utf-8", newline="") as output:
            output.write(mutation["text"])
        validation = MODULE.validate_citations(response, temp_root)
    errors = validation["errors"]
    require(not validation["valid"], "tampered source must invalidate prior retrieval citations")
    require(any(error["code"] == "source_hash_mismatch" for error in errors), "tampered source did not report hash mismatch")
    expected_stale = case["oracle"]["citationIntegrity"]["expectedStaleStatus"]
    require(expected_stale == "source_hash_mismatch", "fixture stale error code differs from public API")
    return {"fresh": True, "staleErrorCode": expected_stale}


def check_malformed_citation_fails_closed(corpus: dict[str, Any]) -> str:
    case = next(case for case in GOLDEN["queries"] if case["id"] == "same-project-cited-result")
    response = MODULE.retrieve(corpus, adapt_query(case))
    malformed = copy.deepcopy(response)
    malformed["results"][0]["sourcePath"] = None
    validation = MODULE.validate_citations(malformed, ROOT)
    require(not validation["valid"], "non-string sourcePath must fail closed")
    require(
        any(error["code"] == "invalid_citation" for error in validation["errors"]),
        "non-string sourcePath did not return a structured invalid_citation error",
    )
    return "invalid_citation"


def check_cli() -> None:
    case = next(case for case in GOLDEN["queries"] if case["id"] == "same-project-cited-result")
    completed = subprocess.run(
        [sys.executable, str(IMPLEMENTATION_PATH), "--corpus", str(CORPUS_PATH), "--query-file", str(GOLDEN_PATH), "--query-id", case["id"]],
        cwd=ROOT, check=False, capture_output=True, text=True, encoding="utf-8",
    )
    require(completed.returncode == 0, f"CLI representative query failed: {completed.stderr or completed.stdout}")
    response = json.loads(completed.stdout)
    require(response.get("citationValidation", {}).get("valid") is True, "CLI did not report valid citations")


def build_result() -> dict[str, Any]:
    corpus = MODULE.load_corpus(CORPUS_PATH)
    corpus_by_id = {chunk["chunkId"]: chunk for chunk in corpus["chunks"]}
    cases = [check_oracle(case, corpus, corpus_by_id) for case in GOLDEN["queries"]]
    check_scope_precedes_scoring(corpus)
    freshness = check_stale_citation(corpus)
    malformed_citation_error = check_malformed_citation_fails_closed(corpus)
    check_cli()
    return {
        "schemaVersion": 1,
        "spikeId": "M0-04",
        "fixture": str(FIXTURE_DIR.relative_to(ROOT).as_posix()),
        "corpusSha256": hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest(),
        "goldenQueriesSha256": hashlib.sha256(GOLDEN_PATH.read_bytes()).hexdigest(),
        "cases": cases,
        "scopeFilteringVerifiedBeforeScoring": True,
        "citationFreshness": freshness,
        "malformedCitationErrorCode": malformed_citation_error,
        "cliRepresentativeQueryPassed": True,
    }


def main() -> None:
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
        require(RESULT_PATH.exists(), "committed M0-04 result is missing; run with --write")
        require(RESULT_PATH.read_text(encoding="utf-8") == serialized, "M0-04 result is stale; run with --write")
        print(f"Hybrid retrieval QA passed: {len(result['cases'])} oracle cases, scope-order, freshness, and CLI checks.")


if __name__ == "__main__":
    MODULE = load_module()
    GOLDEN = load_json(GOLDEN_PATH)
    main()
