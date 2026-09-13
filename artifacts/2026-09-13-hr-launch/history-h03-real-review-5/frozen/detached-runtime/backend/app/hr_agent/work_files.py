"""Private, server-addressed ephemeral files for one HR work scope."""

from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid5

from .types import LeaseFence


class WorkFileError(RuntimeError):
    pass


def _uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise WorkFileError("invalid file identity")
    return value


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise WorkFileError("unsafe work directory")
        path.chmod(0o700)
    except OSError:
        raise WorkFileError("work directory unavailable") from None


class WorkFiles:
    def __init__(
        self, root: Path, *, ttl_seconds: int, owner_id: UUID | None = None
    ) -> None:
        if (
            not isinstance(root, Path)
            or not root.is_absolute()
            or isinstance(ttl_seconds, bool)
            or ttl_seconds <= 0
        ):
            raise ValueError("invalid work file configuration")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise WorkFileError("unsafe work root")
        root.chmod(0o700)
        self._root = root.resolve(strict=True)
        self._ttl = ttl_seconds
        self._owners: dict[UUID, UUID] = {}
        self._owner = _uuid(owner_id) if owner_id is not None else None

    def for_owner(self, owner_id: UUID) -> ScopedWorkFiles:
        return ScopedWorkFiles(self, _uuid(owner_id))

    def open(self, fence: LeaseFence, file_id: UUID, mode: str) -> BinaryIO:
        if self._owner is None:
            raise WorkFileError("owner scope unavailable")
        return self.for_owner(self._owner).open(fence, file_id, mode)

    def cleanup(
        self, *, active: set[tuple[UUID, UUID, int]], now: float | None = None
    ) -> int:
        current = time.time() if now is None else now
        removed = 0
        for owner_dir in self._root.iterdir():
            owner_id = _parse_uuid_dir(owner_dir)
            for work_dir in owner_dir.iterdir() if owner_id else ():
                work_id = _parse_uuid_dir(work_dir)
                for attempt_dir in work_dir.iterdir() if work_id else ():
                    if not _parse_uuid_dir(attempt_dir):
                        continue
                    metadata = attempt_dir / ".scope"
                    try:
                        epoch = int(metadata.read_text(encoding="ascii"))
                        age = current - attempt_dir.stat(follow_symlinks=False).st_mtime
                    except (OSError, ValueError):
                        continue
                    if age >= self._ttl and (owner_id, work_id, epoch) not in active:
                        shutil.rmtree(attempt_dir)
                        removed += 1
        return removed


class ScopedWorkFiles:
    def __init__(self, store: WorkFiles, owner_id: UUID) -> None:
        self._store = store
        self._owner = owner_id

    @staticmethod
    def _attempt_id(fence: LeaseFence) -> UUID:
        return uuid5(
            fence.work_id, f"{fence.input_revision}:{fence.epoch}:{fence.worker_id}"
        )

    def attempt_dir(self, fence: LeaseFence) -> Path:
        self._validate_fence(fence)
        return (
            self._store._root
            / str(self._owner)
            / str(fence.work_id)
            / str(self._attempt_id(fence))
        )

    def path(self, fence: LeaseFence, file_id: UUID) -> Path:
        _uuid(file_id)
        return self.attempt_dir(fence) / str(file_id)

    def open(self, fence: LeaseFence, file_id: UUID, mode: str) -> BinaryIO:
        if mode not in {"rb", "xb"}:
            raise WorkFileError("unsupported file mode")
        target = self.path(fence, file_id)
        self._prepare_parents(fence)
        flags = os.O_RDONLY if mode == "rb" else os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target, flags, 0o600)
            current = os.fstat(fd)
            if not stat.S_ISREG(current.st_mode):
                os.close(fd)
                raise WorkFileError("unsafe work file")
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, mode)
        except (FileExistsError, FileNotFoundError, OSError):
            raise WorkFileError("work file unavailable") from None

    def _validate_fence(self, fence: LeaseFence) -> None:
        if (
            not isinstance(fence, LeaseFence)
            or fence.input_revision <= 0
            or fence.epoch <= 0
            or not fence.worker_id
        ):
            raise WorkFileError("invalid work scope")
        known = self._store._owners.setdefault(fence.work_id, self._owner)
        if known != self._owner:
            raise WorkFileError("cross-owner work scope")

    def _prepare_parents(self, fence: LeaseFence) -> None:
        owner_dir = self._store._root / str(self._owner)
        work_dir = owner_dir / str(fence.work_id)
        attempt_dir = self.attempt_dir(fence)
        for path in (owner_dir, work_dir, attempt_dir):
            _private_directory(path)
        scope = attempt_dir / ".scope"
        if not scope.exists():
            fd = os.open(
                scope,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(str(fence.epoch))


def _parse_uuid_dir(path: Path) -> UUID | None:
    try:
        if path.is_symlink() or not path.is_dir():
            return None
        return UUID(path.name)
    except (OSError, ValueError):
        return None
