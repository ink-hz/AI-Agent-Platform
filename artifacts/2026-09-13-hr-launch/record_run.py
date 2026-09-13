"""Record a local command, its output and reconstructable source snapshot."""
import datetime
import os
import hashlib
import json
from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parents[2]
destination = Path(__file__).resolve().parent / "runs" / sys.argv[1]
destination.mkdir(parents=True, exist_ok=False)
command = sys.argv[2:]
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
patch = subprocess.check_output(
    ["git", "diff", "--binary", "HEAD", "--", "backend", "deploy", "docs", "webui", "HR总体架构设计.md", "HR_Agent工作流.md"], cwd=root,
)
(destination / "source.patch").write_bytes(patch)
untracked = subprocess.check_output(
    ["git", "ls-files", "--others", "--exclude-standard", "-z", "backend", "deploy", "webui"], cwd=root,
).decode().split("\0")
snapshots = {}
for name in untracked:
    path = root / name
    if not name or not path.is_file() or path.is_symlink():
        continue
    target = destination / "untracked" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    data = path.read_bytes()
    target.write_bytes(data)
    snapshots[name] = hashlib.sha256(data).hexdigest()
metadata = {
    "head": head, "command": command, "cwd": str(root),
    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "source_patch_sha256": hashlib.sha256(patch).hexdigest(),
    "untracked_source_snapshots": snapshots,
    "boundary": os.environ.get("HR_RUN_BOUNDARY", "Local synthetic fixtures; each test documents substituted boundaries; no production or model call"),
}
with (destination / "output.log").open("x") as output:
    result = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT, check=False)
metadata.update(exit_code=result.returncode, finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
(destination / "command.json").write_text(json.dumps(metadata, indent=2) + "\n")
print((destination / "output.log").read_text()[-16000:])
raise SystemExit(result.returncode)
