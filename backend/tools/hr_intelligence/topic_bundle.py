"""Publish reviewed topic metadata without rerunning or rewriting accepted analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.hr.intelligence_bundle import verify_import_bundle
from app.hr.topic_catalog import validate_topic_catalog

from .agent_markdown import compile_agent_markdown
from .bundle import (
    _atomic_write,
    _canonical_json,
    _json_bytes,
    _write_checksums,
    verify_bundle,
)


def derive_topic_bundle(
    source: str | Path, topics: list[dict], *, root: str | Path
) -> Path:
    """Derive a deterministic immutable publication; only metadata and Agent docs change."""
    original = verify_import_bundle(source)
    verify_bundle(source, strict=True)
    catalog = dict(original.catalog) | {"topics": topics}
    validate_topic_catalog(catalog, original.analysis)
    fingerprint = hashlib.sha256(
        _canonical_json(
            {
                "derivation": "reviewed-topic-catalog-v1",
                "source_manifest_sha256": original.manifest_sha256,
                "catalog": catalog,
            }
        ).encode()
    ).hexdigest()
    bundle_id = uuid5(NAMESPACE_URL, f"hr-intelligence:reviewed-topics:{fingerprint}")
    selected_root = Path(root)
    if not selected_root.is_absolute():
        raise ValueError("topic bundle root must be absolute")
    selected_root = selected_root.resolve()
    if selected_root == original.path or original.path in selected_root.parents:
        raise ValueError("topic output cannot modify source bundle")
    final = selected_root / str(bundle_id)
    if final.exists():
        existing = verify_import_bundle(final)
        verify_bundle(final, strict=True)
        if existing.manifest.get("input_sha256") != fingerprint:
            raise ValueError("topic bundle is immutable")
        return final
    # A derived bundle may itself be an input: retain the true original analysis identity.
    origins = {str(unit["bundle_id"]) for unit in original.analysis}
    if len(origins) > 1:
        raise ValueError("topic analysis has mixed origins")
    analysis_origin = UUID(next(iter(origins))) if origins else original.bundle_id
    package = compile_agent_markdown(
        bundle_id=bundle_id,
        generated_at=original.generated_at,
        catalog=catalog,
        coverage=original.coverage,
        aggregates=original.aggregates,
        analyses=original.analysis,
        analysis_bundle_id=analysis_origin,
    )
    selected_root.mkdir(parents=True, exist_ok=True)
    staging = selected_root / f".{bundle_id}.{uuid4().hex}.building"
    try:
        shutil.copytree(original.path, staging)
        if (staging / "agent").exists():
            shutil.rmtree(staging / "agent")
        _atomic_write(staging / "source-catalog.json", _json_bytes(catalog))
        for relative, body in package.files.items():
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(destination, body)
        manifest = dict(original.manifest) | {
            "schema_version": 2,
            "bundle_id": str(bundle_id),
            "input_sha256": fingerprint,
            "agent_chunk_count": len(package.chunks),
            "agent_document_index": {
                name: {
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                    "mime": "application/json"
                    if name.endswith(".json")
                    else "text/markdown; charset=utf-8",
                }
                for name, body in sorted(package.files.items())
            },
            "provenance": {
                "kind": "reviewed_topic_catalog",
                "source_bundle_id": str(original.bundle_id),
                "source_manifest_sha256": original.manifest_sha256,
                "analysis_bundle_id": str(analysis_origin),
                "analysis_generated_at": original.generated_at.isoformat(),
                "analysis_sha256": hashlib.sha256(
                    (original.path / "analysis.json").read_bytes()
                ).hexdigest(),
                "origin_documents": original.manifest["document_index"],
                "origin_documents_bundle_id": original.manifest.get(
                    "provenance", {}
                ).get("origin_documents_bundle_id", str(original.bundle_id)),
                "reviewed_catalog_sha256": hashlib.sha256(
                    _json_bytes(topics)
                ).hexdigest(),
            },
        }
        _atomic_write(staging / "manifest.json", _json_bytes(manifest))
        _write_checksums(staging)
        verify_import_bundle(staging, expected_bundle_id=bundle_id)
        verify_bundle(staging, strict=True, expected_bundle_id=bundle_id)
        # Recheck immutable source after copying to detect concurrent source changes.
        if verify_import_bundle(source).manifest_sha256 != original.manifest_sha256:
            raise ValueError("topic source changed during derivation")
        os.rename(staging, final)
        return final
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--topics", required=True, type=Path, help="Reviewed JSON topics array"
    )
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    topics = json.loads(args.topics.read_text("utf-8"))
    print(derive_topic_bundle(args.source, topics, root=args.root))


if __name__ == "__main__":
    main()
