from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_learning.approval import ApprovalDecisionRequest
from apps.api.marketops_learning.approval_postgres import (
    AsyncpgKnowledgeApprovalRepository,
)
from apps.api.marketops_learning.service import LearningScopeContext


ROOT = Path(__file__).resolve().parents[3]
ADAPTER = ROOT / "apps" / "api" / "marketops_learning" / "approval_postgres.py"


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Connection:
    def __init__(self, candidate, root=None, decisions=(), citation=None, replay=None):
        self.candidate = candidate
        self.root = root
        self.decisions = decisions
        self.citation = citation
        self.replay = replay
        self.executed = []
        self.fetchrows = []
        self.fetchvals = []

    def transaction(self, **_kwargs):
        return _Transaction()

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "INSERT 0 1"

    async def fetchval(self, query, *args):
        self.fetchvals.append((query, args))
        return None

    async def fetchrow(self, query, *args):
        self.fetchrows.append((query, args))
        if "FROM marketops.knowledge_items AS item" in query:
            return self.candidate
        if "FROM marketops.knowledge_promotion_roots" in query:
            return self.root
        if "ORDER BY version DESC" in query:
            return self.decisions[-1] if self.decisions else None
        if "request_sha256 = decode" in query:
            return self.replay
        if "FROM marketops.projects" in query:
            return {"id": args[0]}
        if "FROM marketops.knowledge_citations" in query:
            return self.citation
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "knowledge_promotion_versions" in query:
            return list(self.decisions)
        raise AssertionError(query)


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class ApprovalPostgresAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.organization_id = str(uuid4())
        self.workspace_id = str(uuid4())
        self.client_id = str(uuid4())
        self.actor_id = str(uuid4())
        self.source_project_id = str(uuid4())
        self.target_project_id = str(uuid4())
        self.knowledge_id = str(uuid4())
        self.promotion_id = str(uuid4())
        self.now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        self.scope = LearningScopeContext(
            self.organization_id, self.workspace_id, self.client_id, self.actor_id
        )
        content = "Use a venue handoff rehearsal."
        self.candidate = {
            "knowledge_id": self.knowledge_id,
            "source_project_id": self.source_project_id,
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).digest(),
            "evidence_digest": b"e" * 32,
        }

    def repository(self, connection):
        ids = iter([str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())])
        return AsyncpgKnowledgeApprovalRepository(
            _Pool(connection), id_factory=lambda: next(ids), clock=lambda: self.now
        )

    async def test_initial_decision_sets_scope_locks_and_writes_only_closed_rows(self):
        connection = _Connection(self.candidate)

        result = await self.repository(connection).decide(
            self.scope,
            self.source_project_id,
            self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )

        self.assertFalse(result.replayed)
        self.assertEqual(result.history.state.current.status, "approved")
        writes = "\n".join(query for query, _args in connection.executed)
        self.assertIn("INSERT INTO marketops.knowledge_promotion_roots", writes)
        self.assertIn("INSERT INTO marketops.knowledge_promotion_versions", writes)
        self.assertTrue(
            any(
                "knowledge_promotion.decided" in args
                for query, args in connection.executed
                if "INSERT INTO marketops.audit_events" in query
            )
        )
        self.assertNotIn("UPDATE marketops.knowledge", writes)
        self.assertNotIn("DELETE FROM marketops.knowledge", writes)
        self.assertTrue(any("pg_advisory_xact_lock" in query for query, _ in connection.fetchvals))
        self.assertTrue(any("set_config('app.project_id'" in query for query, _ in connection.executed))

    async def test_citation_rechecks_source_then_switches_to_target_scope(self):
        content = self.candidate["content"]
        decision = {
            "version": 1,
            "action": "approve",
            "status": "approved",
            "effective_scope": "project",
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).digest(),
            "reason": None,
            "created_by": self.actor_id,
            "request_sha256": b"r" * 32,
            "replay_sha256": b"s" * 32,
        }
        elevated = dict(decision)
        elevated.update(
            {
                "version": 2,
                "action": "elevate",
                "effective_scope": "client",
                "reason": "Approved for this client.",
                "request_sha256": b"t" * 32,
                "replay_sha256": b"u" * 32,
            }
        )
        connection = _Connection(
            self.candidate,
            {"id": self.promotion_id, "knowledge_id": self.knowledge_id},
            (decision, elevated),
        )

        result = await self.repository(connection).cite_approved_knowledge(
            self.scope,
            source_project_id=self.source_project_id,
            source_knowledge_id=self.knowledge_id,
            target_project_id=self.target_project_id,
            reason="Apply the approved control.",
        )

        self.assertFalse(result.replayed)
        self.assertEqual(result.citation.authorization.promotion_version, 2)
        scope_sets = [args[2] for query, args in connection.executed if "set_config('app.project_id'" in query]
        self.assertEqual(
            scope_sets,
            [self.source_project_id, self.target_project_id, self.source_project_id],
        )
        writes = "\n".join(query for query, _args in connection.executed)
        self.assertIn("INSERT INTO marketops.knowledge_citations", writes)
        self.assertTrue(
            any(
                "knowledge_citation.created" in args
                for query, args in connection.executed
                if "INSERT INTO marketops.audit_events" in query
            )
        )
        self.assertNotIn("SELECT *", "\n".join(query for query, _args in connection.fetchrows))

    async def test_exact_prior_request_replays_its_historical_snapshot(self):
        content = self.candidate["content"]
        prior = {
            "version": 1,
            "action": "approve",
            "status": "approved",
            "effective_scope": "project",
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).digest(),
            "reason": None,
            "created_by": self.actor_id,
            "request_sha256": b"r" * 32,
            "replay_sha256": b"s" * 32,
        }
        current = dict(prior)
        current.update(
            {
                "version": 2,
                "action": "revoke",
                "status": "revoked",
                "effective_scope": None,
                "content": None,
                "content_sha256": None,
                "reason": "Superseded.",
                "request_sha256": b"t" * 32,
                "replay_sha256": b"u" * 32,
            }
        )
        connection = _Connection(
            self.candidate,
            {"id": self.promotion_id, "knowledge_id": self.knowledge_id},
            (prior, current),
            replay={"version": 1},
        )

        result = await self.repository(connection).decide(
            self.scope,
            self.source_project_id,
            self.knowledge_id,
            ApprovalDecisionRequest("approve", 0, "project"),
        )

        self.assertTrue(result.replayed)
        self.assertEqual(result.history.state.version, 1)
        self.assertEqual(result.history.state.current.status, "approved")
        self.assertFalse(
            any("INSERT INTO marketops.knowledge_promotion_versions" in query for query, _ in connection.executed)
        )

    def test_adapter_has_scope_switching_locks_and_no_generic_cross_project_listing(self):
        adapter = ADAPTER.read_text(encoding="utf-8")
        for marker in (
            "set_config('app.workspace_id', $1, true)",
            "set_config('app.client_id', $2, true)",
            "set_config('app.project_id', $3, true)",
            "set_config('app.actor_id', $4, true)",
            "pg_catalog.pg_advisory_xact_lock",
            "decode($12, 'hex')",
            "decode($14, 'hex')",
            "knowledge_citation.created",
            "knowledge_promotion.decided",
            "async def cite_approved_knowledge",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, adapter)
        self.assertNotIn("async def list_eligible", adapter)
        self.assertNotIn("postgresql://", adapter)


if __name__ == "__main__":
    unittest.main()
