from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_calendar import CalendarError, CalendarItemService, CalendarScope
from apps.api.marketops_content import ContentAssetService, ContentError, ContentScope


def uid() -> str:
    return str(uuid4())


class WB04ContentCalendarTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.content = ContentAssetService(Path(self.temp.name))
        self.calendar = CalendarItemService(Path(self.temp.name))
        self.scope = ContentScope(uid(), uid(), uid(), uid())

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_content_approval_gate_image_authorization_and_conflict(self):
        brief = await self.content.create_brief(self.scope, {"topic": "印度市场指南", "channel": "知乎", "format": "长文", "audience": "决策者"})
        with self.assertRaisesRegex(ContentError, "approve"):
            await self.content.create_asset(self.scope, {"briefId": brief["briefId"], "title": "正文", "channel": "知乎", "format": "长文", "assetType": "content"})
        approved = await self.content.approve_brief(self.scope, brief["briefId"], {"expectedVersion": 1})
        image = await self.content.create_asset(self.scope, {"briefId": approved["briefId"], "title": "封面", "channel": "图片资产", "format": "提示词任务", "assetType": "image", "prompt": "工业场景"})
        self.assertEqual(image["status"], "needs_authorization")
        revised = await self.content.create_brief(self.scope, {"briefId": approved["briefId"], "expectedVersion": 1, "topic": "新版", "channel": "知乎", "format": "长文", "audience": "决策者"})
        self.assertEqual(revised["version"], 2)
        with self.assertRaisesRegex(ContentError, "changed"):
            await self.content.create_brief(self.scope, {"briefId": approved["briefId"], "expectedVersion": 1, "topic": "过期", "channel": "知乎", "format": "长文", "audience": "决策者"})
        other = ContentScope(self.scope.organization_id, self.scope.workspace_id, self.scope.client_id, uid())
        self.assertEqual(await self.content.list_assets(other), [])

    async def test_calendar_date_scope_and_optimistic_confirmation(self):
        scope = CalendarScope(self.scope.organization_id, self.scope.workspace_id, self.scope.client_id, self.scope.actor_id)
        item = await self.calendar.create_item(scope, {"title": "审核", "date": date.today().isoformat(), "source": "人工安排", "note": ""})
        self.assertEqual(item["status"], "draft")
        confirmed = await self.calendar.update_item(scope, item["itemId"], {"expectedVersion": 1, "status": "confirmed"})
        self.assertEqual(confirmed["status"], "confirmed")
        with self.assertRaisesRegex(CalendarError, "changed"):
            await self.calendar.update_item(scope, item["itemId"], {"expectedVersion": 1, "status": "draft"})
        future = await self.calendar.create_item(scope, {"title": "远期", "date": (date.today() + timedelta(days=40)).isoformat(), "source": "人工安排", "note": ""})
        self.assertEqual(len(await self.calendar.list_items(scope, "week")), 1)
        self.assertEqual(len(await self.calendar.list_items(scope, "month")), 1)
        self.assertNotEqual(item["itemId"], future["itemId"])
