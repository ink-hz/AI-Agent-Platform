"""Build a content-addressed public HR release from reviewed repository assets."""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import yaml


def build(source, output):
    source, output = Path(source), Path(output)
    provenance = json.loads((source / "provenance.json").read_text())
    files = {}
    resources = []
    for item in provenance["files"]:
        path = Path(item["path"])
        if path.is_absolute() or ".." in path.parts or (source / path).is_symlink():
            raise ValueError("invalid content path")
        raw = (source / path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item["sha256"]:
            raise ValueError("source digest mismatch")
        files[str(path)] = raw
        text = raw.decode("utf8")
        if path.parts[0] == "methods":
            parts = text.split("---", 2)
            if len(parts) != 3 or parts[0].strip():
                raise ValueError("frontmatter required")
            meta = yaml.safe_load(parts[1])
            identity = meta["id"]
            revision = str(meta["revision"])
            title = meta["title"]
            if identity != path.stem:
                raise ValueError("content identity mismatch")
            paragraphs = parts[2].split("\n\n")
            description = next(
                (
                    p.strip()
                    for p in paragraphs
                    if p.strip() and not p.lstrip().startswith("#")
                ),
                title,
            )
        elif path.name.startswith("2026-"):
            identity = path.stem
            revision = provenance["source_commit"]
            title = next(
                line.lstrip("# ") for line in text.splitlines() if line.startswith("# ")
            )
            description = (
                "来源核验与适配边界"
                if "sources" in path.stem
                else "公开 JD 阅读案例与证据边界"
            )
        else:
            continue
        resources.append(
            {
                "ref": {
                    "kind": "method",
                    "id": identity,
                    "revision": revision,
                    "sha256": digest,
                },
                "path": str(path),
                "title": title,
                "description": description,
                "objects": [],
            }
        )
    role = (source / "role.md").read_bytes()
    files["role.md"] = role
    files["provenance.json"] = (source / "provenance.json").read_bytes()
    identity = hashlib.sha256(
        b"".join(name.encode() + b"\0" + files[name] for name in sorted(files))
    ).hexdigest()
    release_id = "hr-" + identity[:24]
    manifest = {
        "release_id": release_id,
        "role": {"path": "role.md", "sha256": hashlib.sha256(role).hexdigest()},
        "resources": sorted(resources, key=lambda i: i["ref"]["id"]),
    }
    files["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode()
    releases = output / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or releases.is_symlink():
        raise ValueError("symlink release root")
    target = releases / release_id
    if target.exists():
        if target.is_symlink() or any(
            (target / p).is_symlink()
            or not (target / p).is_file()
            or (target / p).read_bytes() != raw
            for p, raw in files.items()
        ):
            raise ValueError("immutable release mismatch")
    else:
        with tempfile.TemporaryDirectory(dir=releases, prefix=".build-") as temp:
            stage = Path(temp) / release_id
            stage.mkdir()
            for name, raw in files.items():
                dest = stage / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
            os.rename(stage, target)
    fd, temp = tempfile.mkstemp(dir=output, prefix=".current-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump({"release_id": release_id}, handle)
        os.replace(temp, output / "current.json")
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).parents[2] / "hr_agent_knowledge"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.source, args.output)
    print(
        json.dumps(
            {
                "release_id": manifest["release_id"],
                "resources": len(manifest["resources"]),
            }
        )
    )
