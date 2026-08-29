from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_brief import BriefResearchError, BriefResearchService, BriefScope


def uid() -> str:
    return str(uuid4())


class BriefResearchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = BriefResearchService(Path(self.temp.name))
        self.scope = BriefScope(uid(), uid(), uid(), uid())

    async def asyncTearDown(self):
        self.temp.cleanup()

    def brief(self, **changes):
        value = {
            "deidentified": True, "productName": "工业连接器方案", "productType": "B2B 制造业产品",
            "targetMarket": "印度，英语", "audience": "制造业采购与海外业务负责人",
            "objective": "形成首轮市场进入方案", "timeframe": "未来十周",
            "background": "需要明确市场进入路径", "constraints": ["不使用真实客户资料"],
        }
        value.update(changes)
        return value

    async def test_brief_missing_questions_and_scope_isolation(self):
        brief = await self.service.create_brief(self.scope, self.brief(objective=""))
        self.assertEqual(brief["status"], "needs_clarification")
        self.assertIn("本轮希望推动的业务动作是什么？", brief["missingQuestions"])
        self.assertEqual(await self.service.list_briefs(BriefScope(uid(), uid(), uid(), uid())), [])
        source = {"url": "https://example.com/market", "title": "Official source", "excerpt": "A bounded observation.", "observedAt": "2026-08-21", "scope": "India manufacturing", "confidence": "medium"}
        with self.assertRaisesRegex(BriefResearchError, "complete the Brief"):
            await self.service.create_research_run(self.scope, {"briefId": brief["briefId"], "sources": [source], "observations": [{"claim": "x", "classification": "fact", "confidence": "medium"}]})

    async def test_deidentification_and_citation_boundaries(self):
        with self.assertRaisesRegex(BriefResearchError, "deidentified"):
            await self.service.create_brief(self.scope, self.brief(deidentified=False))
        with self.assertRaises(BriefResearchError):
            await self.service.create_brief(self.scope, self.brief(contactName="Someone"))
        brief = await self.service.create_brief(self.scope, self.brief())
        source = {"url": "https://example.com/market", "title": "Official source", "excerpt": "A bounded observation.", "observedAt": "2026-08-21", "scope": "India manufacturing", "confidence": "medium"}
        with self.assertRaises(BriefResearchError):
            await self.service.create_research_run(self.scope, {"briefId": brief["briefId"], "sources": [source], "observations": [{"claim": "Observed fact", "classification": "fact", "confidence": "medium", "sourceId": "bad"}]})
        bad_date = {**source, "observedAt": "not-a-date"}
        with self.assertRaisesRegex(BriefResearchError, "ISO date"):
            await self.service.create_research_run(self.scope, {"briefId": brief["briefId"], "sources": [bad_date], "observations": [{"claim": "Observed fact", "classification": "fact", "confidence": "medium"}]})

    async def test_research_proposal_and_approval_never_changes_brief(self):
        brief = await self.service.create_brief(self.scope, self.brief())
        source = {"url": "https://example.com/market", "title": "Official source", "excerpt": "A bounded observation.", "observedAt": "2026-08-21", "scope": "India manufacturing", "confidence": "high"}
        draft_input = {"briefId": brief["briefId"], "sources": [source], "observations": [{"claim": "Observed fact", "classification": "fact", "confidence": "high", "nextAction": "人工核对"}]}
        run = await self.service.create_research_run(self.scope, draft_input)
        draft = await self.service.create_proposal_draft(self.scope, {"briefId": brief["briefId"], "researchRunId": run["runId"]})
        self.assertEqual(draft["status"], "needs_review")
        self.assertEqual(draft["sections"]["positioning"][0]["sources"][0]["url"], source["url"])
        approved = await self.service.decide_proposal_draft(self.scope, draft["draftId"], {"expectedVersion": 1, "action": "approve", "reason": "人工确认来源适用范围"})
        self.assertEqual(approved["status"], "approved")
        self.assertEqual((await self.service.get_brief(self.scope, brief["briefId"]))["status"], "ready")
        with self.assertRaises(BriefResearchError):
            await self.service.decide_proposal_draft(self.scope, draft["draftId"], {"expectedVersion": 1, "action": "reject", "reason": "stale"})
        with self.assertRaisesRegex(BriefResearchError, "finalized"):
            await self.service.decide_proposal_draft(self.scope, draft["draftId"], {"expectedVersion": 2, "action": "reject", "reason": "cannot overwrite"})
