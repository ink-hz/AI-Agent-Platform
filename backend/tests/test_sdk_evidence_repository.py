from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi import HTTPException

from app.sdk_workbench.evidence_repository import EvidenceRepository
from app.sdk_workbench.repository import SdkRepository
from test_control_plane_migration import control_database


@pytest.mark.postgres
def test_evidence_is_immutable_versioned_and_idempotent(control_database):
    environment = control_database["environments"]["production"]
    creator, reviewer = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'Creator','active'),(%s,'Reviewer','active')", (creator, reviewer),
        )
    repository = EvidenceRepository(environment["urls"]["platform_control_app"])
    request_id = uuid.uuid4()
    manifest = {"packaging_sha": "a" * 40, "sdk_version": "1.0", "script_path": "build.sh", "modules": {"OpenOrbbecSDK": "b" * 40}}
    targets = [{"os": "linux", "arch": "x86_64"}]
    first = repository.create_candidate(creator, request_id, "c" * 64, 12, "1", [31], manifest, targets)
    assert first == repository.create_candidate(creator, request_id, "c" * 64, 12, "1", [31], manifest, targets)
    with pytest.raises(HTTPException) as collision:
        repository.create_candidate(creator, request_id, "d" * 64, 12, "1", [31], manifest, targets)
    assert collision.value.status_code == 409
    candidate_id = first["id"]
    assert repository.get_candidate(candidate_id)["manifest"] == manifest
    build = repository.add_build(creator, uuid.uuid4(), "e" * 64, candidate_id, {
        "os": "linux", "arch": "x86_64", "status": "success", "command": "./build.sh", "log_uri": "https://example.test/log",
        "artifact_name": "sdk.tar.gz", "artifact_sha256": "f" * 64, "artifact_size": 123, "artifact_uri": "https://example.test/sdk.tar.gz",
    })
    assert build["id"] > 0
    test = repository.add_test(reviewer, uuid.uuid4(), "1" * 64, build["id"], {
        "suite": "unit", "status": "passed", "passed": 20, "failed": 0, "skipped": 0,
        "report_uri": "https://example.test/report", "device": "", "firmware": "",
    })
    assert test["id"] > 0
    detail = repository.get_candidate(candidate_id)
    assert detail["evidence_version"] == 2
    assert detail["builds"][0]["artifact_sha256"] == "f" * 64
    assert detail["tests"][0]["suite"] == "unit"
    assert detail["approvals"] == []
    assert repository.list_candidates(1)["items"][0]["id"] == candidate_id
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(environment["urls"]["platform_control_app"]) as connection:
            connection.execute("update sdk.candidates set story_version='2' where id=%s", (candidate_id,))

    repository.add_test(reviewer, uuid.uuid4(), "2" * 64, build["id"], {
        "suite": "hardware", "status": "passed", "passed": 2, "failed": 0, "skipped": 0,
        "report_uri": "https://example.test/hardware", "device": "Gemini 2", "firmware": "1.0",
    })
    approved = repository.add_approval(reviewer, uuid.uuid4(), "3" * 64, candidate_id, 3, "证据齐全")
    released = repository.add_release(reviewer, uuid.uuid4(), "4" * 64, candidate_id, approved["id"], 3,
                                      "stable", "https://example.test/rollback", datetime.now(timezone.utc), ["f" * 64])
    assert repository.get_candidate(candidate_id)["releases"][0]["id"] == released["id"]
    sdk = SdkRepository(environment["urls"]["platform_control_app"])
    review_request = uuid.uuid4()
    sdk.reserve(reviewer, review_request, "7" * 64, "review_requirement", 12)
    sdk.add_review(reviewer, 12, "1", "rejected", "新问题", review_request)
    assert repository.get_candidate(candidate_id)["evidence_version"] == 4
    with pytest.raises(HTTPException) as review_stale:
        repository.add_release(reviewer, uuid.uuid4(), "8" * 64, candidate_id, approved["id"], 3,
                               "stable", "https://example.test/rollback", datetime.now(timezone.utc), ["f" * 64])
    assert review_stale.value.status_code == 409
    repository.add_test(reviewer, uuid.uuid4(), "5" * 64, build["id"], {
        "suite": "hardware", "status": "failed", "passed": 0, "failed": 1, "skipped": 0,
        "report_uri": "https://example.test/fail", "device": "Gemini 2", "firmware": "1.0",
    })
    with pytest.raises(HTTPException) as stale:
        repository.add_release(reviewer, uuid.uuid4(), "6" * 64, candidate_id, approved["id"], 3,
                               "stable", "https://example.test/rollback", datetime.now(timezone.utc), ["f" * 64])
    assert stale.value.status_code == 409
    with psycopg.connect(environment["admin"]) as connection:
        audit = connection.execute(
            "select kind,object_id from sdk.evidence_requests where actor_id=%s and request_id=%s",
            (creator, request_id),
        ).fetchone()
        created_row = connection.execute("select request_id from sdk.candidates where id=%s", (candidate_id,)).fetchone()
    assert audit == ("candidate", candidate_id)
    assert created_row[0] == request_id
