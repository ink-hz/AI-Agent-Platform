from __future__ import annotations

import argparse
import json
import stat
from collections.abc import Iterable, Sequence
from hashlib import sha256
from pathlib import Path

CONTRACT_VERSION = "orbbec-http-task/v1"
REQUIRES_PYTHON = ">=3.11"
IGNORED_DIRECTORY_NAMES = frozenset({"__pycache__", ".pytest_cache", "build"})
IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})


def contract_files(source: Path) -> Iterable[Path]:
    if not source.is_dir():
        raise ValueError(f"contract source is not a directory: {source}")
    candidates = sorted(
        source.rglob("*"), key=lambda path: path.relative_to(source).as_posix()
    )
    for path in candidates:
        relative = path.relative_to(source)
        if any(
            part in IGNORED_DIRECTORY_NAMES or part.endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        if path.suffix in IGNORED_SUFFIXES:
            continue
        if stat.S_ISREG(path.lstat().st_mode):
            yield path


def directory_sha256(source: Path) -> str:
    digest = sha256()
    for path in contract_files(source):
        relative = path.relative_to(source).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def write_manifest(*, source: Path, source_commit: str, output: Path) -> dict[str, str]:
    if len(source_commit) != 40 or any(
        character not in "0123456789abcdef" for character in source_commit
    ):
        raise ValueError(
            "source_commit must be a full lowercase 40-character Git SHA-1"
        )
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "source_commit": source_commit,
        "sha256": directory_sha256(source),
        "requires_python": REQUIRES_PYTHON,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hash the HTTP Task Contract source tree"
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = write_manifest(
        source=args.source,
        source_commit=args.source_commit,
        output=args.output,
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
