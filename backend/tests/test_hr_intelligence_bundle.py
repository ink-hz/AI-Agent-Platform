import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from tools.hr_intelligence.bundle import (
    BundleInputs,
    BundleVerificationError,
    build_bundle,
    verify_bundle,
)
from tools.hr_intelligence.models import NormalizedJob

NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)
EVIDENCE_BODY = b"public source evidence"
EVIDENCE_SHA256 = hashlib.sha256(EVIDENCE_BODY).hexdigest()
REQUIRED = {
    "manifest.json",
    "source-catalog.json",
    "source-coverage.json",
    "raw-evidence-index.json",
    "normalized-jobs.jsonl",
    "aggregates.json",
    "analysis.json",
    "analysis-usage.json",
    "report.md",
    "report.pdf",
    "report.xlsx",
    "checksums.sha256",
    "evidence",
}


def _inputs(tmp_path: Path) -> BundleInputs:
    bundle_id = uuid4()
    evidence_root = tmp_path / "evidence-source"
    evidence_path = evidence_root / "sha256" / EVIDENCE_SHA256[:2] / EVIDENCE_SHA256
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_bytes(EVIDENCE_BODY)
    return BundleInputs(
        bundle_id=bundle_id,
        generated_at=NOW,
        source_catalog={
            "schema_version": 1,
            "companies": [
                {
                    "company_key": "hesai",
                    "canonical_name": "禾赛科技",
                    "approved_urls": ["https://example.com/jobs"],
                }
            ],
        },
        source_coverage={
            "schema_version": 1,
            "companies": [
                {
                    "company_key": "hesai",
                    "state": "succeeded",
                    "observed_at": NOW.isoformat(),
                    "job_count": 1,
                }
            ],
        },
        jobs=(
            NormalizedJob(
                job_id=uuid4(),
                source_id=uuid4(),
                company_key="hesai",
                public_job_key="algorithm-1",
                title="高级点云算法工程师",
                location="上海",
                duty_excerpt="负责激光雷达点云算法",
                requirement_excerpt="硕士，熟悉 C++ 和 Python",
                source_url="https://example.com/jobs/algorithm-1",
                evidence_sha256=EVIDENCE_SHA256,
                observed_at=NOW,
            ),
        ),
        aggregates={"schema_version": 2, "tracks": {"social": 1}},
        analyses=(),
        evidence_root=evidence_root,
    )


def test_verified_bundle_contains_every_required_file(tmp_path) -> None:
    inputs = _inputs(tmp_path)

    path = build_bundle(inputs, root=tmp_path / "bundles")
    verified = verify_bundle(path, strict=True)

    assert {item.name for item in path.iterdir()} == REQUIRED
    assert verified.bundle_id == inputs.bundle_id
    assert verified.job_count == 1


def test_bundle_evidence_index_preserves_archived_mime_type(tmp_path) -> None:
    inputs = _inputs(tmp_path)
    evidence_path = (
        inputs.evidence_root / "sha256" / EVIDENCE_SHA256[:2] / EVIDENCE_SHA256
    )
    metadata = {
        "mime": "application/json; charset=utf-8",
        "response_headers": {"content-type": "application/json; charset=utf-8"},
        "sha256": EVIDENCE_SHA256,
        "size_bytes": len(EVIDENCE_BODY),
        "source_url": "https://example.com/jobs",
    }
    body = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    metadata_sha = hashlib.sha256(body.encode()).hexdigest()
    evidence_path.with_name(
        f"{EVIDENCE_SHA256}.metadata.{metadata_sha}.json"
    ).write_text(body, encoding="utf-8")

    path = build_bundle(inputs, root=tmp_path / "bundles")
    index = json.loads((path / "raw-evidence-index.json").read_text("utf-8"))

    assert index[0]["mime"] == "application/json; charset=utf-8"


def test_verification_fails_after_tampering(tmp_path) -> None:
    path = build_bundle(_inputs(tmp_path), root=tmp_path / "bundles")
    (path / "analysis.json").write_text("{}", encoding="utf-8")

    with pytest.raises(BundleVerificationError, match="checksum"):
        verify_bundle(path, strict=True)


def test_existing_bundle_is_immutable(tmp_path) -> None:
    inputs = _inputs(tmp_path)
    path = build_bundle(inputs, root=tmp_path / "bundles")

    assert build_bundle(inputs, root=tmp_path / "bundles") == path
    changed = replace(
        inputs,
        aggregates={"schema_version": 2, "tracks": {"social": 2}},
    )
    with pytest.raises(BundleVerificationError, match="immutable"):
        build_bundle(changed, root=tmp_path / "bundles")
