from __future__ import annotations

import argparse
import io
import json
import stat
import subprocess
import tarfile
from collections.abc import Iterable, Sequence
from hashlib import sha256
from pathlib import Path

CONTRACT_VERSION = "orbbec-http-task/v1"
REQUIRES_PYTHON = ">=3.11"
CONTRACT_RELATIVE_PATH = Path("contracts/http_task_v1")
IGNORED_DIRECTORY_NAMES = frozenset({"__pycache__", ".pytest_cache"})
IGNORED_ROOT_DIRECTORY_NAMES = frozenset({"build", "dist"})
IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})


def _is_ignored(relative: Path) -> bool:
    if relative.parts and relative.parts[0] in IGNORED_ROOT_DIRECTORY_NAMES:
        return True
    return any(
        part in IGNORED_DIRECTORY_NAMES or part.endswith(".egg-info")
        for part in relative.parts
    ) or relative.suffix in IGNORED_SUFFIXES


def contract_files(source: Path) -> Iterable[Path]:
    if not source.is_dir():
        raise ValueError(f"contract source is not a directory: {source}")
    candidates = sorted(
        source.rglob("*"), key=lambda path: path.relative_to(source).as_posix()
    )
    for path in candidates:
        relative = path.relative_to(source)
        if _is_ignored(relative):
            continue
        if stat.S_ISREG(path.lstat().st_mode):
            yield path


def directory_sha256(source: Path) -> str:
    return _content_sha256(
        (path.relative_to(source).as_posix(), path.read_bytes())
        for path in contract_files(source)
    )


def _content_sha256(files: Iterable[tuple[str, bytes]]) -> str:
    digest = sha256()
    for relative, content in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def archive_sha256(repository: Path, source_commit: str) -> str:
    if not repository.is_dir():
        raise ValueError(f"repository is not a directory: {repository}")
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "archive",
            "--format=tar",
            f"{source_commit}:{CONTRACT_RELATIVE_PATH.as_posix()}",
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError("source_commit does not contain the HTTP task contract")
    files: list[tuple[str, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            relative = Path(member.name)
            if not member.isfile() or _is_ignored(relative):
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("contract archive contains an unreadable regular file")
            files.append((relative.as_posix(), extracted.read()))
    return _content_sha256(files)


def write_manifest(
    *, repository: Path, source_commit: str, output: Path
) -> dict[str, str]:
    if len(source_commit) != 40 or any(
        character not in "0123456789abcdef" for character in source_commit
    ):
        raise ValueError(
            "source_commit must be a full lowercase 40-character Git SHA-1"
        )
    committed_digest = archive_sha256(repository, source_commit)
    working_source = repository / CONTRACT_RELATIVE_PATH
    working_digest = directory_sha256(working_source)
    if working_digest != committed_digest:
        raise ValueError("working contract tree differs from source_commit")
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "source_commit": source_commit,
        "sha256": committed_digest,
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
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = write_manifest(
        repository=args.repository,
        source_commit=args.source_commit,
        output=args.output,
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
