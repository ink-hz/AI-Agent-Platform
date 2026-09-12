"""Verify this receipt's files and final source identities without running services."""

import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    evidence = Path(__file__).resolve().parent
    project = evidence.parents[1]
    manifest = json.loads((evidence / "manifest.json").read_text())
    expected = {row["path"]: row for row in manifest["files"]}
    actual = {
        path.relative_to(evidence).as_posix()
        for path in evidence.rglob("*")
        if path.is_file() and path.relative_to(evidence).as_posix() not in manifest["excluded"]
    }
    problems = [f"missing artifact: {name}" for name in sorted(expected.keys() - actual)]
    problems += [f"extra artifact: {name}" for name in sorted(actual - expected.keys())]
    for name, row in expected.items():
        if digest(evidence / name) != row["sha256"]:
            problems.append(f"artifact digest mismatch: {name}")

    sources = json.loads((evidence / "final/source-fingerprints.json").read_text())
    for name, checksum in sources.items():
        if digest(project / name) != checksum:
            problems.append(f"source digest mismatch: {name}")

    history = json.loads((evidence / "final/historical-identity.json").read_text())
    for row in history["files"]:
        if digest(project / row["path"]) != row["baseline_sha256"]:
            problems.append(f"historical digest mismatch: {row['path']}")

    result = {
        "artifact_files": len(expected),
        "source_files": len(sources),
        "historical_files_and_migrations": len(history["files"]),
        "excluded_self_receipts": manifest["excluded"],
        "problems": problems,
    }
    print(json.dumps(result, indent=2))
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
