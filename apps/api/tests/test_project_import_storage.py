from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_import.service import StoredObject
from apps.api.marketops_import.storage import LocalObjectStore


class LocalObjectStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "objects"
        self.source = Path(self.temp.name) / "upload"
        self.content = b"# synthetic source\n"
        self.source.write_bytes(self.content)
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        self.key = self.storage_key("source", self.sha256)
        self.store = LocalObjectStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def storage_key(kind: str, digest: str) -> str:
        return (
            f"workspaces/{uuid4()}/clients/{uuid4()}/imports/"
            f"{'a' * 64}/{'b' * 64}/{kind}/{digest}"
        )

    async def put(self, **overrides):
        values = {
            "kind": "source",
            "storage_key": self.key,
            "source_path": self.source,
            "size_bytes": len(self.content),
            "sha256": self.sha256,
        }
        values.update(overrides)
        return await self.store.put_immutable(**values)

    def destination(self):
        physical_key = hashlib.sha256(self.key.encode("ascii")).hexdigest()
        return self.root / physical_key[:2] / physical_key[2:]

    async def test_writes_and_verifies_content_under_controlled_key(self):
        stored = await self.put()
        self.assertEqual(stored.storage_key, self.key)
        self.assertEqual(self.destination().read_bytes(), self.content)
        self.assertEqual(list(self.root.rglob(".object-*")), [])

    async def test_replay_verifies_existing_content_without_overwrite(self):
        await self.put()
        self.source.write_bytes(b"different")
        replayed = await self.put(source_path=self.source)
        self.assertEqual(replayed.sha256, self.sha256)

    async def test_read_only_verification_accepts_retained_content(self):
        stored = await self.put()

        await self.store.verify_immutable(kind="source", stored=stored)

    async def test_read_only_verification_does_not_recreate_a_missing_object(self):
        stored = StoredObject(self.key, len(self.content), self.sha256)

        with self.assertRaisesRegex(ValueError, "object is missing"):
            await self.store.verify_immutable(kind="source", stored=stored)

        self.assertFalse(self.destination().exists())

    async def test_read_only_verification_rejects_corruption(self):
        stored = await self.put()
        self.destination().write_bytes(b"different")

        with self.assertRaisesRegex(ValueError, "different content"):
            await self.store.verify_immutable(kind="source", stored=stored)

    async def test_existing_different_content_fails_closed(self):
        destination = self.destination()
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "different content"):
            await self.put()

    async def test_declared_size_or_hash_mismatch_never_publishes(self):
        with self.assertRaisesRegex(ValueError, "declared content"):
            await self.put(size_bytes=len(self.content) + 1)
        with self.assertRaisesRegex(ValueError, "declared content"):
            await self.put(sha256="0" * 64)
        self.assertFalse(self.destination().exists())
        self.assertEqual(list(self.root.rglob(".object-*")), [])

    async def test_concurrent_identical_writes_are_idempotent(self):
        first, second = await asyncio.gather(self.put(), self.put())
        self.assertEqual(first, second)
        self.assertEqual(self.destination().read_bytes(), self.content)
        self.assertEqual(list(self.root.rglob(".object-*")), [])

    async def test_rejects_symbolic_link_in_physical_key_prefix(self):
        destination = self.destination()
        self.root.mkdir(parents=True)
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        try:
            destination.parent.symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symbolic-link creation is unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "symbolic link"):
            await self.put()
        self.assertEqual(list(outside.iterdir()), [])

    async def test_rejects_traversal_uncontrolled_and_wrong_kind_keys(self):
        invalid = [
            "../outside",
            "/absolute",
            self.key.replace("workspaces/", "other/", 1),
            self.key.replace("/source/", "/proposal/", 1),
        ]
        for key in invalid:
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.put(storage_key=key)
