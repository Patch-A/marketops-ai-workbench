"""Dry-run-first cleanup for the local immutable object adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .service import StoredObject
from .storage import LocalObjectStore


PLAN_SCHEMA_VERSION = 1
DEFAULT_OBSERVATION_INTERVAL = timedelta(hours=24)
DEFAULT_PLAN_LIFETIME = timedelta(days=7)
_HEX_2 = re.compile(r"[0-9a-f]{2}")
_HEX_62 = re.compile(r"[0-9a-f]{62}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LOCK_FILE = ".marketops-object-store.lock"
_QUARANTINE = ".cleanup-quarantine"
_CHUNK_SIZE = 1024 * 1024


class CleanupFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        recovery_state: str = "unchanged",
    ):
        self.code = code
        self.retryable = retryable
        self.recovery_state = recovery_state
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReferenceSnapshot:
    observed_at: datetime
    references: tuple[StoredObject, ...]
    complete: bool = True


class CompleteReferenceReader(Protocol):
    async def snapshot_references(self) -> ReferenceSnapshot: ...


@dataclass(frozen=True)
class CleanupCandidate:
    physical_key: str
    size_bytes: int
    content_sha256: str
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int
    link_count: int


@dataclass(frozen=True)
class CleanupPlan:
    schema_version: int
    root_fingerprint: str
    observed_at: datetime
    eligible_after: datetime
    expires_at: datetime
    reference_set_sha256: str
    candidates: tuple[CleanupCandidate, ...]


@dataclass(frozen=True)
class CleanupResult:
    plan_sha256: str
    applied_at: datetime
    deleted_physical_keys: tuple[str, ...]
    retained_referenced_physical_keys: tuple[str, ...]


def cleanup_plan_sha256(payload: bytes) -> str:
    if not isinstance(payload, bytes):
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "cleanup plan must be bytes")
    return hashlib.sha256(payload).hexdigest()


def serialize_cleanup_plan(plan: CleanupPlan) -> bytes:
    document = {
        "schemaVersion": plan.schema_version,
        "rootFingerprint": plan.root_fingerprint,
        "observedAt": _timestamp(plan.observed_at),
        "eligibleAfter": _timestamp(plan.eligible_after),
        "expiresAt": _timestamp(plan.expires_at),
        "referenceSetSha256": plan.reference_set_sha256,
        "candidates": [
            {
                "physicalKey": item.physical_key,
                "sizeBytes": item.size_bytes,
                "contentSha256": item.content_sha256,
                "device": item.device,
                "inode": item.inode,
                "mtimeNs": item.mtime_ns,
                "ctimeNs": item.ctime_ns,
                "linkCount": item.link_count,
            }
            for item in plan.candidates
        ],
    }
    return json.dumps(
        document, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def parse_cleanup_plan(payload: bytes, expected_sha256: str) -> CleanupPlan:
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "plan hash must be lowercase SHA-256")
    if cleanup_plan_sha256(payload) != expected_sha256:
        raise CleanupFailure("CLEANUP_PLAN_TAMPERED", "cleanup plan hash does not match")
    try:
        document = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "cleanup plan is not canonical JSON") from error
    expected = {
        "schemaVersion",
        "rootFingerprint",
        "observedAt",
        "eligibleAfter",
        "expiresAt",
        "referenceSetSha256",
        "candidates",
    }
    if not isinstance(document, dict) or set(document) != expected:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "cleanup plan fields do not match")
    if document["schemaVersion"] != PLAN_SCHEMA_VERSION:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "cleanup plan schema is unsupported")
    root_fingerprint = _sha256_text(document["rootFingerprint"], "root fingerprint")
    reference_digest = _sha256_text(
        document["referenceSetSha256"], "reference-set hash"
    )
    observed_at = _parse_timestamp(document["observedAt"], "observedAt")
    eligible_after = _parse_timestamp(document["eligibleAfter"], "eligibleAfter")
    expires_at = _parse_timestamp(document["expiresAt"], "expiresAt")
    if not observed_at < eligible_after < expires_at:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "cleanup plan time bounds are invalid")
    raw_candidates = document["candidates"]
    if not isinstance(raw_candidates, list):
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "cleanup candidates must be an array")
    candidates = tuple(_parse_candidate(item) for item in raw_candidates)
    physical_keys = [item.physical_key for item in candidates]
    if physical_keys != sorted(physical_keys) or len(set(physical_keys)) != len(physical_keys):
        raise CleanupFailure(
            "INVALID_CLEANUP_PLAN", "cleanup candidates must be unique and sorted"
        )
    plan = CleanupPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        root_fingerprint=root_fingerprint,
        observed_at=observed_at,
        eligible_after=eligible_after,
        expires_at=expires_at,
        reference_set_sha256=reference_digest,
        candidates=candidates,
    )
    if serialize_cleanup_plan(plan) != payload:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "cleanup plan is not canonical")
    return plan


class OrphanCleaner:
    def __init__(
        self,
        object_store: LocalObjectStore,
        reference_reader: CompleteReferenceReader,
        *,
        observation_interval: timedelta = DEFAULT_OBSERVATION_INTERVAL,
        plan_lifetime: timedelta = DEFAULT_PLAN_LIFETIME,
        lock_timeout: float = 30.0,
    ):
        if observation_interval <= timedelta(0):
            raise ValueError("cleanup observation interval must be positive")
        if plan_lifetime <= observation_interval:
            raise ValueError("cleanup plan lifetime must exceed its observation interval")
        if isinstance(lock_timeout, bool) or lock_timeout <= 0:
            raise ValueError("cleanup lock timeout must be positive")
        self.object_store = object_store
        self.reference_reader = reference_reader
        self.observation_interval = observation_interval
        self.plan_lifetime = plan_lifetime
        self.lock_timeout = float(lock_timeout)

    async def dry_run(self) -> CleanupPlan:
        try:
            async with self.object_store.cleanup_guard(
                timeout_seconds=self.lock_timeout
            ):
                snapshot = await self._snapshot()
                root_fingerprint = self._root_fingerprint()
                references = await self._validated_references(snapshot)
                observed = await self._scan_objects()
                self._require_referenced_objects(observed, references)
                candidates = tuple(
                    observed[key]
                    for key in sorted(set(observed).difference(references))
                )
                return CleanupPlan(
                    schema_version=PLAN_SCHEMA_VERSION,
                    root_fingerprint=root_fingerprint,
                    observed_at=snapshot.observed_at,
                    eligible_after=snapshot.observed_at + self.observation_interval,
                    expires_at=snapshot.observed_at + self.plan_lifetime,
                    reference_set_sha256=_reference_digest(snapshot.references),
                    candidates=candidates,
                )
        except CleanupFailure:
            raise
        except TimeoutError as error:
            raise CleanupFailure(
                "CLEANUP_LOCK_BUSY", "another storage operation holds the cleanup lock", retryable=True
            ) from error
        except RuntimeError as error:
            if "requires Linux flock" in str(error):
                raise CleanupFailure(
                    "UNSUPPORTED_CLEANUP_PLATFORM", "orphan cleanup requires Linux flock"
                ) from error
            raise
        except (OSError, ValueError):
            raise CleanupFailure(
                "UNSAFE_OBJECT_LAYOUT", "object-store layout could not be inspected safely"
            ) from None

    async def apply(self, plan_payload: bytes, expected_sha256: str) -> CleanupResult:
        plan = parse_cleanup_plan(plan_payload, expected_sha256)
        try:
            async with self.object_store.cleanup_guard(
                timeout_seconds=self.lock_timeout
            ):
                snapshot = await self._snapshot()
                if snapshot.observed_at < plan.eligible_after:
                    raise CleanupFailure(
                        "CLEANUP_PLAN_PREMATURE", "cleanup observation interval has not elapsed"
                    )
                if snapshot.observed_at > plan.expires_at:
                    raise CleanupFailure("CLEANUP_PLAN_EXPIRED", "cleanup plan has expired")
                if self._root_fingerprint() != plan.root_fingerprint:
                    raise CleanupFailure(
                        "CLEANUP_ROOT_MISMATCH", "cleanup plan belongs to another object root"
                    )
                references = await self._validated_references(snapshot)
                observed = await self._scan_objects()
                self._require_referenced_objects(observed, references)

                retained: list[str] = []
                deletions: list[CleanupCandidate] = []
                for planned in plan.candidates:
                    if planned.physical_key in references:
                        retained.append(planned.physical_key)
                        continue
                    current = observed.get(planned.physical_key)
                    if current is None:
                        raise CleanupFailure(
                            "STALE_CLEANUP_PLAN", "a planned object is no longer present"
                        )
                    if current != planned:
                        raise CleanupFailure(
                            "CLEANUP_OBJECT_CHANGED", "a planned object changed after dry-run"
                        )
                    deletions.append(planned)

                self._quarantine_and_delete(deletions, expected_sha256)
                return CleanupResult(
                    plan_sha256=expected_sha256,
                    applied_at=snapshot.observed_at,
                    deleted_physical_keys=tuple(item.physical_key for item in deletions),
                    retained_referenced_physical_keys=tuple(sorted(retained)),
                )
        except CleanupFailure:
            raise
        except TimeoutError as error:
            raise CleanupFailure(
                "CLEANUP_LOCK_BUSY", "another storage operation holds the cleanup lock", retryable=True
            ) from error
        except RuntimeError as error:
            if "requires Linux flock" in str(error):
                raise CleanupFailure(
                    "UNSUPPORTED_CLEANUP_PLATFORM", "orphan cleanup requires Linux flock"
                ) from error
            raise
        except (OSError, ValueError):
            raise CleanupFailure(
                "UNSAFE_OBJECT_LAYOUT", "object-store layout could not be inspected safely"
            ) from None

    async def _snapshot(self) -> ReferenceSnapshot:
        try:
            snapshot = await self.reference_reader.snapshot_references()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise CleanupFailure(
                "REFERENCE_SNAPSHOT_FAILED",
                "the complete database reference snapshot is unavailable",
                retryable=True,
            ) from None
        if not isinstance(snapshot, ReferenceSnapshot) or snapshot.complete is not True:
            raise CleanupFailure(
                "INCOMPLETE_REFERENCE_SNAPSHOT", "cleanup requires a complete reference snapshot"
            )
        _require_aware_utc(snapshot.observed_at, "reference snapshot time")
        if not isinstance(snapshot.references, tuple):
            raise CleanupFailure(
                "INCOMPLETE_REFERENCE_SNAPSHOT", "reference rows must be an immutable tuple"
            )
        return snapshot

    async def _validated_references(
        self, snapshot: ReferenceSnapshot
    ) -> dict[str, StoredObject]:
        physical: dict[str, StoredObject] = {}
        storage_keys: set[str] = set()
        for reference in snapshot.references:
            if not isinstance(reference, StoredObject):
                raise CleanupFailure(
                    "INVALID_OBJECT_REFERENCE", "database reference has an invalid shape"
                )
            try:
                parts = PurePosixPath(reference.storage_key).parts
                kind = parts[-2]
                path = self.object_store._destination(
                    kind, reference.storage_key, reference.sha256
                )
                self.object_store._validate_expected(
                    reference.size_bytes, reference.sha256
                )
            except (IndexError, ValueError) as error:
                raise CleanupFailure(
                    "INVALID_OBJECT_REFERENCE", "database reference is non-canonical"
                ) from error
            key = path.parent.name + path.name
            if reference.storage_key in storage_keys or key in physical:
                raise CleanupFailure(
                    "DUPLICATE_OBJECT_REFERENCE", "database references have a duplicate physical mapping"
                )
            storage_keys.add(reference.storage_key)
            physical[key] = reference
        return physical

    async def _scan_objects(self) -> dict[str, CleanupCandidate]:
        root = self.object_store.root
        if self.object_store.configured_root.is_symlink():
            raise CleanupFailure("UNSAFE_OBJECT_LAYOUT", "object root is a symbolic link")
        observed: dict[str, CleanupCandidate] = {}
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if child.name == _LOCK_FILE:
                if child.is_symlink() or not child.is_file():
                    raise CleanupFailure("UNSAFE_OBJECT_LAYOUT", "cleanup lock path is unsafe")
                continue
            if child.name == _QUARANTINE:
                if child.is_symlink() or not child.is_dir() or any(child.iterdir()):
                    raise CleanupFailure(
                        "CLEANUP_RECOVERY_REQUIRED", "cleanup quarantine is not empty"
                    )
                continue
            if _HEX_2.fullmatch(child.name) is None or child.is_symlink() or not child.is_dir():
                raise CleanupFailure("UNSAFE_OBJECT_LAYOUT", "object root contains an unknown entry")
            for item in sorted(child.iterdir(), key=lambda entry: entry.name):
                if item.name.startswith(".object-"):
                    raise CleanupFailure(
                        "CLEANUP_RECOVERY_REQUIRED", "temporary object residue requires recovery"
                    )
                if _HEX_62.fullmatch(item.name) is None:
                    raise CleanupFailure("UNSAFE_OBJECT_LAYOUT", "object shard contains an unknown entry")
                if item.is_symlink():
                    raise CleanupFailure("UNSAFE_OBJECT_LAYOUT", "object path is a symbolic link")
                metadata = item.stat(follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise CleanupFailure(
                        "UNSAFE_OBJECT_LAYOUT", "object path must be a single-link regular file"
                    )
                size, digest = await _hash_file(item)
                after = item.stat(follow_symlinks=False)
                if _identity_tuple(metadata) != _identity_tuple(after) or size != after.st_size:
                    raise CleanupFailure("CLEANUP_OBJECT_CHANGED", "object changed during scan")
                physical_key = child.name + item.name
                observed[physical_key] = CleanupCandidate(
                    physical_key=physical_key,
                    size_bytes=size,
                    content_sha256=digest,
                    device=int(after.st_dev),
                    inode=int(after.st_ino),
                    mtime_ns=int(after.st_mtime_ns),
                    ctime_ns=int(after.st_ctime_ns),
                    link_count=int(after.st_nlink),
                )
        return observed

    def _require_referenced_objects(
        self,
        observed: dict[str, CleanupCandidate],
        references: dict[str, StoredObject],
    ) -> None:
        for physical_key, reference in references.items():
            candidate = observed.get(physical_key)
            if candidate is None:
                raise CleanupFailure(
                    "REFERENCED_OBJECT_MISSING", "a database-referenced object is missing"
                )
            if (
                candidate.size_bytes != reference.size_bytes
                or candidate.content_sha256 != reference.sha256
            ):
                raise CleanupFailure(
                    "REFERENCED_OBJECT_CORRUPT", "a database-referenced object failed integrity checks"
                )

    def _root_fingerprint(self) -> str:
        metadata = self.object_store.root.stat(follow_symlinks=False)
        value = f"local-v1:{metadata.st_dev}:{metadata.st_ino}".encode("ascii")
        return hashlib.sha256(value).hexdigest()

    def _quarantine_and_delete(
        self, candidates: list[CleanupCandidate], plan_sha256: str
    ) -> None:
        if not candidates:
            return
        root = self.object_store.root
        quarantine_root = root / _QUARANTINE
        if quarantine_root.exists():
            if quarantine_root.is_symlink() or not quarantine_root.is_dir():
                raise CleanupFailure("UNSAFE_OBJECT_LAYOUT", "cleanup quarantine path is unsafe")
            if any(quarantine_root.iterdir()):
                raise CleanupFailure(
                    "CLEANUP_RECOVERY_REQUIRED", "cleanup quarantine is not empty"
                )
        else:
            quarantine_root.mkdir(mode=0o700)
        run_root = quarantine_root / plan_sha256
        run_root.mkdir(mode=0o700)
        moved: list[tuple[Path, Path]] = []
        deleted = 0
        try:
            for candidate in candidates:
                source = root / candidate.physical_key[:2] / candidate.physical_key[2:]
                target = run_root / candidate.physical_key
                os.rename(source, target)
                moved.append((source, target))
            for _source, target in moved:
                target.unlink()
                deleted += 1
            run_root.rmdir()
            quarantine_root.rmdir()
            for shard in sorted({source.parent for source, _target in moved}):
                try:
                    shard.rmdir()
                except OSError:
                    pass
        except BaseException as error:
            restored = 0
            for source, target in reversed(moved[deleted:]):
                if target.exists() and not source.exists():
                    try:
                        os.rename(target, source)
                        restored += 1
                    except OSError:
                        pass
            state = f"moved={len(moved)},deleted={deleted},restored={restored}"
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise CleanupFailure(
                "CLEANUP_APPLY_FAILED",
                "cleanup could not finish its quarantine operation",
                retryable=False,
                recovery_state=state,
            ) from None


async def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_SIZE):
            size += len(chunk)
            digest.update(chunk)
            await asyncio.sleep(0)
    return size, digest.hexdigest()


def _reference_digest(references: tuple[StoredObject, ...]) -> str:
    rows = sorted(
        (item.storage_key, item.size_bytes, item.sha256) for item in references
    )
    payload = json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _identity_tuple(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
        int(value.st_nlink),
    )


def _timestamp(value: datetime) -> str:
    _require_aware_utc(value, "cleanup plan timestamp")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CleanupFailure("INVALID_CLEANUP_PLAN", f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", f"{label} is invalid") from error
    _require_aware_utc(parsed, label)
    return parsed


def _require_aware_utc(value: Any, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CleanupFailure("INVALID_REFERENCE_TIME", f"{label} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise CleanupFailure("INVALID_REFERENCE_TIME", f"{label} must use UTC")


def _sha256_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", f"{label} must be lowercase SHA-256")
    return value


def _parse_candidate(value: Any) -> CleanupCandidate:
    fields = {
        "physicalKey",
        "sizeBytes",
        "contentSha256",
        "device",
        "inode",
        "mtimeNs",
        "ctimeNs",
        "linkCount",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "cleanup candidate fields do not match")
    physical_key = value["physicalKey"]
    if not isinstance(physical_key, str) or _SHA256.fullmatch(physical_key) is None:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "candidate physical key is invalid")
    content_sha256 = _sha256_text(value["contentSha256"], "candidate content hash")
    integers = {
        name: value[name]
        for name in ("sizeBytes", "device", "inode", "mtimeNs", "ctimeNs", "linkCount")
    }
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in integers.values()):
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "candidate identity values are invalid")
    if integers["linkCount"] != 1:
        raise CleanupFailure("INVALID_CLEANUP_PLAN", "candidate must have exactly one hard link")
    return CleanupCandidate(
        physical_key=physical_key,
        size_bytes=integers["sizeBytes"],
        content_sha256=content_sha256,
        device=integers["device"],
        inode=integers["inode"],
        mtime_ns=integers["mtimeNs"],
        ctime_ns=integers["ctimeNs"],
        link_count=integers["linkCount"],
    )
