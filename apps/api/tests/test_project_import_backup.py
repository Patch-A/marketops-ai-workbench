from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from apps.api.marketops_import.backup import (
    BUSINESS_TABLES,
    BackupBundleError,
    IDEMPOTENCY_BUSINESS_TABLES,
    IDEMPOTENCY_MIGRATION_SET,
    IDEMPOTENCY_SCHEMA_VERSION,
    LEGACY_BUSINESS_TABLES,
    MIGRATION_SET,
    MIGRATION_SHA256,
    REVIEW_BUSINESS_TABLES,
    REVIEW_MIGRATION_SET,
    REVIEW_SCHEMA_VERSION,
    SCHEDULE_BUSINESS_TABLES,
    SCHEDULE_MIGRATION_SET,
    SCHEDULE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    canonical_archive_path,
    create_backup_bundle,
    load_backup_bundle,
    restore_object_bundle,
    validate_manifest,
)


class BackupBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.object_root = self.root / "source-objects"
        self.object_root.mkdir()
        self.dump = self.root / "source.dump"
        self.dump.write_bytes(b"synthetic custom-format dump")
        self.records = [self.object_record("source", b"source"), self.object_record("proposal", b"proposal")]
        self.snapshot = {
            table: {
                "count": 1 if table not in {"artifacts", "artifact_versions"} else 2,
                "rowSetSha256": hashlib.sha256(table.encode("ascii")).hexdigest(),
            }
            for table in BUSINESS_TABLES
        }

    def tearDown(self):
        self.temporary.cleanup()

    def object_record(self, kind: str, content: bytes):
        digest = hashlib.sha256(content).hexdigest()
        storage_key = (
            "workspaces/00000000-0000-0000-0000-000000000001/"
            "clients/00000000-0000-0000-0000-000000000002/imports/"
            f"{'a' * 64}/{'b' * 64}/{kind}/{digest}"
        )
        archive = canonical_archive_path(storage_key)
        physical = self.object_root / Path(archive).relative_to("objects")
        physical.parent.mkdir(parents=True, exist_ok=True)
        physical.write_bytes(content)
        return {
            "storageKey": storage_key,
            "kind": kind,
            "sizeBytes": len(content),
            "sha256": digest,
        }

    def create(self):
        return create_backup_bundle(
            self.root / "bundle",
            database_dump=self.dump,
            object_root=self.object_root,
            objects=self.records,
            postgres={
                "serverVersionNum": 180004,
                "dumpVersionNum": 180004,
                "restoreVersionNum": 180004,
            },
            migrations=MIGRATION_SET,
            snapshot=self.snapshot,
        )

    def test_creates_valid_exact_bundle_and_restores_objects(self):
        bundle = self.create()
        reloaded = load_backup_bundle(bundle.root)
        restored = restore_object_bundle(reloaded, self.root / "restored")
        self.assertEqual(len(reloaded.manifest["objects"]), 2)
        for item in reloaded.manifest["objects"]:
            path = restored / Path(item["archivePath"]).relative_to("objects")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])

    def test_refuses_existing_destinations(self):
        (self.root / "bundle").mkdir()
        with self.assertRaisesRegex(BackupBundleError, "must not already exist"):
            self.create()
        (self.root / "restore").mkdir()
        self.assertFalse(any((self.root / "restore").iterdir()))
        bundle_root = self.root / "other-bundle"
        bundle = create_backup_bundle(
            bundle_root,
            database_dump=self.dump,
            object_root=self.object_root,
            objects=self.records,
            postgres={"serverVersionNum": 180004, "dumpVersionNum": 180004, "restoreVersionNum": 180004},
            migrations=MIGRATION_SET,
            snapshot=self.snapshot,
        )
        with self.assertRaisesRegex(BackupBundleError, "must not already exist"):
            restore_object_bundle(bundle, self.root / "restore")

    def test_rejects_tampered_dump_object_and_extra_path(self):
        for target in ("dump", "object", "extra"):
            with self.subTest(target=target):
                bundle = self.create()
                if target == "dump":
                    (bundle.root / "database.dump").write_bytes(b"tampered")
                elif target == "object":
                    item = bundle.manifest["objects"][0]
                    (bundle.root / Path(item["archivePath"])).write_bytes(b"tampered")
                else:
                    (bundle.root / "unexpected").write_text("extra", encoding="utf-8")
                with self.assertRaises(BackupBundleError):
                    load_backup_bundle(bundle.root)
                for child in bundle.root.iterdir():
                    if child.is_dir():
                        for current, dirs, files in os.walk(child, topdown=False):
                            for name in files:
                                (Path(current) / name).unlink()
                            for name in dirs:
                                (Path(current) / name).rmdir()
                        child.rmdir()
                    else:
                        child.unlink()
                bundle.root.rmdir()

    def test_failed_copy_does_not_publish_bundle(self):
        first = self.records[0]
        physical = self.object_root / Path(canonical_archive_path(first["storageKey"])).relative_to("objects")
        physical.write_bytes(b"changed")
        with self.assertRaisesRegex(BackupBundleError, "changed"):
            self.create()
        self.assertFalse((self.root / "bundle").exists())

    def test_manifest_rejects_unknown_fields_duplicate_identity_and_bad_versions(self):
        bundle = self.create()
        base = bundle.manifest
        bad = dict(base)
        bad["unexpected"] = True
        with self.assertRaisesRegex(BackupBundleError, "frozen schema"):
            validate_manifest(bad)

        bad = {**base, "objects": [base["objects"][0], base["objects"][0]]}
        with self.assertRaisesRegex(BackupBundleError, "duplicate"):
            validate_manifest(bad)

        bad = {**base, "postgres": {**base["postgres"], "dumpVersionNum": 170005}}
        with self.assertRaisesRegex(BackupBundleError, "18.4"):
            validate_manifest(bad)

        bad_migrations = [dict(item) for item in base["migrations"]]
        bad_migrations[1]["sha256"] = "d" * 64
        bad = {**base, "migrations": bad_migrations}
        with self.assertRaisesRegex(BackupBundleError, "migration set"):
            validate_manifest(bad)

    def test_legacy_v1_bundle_loads_and_restores_verified_objects(self):
        legacy_tables = LEGACY_BUSINESS_TABLES
        bundle_root = self.root / "legacy-bundle"
        bundle_root.mkdir()
        dump_content = b"synthetic legacy custom-format dump"
        (bundle_root / "database.dump").write_bytes(dump_content)
        record = self.records[0]
        archive_path = canonical_archive_path(record["storageKey"])
        archived_object = bundle_root / archive_path
        archived_object.parent.mkdir(parents=True)
        archived_object.write_bytes(b"source")
        manifest = {
            "schemaVersion": 1,
            "taskId": "M1-01",
            "postgres": {
                "serverVersionNum": 180004,
                "dumpVersionNum": 180004,
                "restoreVersionNum": 180004,
            },
            "migration": {"name": "0001_project_import.sql", "sha256": MIGRATION_SHA256},
            "database": {
                "archivePath": "database.dump",
                "sha256": hashlib.sha256(dump_content).hexdigest(),
                "tableData": list(legacy_tables),
            },
            "snapshot": {
                table: {"count": 0, "rowSetSha256": "e" * 64}
                for table in legacy_tables
            },
            "objects": [
                {
                    **record,
                    "archivePath": archive_path,
                }
            ],
        }
        (bundle_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        loaded = load_backup_bundle(bundle_root)
        restored = restore_object_bundle(loaded, self.root / "legacy-restored")

        self.assertEqual(loaded.manifest["schemaVersion"], 1)
        restored_object = restored / Path(archive_path).relative_to("objects")
        self.assertEqual(restored_object.read_bytes(), b"source")
        self.assertEqual(
            hashlib.sha256(restored_object.read_bytes()).hexdigest(), record["sha256"]
        )

    def test_storage_keys_require_canonical_scope_and_paths(self):
        record = self.records[0]
        self.assertEqual(record["kind"], "source")
        self.assertEqual(str(UUID(record["storageKey"].split("/")[1])), record["storageKey"].split("/")[1])
        for value in (
            "../object",
            "/absolute/object",
            record["storageKey"].replace("00000000-0000-0000-0000-000000000001", "NOT-A-UUID"),
        ):
            with self.subTest(value=value), self.assertRaises(BackupBundleError):
                canonical_archive_path(value)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_bundle_loader_rejects_symbolic_link(self):
        bundle = self.create()
        item = bundle.manifest["objects"][0]
        object_path = bundle.root / Path(item["archivePath"])
        replacement = object_path.with_suffix(".replacement")
        object_path.rename(replacement)
        try:
            os.symlink(replacement, object_path)
        except OSError as error:
            self.skipTest(f"symbolic links are not permitted: {error}")
        with self.assertRaisesRegex(BackupBundleError, "symbolic link"):
            load_backup_bundle(bundle.root)

    def test_bundle_loader_rejects_root_and_nested_duplicate_json_members(self):
        for target in ("root", "nested"):
            with self.subTest(target=target):
                bundle = self.create()
                manifest_path = bundle.root / "manifest.json"
                text = manifest_path.read_text(encoding="utf-8")
                if target == "root":
                    text = text.replace(
                        f'"schemaVersion": {SCHEMA_VERSION}',
                        f'"schemaVersion": 0,\n  "schemaVersion": {SCHEMA_VERSION}',
                        1,
                    )
                else:
                    text = text.replace(
                        '"serverVersionNum": 180004',
                        '"serverVersionNum": 170000,\n    "serverVersionNum": 180004',
                        1,
                    )
                manifest_path.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(BackupBundleError, "duplicate JSON member"):
                    load_backup_bundle(bundle.root)
                for path in sorted(bundle.root.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                bundle.root.rmdir()

    def test_review_v2_manifest_remains_readable_after_v3_upgrade(self):
        bundle = self.create()
        manifest = dict(bundle.manifest)
        manifest["schemaVersion"] = REVIEW_SCHEMA_VERSION
        manifest["taskId"] = "M1-02"
        manifest["migrations"] = list(REVIEW_MIGRATION_SET)
        manifest["database"] = {
            **manifest["database"],
            "tableData": list(REVIEW_BUSINESS_TABLES),
        }
        manifest["snapshot"] = {
            table: manifest["snapshot"][table]
            for table in REVIEW_BUSINESS_TABLES
        }
        (bundle.root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        loaded = load_backup_bundle(bundle.root)

        self.assertEqual(loaded.manifest["schemaVersion"], REVIEW_SCHEMA_VERSION)
        self.assertEqual(
            loaded.manifest["database"]["tableData"],
            list(REVIEW_BUSINESS_TABLES),
        )

    def test_idempotency_v3_manifest_remains_readable_after_v4_upgrade(self):
        bundle = self.create()
        manifest = dict(bundle.manifest)
        manifest["schemaVersion"] = IDEMPOTENCY_SCHEMA_VERSION
        manifest["taskId"] = "M1-02"
        manifest["migrations"] = list(IDEMPOTENCY_MIGRATION_SET)
        manifest["database"] = {
            **manifest["database"],
            "tableData": list(IDEMPOTENCY_BUSINESS_TABLES),
        }
        manifest["snapshot"] = {
            table: manifest["snapshot"][table]
            for table in IDEMPOTENCY_BUSINESS_TABLES
        }
        (bundle.root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        loaded = load_backup_bundle(bundle.root)

        self.assertEqual(loaded.manifest["schemaVersion"], IDEMPOTENCY_SCHEMA_VERSION)
        self.assertEqual(
            loaded.manifest["database"]["tableData"],
            list(IDEMPOTENCY_BUSINESS_TABLES),
        )

    def test_schedule_v4_manifest_remains_readable_after_v5_upgrade(self):
        bundle = self.create()
        manifest = dict(bundle.manifest)
        manifest["schemaVersion"] = SCHEDULE_SCHEMA_VERSION
        manifest["taskId"] = "M1-03"
        manifest["migrations"] = list(SCHEDULE_MIGRATION_SET)
        manifest["database"] = {
            **manifest["database"],
            "tableData": list(SCHEDULE_BUSINESS_TABLES),
        }
        manifest["snapshot"] = {
            table: manifest["snapshot"][table]
            for table in SCHEDULE_BUSINESS_TABLES
        }
        (bundle.root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        loaded = load_backup_bundle(bundle.root)

        self.assertEqual(loaded.manifest["schemaVersion"], SCHEDULE_SCHEMA_VERSION)
        self.assertEqual(
            loaded.manifest["database"]["tableData"],
            list(SCHEDULE_BUSINESS_TABLES),
        )


if __name__ == "__main__":
    unittest.main()
