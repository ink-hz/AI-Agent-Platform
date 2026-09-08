from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from typing import Any

from app.hr.reference_knowledge import HrKnowledgeError, _resource_metadata


_SOURCE_PREFIX = "bots/hr/knowledge/"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def _git(repo: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *arguments], cwd=repo, check=True, capture_output=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HrKnowledgeError("knowledge source revision unavailable") from exc


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _validate_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise HrKnowledgeError("invalid knowledge path")
    return path


def _manifest_matches(release: Path, expected: dict[str, Any]) -> bool:
    manifest_path = release / "manifest.json"
    if (
        release.is_symlink()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        return False
    try:
        actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if actual != expected:
        return False
    expected_paths = {"manifest.json", *expected["files"]}
    observed_paths: set[str] = set()
    for path in release.rglob("*"):
        if path.is_symlink():
            return False
        if path.is_file():
            observed_paths.add(path.relative_to(release).as_posix())
    if observed_paths != expected_paths:
        return False
    for relative, digest in expected["files"].items():
        path = release.joinpath(*PurePosixPath(relative).parts)
        if path.is_symlink() or not path.is_file():
            return False
        try:
            if _digest(path.read_bytes()) != digest:
                return False
        except OSError:
            return False
    return True


def build_release(source_repo: Path, source_commit: str, releases_root: Path) -> Path:
    source_repo = Path(source_repo)
    releases_root = Path(releases_root)
    if _COMMIT.fullmatch(source_commit) is None:
        raise HrKnowledgeError("invalid source_commit")
    resolved = (
        _git(
            source_repo,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{source_commit}^{{commit}}",
        )
        .decode("ascii")
        .strip()
    )
    if resolved != source_commit:
        raise HrKnowledgeError("invalid source_commit")
    tree = _git(
        source_repo,
        "ls-tree",
        "-rz",
        "--full-tree",
        source_commit,
        "--",
        _SOURCE_PREFIX.rstrip("/"),
    )
    blobs: list[tuple[str, str]] = []
    for record in tree.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = header.decode("ascii").split(" ")
            git_path = raw_path.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise HrKnowledgeError("invalid knowledge path") from exc
        if (
            kind != "blob"
            or mode == "120000"
            or not git_path.startswith(_SOURCE_PREFIX)
        ):
            raise HrKnowledgeError("invalid knowledge path")
        relative = git_path.removeprefix(_SOURCE_PREFIX)
        _validate_relative(relative)
        blobs.append((relative, object_id))
    relative_names = {relative for relative, _ in blobs}
    if "README.md" not in relative_names or not any(
        name.startswith("sources/") and name.endswith(".md") for name in relative_names
    ):
        raise HrKnowledgeError("knowledge release is incomplete")
    resource_paths = sorted(
        relative
        for relative in relative_names
        if not relative.startswith("sources/")
        and PurePosixPath(relative).name != "README.md"
        and PurePosixPath(relative).suffix == ".md"
    )
    if not resource_paths:
        raise HrKnowledgeError("knowledge release is incomplete")

    releases_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{source_commit}.", dir=releases_root))
    try:
        files: dict[str, str] = {}
        for relative, object_id in blobs:
            destination = staging.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            body = _git(source_repo, "cat-file", "blob", object_id)
            destination.write_bytes(body)
            files[relative] = _digest(body)
        resources = [
            _resource_metadata(staging / relative, relative, files[relative])
            for relative in resource_paths
        ]
        identities = [(item["id"], item["revision"]) for item in resources]
        if len({item["id"] for item in resources}) != len(resources) or len(
            set(identities)
        ) != len(resources):
            raise HrKnowledgeError("knowledge resource IDs/revisions must be unique")
        manifest = {
            "source_commit": source_commit,
            "index_path": "README.md",
            "files": dict(sorted(files.items())),
            "resources": resources,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        destination = releases_root / source_commit
        if destination.exists() or destination.is_symlink():
            if _manifest_matches(destination, manifest):
                return destination
            raise HrKnowledgeError(
                "immutable knowledge release already exists with changed content"
            )
        os.replace(staging, destination)
        staging = Path()
        return destination
    finally:
        if staging != Path() and staging.exists():
            shutil.rmtree(staging)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an immutable HR knowledge release"
    )
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--releases-root", type=Path, required=True)
    arguments = parser.parse_args()
    print(
        build_release(
            arguments.source_repo, arguments.source_commit, arguments.releases_root
        )
    )


if __name__ == "__main__":
    main()
