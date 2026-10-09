#!/usr/bin/env python3
"""Install one forced-command SSH key from a trusted administrator's stdin."""

from __future__ import annotations

import fcntl
import os
import re
import sys
from pathlib import Path


PUBLIC_KEY = re.compile(r"ssh-ed25519 ([A-Za-z0-9+/=]{60,120}) orbbec-hr-position-sync\Z")
IMPORT_SCRIPT = "/opt/orbbec-agent-platform/current/deploy/cloud/forced-hr-position-import.sh"
FORCED_COMMAND = f"/bin/bash {IMPORT_SCRIPT}"
OPTIONS = (
    f'restrict,command="{FORCED_COMMAND}",no-pty,no-agent-forwarding,'
    "no-port-forwarding,no-X11-forwarding"
)
LEGACY_OPTIONS = OPTIONS.replace(FORCED_COMMAND, IMPORT_SCRIPT)


def install(public_line: str, authorized_keys: Path, *, expected_uid: int = 0) -> bool:
    match = PUBLIC_KEY.fullmatch(public_line.strip())
    if match is None:
        raise ValueError("invalid HR position sync public key")
    if authorized_keys.is_symlink() or not authorized_keys.is_file():
        raise ValueError("authorized_keys must already be a regular file")
    stat = authorized_keys.stat()
    if stat.st_uid != expected_uid or (stat.st_mode & 0o777) != 0o600:
        raise ValueError("authorized_keys owner or mode invalid")
    original = authorized_keys.read_text(encoding="utf-8")
    key_blob = match.group(1)
    desired = f"{OPTIONS} {public_line.strip()}"
    matching = [line for line in original.splitlines() if key_blob in line]
    legacy = f"{LEGACY_OPTIONS} {public_line.strip()}"
    if len(matching) > 1 or (matching and matching[0] not in (desired, legacy)):
        raise ValueError("key already installed with a different command")
    if matching == [desired]:
        return False
    if matching == [legacy]:
        next_text = original.replace(legacy, desired, 1)
    else:
        next_text = original + ("" if not original or original.endswith("\n") else "\n") + desired + "\n"
    temporary = authorized_keys.with_name(".authorized_keys.hr-position-sync.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(next_text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, authorized_keys)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("HR_POSITION_KEY_INSTALL_FAILED")
    key = sys.stdin.read(512)
    if len(key) >= 512:
        raise SystemExit("HR_POSITION_KEY_INSTALL_FAILED")
    authorized = Path("/root/.ssh/authorized_keys")
    lock_path = Path("/root/.ssh/.hr-position-sync-key.lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            changed = install(key, authorized)
        except (OSError, ValueError):
            raise SystemExit("HR_POSITION_KEY_INSTALL_FAILED") from None
    print("HR_POSITION_KEY_INSTALLED" if changed else "HR_POSITION_KEY_ALREADY_INSTALLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
