from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from apps.api.marketops_import.service import StoredObject
from apps.api.marketops_import.storage import LocalObjectStore


class VerifiedObjectReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "objects"
        self.upload = Path(self.temp.name) / "proposal"
        self.payload = b"# Deliverables\nRegistration page\n"
        self.upload.write_bytes(self.payload)
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.key = (
            f"workspaces/{uuid4()}/clients/{uuid4()}/imports/"
            f"{'a' * 64}/{'b' * 64}/proposal/{self.digest}"
        )
        self.store = LocalObjectStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    async def retain(self) -> StoredObject:
        return await self.store.put_immutable(
            kind="proposal",
            storage_key=self.key,
            source_path=self.upload,
            size_bytes=len(self.payload),
            sha256=self.digest,
        )

    def destination(self) -> Path:
        physical = hashlib.sha256(self.key.encode("ascii")).hexdigest()
        return self.root / physical[:2] / physical[2:]

    async def test_returns_exact_bytes_after_single_pass_size_and_hash_check(self):
        stored = await self.retain()
        self.assertEqual(
            await self.store.read_verified_immutable(kind="proposal", stored=stored),
            self.payload,
        )

    async def test_size_and_hash_tampering_fail_without_recreating_or_replacing(self):
        stored = await self.retain()
        original = self.destination().read_bytes()
        altered = (
            StoredObject(stored.storage_key, stored.size_bytes + 1, stored.sha256),
            StoredObject(stored.storage_key, stored.size_bytes, "0" * 64),
        )
        for declaration in altered:
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                await self.store.read_verified_immutable(
                    kind="proposal", stored=declaration
                )
            self.assertEqual(self.destination().read_bytes(), original)

        self.destination().write_bytes(b"same-size-content-is-not-required")
        with self.assertRaises(ValueError):
            await self.store.read_verified_immutable(kind="proposal", stored=stored)


if __name__ == "__main__":
    unittest.main()
