from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.hr.intelligence_documents import IntelligenceDocumentStore
from app.hr.panorama_repository import PanoramaNotFound, PanoramaUnavailable


class Repository:
    def __init__(self, record):
        self.record = record

    def bundle(self, bundle_id):
        assert bundle_id == self.record["bundle_id"]
        return self.record


def _fixture(tmp_path: Path):
    bundle_id = uuid4()
    root = tmp_path / "published"
    bundle = root / "bundles" / str(bundle_id)
    bundle.mkdir(parents=True)
    pdf = b"prebuilt-pdf"
    evidence = b"archived-source"
    evidence_sha = hashlib.sha256(evidence).hexdigest()
    (bundle / "report.pdf").write_bytes(pdf)
    markdown = b"# Company\n\n## Facts\n\nVerified fact.\n"
    markdown_path = bundle / "agent/companies/hesai.md"
    markdown_path.parent.mkdir(parents=True)
    markdown_path.write_bytes(markdown)
    evidence_path = bundle / "evidence" / "sha256" / evidence_sha[:2] / evidence_sha
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_bytes(evidence)
    record = {
        "bundle_id": bundle_id,
        "bundle_locator": f"bundles/{bundle_id}",
        "document_index": {"report.pdf": {"sha256": hashlib.sha256(pdf).hexdigest(), "size_bytes": len(pdf), "mime": "application/pdf"}},
        "agent_document_index": {"agent/companies/hesai.md": {"sha256": hashlib.sha256(markdown).hexdigest(), "size_bytes": len(markdown), "mime": "text/markdown; charset=utf-8"}},
        "evidence_index": [{"sha256": evidence_sha, "locator": f"evidence/sha256/{evidence_sha[:2]}/{evidence_sha}", "mime": "text/html; charset=utf-8", "size_bytes": len(evidence)}],
    }
    return IntelligenceDocumentStore(root, Repository(record)), record, bundle


def test_reads_only_hash_verified_prebuilt_documents(tmp_path: Path) -> None:
    store, record, _bundle = _fixture(tmp_path)
    selected = store.read_document(record["bundle_id"], "report.pdf")
    assert selected.body == b"prebuilt-pdf"
    assert selected.sha256 == hashlib.sha256(selected.body).hexdigest()
    assert selected.mime == "application/pdf"


def test_rejects_a_tampered_prebuilt_document(tmp_path: Path) -> None:
    store, record, bundle = _fixture(tmp_path)
    (bundle / "report.pdf").write_bytes(b"tampered-pdf")
    with pytest.raises(PanoramaUnavailable, match="checksum"):
        store.read_document(record["bundle_id"], "report.pdf")


def test_reads_only_evidence_listed_in_the_selected_bundle(tmp_path: Path) -> None:
    store, record, _bundle = _fixture(tmp_path)
    sha256 = record["evidence_index"][0]["sha256"]
    selected = store.read_evidence(record["bundle_id"], sha256)
    assert selected.body == b"archived-source"
    assert selected.mime == "text/html"
    with pytest.raises(PanoramaNotFound):
        store.read_evidence(record["bundle_id"], "a" * 64)


def test_rejects_bundle_locator_escape(tmp_path: Path) -> None:
    store, record, _bundle = _fixture(tmp_path)
    record["bundle_locator"] = "../outside"
    with pytest.raises(PanoramaUnavailable, match="locator"):
        store.read_document(record["bundle_id"], "report.pdf")


def test_reads_only_hash_verified_indexed_agent_path(tmp_path: Path) -> None:
    store, record, _bundle = _fixture(tmp_path)

    selected = store.read_indexed_path(
        record["bundle_id"],
        "agent/companies/hesai.md",
        expected_mime="text/markdown",
    )

    assert selected.name == "agent/companies/hesai.md"
    assert selected.body.startswith(b"# Company")


def test_rejects_unindexed_or_escaping_agent_path(tmp_path: Path) -> None:
    store, record, _bundle = _fixture(tmp_path)

    with pytest.raises(PanoramaNotFound):
        store.read_indexed_path(
            record["bundle_id"], "agent/missing.md", expected_mime="text/markdown"
        )
    with pytest.raises(PanoramaUnavailable):
        store.read_indexed_path(
            record["bundle_id"], "../outside.md", expected_mime="text/markdown"
        )
