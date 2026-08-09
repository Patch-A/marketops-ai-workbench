from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from apps.api.marketops_import.cleanup import (
    CleanupFailure,
    OrphanCleaner,
    ReferenceSnapshot,
    cleanup_plan_sha256,
    parse_cleanup_plan,
    serialize_cleanup_plan,
)
from apps.api.marketops_import.service import StoredObject
from apps.api.marketops_import.storage import LocalObjectStore


class UnlockedTestStore(LocalObjectStore):
    @asynccontextmanager
    async def cleanup_guard(self, *, timeout_seconds=30.0):
        yield


class TrackingTestStore(UnlockedTestStore):
    def __init__(self, root):
        super().__init__(root)
        self.cleanup_guard_active = False

    @asynccontextmanager
    async def cleanup_guard(self, *, timeout_seconds=30.0):
        self.cleanup_guard_active = True
        try:
            yield
        finally:
            self.cleanup_guard_active = False


class SnapshotReader:
    def __init__(self, *snapshots):
        self.snapshots = list(snapshots)

    async def snapshot_references(self):
        if not self.snapshots:
            raise AssertionError("no reference snapshot remains")
        return self.snapshots.pop(0)


class FailingReader:
    async def snapshot_references(self):
        raise RuntimeError("postgresql://user:secret@db/customer")


class CancellingReader:
    async def snapshot_references(self):
        raise asyncio.CancelledError


class ProjectImportCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "objects"
        self.root.mkdir()
        self.store = UnlockedTestStore(self.root)
        self.observed_at = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)
        self.apply_at = self.observed_at + timedelta(hours=2)

    def storage_key(self, kind, digest, marker):
        return (
            f"workspaces/{uuid4()}/clients/{uuid4()}/imports/"
            f"{marker * 64}/{'b' * 64}/{kind}/{digest}"
        )

    async def put(self, marker, content, kind="source"):
        source = Path(self.temporary.name) / f"upload-{marker}-{kind}"
        source.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        stored = StoredObject(
            self.storage_key(kind, digest, marker), len(content), digest
        )
        await self.store.put_immutable(
            kind=kind,
            storage_key=stored.storage_key,
            source_path=source,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )
        return stored

    def path(self, stored):
        physical = hashlib.sha256(stored.storage_key.encode("ascii")).hexdigest()
        return self.root / physical[:2] / physical[2:]

    def snapshot(self, when, *references, complete=True):
        return ReferenceSnapshot(when, tuple(references), complete)

    def cleaner(self, reader):
        return OrphanCleaner(
            self.store,
            reader,
            observation_interval=timedelta(hours=1),
            plan_lifetime=timedelta(days=1),
        )

    async def test_dry_run_is_canonical_and_does_not_delete_payloads(self):
        referenced = await self.put("a", b"referenced")
        orphan = await self.put("c", b"orphan")
        before = {path: path.read_bytes() for path in self.root.glob("*/*")}

        plan = await self.cleaner(
            SnapshotReader(self.snapshot(self.observed_at, referenced))
        ).dry_run()
        payload = serialize_cleanup_plan(plan)
        digest = cleanup_plan_sha256(payload)

        self.assertEqual(parse_cleanup_plan(payload, digest), plan)
        self.assertEqual(len(plan.candidates), 1)
        self.assertEqual(
            plan.candidates[0].physical_key,
            hashlib.sha256(orphan.storage_key.encode("ascii")).hexdigest(),
        )
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.glob("*/*")})
        self.assertNotIn(str(self.root), payload.decode("ascii"))

    async def test_apply_preserves_new_reference_and_deletes_other_orphan(self):
        referenced = await self.put("a", b"always referenced")
        race = await self.put("c", b"becomes referenced")
        orphan = await self.put("d", b"remains orphan")
        reader = SnapshotReader(
            self.snapshot(self.observed_at, referenced),
            self.snapshot(self.apply_at, referenced, race),
        )
        cleaner = self.cleaner(reader)
        plan = await cleaner.dry_run()
        payload = serialize_cleanup_plan(plan)
        digest = cleanup_plan_sha256(payload)

        result = await cleaner.apply(payload, digest)

        race_key = hashlib.sha256(race.storage_key.encode("ascii")).hexdigest()
        orphan_key = hashlib.sha256(orphan.storage_key.encode("ascii")).hexdigest()
        self.assertEqual(result.retained_referenced_physical_keys, (race_key,))
        self.assertEqual(result.deleted_physical_keys, (orphan_key,))
        self.assertTrue(self.path(referenced).exists())
        self.assertTrue(self.path(race).exists())
        self.assertFalse(self.path(orphan).exists())

    async def test_object_created_after_plan_is_not_deleted(self):
        orphan = await self.put("a", b"planned")
        reader = SnapshotReader(
            self.snapshot(self.observed_at), self.snapshot(self.apply_at)
        )
        cleaner = self.cleaner(reader)
        plan = await cleaner.dry_run()
        new_object = await self.put("c", b"after plan")
        payload = serialize_cleanup_plan(plan)

        result = await cleaner.apply(payload, cleanup_plan_sha256(payload))

        self.assertFalse(self.path(orphan).exists())
        self.assertTrue(self.path(new_object).exists())
        self.assertEqual(len(result.deleted_physical_keys), 1)

    async def test_apply_rejects_tampered_premature_and_expired_plans(self):
        await self.put("a", b"orphan")
        plan = await self.cleaner(
            SnapshotReader(self.snapshot(self.observed_at))
        ).dry_run()
        payload = serialize_cleanup_plan(plan)
        digest = cleanup_plan_sha256(payload)
        tampered = payload.replace(b'"sizeBytes":6', b'"sizeBytes":7')
        with self.assertRaisesRegex(CleanupFailure, "CLEANUP_PLAN_TAMPERED"):
            await self.cleaner(SnapshotReader()).apply(tampered, digest)

        with self.assertRaisesRegex(CleanupFailure, "CLEANUP_PLAN_PREMATURE"):
            await self.cleaner(
                SnapshotReader(self.snapshot(self.observed_at + timedelta(minutes=30)))
            ).apply(payload, digest)
        with self.assertRaisesRegex(CleanupFailure, "CLEANUP_PLAN_EXPIRED"):
            await self.cleaner(
                SnapshotReader(self.snapshot(self.observed_at + timedelta(days=2)))
            ).apply(payload, digest)

    async def test_identity_drift_fails_before_any_candidate_moves(self):
        first = await self.put("a", b"first")
        second = await self.put("c", b"second")
        reader = SnapshotReader(
            self.snapshot(self.observed_at), self.snapshot(self.apply_at)
        )
        cleaner = self.cleaner(reader)
        plan = await cleaner.dry_run()
        self.path(first).write_bytes(b"changed")
        payload = serialize_cleanup_plan(plan)

        with self.assertRaisesRegex(CleanupFailure, "CLEANUP_OBJECT_CHANGED"):
            await cleaner.apply(payload, cleanup_plan_sha256(payload))

        self.assertTrue(self.path(first).exists())
        self.assertTrue(self.path(second).exists())
        self.assertFalse((self.root / ".cleanup-quarantine").exists())

    async def test_missing_planned_candidate_and_wrong_root_are_rejected(self):
        orphan = await self.put("a", b"orphan")
        plan = await self.cleaner(
            SnapshotReader(self.snapshot(self.observed_at))
        ).dry_run()
        payload = serialize_cleanup_plan(plan)
        digest = cleanup_plan_sha256(payload)
        self.path(orphan).unlink()
        with self.assertRaisesRegex(CleanupFailure, "STALE_CLEANUP_PLAN"):
            await self.cleaner(
                SnapshotReader(self.snapshot(self.apply_at))
            ).apply(payload, digest)

        other_root = Path(self.temporary.name) / "other-objects"
        other_root.mkdir()
        other = OrphanCleaner(
            UnlockedTestStore(other_root),
            SnapshotReader(self.snapshot(self.apply_at)),
            observation_interval=timedelta(hours=1),
            plan_lifetime=timedelta(days=1),
        )
        with self.assertRaisesRegex(CleanupFailure, "CLEANUP_ROOT_MISMATCH"):
            await other.apply(payload, digest)

    async def test_missing_or_corrupt_reference_fails_closed(self):
        referenced = await self.put("a", b"referenced")
        missing = StoredObject(
            self.storage_key("source", "0" * 64, "c"), 1, "0" * 64
        )
        for reference, message in ((missing, "MISSING"), (referenced, "CORRUPT")):
            with self.subTest(message=message):
                if message == "CORRUPT":
                    self.path(referenced).write_bytes(b"bad")
                with self.assertRaisesRegex(CleanupFailure, message):
                    await self.cleaner(
                        SnapshotReader(self.snapshot(self.observed_at, reference))
                    ).dry_run()

    async def test_incomplete_snapshot_and_unknown_layout_fail_closed(self):
        with self.assertRaisesRegex(CleanupFailure, "INCOMPLETE_REFERENCE_SNAPSHOT"):
            await self.cleaner(
                SnapshotReader(self.snapshot(self.observed_at, complete=False))
            ).dry_run()

        unknown = self.root / "unknown"
        unknown.parent.mkdir(parents=True, exist_ok=True)
        unknown.write_bytes(b"x")
        with self.assertRaisesRegex(CleanupFailure, "UNSAFE_OBJECT_LAYOUT"):
            await self.cleaner(
                SnapshotReader(self.snapshot(self.observed_at))
            ).dry_run()

    async def test_hard_linked_object_fails_closed(self):
        orphan = await self.put("a", b"orphan")
        outside_link = Path(self.temporary.name) / "outside-hard-link"
        try:
            os.link(self.path(orphan), outside_link)
        except OSError as error:
            self.skipTest(f"hard-link creation is unavailable: {error}")
        with self.assertRaisesRegex(CleanupFailure, "single-link regular file"):
            await self.cleaner(
                SnapshotReader(self.snapshot(self.observed_at))
            ).dry_run()

    async def test_quarantine_move_failure_restores_already_moved_candidates(self):
        first = await self.put("a", b"first")
        second = await self.put("c", b"second")
        reader = SnapshotReader(
            self.snapshot(self.observed_at), self.snapshot(self.apply_at)
        )
        cleaner = self.cleaner(reader)
        plan = await cleaner.dry_run()
        payload = serialize_cleanup_plan(plan)
        real_rename = os.rename
        calls = 0

        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic move failure")
            return real_rename(source, target)

        with patch("apps.api.marketops_import.cleanup.os.rename", side_effect=fail_second):
            with self.assertRaises(CleanupFailure) as caught:
                await cleaner.apply(payload, cleanup_plan_sha256(payload))

        self.assertEqual(caught.exception.code, "CLEANUP_APPLY_FAILED")
        self.assertIn("restored=1", caught.exception.recovery_state)
        self.assertTrue(self.path(first).exists())
        self.assertTrue(self.path(second).exists())

    async def test_second_delete_failure_reports_partial_state_and_restores_remaining(self):
        first = await self.put("a", b"first")
        second = await self.put("c", b"second")
        reader = SnapshotReader(
            self.snapshot(self.observed_at), self.snapshot(self.apply_at)
        )
        cleaner = self.cleaner(reader)
        plan = await cleaner.dry_run()
        payload = serialize_cleanup_plan(plan)
        paths = {
            hashlib.sha256(item.storage_key.encode("ascii")).hexdigest(): self.path(item)
            for item in (first, second)
        }
        real_unlink = Path.unlink
        delete_calls = 0

        def fail_second_delete(path, *args, **kwargs):
            nonlocal delete_calls
            if ".cleanup-quarantine" in path.parts and len(path.name) == 64:
                delete_calls += 1
                if delete_calls == 2:
                    raise OSError("synthetic second delete failure")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=fail_second_delete):
            with self.assertRaises(CleanupFailure) as caught:
                await cleaner.apply(payload, cleanup_plan_sha256(payload))

        self.assertEqual(caught.exception.code, "CLEANUP_APPLY_FAILED")
        self.assertEqual(
            caught.exception.recovery_state, "moved=2,deleted=1,restored=1"
        )
        first_key, second_key = (item.physical_key for item in plan.candidates)
        self.assertFalse(paths[first_key].exists())
        self.assertTrue(paths[second_key].exists())
        self.assertTrue((self.root / ".cleanup-quarantine").exists())

    async def test_reference_reader_failure_is_stable_retryable_and_sanitized(self):
        cleaner = self.cleaner(FailingReader())
        with self.assertRaises(CleanupFailure) as caught:
            await cleaner.dry_run()
        self.assertEqual(caught.exception.code, "REFERENCE_SNAPSHOT_FAILED")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertFalse((self.root / ".cleanup-quarantine").exists())

    async def test_cleanup_cancellation_before_mutation_propagates_and_releases_guard(self):
        orphan = await self.put("a", b"orphan")
        tracking_store = TrackingTestStore(self.root)
        dry_cleaner = OrphanCleaner(
            tracking_store,
            SnapshotReader(self.snapshot(self.observed_at)),
            observation_interval=timedelta(hours=1),
            plan_lifetime=timedelta(days=1),
        )
        plan = await dry_cleaner.dry_run()
        payload = serialize_cleanup_plan(plan)
        cancelling = OrphanCleaner(
            tracking_store,
            CancellingReader(),
            observation_interval=timedelta(hours=1),
            plan_lifetime=timedelta(days=1),
        )

        with self.assertRaises(asyncio.CancelledError):
            await cancelling.apply(payload, cleanup_plan_sha256(payload))

        self.assertFalse(tracking_store.cleanup_guard_active)
        self.assertTrue(self.path(orphan).exists())
        self.assertFalse((self.root / ".cleanup-quarantine").exists())

    async def test_duplicate_reference_mapping_is_rejected(self):
        referenced = await self.put("a", b"referenced")
        with self.assertRaisesRegex(CleanupFailure, "DUPLICATE_OBJECT_REFERENCE"):
            await self.cleaner(
                SnapshotReader(
                    self.snapshot(self.observed_at, referenced, referenced)
                )
            ).dry_run()

    async def test_noncanonical_plan_json_is_rejected_even_with_matching_hash(self):
        plan = await self.cleaner(
            SnapshotReader(self.snapshot(self.observed_at))
        ).dry_run()
        document = json.loads(serialize_cleanup_plan(plan))
        payload = json.dumps(document, indent=2).encode("ascii")
        with self.assertRaisesRegex(CleanupFailure, "not canonical"):
            parse_cleanup_plan(payload, cleanup_plan_sha256(payload))

    async def test_real_store_cleanup_fails_closed_off_linux(self):
        if sys.platform.startswith("linux"):
            self.skipTest("non-Linux cleanup boundary")
        cleaner = OrphanCleaner(
            LocalObjectStore(Path(self.temporary.name) / "real-store"),
            SnapshotReader(self.snapshot(self.observed_at)),
        )
        with self.assertRaises(CleanupFailure) as caught:
            await cleaner.dry_run()
        self.assertEqual(caught.exception.code, "UNSUPPORTED_CLEANUP_PLATFORM")


if __name__ == "__main__":
    unittest.main()
