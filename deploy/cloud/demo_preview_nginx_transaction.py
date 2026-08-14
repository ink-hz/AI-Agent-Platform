from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import stat


FAILPOINTS = (
    "before_snippet_part",
    "after_snippet_part",
    "after_snippet_replace",
    "after_config_part",
    "after_config_replace",
    "interrupt_after_config_replace",
)


class InjectedTransactionFailure(RuntimeError):
    pass


class TransactionInterrupted(RuntimeError):
    pass


def _regular_file(path: Path) -> os.stat_result:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("transaction_source_not_regular")
    return value


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stage(path: Path, payload: bytes, *, mode: int, uid: int, gid: int) -> Path:
    part = path.with_name(f"{path.name}.part")
    try:
        part.unlink()
    except FileNotFoundError:
        pass
    descriptor = os.open(
        part,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("transaction_short_write")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return part


def _replace(part: Path, target: Path) -> None:
    os.replace(part, target)
    _fsync_parent(target)


def install_preview_files(
    *,
    live_config: Path,
    candidate: Path,
    live_snippet: Path,
    snippet_candidate: Path,
    mode: int,
    uid: int,
    gid: int,
    failpoint: str | None = None,
) -> None:
    if failpoint is not None and failpoint not in FAILPOINTS:
        raise ValueError("unknown_transaction_failpoint")
    live_config = Path(live_config)
    candidate = Path(candidate)
    live_snippet = Path(live_snippet)
    snippet_candidate = Path(snippet_candidate)
    live_stat = _regular_file(live_config)
    _regular_file(candidate)
    _regular_file(snippet_candidate)
    if live_snippet.exists() or live_snippet.is_symlink():
        raise ValueError("transaction_snippet_already_exists")
    if stat.S_IMODE(live_stat.st_mode) != mode:
        raise ValueError("transaction_config_mode_changed")
    if live_stat.st_uid != uid or live_stat.st_gid != gid:
        raise ValueError("transaction_config_owner_changed")

    original = live_config.read_bytes()
    config_payload = candidate.read_bytes()
    snippet_payload = snippet_candidate.read_bytes()
    config_part = live_config.with_name(f"{live_config.name}.part")
    snippet_part = live_snippet.with_name(f"{live_snippet.name}.part")

    def inject(name: str) -> None:
        if failpoint == name:
            raise InjectedTransactionFailure(name)

    try:
        inject("before_snippet_part")
        snippet_part = _stage(
            live_snippet, snippet_payload, mode=0o644, uid=uid, gid=gid
        )
        inject("after_snippet_part")
        _replace(snippet_part, live_snippet)
        inject("after_snippet_replace")
        config_part = _stage(
            live_config, config_payload, mode=mode, uid=uid, gid=gid
        )
        inject("after_config_part")
        _replace(config_part, live_config)
        inject("after_config_replace")
        inject("interrupt_after_config_replace")
    except BaseException:
        try:
            restored = _stage(
                live_config, original, mode=mode, uid=uid, gid=gid
            )
            _replace(restored, live_config)
            for path in (live_snippet, snippet_part, config_part):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            _fsync_parent(live_config)
            _fsync_parent(live_snippet)
        except BaseException as restore_error:
            raise RuntimeError("preview_file_transaction_restore_failed") from restore_error
        raise


def _main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--live-config", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--live-snippet", required=True, type=Path)
    parser.add_argument("--snippet-candidate", required=True, type=Path)
    parser.add_argument("--mode", required=True, type=lambda value: int(value, 8))
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--gid", required=True, type=int)
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("preview_file_transaction_requires_root")

    previous_handlers: dict[int, object] = {}

    def interrupt(signum: int, _frame: object) -> None:
        raise TransactionInterrupted(f"signal_{signum}")

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, interrupt)
    try:
        install_preview_files(
            live_config=arguments.live_config,
            candidate=arguments.candidate,
            live_snippet=arguments.live_snippet,
            snippet_candidate=arguments.snippet_candidate,
            mode=arguments.mode,
            uid=arguments.uid,
            gid=arguments.gid,
        )
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
