from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_obsidian import ObsidianError, ObsidianReadOnlyService, ObsidianScope


class ObsidianReadOnlyTests(unittest.TestCase):
    def test_connect_indexes_selected_markdown_without_returning_body(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            (Path(temp) / "other").mkdir()
            (vault / "Guide.md").write_text("# India guide\nprivate body", encoding="utf-8")
            (vault / "draft.txt").write_text("ignore", encoding="utf-8")
            service = ObsidianReadOnlyService(Path(temp) / "store", vault_root=vault)
            scope = ObsidianScope(*(str(uuid4()) for _ in range(4)))
            connection = asyncio.run(service.connect(scope, {"vaultPath": str(vault), "relativePaths": []}))
            result = asyncio.run(service.list_notes(scope))
            self.assertEqual(connection["status"], "connected")
            self.assertTrue(result["readOnly"])
            self.assertEqual(result["notes"][0]["title"], "India guide")
            self.assertNotIn("private body", result["notes"][0])
            self.assertEqual(len(result["notes"][0]["sha256"]), 64)
            self.assertEqual(connection["relativePaths"], [])

    def test_normalizes_windows_separators_and_deduplicates_overlapping_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            folder = vault / "Projects"
            folder.mkdir(parents=True)
            (folder / "Plan.md").write_text("# Plan\nbody", encoding="utf-8")
            service = ObsidianReadOnlyService(Path(temp) / "store", vault_root=vault)
            scope = ObsidianScope(*(str(uuid4()) for _ in range(4)))
            connection = asyncio.run(service.connect(scope, {"vaultPath": str(vault), "relativePaths": [" Projects\\Plan.md ", "Projects"]}))
            result = asyncio.run(service.list_notes(scope))
            self.assertEqual(connection["relativePaths"], ["Projects/Plan.md", "Projects"])
            self.assertEqual([item["relativePath"] for item in result["notes"]], ["Projects/Plan.md"])

    def test_rejects_other_vault_and_unsafe_relative_path(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            (Path(temp) / "other").mkdir()
            service = ObsidianReadOnlyService(Path(temp) / "store", vault_root=vault)
            scope = ObsidianScope(*(str(uuid4()) for _ in range(4)))
            with self.assertRaisesRegex(ObsidianError, "allowed"):
                asyncio.run(service.connect(scope, {"vaultPath": str(Path(temp) / "other"), "relativePaths": [""]}))
            with self.assertRaisesRegex(ObsidianError, "safe"):
                asyncio.run(service.connect(scope, {"vaultPath": str(vault), "relativePaths": ["../"]}))
            with self.assertRaisesRegex(ObsidianError, "empty"):
                asyncio.run(service.connect(scope, {"vaultPath": str(vault), "relativePaths": [" " ]}))
