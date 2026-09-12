"""Verify this round against immutable Git objects and retained artifact bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXCLUDED = {"manifest.json", "final/manifest-verification.json"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def artifact_files():
    return {
        str(p.relative_to(HERE)): digest(p.read_bytes())
        for p in sorted(HERE.rglob("*"))
        if p.is_file() and str(p.relative_to(HERE)) not in EXCLUDED
    }


parser = argparse.ArgumentParser()
parser.add_argument("--write", metavar="REVIEWED_COMMIT")
args = parser.parse_args()
if args.write:
    commit = git("rev-parse", args.write).decode().strip()
    paths = git("ls-tree", "-r", "--name-only", "-z", commit, "backend", "deploy",
                "HR总体架构设计.md", "HR_Agent工作流.md",
                "docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md",
                "docs/reviews/2026-09-12-hr-e-audit-hardening.md",
                "docs/superpowers/plans/2026-09-12-hr-e-audit-hardening.md").decode().strip("\0").split("\0")
    manifest = {
        "reviewed_commit": commit,
        "artifacts": artifact_files(),
        "sources": {p: digest(git("show", commit + ":" + p)) for p in paths},
        "excluded_self_referential_files": sorted(EXCLUDED),
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

manifest = json.loads((HERE / "manifest.json").read_text())
problems = []
actual = artifact_files()
for path in sorted(actual.keys() | manifest["artifacts"].keys()):
    if actual.get(path) != manifest["artifacts"].get(path):
        problems.append("artifact: " + path)
for path, expected in manifest["sources"].items():
    if digest(git("show", manifest["reviewed_commit"] + ":" + path)) != expected:
        problems.append("git source: " + path)
    if not (ROOT / path).is_file() or digest((ROOT / path).read_bytes()) != expected:
        problems.append("working source: " + path)
history = json.loads((HERE / "final/historical-identity.json").read_text())
for item in history["files"]:
    path = item["path"]
    expected = digest(git("show", history["baseline"] + ":" + path))
    if expected != item["baseline_sha256"] or digest((ROOT / path).read_bytes()) != expected:
        problems.append("historical: " + path)
for item in json.loads((HERE / "forensics/manifest.json").read_text())["items"]:
    if item["path"] and digest((HERE / "forensics" / item["path"]).read_bytes()) != item["sha256"]:
        problems.append("forensic: " + item["path"])
for item in json.loads((HERE / "cosmetic-before/receipt.json").read_text())["files"]:
    if digest((HERE / "cosmetic-before" / item["path"]).read_bytes()) != item["before_sha256"]:
        problems.append("cosmetic before: " + item["path"])
    if digest((ROOT / item["path"]).read_bytes()) != item["after_sha256"]:
        problems.append("cosmetic after: " + item["path"])
print(json.dumps({"reviewed_commit": manifest["reviewed_commit"],
                  "artifacts": len(actual), "sources": len(manifest["sources"]),
                  "historical": len(history["files"]), "problems": problems}, indent=2))
raise SystemExit(bool(problems))
