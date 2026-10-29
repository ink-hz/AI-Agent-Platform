#!/usr/bin/env python3
"""Validate an isolated build receipt before copying executables into an image."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path

FILES = frozenset({"php", "libphp.so", "opcache.so"})
PINNED_FROM = "hub.zentao.net/app/zentao@sha256:d7266f4cd4a5da594c987bcdaaac6c74b4a6a2946384cc378dc949b69ea0b99e"
PROVENANCE = frozenset({
    "source_commit", "source_sha256", "php_version", "php_api",
    "zend_extension_build", "base_image",
})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("manifest_duplicate_key")
        result[key] = value
    return result


def verify_artifacts(root: Path, lock: dict) -> dict:
    root = Path(root)
    path = root / "manifest.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("manifest_not_regular")
    manifest = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict) or set(manifest) != PROVENANCE | {"files"}:
        raise ValueError("manifest_contract_invalid")
    if set(lock) != PROVENANCE or any(manifest[key] != lock[key] for key in PROVENANCE):
        raise ValueError("provenance_mismatch")
    files = manifest["files"]
    if not isinstance(files, dict) or not FILES.issubset(files) or any(
        name not in FILES and re.fullmatch(r"lib/[A-Za-z0-9_.+-]+", name) is None
        for name in files
    ):
        raise ValueError("artifact_set_invalid")
    library_dir = root / "lib"
    if library_dir.is_symlink():
        raise ValueError("artifact_not_regular")
    actual_libraries = {
        str(path.relative_to(root)) for path in library_dir.iterdir()
    } if library_dir.exists() else set()
    if actual_libraries != set(files) - FILES:
        raise ValueError("artifact_set_invalid")
    for name, expected in files.items():
        artifact = root / name
        if (artifact.is_symlink() or artifact.parent.is_symlink()
                or not stat.S_ISREG(artifact.lstat().st_mode)):
            raise ValueError("artifact_not_regular")
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError("artifact_hash_invalid")
        with artifact.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("artifact_hash_mismatch")
    return manifest


def prepare_package(package: Path, lock: dict) -> dict:
    package = Path(package)
    if package.is_symlink() or not package.is_dir():
        raise ValueError("package_directory_invalid")
    context_lock = package / "php-source.json"
    if context_lock.is_symlink() or not context_lock.is_file() or context_lock.stat().st_size > 16384:
        raise ValueError("package_source_lock_mismatch")
    if json.loads(context_lock.read_text(), object_pairs_hook=_unique_object) != lock:
        raise ValueError("package_source_lock_mismatch")
    dockerfile = package / "Dockerfile.hardened"
    if dockerfile.is_symlink() or not dockerfile.is_file():
        raise ValueError("package_base_digest_mismatch")
    from_lines = [line.strip() for line in dockerfile.read_text().splitlines() if line.strip().upper().startswith("FROM ")]
    if from_lines != ["FROM " + PINNED_FROM]:
        raise ValueError("package_base_digest_mismatch")
    runtime = package / "php-runtime"
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError("package_directory_invalid")
    manifest = verify_artifacts(runtime, lock)
    for output in (runtime / "artifact.sha256", package / "package-receipt.json"):
        if output.is_symlink() or (output.exists() and not output.is_file()):
            raise ValueError("package_output_invalid")
    # The independently supplied checksum is not an authority. Generate it
    # from the manifest that was just checked against the actual Docker COPY.
    checksum = "".join(digest + "  " + name + "\n" for name, digest in sorted(manifest["files"].items()))
    (runtime / "artifact.sha256").write_text(checksum)
    receipt = {
        "source_commit": manifest["source_commit"], "vendor_base": PINNED_FROM,
        "source_lock_sha256": hashlib.sha256(context_lock.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256((runtime / "manifest.json").read_bytes()).hexdigest(),
        "artifact_checksum_sha256": hashlib.sha256(checksum.encode()).hexdigest(),
    }
    (package / "package-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def build_package(package: Path, lock: dict, *, run=subprocess.run) -> None:
    receipt = prepare_package(package, lock)
    print("PHP_PACKAGE_VERIFIED " + receipt["source_commit"], flush=True)
    run(["docker", "build", "--network", "none", "--pull=false", "-f", str(package / "Dockerfile.hardened"),
         "-t", "orbbec-sdk-zentao:22.6-php81-security-20261029", str(package)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path, nargs="?")
    parser.add_argument("--build-package", type=Path)
    args = parser.parse_args()
    lock = json.loads(Path(__file__).with_name("php-source.json").read_text())
    if args.build_package is not None:
        if args.artifacts is not None:
            parser.error("choose artifacts verification or package build")
        build_package(args.build_package, lock)
        return
    if args.artifacts is None:
        parser.error("artifacts or --build-package is required")
    manifest = verify_artifacts(args.artifacts, lock)
    print("PHP_ARTIFACTS_VERIFIED " + manifest["source_commit"])


if __name__ == "__main__":
    main()
