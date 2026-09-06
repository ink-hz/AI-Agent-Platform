from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.hr.intelligence_documents import IntelligenceDocumentStore
from app.hr.intelligence_markdown import IntelligenceMarkdownStore
from app.hr.panorama_repository import PanoramaUnavailable


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
    path = bundle / "agent/companies/hesai.md"
    path.parent.mkdir(parents=True)
    body = b"# Hesai\n\n## Facts\n\nVerified recruiting fact.\n"
    path.write_bytes(body)
    record = {
        "bundle_id": bundle_id,
        "bundle_locator": f"bundles/{bundle_id}",
        "agent_document_index": {
            "agent/companies/hesai.md": {
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
                "mime": "text/markdown; charset=utf-8",
            }
        },
    }
    start = body.index(b"## Facts")
    chunk_body = body[start:]
    chunk = {
        "chunk_id": "chunk-hesai-facts",
        "path": "agent/companies/hesai.md",
        "byte_start": start,
        "byte_end": len(body),
        "sha256": hashlib.sha256(chunk_body).hexdigest(),
    }
    documents = IntelligenceDocumentStore(root, Repository(record))
    return IntelligenceMarkdownStore(documents), record, chunk


def test_reads_exact_hash_verified_markdown_chunk(tmp_path) -> None:
    store, record, chunk = _fixture(tmp_path)

    selected = store.read_chunk(record["bundle_id"], chunk)

    assert selected.text.startswith("## Facts")
    assert selected.sha256 == chunk["sha256"]


@pytest.mark.parametrize("field", ["byte_end", "sha256", "path"])
def test_rejects_tampered_or_out_of_bounds_chunk(tmp_path, field) -> None:
    store, record, chunk = _fixture(tmp_path)
    if field == "byte_end":
        chunk[field] += 1
    elif field == "sha256":
        chunk[field] = "a" * 64
    else:
        chunk[field] = "../outside.md"

    with pytest.raises(PanoramaUnavailable):
        store.read_chunk(record["bundle_id"], chunk)
