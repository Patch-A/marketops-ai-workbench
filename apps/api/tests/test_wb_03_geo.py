from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_geo import GeoError, GeoScope, GeoSnapshotService


def uid() -> str:
    return str(uuid4())


class GeoSnapshotServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = GeoSnapshotService(Path(self.temp.name))
        self.scope = GeoScope(uid(), uid(), uid(), uid())

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_query_set_and_snapshot_create_gap_task(self):
        query_set = await self.service.create_query_set(self.scope, {
            "product": "工业连接器方案", "market": "印度", "language": "中文 / English",
            "queries": ["印度工业连接器供应商怎么选？", "industrial connector supplier India"],
        })
        self.assertEqual(len(query_set["queries"]), 2)
        result = await self.service.create_snapshot(self.scope, query_set["querySetId"], {
            "platform": "ChatGPT", "queryId": query_set["queries"][0]["queryId"],
            "visibility": "not_mentioned", "observation": "未提到目标方案",
            "observedAt": "2026-08-29", "citation": "https://example.com/answer",
        })
        self.assertEqual(result["snapshot"]["visibility"], "not_mentioned")
        self.assertEqual(result["task"]["status"], "needs_review")
        self.assertEqual(len(await self.service.list_snapshots(self.scope, query_set["querySetId"])), 1)

    async def test_scope_and_validation_fail_closed(self):
        with self.assertRaises(GeoError):
            await self.service.create_query_set(self.scope, {
                "product": "x", "market": "y", "language": "z", "queries": ["same", "same"],
            })
        query_set = await self.service.create_query_set(self.scope, {
            "product": "x", "market": "y", "language": "z", "queries": ["q"],
        })
        with self.assertRaises(GeoError):
            await self.service.create_snapshot(self.scope, query_set["querySetId"], {
                "platform": "ChatGPT", "queryId": query_set["queries"][0]["queryId"],
                "visibility": "mentioned", "observation": "answer", "observedAt": "2026-08-29",
                "citation": "javascript:alert(1)",
            })
        other = GeoScope(uid(), self.scope.workspace_id, self.scope.client_id, self.scope.actor_id)
        self.assertEqual(await self.service.list_query_sets(other), [])
