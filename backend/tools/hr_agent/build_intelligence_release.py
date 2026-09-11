"""Build a local HR release from reviewed methods and an explicit public Bundle v2.

This copies authored Markdown, without collecting, analyzing, or certifying its
professional quality. Output selection is explicit; no production paths/defaults.
"""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import yaml

from app.hr.intelligence_bundle import BundleVerificationError, verify_import_bundle
from app.hr_agent.resources import PublishedKnowledge
from app.hr_agent.types import validate_contract
from tools.hr_agent.build_knowledge_release import build as build_methods


def _json(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def build(source, bundle_path, output):
    bundle = verify_import_bundle(bundle_path)
    if bundle.schema_version != 2:
        raise BundleVerificationError("Agent Markdown requires Bundle v2")
    output = Path(output)
    source = Path(source)
    if any(p.is_symlink() for p in (output, *output.parents)):
        # macOS /var itself may be an alias: callers can resolve that root explicitly.
        raise ValueError("symlink output root")
    if output.resolve() == bundle.path or bundle.path in output.resolve().parents:
        raise ValueError("output must not modify source bundle")
    if (
        output.resolve() == source.resolve()
        or source.resolve() in output.resolve().parents
    ):
        raise ValueError("output must not modify reviewed source")
    files = {}
    with tempfile.TemporaryDirectory(prefix="hr-method-input-") as temp:
        method_manifest = build_methods(source, Path(temp))
        base = Path(temp) / "releases" / method_manifest["release_id"]
        files.update(
            {
                p.relative_to(base).as_posix(): p.read_bytes()
                for p in base.rglob("*")
                if p.is_file() and p.name != "manifest.json"
            }
        )
    resources = list(method_manifest["resources"])
    for name, record in sorted(bundle.agent_document_index.items()):
        if not name.endswith(".md"):
            continue
        raw = (bundle.path / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise BundleVerificationError("source changed after verification")
        text = raw.decode("utf8")
        parts = text.split("---", 2)
        if len(parts) != 3 or parts[0].strip():
            raise BundleVerificationError("Agent Markdown frontmatter required")
        meta = yaml.safe_load(parts[1])
        if not isinstance(meta, dict) or str(meta.get("bundle_id")) != str(
            bundle.bundle_id
        ):
            raise BundleVerificationError("Agent Markdown bundle identity mismatch")
        scope, scope_key = meta.get("scope"), meta.get("scope_key")
        if (
            not isinstance(scope, str)
            or not scope
            or not isinstance(scope_key, str)
            or not scope_key
        ):
            raise BundleVerificationError("Agent Markdown scope required")
        objects = (
            []
            if scope in ("index", "executive")
            else [
                {
                    "kind": "company" if scope == "company" else "topic",
                    "id": scope_key if scope == "company" else f"{scope}:{scope_key}",
                }
            ]
        )
        for obj in objects:
            validate_contract("ObjectRef", obj)
        title = next(
            (
                line[2:].strip()
                for line in parts[2].splitlines()
                if line.startswith("# ")
            ),
            None,
        )
        if not title:
            raise BundleVerificationError("Agent Markdown title required")
        dest = "intelligence/" + name
        files[dest] = raw
        resources.append(
            {
                "ref": {
                    "kind": "intelligence",
                    "id": "intelligence:"
                    + ":".join(Path(name).with_suffix("").parts[1:]),
                    "revision": str(bundle.bundle_id),
                    "sha256": record["sha256"],
                },
                "path": dest,
                "title": title,
                "description": f"{scope}/{scope_key}；资料时点 {bundle.generated_at.isoformat()}；"
                f"覆盖 {meta.get('coverage', 'unknown')}；原报告适用边界见正文",
                "objects": objects,
            }
        )
    if len(resources) == len(method_manifest["resources"]):
        raise BundleVerificationError("Agent Markdown unavailable")
    manifest_raw = (bundle.path / "manifest.json").read_bytes()
    if hashlib.sha256(manifest_raw).hexdigest() != bundle.manifest_sha256:
        raise BundleVerificationError("source changed after verification")
    files["intelligence-source-manifest.json"] = manifest_raw
    files["intelligence-provenance.json"] = _json(
        {
            "bundle_id": str(bundle.bundle_id),
            "manifest_sha256": bundle.manifest_sha256,
            "generated_at": bundle.generated_at.isoformat(),
            "method_release": method_manifest["release_id"],
            "quality_acceptance": "not_assessed_by_import",
        }
    )
    identity = hashlib.sha256(
        b"".join(name.encode() + b"\0" + files[name] for name in sorted(files))
        + _json(resources)
    ).hexdigest()
    release_id = "hr-intelligence-" + identity[:24]
    manifest = {
        "release_id": release_id,
        "role": method_manifest["role"],
        "resources": resources,
    }
    files["manifest.json"] = _json(manifest)
    releases = output / "releases"
    if releases.is_symlink():
        raise ValueError("symlink release root")
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / release_id
    if target.exists() or target.is_symlink():
        if (
            target.is_symlink()
            or not target.is_dir()
            or any(p.is_symlink() for p in target.rglob("*"))
            or {
                p.relative_to(target).as_posix()
                for p in target.rglob("*")
                if p.is_file()
            }
            != set(files)
        ):
            raise ValueError("immutable release mismatch")
        if any((target / name).read_bytes() != raw for name, raw in files.items()):
            raise ValueError("immutable release mismatch")
    else:
        with tempfile.TemporaryDirectory(dir=releases, prefix=".build-") as temp:
            stage = Path(temp) / release_id
            stage.mkdir()
            for name, raw in files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            PublishedKnowledge(stage).check()
            os.rename(stage, target)
    fd, temp = tempfile.mkstemp(dir=output, prefix=".current-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json({"release_id": release_id}))
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
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.source, args.bundle, args.output)
    print(
        json.dumps(
            {"release_id": result["release_id"], "resources": len(result["resources"])}
        )
    )
