"""Read-only verification of the E review package; run from the repository root."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote


root = Path.cwd()
package = root / "artifacts/2026-09-11-hr-e-review"
manifest = json.loads((package / "manifest.json").read_text())
problems = []
listed = {item["path"] for item in manifest["files"]}
actual = {
    str(path.relative_to(package))
    for path in package.rglob("*")
    if path.is_file() and path != package / "manifest.json"
}
for item in manifest["files"]:
    path = package / item["path"]
    if not path.is_file():
        problems.append("missing: " + item["path"])
        continue
    data = path.read_bytes()
    if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        problems.append("bytes/hash: " + item["path"])
if listed != actual:
    problems.append({"unlisted": sorted(actual - listed), "absent": sorted(listed - actual)})

fingerprints = json.loads((package / "final/code-fingerprints.json").read_text())
checked_fingerprints = 0
for group in ("file_sha256", "frontend_test_sha256"):
    for name, expected in fingerprints[group].items():
        path = root / name
        checked_fingerprints += 1
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append("code fingerprint: " + name)

history = []
historical_record = json.loads((package / "final/historical-evidence-check.json").read_text())
for check in historical_record["historical_checks"]:
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", check["baseline"], "--", *check["paths"]], text=True
    ).splitlines()
    changed = [
        name for name in paths
        if not (root / name).is_file()
        or (root / name).read_bytes() != subprocess.check_output(["git", "show", check["baseline"] + ":" + name])
    ]
    history.append({"baseline": check["baseline"], "files_checked": len(paths), "changed": changed})
    if changed or len(paths) != check["files_checked"]:
        problems.append("historical evidence: " + check["baseline"])

migrations = []
for number in historical_record["existing_migrations_unchanged"]:
    paths = sorted((root / "backend/control_migrations").rglob(f"{number:03d}_*.sql"))
    if not paths:
        problems.append(f"missing migration: {number}")
    for path in paths:
        name = str(path.relative_to(root))
        if path.read_bytes() != subprocess.check_output(["git", "show", "b974a87:" + name]):
            problems.append("migration changed: " + name)
        migrations.append(name)

link_record = json.loads((package / "final/document-links.json").read_text())
links_checked = 0
for name in link_record["files"]:
    path = root / name
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
        if target.startswith(("http:", "https:", "mailto:", "#")):
            continue
        target = unquote(target.split("#", 1)[0].strip("<>"))
        links_checked += 1
        if not (path.parent / target).exists():
            problems.append("document link: " + name + " -> " + target)

print(json.dumps({
    "reviewed_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "manifest_files_checked": len(listed),
    "code_fingerprints_checked": checked_fingerprints,
    "historical_checks": history,
    "unchanged_migrations": migrations,
    "local_document_links_checked": links_checked,
    "problems": problems,
}, indent=2, ensure_ascii=False))
raise SystemExit(bool(problems))
