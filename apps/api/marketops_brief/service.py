"""A bounded local vertical slice for Brief -> research -> proposal draft.

The service deliberately accepts only deidentified input and user-supplied
sources. It does not call a model or fetch the public web, and proposal drafts
never mutate the existing project or approved proposal facts.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from uuid import UUID, uuid4


class BriefResearchError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BriefScope:
    organization_id: str
    workspace_id: str
    client_id: str
    actor_id: str


_BRIEF_FIELDS = (
    "deidentified", "productName", "productType", "targetMarket", "audience",
    "objective", "timeframe", "background", "constraints",
)
_MISSING_QUESTIONS = {
    "productName": "产品或解决方案的去标识名称是什么？",
    "productType": "产品属于什么类型？",
    "targetMarket": "首个目标市场、地区和语言是什么？",
    "audience": "要影响的决策者或使用者是谁？",
    "objective": "本轮希望推动的业务动作是什么？",
    "timeframe": "希望在什么时间范围内推进？",
}
_IDENTIFIER_KEYS = {"email", "phone", "mobile", "contactName", "realName", "customerName", "companyName"}
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_URL_SCHEMES = {"http", "https"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid(value: Any, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise BriefResearchError("INVALID_INPUT", f"{field} must be a UUID") from exc


def _text(value: Any, field: str, maximum: int = 4000) -> str:
    if not isinstance(value, str):
        raise BriefResearchError("INVALID_INPUT", f"{field} must be text")
    result = value.strip()
    if not result or len(result) > maximum:
        raise BriefResearchError("INVALID_INPUT", f"{field} is outside its allowed length")
    return result


def _brief_text(value: Any, field: str, maximum: int = 4000) -> str:
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise BriefResearchError("INVALID_INPUT", f"{field} is outside its allowed length")
    return value.strip()


def _scope(scope: BriefScope) -> BriefScope:
    return BriefScope(*(_uuid(value, field) for value, field in zip(
        (scope.organization_id, scope.workspace_id, scope.client_id, scope.actor_id),
        ("organizationId", "workspaceId", "clientId", "actorId"),
    )))


def _scope_match(item: Mapping[str, Any], scope: BriefScope) -> bool:
    return all(item[key] == value for key, value in (
        ("organizationId", scope.organization_id), ("workspaceId", scope.workspace_id),
        ("clientId", scope.client_id), ("createdBy", scope.actor_id),
    ))


def _deidentified(value: Any) -> bool:
    if value is True:
        return True
    raise BriefResearchError("DEIDENTIFICATION_REQUIRED", "only deidentified Briefs are accepted")


def _check_no_identifiers(value: Any, path: str = "brief") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in _IDENTIFIER_KEYS:
                raise BriefResearchError("DEIDENTIFICATION_REQUIRED", f"{path}.{key} is not allowed")
            _check_no_identifiers(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _check_no_identifiers(nested, f"{path}[{index}]")
    elif isinstance(value, str) and _EMAIL.search(value):
        raise BriefResearchError("DEIDENTIFICATION_REQUIRED", f"{path} contains an email address")


def _url(value: Any) -> str:
    result = _text(value, "source.url", 1000)
    parsed = urlparse(result)
    if parsed.scheme not in _URL_SCHEMES or not parsed.netloc or parsed.username or parsed.password:
        raise BriefResearchError("INVALID_SOURCE", "source.url must be a public HTTP(S) URL without credentials")
    return result


class BriefResearchService:
    def __init__(self, root: Path, *, id_factory=uuid4, clock=_now):
        self._path = Path(root) / "brief-research" / "records.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._id_factory = id_factory
        self._clock = clock
        self._lock = asyncio.Lock()

    async def create_brief(self, scope: BriefScope, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        _check_no_identifiers(payload)
        if set(payload) != set(_BRIEF_FIELDS):
            raise BriefResearchError("INVALID_INPUT", "Brief fields are incomplete or unknown")
        _deidentified(payload["deidentified"])
        brief = {
            "briefId": self._new_id(), "organizationId": scope.organization_id,
            "workspaceId": scope.workspace_id, "clientId": scope.client_id,
            "createdBy": scope.actor_id, "createdAt": self._clock(), "version": 1,
            "deidentified": True,
        }
        for field in _BRIEF_FIELDS[1:]:
            value = payload[field]
            if field == "constraints":
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise BriefResearchError("INVALID_INPUT", "constraints must be a list of text")
                brief[field] = [item.strip() for item in value if item.strip()][:20]
            else:
                brief[field] = _brief_text(value, field)
        brief["missingQuestions"] = [question for field, question in _MISSING_QUESTIONS.items() if not brief[field]]
        brief["status"] = "needs_clarification" if brief["missingQuestions"] else "ready"
        async with self._lock:
            records = self._read()
            records["briefs"].append(brief)
            self._write(records)
        return self._public_brief(brief)

    async def list_briefs(self, scope: BriefScope) -> list[dict[str, Any]]:
        scope = _scope(scope)
        async with self._lock:
            records = self._read()
        return [self._public_brief(item) for item in records["briefs"] if _scope_match(item, scope)]

    async def get_brief(self, scope: BriefScope, brief_id: str) -> dict[str, Any]:
        item = await self._find(scope, "briefs", brief_id, "briefId")
        return self._public_brief(item)

    async def create_research_run(self, scope: BriefScope, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        if set(payload) != {"briefId", "sources", "observations"}:
            raise BriefResearchError("INVALID_INPUT", "research run fields are incomplete or unknown")
        brief = await self._find(scope, "briefs", payload["briefId"], "briefId")
        if brief["status"] != "ready":
            raise BriefResearchError("BRIEF_NOT_READY", "complete the Brief missing questions before starting research")
        sources = self._sources(payload["sources"])
        observations = self._observations(payload["observations"], {item["sourceId"] for item in sources})
        run = {
            "runId": self._new_id(), "briefId": brief["briefId"], "organizationId": scope.organization_id,
            "workspaceId": scope.workspace_id, "clientId": scope.client_id, "createdBy": scope.actor_id,
            "createdAt": self._clock(), "status": "needs_review", "sourceCount": len(sources),
            "researchTask": {"taskId": self._new_id(), "type": "research", "status": "completed",
                             "query": f"{brief['productType']} in {brief['targetMarket']}",
                             "note": "结果来自用户提供的来源和观察，未执行外部抓取。"},
            "sources": sources, "observations": observations,
        }
        async with self._lock:
            records = self._read()
            records["researchRuns"].append(run)
            self._write(records)
        return self._public_run(run)

    async def get_research_run(self, scope: BriefScope, run_id: str) -> dict[str, Any]:
        return self._public_run(await self._find(scope, "researchRuns", run_id, "runId"))

    async def create_proposal_draft(self, scope: BriefScope, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        if set(payload) != {"briefId", "researchRunId"}:
            raise BriefResearchError("INVALID_INPUT", "proposal draft fields are incomplete or unknown")
        brief = await self._find(scope, "briefs", payload["briefId"], "briefId")
        run = await self._find(scope, "researchRuns", payload["researchRunId"], "runId")
        if run["briefId"] != brief["briefId"]:
            raise BriefResearchError("INVALID_INPUT", "research run does not belong to Brief")
        sources_by_id = {source["sourceId"]: source for source in run["sources"]}
        claims = []
        for item in run["observations"]:
            source = sources_by_id.get(item.get("sourceId"))
            claims.append({"text": item["claim"], "classification": item["classification"],
                           "sourceIds": [item["sourceId"]] if item.get("sourceId") else [],
                           "sources": [source] if source else [], "confidence": item["confidence"]})
        draft = {
            "draftId": self._new_id(), "briefId": brief["briefId"], "researchRunId": run["runId"],
            "organizationId": scope.organization_id, "workspaceId": scope.workspace_id,
            "clientId": scope.client_id, "createdBy": scope.actor_id, "createdAt": self._clock(),
            "version": 1, "status": "needs_review", "decision": None, "decisionHistory": [],
            "sections": {
                "objective": brief["objective"], "audience": brief["audience"],
                "market": brief["targetMarket"], "positioning": claims,
                "channels": [], "contentIdeas": [item["nextAction"] for item in run["observations"] if item.get("nextAction")],
                "dependencies": ["人工确认来源适用范围", "人工确认渠道与资源"],
                "risks": ["来源覆盖有限，不能推断全市场", "未确认信息不会自动写入正式项目事实"],
                "metrics": ["由用户在审核时补充可测指标"],
                "unknowns": brief["missingQuestions"] or ["预算、供应商和转化率尚未提供"],
            },
        }
        async with self._lock:
            records = self._read()
            records["proposalDrafts"].append(draft)
            self._write(records)
        return self._public_draft(draft)

    async def get_proposal_draft(self, scope: BriefScope, draft_id: str) -> dict[str, Any]:
        return self._public_draft(await self._find(scope, "proposalDrafts", draft_id, "draftId"))

    async def decide_proposal_draft(self, scope: BriefScope, draft_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = _scope(scope)
        if set(payload) != {"expectedVersion", "action", "reason"}:
            raise BriefResearchError("INVALID_INPUT", "decision fields are incomplete or unknown")
        expected = payload["expectedVersion"]
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise BriefResearchError("INVALID_INPUT", "expectedVersion must be a positive integer")
        action = payload["action"]
        if action not in {"approve", "revise", "reject"}:
            raise BriefResearchError("INVALID_INPUT", "action must be approve, revise, or reject")
        reason = _text(payload["reason"], "reason", 2000)
        async with self._lock:
            records = self._read()
            draft = next((item for item in records["proposalDrafts"] if item["draftId"] == str(draft_id) and _scope_match(item, scope)), None)
            if draft is None:
                raise BriefResearchError("PROPOSAL_NOT_FOUND", "proposal draft was not found")
            if draft["version"] != expected:
                raise BriefResearchError("PROPOSAL_CONFLICT", "proposal draft changed and must be refreshed")
            if draft["status"] not in {"needs_review", "needs_revision"}:
                raise BriefResearchError("PROPOSAL_INVALID_TRANSITION", "a finalized proposal draft cannot be decided again")
            status = {"approve": "approved", "revise": "needs_revision", "reject": "rejected"}[action]
            if draft.get("decision"):
                draft.setdefault("decisionHistory", []).append(draft["decision"])
            draft["status"] = status
            draft["version"] += 1
            draft["decision"] = {"action": action, "reason": reason, "decidedAt": self._clock(), "decidedBy": scope.actor_id}
            self._write(records)
            return self._public_draft(draft)

    async def _find(self, scope: BriefScope, collection: str, value: Any, key: str) -> dict[str, Any]:
        scope = _scope(scope)
        identifier = _uuid(value, key)
        async with self._lock:
            records = self._read()
        item = next((entry for entry in records[collection] if entry[key] == identifier and _scope_match(entry, scope)), None)
        if item is None:
            raise BriefResearchError({"briefs": "BRIEF_NOT_FOUND", "researchRuns": "RESEARCH_NOT_FOUND", "proposalDrafts": "PROPOSAL_NOT_FOUND"}[collection], f"{collection} item was not found")
        return item

    def _sources(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value or len(value) > 30:
            raise BriefResearchError("INVALID_SOURCE", "at least one source is required")
        result = []
        for item in value:
            if not isinstance(item, Mapping) or set(item) != {"url", "title", "excerpt", "observedAt", "scope", "confidence"}:
                raise BriefResearchError("INVALID_SOURCE", "source must include URL, title, excerpt, time, scope, and confidence")
            observed_at = _text(item["observedAt"], "source.observedAt", 80)
            try:
                date.fromisoformat(observed_at)
            except ValueError as exc:
                raise BriefResearchError("INVALID_SOURCE", "source.observedAt must be an ISO date") from exc
            result.append({"sourceId": self._new_id(), "url": _url(item["url"]), "title": _text(item["title"], "source.title", 300),
                           "excerpt": _text(item["excerpt"], "source.excerpt", 2000), "observedAt": observed_at,
                           "scope": _text(item["scope"], "source.scope", 500), "confidence": self._confidence(item["confidence"])})
        return result

    def _observations(self, value: Any, source_ids: set[str]) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value or len(value) > 50:
            raise BriefResearchError("INVALID_INPUT", "at least one observation is required")
        result = []
        for item in value:
            if not isinstance(item, Mapping) or not set(item).issubset({"claim", "classification", "sourceId", "confidence", "nextAction"}) or "claim" not in item or "classification" not in item or "confidence" not in item:
                raise BriefResearchError("INVALID_INPUT", "observation fields are invalid")
            classification = item["classification"]
            if classification not in {"fact", "research_observation", "hypothesis", "unknown"}:
                raise BriefResearchError("INVALID_INPUT", "unsupported observation classification")
            source_id = item.get("sourceId")
            if source_id is None and classification in {"fact", "research_observation"} and len(source_ids) == 1:
                source_id = next(iter(source_ids))
            if classification in {"fact", "research_observation"} and source_id not in source_ids:
                raise BriefResearchError("CITATION_REQUIRED", "fact and research_observation need a sourceId")
            if source_id is not None and source_id not in source_ids:
                raise BriefResearchError("INVALID_SOURCE", "observation sourceId is unknown")
            result.append({"observationId": self._new_id(), "claim": _text(item["claim"], "observation.claim", 1200),
                           "classification": classification, "sourceId": source_id, "confidence": self._confidence(item["confidence"]),
                           "nextAction": _text(item.get("nextAction", "需人工确认"), "observation.nextAction", 500)})
        return result

    @staticmethod
    def _confidence(value: Any) -> str:
        if value not in {"low", "medium", "high"}:
            raise BriefResearchError("INVALID_INPUT", "confidence must be low, medium, or high")
        return value

    def _new_id(self) -> str:
        return _uuid(str(self._id_factory()), "id")

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self._path.exists():
            return {"briefs": [], "researchRuns": [], "proposalDrafts": []}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping):
                raise ValueError
            return {name: list(value.get(name, [])) for name in ("briefs", "researchRuns", "proposalDrafts")}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise BriefResearchError("BRIEF_STORE_FAILED", "brief store is unreadable") from exc

    def _write(self, records: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        payload = {"schemaVersion": 1, **{name: list(values) for name, values in records.items()}}
        handle, name = tempfile.mkstemp(prefix="briefs-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self._path)
        except OSError as exc:
            try: os.unlink(name)
            except OSError: pass
            raise BriefResearchError("BRIEF_STORE_FAILED", "brief store could not be written") from exc

    @staticmethod
    def _public_brief(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in ("briefId", "createdAt", "version", "deidentified", *_BRIEF_FIELDS[1:], "missingQuestions", "status")}

    @staticmethod
    def _public_run(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in ("runId", "briefId", "createdAt", "status", "sourceCount", "researchTask", "sources", "observations")}

    @staticmethod
    def _public_draft(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in ("draftId", "briefId", "researchRunId", "createdAt", "version", "status", "decision", "decisionHistory", "sections")}
