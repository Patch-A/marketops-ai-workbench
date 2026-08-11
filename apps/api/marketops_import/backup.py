"""Strict directory bundles for PostgreSQL and immutable import objects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 2
TASK_ID = "M1-02"
LEGACY_SCHEMA_VERSION = 1
LEGACY_TASK_ID = "M1-01"
MIGRATION_NAME = "0001_project_import.sql"
MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / MIGRATION_NAME
MIGRATION_SHA256 = hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()
REVIEW_MIGRATION_NAME = "0002_extraction_review.sql"
REVIEW_MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / REVIEW_MIGRATION_NAME
REVIEW_MIGRATION_SHA256 = hashlib.sha256(REVIEW_MIGRATION_PATH.read_bytes()).hexdigest()
MIGRATION_SET = (
    {"name": MIGRATION_NAME, "sha256": MIGRATION_SHA256},
    {"name": REVIEW_MIGRATION_NAME, "sha256": REVIEW_MIGRATION_SHA256},
)
DATABASE_ARCHIVE_PATH = "database.dump"
MANIFEST_PATH = "manifest.json"
LEGACY_BUSINESS_TABLES = (
    "organizations",
    "workspaces",
    "clients",
    "projects",
    "artifacts",
    "artifact_versions",
    "audit_events",
)
BUSINESS_TABLES = (
    *LEGACY_BUSINESS_TABLES,
    "extraction_runs",
    "extraction_candidates",
    "review_snapshots",
    "review_snapshot_items",
    "review_decisions",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ARCHIVE_PATH = re.compile(r"objects/[0-9a-f]{2}/[0-9a-f]{62}")
_STORAGE_KEY = re.compile(
    r"workspaces/[0-9a-f-]{36}/clients/[0-9a-f-]{36}/imports/"
    r"[0-9a-f]{64}/[0-9a-f]{64}/(source|proposal)/[0-9a-f]{64}"
)
_ROOT_KEYS = frozenset(
    {
        "schemaVersion",
        "taskId",
        "postgres",
        "migrations",
        "database",
        "snapshot",
        "objects",
    }
)
_LEGACY_ROOT_KEYS = frozenset((*(_ROOT_KEYS - {"migrations"}), "migration"))
_POSTGRES_KEYS = frozenset({"serverVersionNum", "dumpVersionNum", "restoreVersionNum"})
_MIGRATION_KEYS = frozenset({"name", "sha256"})
_DATABASE_KEYS = frozenset({"archivePath", "sha256", "tableData"})
_SNAPSHOT_KEYS = frozenset(BUSINESS_TABLES)
_TABLE_SUMMARY_KEYS = frozenset({"count", "rowSetSha256"})
_OBJECT_KEYS = frozenset({"storageKey", "kind", "sizeBytes", "sha256", "archivePath"})


class BackupBundleError(RuntimeError):
    """A backup bundle is unsafe, incomplete, or internally inconsistent."""


@dataclass(frozen=True)
class ValidatedBundle:
    root: Path
    manifest: dict[str, Any]

    @property
    def database_dump(self) -> Path:
        return self.root / DATABASE_ARCHIVE_PATH


def sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def canonical_archive_path(storage_key: str) -> str:
    if not isinstance(storage_key, str) or _STORAGE_KEY.fullmatch(storage_key) is None:
        raise BackupBundleError("object storage key is outside the import namespace")
    parts = PurePosixPath(storage_key).parts
    try:
        from uuid import UUID

        if str(UUID(parts[1])) != parts[1] or str(UUID(parts[3])) != parts[3]:
            raise ValueError
    except (ValueError, IndexError) as error:
        raise BackupBundleError("object storage key has a non-canonical scope") from error
    physical = hashlib.sha256(storage_key.encode("ascii")).hexdigest()
    return f"objects/{physical[:2]}/{physical[2:]}"


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != expected:
        raise BackupBundleError(f"{label} fields do not match the frozen schema")
    return value


def _lower_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BackupBundleError(f"{label} must be a lowercase SHA-256")
    return value


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BackupBundleError(f"{label} must be a positive integer")
    return value


def validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BackupBundleError("manifest fields do not match the frozen schema")
    version = value.get("schemaVersion")
    if version == LEGACY_SCHEMA_VERSION:
        root = _exact_keys(value, _LEGACY_ROOT_KEYS, "manifest")
        expected_task = LEGACY_TASK_ID
        tables = LEGACY_BUSINESS_TABLES
    elif version == SCHEMA_VERSION:
        root = _exact_keys(value, _ROOT_KEYS, "manifest")
        expected_task = TASK_ID
        tables = BUSINESS_TABLES
    else:
        raise BackupBundleError("manifest contract version is unsupported")
    if root["taskId"] != expected_task:
        raise BackupBundleError("manifest contract version is unsupported")

    postgres = _exact_keys(root["postgres"], _POSTGRES_KEYS, "postgres")
    for name in _POSTGRES_KEYS:
        if _positive_integer(postgres[name], f"postgres.{name}") != 180004:
            raise BackupBundleError("backup requires PostgreSQL 18.4 server and tools")

    if version == LEGACY_SCHEMA_VERSION:
        migration = _exact_keys(root["migration"], _MIGRATION_KEYS, "migration")
        if migration["name"] != MIGRATION_NAME:
            raise BackupBundleError("backup migration name differs from the reviewed migration")
        if _lower_sha256(migration["sha256"], "migration.sha256") != MIGRATION_SHA256:
            raise BackupBundleError("backup migration checksum differs from the reviewed migration")
    else:
        migrations = root["migrations"]
        if not isinstance(migrations, list) or len(migrations) != len(MIGRATION_SET):
            raise BackupBundleError("backup migration set is incomplete")
        normalized_migrations = []
        for index, item in enumerate(migrations):
            record = dict(_exact_keys(item, _MIGRATION_KEYS, f"migrations[{index}]"))
            record["sha256"] = _lower_sha256(record["sha256"], f"migrations[{index}].sha256")
            normalized_migrations.append(record)
        if normalized_migrations != list(MIGRATION_SET):
            raise BackupBundleError("backup migration set differs from reviewed migrations")

    database = _exact_keys(root["database"], _DATABASE_KEYS, "database")
    if database["archivePath"] != DATABASE_ARCHIVE_PATH:
        raise BackupBundleError("database archive path is not canonical")
    _lower_sha256(database["sha256"], "database.sha256")
    table_data = database["tableData"]
    if not isinstance(table_data, list) or table_data != list(tables):
        raise BackupBundleError("database table-data allowlist drifted")

    snapshot_keys = frozenset(tables)
    snapshot = _exact_keys(root["snapshot"], snapshot_keys, "snapshot")
    for table in tables:
        summary = _exact_keys(snapshot[table], _TABLE_SUMMARY_KEYS, f"snapshot.{table}")
        count = summary["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise BackupBundleError(f"snapshot.{table}.count is invalid")
        _lower_sha256(summary["rowSetSha256"], f"snapshot.{table}.rowSetSha256")

    objects = root["objects"]
    if not isinstance(objects, list) or not objects:
        raise BackupBundleError("manifest must contain referenced immutable objects")
    seen_keys: set[str] = set()
    seen_paths: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(objects):
        record = dict(_exact_keys(item, _OBJECT_KEYS, f"objects[{index}]"))
        storage_key = record["storageKey"]
        expected_path = canonical_archive_path(storage_key)
        if record["archivePath"] != expected_path or _ARCHIVE_PATH.fullmatch(expected_path) is None:
            raise BackupBundleError("object archive path is not canonical")
        parts = PurePosixPath(storage_key).parts
        kind = record["kind"]
        if kind not in {"source", "proposal"} or parts[-2] != kind:
            raise BackupBundleError("object kind does not match its storage key")
        digest = _lower_sha256(record["sha256"], "object.sha256")
        if parts[-1] != digest:
            raise BackupBundleError("object hash does not match its storage key")
        _positive_integer(record["sizeBytes"], "object.sizeBytes")
        if storage_key in seen_keys or expected_path in seen_paths:
            raise BackupBundleError("manifest contains duplicate object identity")
        seen_keys.add(storage_key)
        seen_paths.add(expected_path)
        normalized.append(record)
    if normalized != sorted(normalized, key=lambda item: item["storageKey"]):
        raise BackupBundleError("manifest objects are not canonically ordered")
    return dict(root)


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise BackupBundleError("backup path contains a symbolic link")
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            if (current_path / name).is_symlink():
                raise BackupBundleError("backup path contains a symbolic link")


def _expected_bundle_paths(manifest: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    files = {MANIFEST_PATH, DATABASE_ARCHIVE_PATH}
    directories = {"objects"}
    for item in manifest["objects"]:
        archive_path = item["archivePath"]
        files.add(archive_path)
        directories.add(str(PurePosixPath(archive_path).parent))
    return files, directories


def _observed_bundle_paths(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for current, names, filenames in os.walk(root, followlinks=False):
        relative_current = Path(current).relative_to(root)
        for name in names:
            relative = (relative_current / name).as_posix()
            directories.add(relative)
        for name in filenames:
            relative = (relative_current / name).as_posix()
            files.add(relative)
    return files, directories


def _reject_duplicate_json_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BackupBundleError("backup manifest contains a duplicate JSON member")
        value[key] = item
    return value


def load_backup_bundle(root: Path | str) -> ValidatedBundle:
    bundle_root = Path(root)
    if not bundle_root.is_dir():
        raise BackupBundleError("backup bundle directory is missing")
    _reject_symlinks(bundle_root)
    manifest_path = bundle_root / MANIFEST_PATH
    try:
        raw_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_members,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackupBundleError("backup manifest is missing or malformed") from error
    manifest = validate_manifest(raw_manifest)
    expected_files, expected_directories = _expected_bundle_paths(manifest)
    observed_files, observed_directories = _observed_bundle_paths(bundle_root)
    if observed_files != expected_files or observed_directories != expected_directories:
        raise BackupBundleError("backup bundle contains missing or unexpected paths")

    _, dump_hash = sha256_file(bundle_root / DATABASE_ARCHIVE_PATH)
    if dump_hash != manifest["database"]["sha256"]:
        raise BackupBundleError("database dump hash does not match the manifest")
    for item in manifest["objects"]:
        path = bundle_root / Path(PurePosixPath(item["archivePath"]))
        size, digest = sha256_file(path)
        if size != item["sizeBytes"] or digest != item["sha256"]:
            raise BackupBundleError("object content does not match the manifest")
    return ValidatedBundle(bundle_root.resolve(), manifest)


def _copy_verified(source: Path, destination: Path, expected_size: int, expected_hash: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if size != expected_size or digest.hexdigest() != expected_hash:
        raise BackupBundleError("copied content changed during backup operation")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_backup_bundle(
    destination: Path | str,
    *,
    database_dump: Path | str,
    object_root: Path | str,
    objects: Iterable[Mapping[str, Any]],
    postgres: Mapping[str, int],
    migrations: Iterable[Mapping[str, str]],
    snapshot: Mapping[str, Mapping[str, Any]],
) -> ValidatedBundle:
    destination_path = Path(destination)
    if destination_path.exists() or destination_path.is_symlink():
        raise BackupBundleError("backup destination must not already exist")
    dump_path = Path(database_dump)
    source_root = Path(object_root)
    if not dump_path.is_file() or not source_root.is_dir():
        raise BackupBundleError("database dump or object root is missing")
    _reject_symlinks(source_root)
    dump_size, dump_hash = sha256_file(dump_path)
    if dump_size <= 0:
        raise BackupBundleError("database dump is empty")

    object_records = []
    for raw in objects:
        record = dict(raw)
        record["archivePath"] = canonical_archive_path(record.get("storageKey"))
        object_records.append(record)
    object_records.sort(key=lambda item: item["storageKey"])
    manifest = validate_manifest(
        {
            "schemaVersion": SCHEMA_VERSION,
            "taskId": TASK_ID,
            "postgres": dict(postgres),
            "migrations": [dict(migration) for migration in migrations],
            "database": {
                "archivePath": DATABASE_ARCHIVE_PATH,
                "sha256": dump_hash,
                "tableData": list(BUSINESS_TABLES),
            },
            "snapshot": {name: dict(snapshot[name]) for name in BUSINESS_TABLES},
            "objects": object_records,
        }
    )

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{destination_path.name}-", dir=destination_path.parent)
    )
    try:
        _copy_verified(dump_path, stage / DATABASE_ARCHIVE_PATH, dump_size, dump_hash)
        for item in manifest["objects"]:
            relative = Path(PurePosixPath(item["archivePath"]))
            source = source_root / relative.relative_to("objects")
            if source.is_symlink() or not source.is_file():
                raise BackupBundleError("referenced immutable object is missing or unsafe")
            _copy_verified(
                source,
                stage / relative,
                item["sizeBytes"],
                item["sha256"],
            )
        manifest_file = stage / MANIFEST_PATH
        with manifest_file.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for directory in sorted(
            (path for path in stage.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            _fsync_directory(directory)
        _fsync_directory(stage)
        if destination_path.exists() or destination_path.is_symlink():
            raise BackupBundleError("backup destination appeared during publication")
        os.rename(stage, destination_path)
        _fsync_directory(destination_path.parent)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return load_backup_bundle(destination_path)


def restore_object_bundle(bundle: ValidatedBundle, target_root: Path | str) -> Path:
    target = Path(target_root)
    if target.exists() or target.is_symlink():
        raise BackupBundleError("object restore target must not already exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        for item in bundle.manifest["objects"]:
            archive = bundle.root / Path(PurePosixPath(item["archivePath"]))
            relative = Path(PurePosixPath(item["archivePath"])).relative_to("objects")
            _copy_verified(archive, stage / relative, item["sizeBytes"], item["sha256"])
        _reject_symlinks(stage)
        if target.exists() or target.is_symlink():
            raise BackupBundleError("object restore target appeared during publication")
        os.rename(stage, target)
        _fsync_directory(target.parent)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return target.resolve()
