"""Local immutable object storage for project imports."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import AsyncIterator
from uuid import UUID

from .service import StoredObject


_CHUNK_SIZE = 1024 * 1024
_LOCK_FILE = ".marketops-object-store.lock"
_STORAGE_KEY = re.compile(
    r"^workspaces/[0-9a-f-]{36}/clients/[0-9a-f-]{36}/imports/"
    r"[0-9a-f]{64}/[0-9a-f]{64}/(source|proposal)/[0-9a-f]{64}$"
)


class LocalObjectStore:
    """Content-verifying, no-overwrite storage below one trusted local root.

    The configured root and its parent directories must be writable only by the
    service account or an administrator. Symlink checks reduce accidental aliasing;
    they do not eliminate TOCTOU attacks by an arbitrary concurrent local writer.
    """

    def __init__(self, root: Path | str):
        self.configured_root = Path(root).absolute()
        self.root = self.configured_root.resolve()

    @asynccontextmanager
    async def import_guard(
        self, *, timeout_seconds: float = 30.0
    ) -> AsyncIterator[None]:
        """Hold the cooperative shared Linux lease for one complete import."""
        if os.name == "nt":
            # Cleanup apply is unsupported on Windows, so no destructive peer can run.
            yield
            return
        async with self._linux_lock(shared=True, timeout_seconds=timeout_seconds):
            yield

    @asynccontextmanager
    async def cleanup_guard(
        self, *, timeout_seconds: float = 30.0
    ) -> AsyncIterator[None]:
        """Exclude cooperative importers while a Linux cleanup scans and applies."""
        if not sys.platform.startswith("linux"):
            raise RuntimeError("orphan cleanup apply requires Linux flock")
        async with self._linux_lock(shared=False, timeout_seconds=timeout_seconds):
            yield

    @asynccontextmanager
    async def _linux_lock(
        self, *, shared: bool, timeout_seconds: float
    ) -> AsyncIterator[None]:
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("object-store lock timeout must be positive")
        import fcntl

        self.root.mkdir(parents=True, exist_ok=True)
        if self.configured_root.is_symlink():
            raise ValueError("object-store root must not be a symbolic link")
        lock_path = self.root / _LOCK_FILE
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        deadline = time.monotonic() + float(timeout_seconds)
        acquired = False
        try:
            while True:
                try:
                    fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("object-store cleanup lock is busy")
                    await asyncio.sleep(0.05)
            yield
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    async def put_immutable(
        self,
        *,
        kind: str,
        storage_key: str,
        source_path: Path,
        size_bytes: int,
        sha256: str,
    ) -> StoredObject:
        self._validate_expected(size_bytes, sha256)
        destination = self._destination(kind, storage_key, sha256)
        self._reject_symlink_components(destination.parent)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_components(destination.parent)
        self._ensure_below_root(destination.parent.resolve())

        if destination.exists():
            await self._verify(destination, size_bytes, sha256)
            return StoredObject(storage_key, size_bytes, sha256)

        temp_path: Path | None = None
        try:
            handle = tempfile.NamedTemporaryFile(
                mode="xb", prefix=".object-", dir=destination.parent, delete=False
            )
            temp_path = Path(handle.name)
            digest = hashlib.sha256()
            copied = 0
            with handle, Path(source_path).open("rb") as source:
                while chunk := source.read(_CHUNK_SIZE):
                    copied += len(chunk)
                    if copied > size_bytes:
                        raise ValueError("source object size changed during storage")
                    digest.update(chunk)
                    handle.write(chunk)
                    await asyncio.sleep(0)
                handle.flush()
                os.fsync(handle.fileno())
            if copied != size_bytes or digest.hexdigest() != sha256:
                raise ValueError("source object does not match its declared content")

            try:
                os.link(temp_path, destination)
            except FileExistsError:
                await self._verify(destination, size_bytes, sha256)
            else:
                await self._verify(destination, size_bytes, sha256)
            return StoredObject(storage_key, size_bytes, sha256)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    async def verify_immutable(self, *, kind: str, stored: StoredObject) -> None:
        """Verify an already-retained object without creating or replacing it."""
        self._validate_expected(stored.size_bytes, stored.sha256)
        destination = self._destination(kind, stored.storage_key, stored.sha256)
        self._reject_symlink_components(destination)
        if not destination.is_file():
            raise ValueError("immutable object is missing")
        await self._verify(destination, stored.size_bytes, stored.sha256)

    def _destination(self, kind: str, storage_key: str, sha256: str) -> Path:
        if not isinstance(storage_key, str) or not _STORAGE_KEY.fullmatch(storage_key):
            raise ValueError("storage key is outside the project import namespace")
        parts = PurePosixPath(storage_key).parts
        if str(UUID(parts[1])) != parts[1] or str(UUID(parts[3])) != parts[3]:
            raise ValueError("storage key contains a non-canonical scope identifier")
        if parts[-2] != kind or kind not in {"source", "proposal"}:
            raise ValueError("storage kind does not match the storage key")
        if parts[-1] != sha256:
            raise ValueError("storage key content hash does not match declared content")
        physical_key = hashlib.sha256(storage_key.encode("ascii")).hexdigest()
        destination = self.root / physical_key[:2] / physical_key[2:]
        self._reject_symlink_components(destination)
        self._ensure_below_root(destination.resolve(strict=False))
        return destination

    def _reject_symlink_components(self, path: Path) -> None:
        self._ensure_below_root(path)
        current = self.root
        for part in path.relative_to(self.root).parts:
            current /= part
            if current.is_symlink():
                raise ValueError("storage path contains a symbolic link")

    def _ensure_below_root(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as error:
            raise ValueError("storage key escapes the configured root") from error

    @staticmethod
    def _validate_expected(size_bytes: int, sha256: str) -> None:
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
            raise ValueError("object size must be positive")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("object hash must be lowercase SHA-256")

    @staticmethod
    async def _verify(path: Path, size_bytes: int, sha256: str) -> None:
        digest = hashlib.sha256()
        observed_size = 0
        with path.open("rb") as stored:
            while chunk := stored.read(_CHUNK_SIZE):
                observed_size += len(chunk)
                digest.update(chunk)
                await asyncio.sleep(0)
        if observed_size != size_bytes or digest.hexdigest() != sha256:
            raise ValueError("an immutable object already exists with different content")
