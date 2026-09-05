#!/usr/bin/env python3
"""Verify a Web Research release against its deployed flat directory layout."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

_DIGEST = re.compile(r"[0-9a-f]{64}")
_ENTRY = re.compile(r"([0-9a-f]{64})  (release/(?:schemas/)?[A-Za-z0-9._-]+|bin/[A-Za-z0-9._-]+)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(arguments: list[str]) -> None:
    if len(arguments) != 4:
        raise ValueError
    release_text, runtime_text, expected_manifest, expected_source = arguments
    if not _DIGEST.fullmatch(expected_manifest) or not _DIGEST.fullmatch(expected_source):
        raise ValueError
    release = Path(release_text)
    runtime = Path(runtime_text)
    if not release.is_absolute() or not runtime.is_absolute():
        raise ValueError
    if release.is_symlink() or runtime.is_symlink():
        raise ValueError
    release = release.resolve(strict=True)
    runtime = runtime.resolve(strict=True)
    if release.parent != runtime / "releases" or release.name != expected_manifest:
        raise ValueError
    manifest = release / ".manifest.sha256"
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError
    if _sha256(manifest) != expected_manifest:
        raise ValueError

    seen: set[str] = set()
    source_found = False
    lines = manifest.read_text(encoding="ascii").splitlines()
    if not lines:
        raise ValueError
    for line in lines:
        match = _ENTRY.fullmatch(line)
        if match is None:
            raise ValueError
        digest, relative = match.groups()
        if relative in seen:
            raise ValueError
        seen.add(relative)
        if relative.startswith("release/"):
            deployed = release / relative.removeprefix("release/")
        else:
            deployed = runtime / relative
        if not deployed.is_file() or deployed.is_symlink() or _sha256(deployed) != digest:
            raise ValueError
        if relative == "release/codex-process.mjs":
            if digest != expected_source:
                raise ValueError
            source_found = True
    if not source_found:
        raise ValueError


def main() -> int:
    try:
        verify(sys.argv[1:])
    except (OSError, UnicodeError, ValueError):
        return 1
    print("WEB_RESEARCH_RELEASE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
