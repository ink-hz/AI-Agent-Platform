from __future__ import annotations

import os
from pathlib import Path
import stat


class SecretFileUnavailable(RuntimeError):
    pass


def read_secret_file(path: str, *, max_bytes: int = 16_384) -> str:
    """Read one current-user-only secret without exposing failure details."""
    try:
        secret_path = Path(path)
        if not secret_path.is_absolute() or max_bytes <= 0:
            raise SecretFileUnavailable("secret file unavailable")

        metadata = secret_path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SecretFileUnavailable("secret file unavailable")
        if metadata.st_uid != os.getuid():
            raise SecretFileUnavailable("secret file unavailable")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise SecretFileUnavailable("secret file unavailable")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(secret_path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise SecretFileUnavailable("secret file unavailable")
            payload = os.read(descriptor, max_bytes + 1)
        finally:
            os.close(descriptor)

        if len(payload) > max_bytes:
            raise SecretFileUnavailable("secret file unavailable")
        value = payload.decode("utf-8").strip()
        if not value:
            raise SecretFileUnavailable("secret file unavailable")
        return value
    except SecretFileUnavailable:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise SecretFileUnavailable("secret file unavailable") from error
