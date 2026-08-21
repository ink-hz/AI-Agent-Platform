from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import stat
import threading
from uuid import UUID


_ENABLE_ENV = "PLATFORM_WORKER_ACCEPTANCE_HOOKS"
_CONTROL_ENV = "PLATFORM_WORKER_ACCEPTANCE_CONTROL_FILE"
_CONTROL_KEYS = {
    "schema_version",
    "dispatching_crash_run_id",
    "completion_crash_run_id",
}
_STATE_KEYS = {
    "schema_version",
    "metabot_posts",
    "dispatch_pause_complete",
    "completion_pause_complete",
}


def _unavailable() -> ValueError:
    return ValueError("acceptance hooks unavailable")


def _directory_descriptor(path: Path) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise _unavailable()
        return descriptor
    except Exception:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
        raise _unavailable() from None


def _read_file(directory_fd: int, name: str, *, maximum_size: int) -> bytes:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.geteuid()
                or metadata.st_size > maximum_size
            ):
                raise _unavailable()
            value = os.read(descriptor, maximum_size + 1)
            if len(value) > maximum_size or os.read(descriptor, 1):
                raise _unavailable()
            return value
        finally:
            os.close(descriptor)
    except Exception:
        raise _unavailable() from None


def _validate_optional_file(directory_fd: int, name: str) -> bool:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return False
    except OSError:
        raise _unavailable() from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_size > 65_536
        ):
            raise _unavailable()
    finally:
        os.close(descriptor)
    return True


def _atomic_write(directory_fd: int, name: str, value: bytes) -> None:
    if len(value) > 65_536:
        raise _unavailable()
    _validate_optional_file(directory_fd, name)
    temporary = f".{name}.{secrets.token_hex(16)}.part"
    created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        created = True
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(value):
                written = os.write(descriptor, value[offset:])
                if written <= 0:
                    raise _unavailable()
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        created = False
        os.fsync(directory_fd)
    except Exception:
        raise _unavailable() from None
    finally:
        if created:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except OSError:
                pass


class WorkerAcceptanceHooks:
    """Deterministic, explicitly armed crash hooks for staged relay acceptance."""

    def __init__(self, control_file: Path) -> None:
        try:
            if not control_file.is_absolute() or control_file.name != "control.json":
                raise _unavailable()
            self._directory = control_file.parent
            self._directory_fd = _directory_descriptor(self._directory)
            control = json.loads(
                _read_file(
                    self._directory_fd, control_file.name, maximum_size=16_384
                ).decode("utf-8")
            )
            if not isinstance(control, dict) or set(control) != _CONTROL_KEYS:
                raise _unavailable()
            if control["schema_version"] != 1 or isinstance(
                control["schema_version"], bool
            ):
                raise _unavailable()
            self.dispatching_run = UUID(control["dispatching_crash_run_id"])
            self.completion_run = UUID(control["completion_crash_run_id"])
            if self.dispatching_run == self.completion_run:
                raise _unavailable()
            self._lock = threading.Lock()
            self._state = self._load_state()
        except Exception:
            self.close()
            raise _unavailable() from None

    @classmethod
    def from_environment(cls) -> WorkerAcceptanceHooks | None:
        enabled = os.environ.get(_ENABLE_ENV)
        control = os.environ.get(_CONTROL_ENV)
        if enabled is None and control is None:
            return None
        if enabled != "1" or not control:
            raise _unavailable()
        return cls(Path(control))

    def _load_state(self) -> dict[str, object]:
        if not _validate_optional_file(self._directory_fd, "state.json"):
            value: dict[str, object] = {
                "schema_version": 1,
                "metabot_posts": {},
                "dispatch_pause_complete": False,
                "completion_pause_complete": False,
            }
            self._write_state(value)
            return value
        value = json.loads(
            _read_file(self._directory_fd, "state.json", maximum_size=65_536)
        )
        if not isinstance(value, dict) or set(value) != _STATE_KEYS:
            raise _unavailable()
        posts = value["metabot_posts"]
        allowed = {str(self.dispatching_run), str(self.completion_run)}
        if (
            value["schema_version"] != 1
            or not isinstance(posts, dict)
            or not set(posts).issubset(allowed)
            or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 0
                for item in posts.values()
            )
            or not isinstance(value["dispatch_pause_complete"], bool)
            or not isinstance(value["completion_pause_complete"], bool)
        ):
            raise _unavailable()
        return value

    def _write_state(self, value: dict[str, object]) -> None:
        _atomic_write(
            self._directory_fd,
            "state.json",
            (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )

    def before_metabot_post(self, run_id: UUID) -> None:
        if run_id not in {self.dispatching_run, self.completion_run}:
            return
        with self._lock:
            posts = self._state["metabot_posts"]
            if not isinstance(posts, dict):
                raise _unavailable()
            selected = str(run_id)
            posts[selected] = int(posts.get(selected, 0)) + 1
            self._write_state(self._state)

    async def after_metabot_post(self, run_id: UUID) -> None:
        if run_id != self.dispatching_run:
            return
        with self._lock:
            if self._state["dispatch_pause_complete"] is True:
                return
            self._state["dispatch_pause_complete"] = True
            self._write_state(self._state)
            _atomic_write(
                self._directory_fd, "dispatching-paused", str(run_id).encode()
            )
        await asyncio.Event().wait()

    async def before_terminal_upload(self, run_id: UUID) -> None:
        if run_id != self.completion_run:
            return
        with self._lock:
            if self._state["completion_pause_complete"] is True:
                return
            self._state["completion_pause_complete"] = True
            self._write_state(self._state)
            _atomic_write(
                self._directory_fd, "completion-paused", str(run_id).encode()
            )
        await asyncio.Event().wait()

    def close(self) -> None:
        descriptor = getattr(self, "_directory_fd", None)
        if descriptor is not None:
            self._directory_fd = None
            try:
                os.close(descriptor)
            except OSError:
                pass

    def __del__(self) -> None:
        self.close()
