from __future__ import annotations

import json

import pytest
from tools.hr_intelligence.evidence import (
    EvidenceArchive,
    EvidenceCorrupt,
    EvidencePayload,
)

BODY = '<html><h1 data-job-id="structure-1">高级结构工程师</h1></html>'.encode()


def test_archive_preserves_exact_bytes_and_removes_credentials(tmp_path) -> None:
    archive = EvidenceArchive(tmp_path)
    record = archive.store(
        EvidencePayload(
            source_url="https://a.example/jobs",
            mime="text/html; charset=utf-8",
            body=BODY,
            request_headers={"Authorization": "Bearer secret", "Cookie": "sid=secret"},
            response_headers={
                "ETag": '"version-1"',
                "Set-Cookie": "private=secret",
            },
        )
    )

    assert archive.read(record.sha256) == BODY
    assert record.locator == f"sha256/{record.sha256[:2]}/{record.sha256}"
    assert record.size_bytes == len(BODY)
    metadata = json.loads(record.metadata_json)
    assert metadata["source_url"] == "https://a.example/jobs"
    assert metadata["response_headers"] == {"etag": '"version-1"'}
    assert "secret" not in record.metadata_json
    assert not (tmp_path / ".staging").exists()


def test_archive_is_content_addressed_and_does_not_mutate_existing_data(
    tmp_path,
) -> None:
    archive = EvidenceArchive(tmp_path)
    payload = EvidencePayload(
        source_url="https://a.example/jobs", mime="text/html", body=BODY
    )
    first = archive.store(payload)
    second = archive.store(payload)

    assert first == second
    assert archive.read(first.sha256) == BODY


def test_identical_source_bytes_keep_distinct_immutable_source_metadata(
    tmp_path,
) -> None:
    archive = EvidenceArchive(tmp_path)
    first = archive.store(
        EvidencePayload(
            source_url="https://a.example/jobs", mime="text/html", body=BODY
        )
    )
    second = archive.store(
        EvidencePayload(
            source_url="https://b.example/jobs", mime="text/html", body=BODY
        )
    )

    assert first.sha256 == second.sha256
    assert first.metadata_json != second.metadata_json
    metadata_files = tuple(
        (tmp_path / first.locator).parent.glob(f"{first.sha256}.metadata.*.json")
    )
    assert len(metadata_files) == 2


def test_archive_detects_corruption(tmp_path) -> None:
    archive = EvidenceArchive(tmp_path)
    record = archive.store(
        EvidencePayload(
            source_url="https://a.example/jobs", mime="text/html", body=BODY
        )
    )
    (tmp_path / record.locator).write_bytes(b"changed")

    with pytest.raises(EvidenceCorrupt):
        archive.read(record.sha256)


@pytest.mark.parametrize("root", ["/tmp/evidence", "/opt/app/evidence"])
def test_default_production_boundary_rejects_root_disk_locations(root) -> None:
    with pytest.raises(ValueError, match="/data"):
        EvidenceArchive(root, production=True)
