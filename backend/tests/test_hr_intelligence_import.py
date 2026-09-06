import hashlib
import inspect
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from app.hr.intelligence_bundle import BundleVerificationError, verify_import_bundle
from app.hr.intelligence_import import IntelligenceBundleImporter
from tools.hr_intelligence.bundle import BundleInputs, build_bundle
from tools.hr_intelligence.models import NormalizedJob

NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)


def _bundle(tmp_path: Path):
    evidence_body = b"verified source response"
    evidence_sha = hashlib.sha256(evidence_body).hexdigest()
    evidence_root = tmp_path / "source-evidence"
    evidence = evidence_root / "sha256" / evidence_sha[:2] / evidence_sha
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(evidence_body)
    inputs = BundleInputs(
        bundle_id=uuid4(),
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
                uuid4(),
                uuid4(),
                "hesai",
                "algorithm-1",
                "算法工程师",
                "上海",
                "负责点云算法",
                "熟悉 Python",
                "https://example.com/jobs/1",
                evidence_sha,
                NOW,
            ),
        ),
        aggregates={"schema_version": 2, "tracks": {"social": 1}},
        analyses=(),
        evidence_root=evidence_root,
    )
    return inputs, build_bundle(inputs, root=tmp_path / "bundles")


class Repository:
    def __init__(self) -> None:
        self.by_manifest = {}
        self.current = None
        self.import_calls = 0

    def bundle_by_manifest(self, owner_id, manifest_sha256):
        return self.by_manifest.get((owner_id, manifest_sha256))

    def import_verified_bundle(self, owner_id, bundle):
        self.import_calls += 1
        record = {
            "owner_id": owner_id,
            "bundle_id": bundle.bundle_id,
            "manifest_sha256": bundle.manifest_sha256,
        }
        self.by_manifest[(owner_id, bundle.manifest_sha256)] = record
        self.current = record
        return record


def test_import_is_idempotent_and_tamper_preserves_current(tmp_path) -> None:
    _inputs, path = _bundle(tmp_path)
    owner_id = uuid4()
    repository = Repository()
    importer = IntelligenceBundleImporter(repository)

    first = importer.import_bundle(path, owner_id=owner_id)
    second = importer.import_bundle(path, owner_id=owner_id)

    assert second == first
    assert repository.import_calls == 1
    current = repository.current
    (path / "analysis.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BundleVerificationError, match="checksum"):
        importer.import_bundle(path, owner_id=owner_id)
    assert repository.current == current


def test_import_rejects_bundle_identity_mismatch(tmp_path) -> None:
    inputs, path = _bundle(tmp_path)
    repository = Repository()

    with pytest.raises(BundleVerificationError, match="identity"):
        IntelligenceBundleImporter(repository).import_bundle(
            path,
            owner_id=uuid4(),
            expected_bundle_id=uuid4(),
        )
    assert verify_import_bundle(path).bundle_id == inputs.bundle_id


def test_import_module_has_no_http_or_model_dependency() -> None:
    import app.hr.intelligence_import as module

    source = inspect.getsource(module)
    for forbidden in ("httpx", "Anthropic", "OpenAI", "panorama_analysis"):
        assert forbidden not in source
