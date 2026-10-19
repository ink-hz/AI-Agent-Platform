from __future__ import annotations

from copy import deepcopy
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.sdk_workbench.evidence_service import EvidenceService, gate_reasons
from app.sdk_workbench.evidence_repository import EvidenceRepository
from app.sdk_workbench.repository import SdkRepository
from test_sdk_routes import ACTOR, Repository, ZenTao, app_for
from test_control_plane_migration import control_database
import psycopg


MANIFEST = {
    "packaging_sha": "a" * 40, "sdk_version": "1.0.0", "script_path": "build.sh",
    "modules": {name: "b" * 40 for name in (
        "FrameProcessor", "OBEnhancedDepthFilter", "OBFilterLib", "OBSDKPrivFilterLib",
        "OpenOrbbecSDK", "OpenOrbbecSDKExt", "OpenOrbbecViewer", "OpenOrbbecViewer/third_party/OpenOrbbecSDK",
    )},
}


def candidate():
    return {
        "id": 1, "story_id": 12, "story_version": "1", "creator_id": str(ACTOR),
        "task_ids": [31], "manifest": MANIFEST,
        "targets": [{"os": "linux", "arch": "x86_64"}, {"os": "windows", "arch": "x86_64"}],
        "evidence_version": 0, "builds": [], "tests": [], "approvals": [], "releases": [],
    }


def good_build(build_id, os):
    return {"id": build_id, "os": os, "arch": "x86_64", "status": "success", "artifact_sha256": f"{build_id:064x}"}


def good_test(build_id, suite, test_id):
    return {"id": test_id, "build_id": build_id, "suite": suite, "status": "passed", "passed": 1, "failed": 0, "skipped": 0}


def test_gate_requires_latest_successful_build_and_two_test_suites_per_target():
    item = candidate()
    assert "linux/x86_64:build_missing" in gate_reasons(item)
    item["builds"] = [good_build(1, "linux"), good_build(2, "windows")]
    item["tests"] = [good_test(1, "unit", 1), good_test(1, "hardware", 2), good_test(2, "unit", 3), good_test(2, "hardware", 4)]
    assert gate_reasons(item) == []
    item["tests"].append({**good_test(2, "hardware", 5), "status": "blocked"})
    assert "windows/x86_64:hardware_not_passed" in gate_reasons(item)
    item["tests"].pop()
    item["builds"].append({**good_build(3, "linux"), "status": "failed", "artifact_sha256": None})
    assert "linux/x86_64:build_not_successful" in gate_reasons(item)


@pytest.mark.asyncio
async def test_candidate_requires_exact_manifest_current_review_and_task():
    repo, zentao = Repository(), ZenTao()
    evidence = EvidenceService(repo, zentao, FakeEvidence(), product_id=7)
    from app.control_plane.models import AuthContext, Role
    actor = AuthContext(ACTOR, Role.MEMBER, uuid4(), False)
    with pytest.raises(HTTPException) as unreviewed:
        await evidence.create_candidate(actor, uuid4(), 12, [31], MANIFEST, [{"os": "linux", "arch": "x86_64"}])
    assert unreviewed.value.status_code == 409
    repo.reviews.append({"story_id": 12, "story_version": "1", "decision": "approved"})
    repo.tasks.append({"story_id": 12, "task_id": 31})
    bad = deepcopy(MANIFEST)
    bad["modules"].pop("OpenOrbbecSDK")
    with pytest.raises(HTTPException) as incomplete:
        await evidence.create_candidate(actor, uuid4(), 12, [31], bad, [{"os": "linux", "arch": "x86_64"}])
    assert incomplete.value.status_code == 422
    result = await evidence.create_candidate(actor, uuid4(), 12, [31], MANIFEST, [{"os": "linux", "arch": "x86_64"}])
    assert result == {"id": 1}


class FakeEvidence:
    def __init__(self):
        self.item = None
        self.writes = {}

    def replay(self, actor, request_id, digest):
        return self.writes.get((actor, request_id, digest))

    def create_candidate(self, actor, request_id, digest, story_id, story_version, task_ids, manifest, targets):
        self.item = {**candidate(), "task_ids": task_ids, "manifest": manifest, "targets": targets}
        return {"id": 1}

    def get_candidate(self, candidate_id):
        return self.item

    def list_candidates(self, page):
        return {"items": [self.item] if self.item else [], "page": page, "total": int(self.item is not None)}

    def add_approval(self, actor, request_id, digest, candidate_id, version, note):
        self.item["approvals"].append({"id": 1, "actor_id": str(actor), "evidence_version": version})
        return {"id": 1, "evidence_version": version}


@pytest.mark.asyncio
async def test_approval_separates_creator_and_requires_current_evidence():
    repo, zentao, store = Repository(), ZenTao(), FakeEvidence()
    repo.reviews.append({"story_id": 12, "story_version": "1", "decision": "approved"})
    service = EvidenceService(repo, zentao, store, product_id=7)
    from app.control_plane.models import AuthContext, Role
    creator = AuthContext(ACTOR, Role.MEMBER, uuid4(), False)
    store.item = candidate()
    store.item["builds"] = [good_build(1, "linux"), good_build(2, "windows")]
    store.item["tests"] = [good_test(1, "unit", 1), good_test(1, "hardware", 2), good_test(2, "unit", 3), good_test(2, "hardware", 4)]
    store.item["evidence_version"] = 6
    repo.roles[ACTOR] = "release_manager"
    with pytest.raises(HTTPException) as same_actor:
        await service.approve(creator, 1, uuid4(), "准予发布")
    assert same_actor.value.status_code == 409
    other_id = uuid4()
    repo.roles[other_id] = "release_manager"
    approver = AuthContext(other_id, Role.MEMBER, uuid4(), False)
    assert (await service.approve(approver, 1, uuid4(), "准予发布"))["evidence_version"] == 6
    store.item["evidence_version"] = 7
    gate = await service.gate(approver, 1)
    assert not gate["approved"]


@pytest.mark.asyncio
async def test_candidate_api_uses_sdk_identity_and_strict_contract():
    repo, zentao, store = Repository(), ZenTao(), FakeEvidence()
    repo.reviews.append({"story_id": 12, "story_version": "1", "decision": "approved"})
    repo.tasks.append({"story_id": 12, "task_id": 31})
    app = app_for(repo, zentao)
    app.state.sdk_evidence_service = EvidenceService(repo, zentao, store, product_id=7)
    body = {"request_id": str(uuid4()), "story_id": 12, "task_ids": [31], "manifest": MANIFEST,
            "targets": [{"os": "linux", "arch": "x86_64"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.post("/api/v1/sdk/candidates", json={**body, "actor_id": str(uuid4())})
        created = await client.post("/api/v1/sdk/candidates", json=body)
        detail = await client.get("/api/v1/sdk/candidates/1")
        gate = await client.get("/api/v1/sdk/candidates/1/gate")
    assert invalid.status_code == 422
    assert created.status_code == 201 and created.json() == {"id": 1}
    assert detail.status_code == 200 and detail.json()["manifest"] == MANIFEST
    assert gate.status_code == 200 and gate.json()["reasons"] == ["linux/x86_64:build_missing"]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_http_workflow_persists_baseline_evidence_approval_and_release(control_database):
    environment = control_database["environments"]["production"]
    contributor, reviewer, manager = uuid4(), uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'Contributor','active'),(%s,'Reviewer','active'),(%s,'Manager','active')",
            (contributor, reviewer, manager),
        )
    repo = SdkRepository(environment["urls"]["platform_control_app"])
    store = EvidenceRepository(environment["urls"]["platform_control_app"])
    for user, role in ((contributor, "contributor"), (reviewer, "reviewer"), (manager, "release_manager")):
        repo.grant_member(manager, user, role, uuid4())
    review_id = uuid4()
    repo.reserve(reviewer, review_id, "b" * 64, "review_requirement", 12)
    repo.add_review(reviewer, 12, "1", "approved", "范围明确", review_id)
    repo.add_task_link(contributor, 12, 31, uuid4())
    zentao = ZenTao()

    def client_for(actor):
        app = app_for(repo, zentao, actor=actor)
        app.state.sdk_evidence_service = EvidenceService(repo, zentao, store, product_id=7)
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async with client_for(contributor) as client:
        created = await client.post("/api/v1/sdk/candidates", json={
            "request_id": str(uuid4()), "story_id": 12, "task_ids": [31], "manifest": MANIFEST,
            "targets": [{"os": "linux", "arch": "x86_64"}, {"os": "windows", "arch": "x86_64"}],
        })
        assert created.status_code == 201, created.text
        candidate_id = created.json()["id"]
        build_ids = []
        for os in ("linux", "windows"):
            built = await client.post(f"/api/v1/sdk/candidates/{candidate_id}/builds", json={
                "request_id": str(uuid4()), "os": os, "arch": "x86_64", "status": "success",
                "command": "./build.sh", "log_uri": f"https://example.test/{os}/log", "artifact_name": f"sdk-{os}.tgz",
                "artifact_sha256": ("c" if os == "linux" else "d") * 64, "artifact_size": 10,
                "artifact_uri": f"https://example.test/{os}/sdk.tgz",
            })
            assert built.status_code == 201, built.text
            build_ids.append(built.json()["id"])
    async with client_for(reviewer) as client:
        for build_id in build_ids:
            for suite in ("unit", "hardware"):
                tested = await client.post(f"/api/v1/sdk/artifacts/{build_id}/tests", json={
                    "request_id": str(uuid4()), "suite": suite, "status": "passed", "passed": 8,
                    "failed": 0, "skipped": 0, "report_uri": "https://example.test/report",
                    "device": "Gemini 2" if suite == "hardware" else "", "firmware": "1.0" if suite == "hardware" else "",
                })
                assert tested.status_code == 201, tested.text
        ready = await client.get(f"/api/v1/sdk/candidates/{candidate_id}/gate")
        assert ready.json()["ready"] and not ready.json()["approved"]
    async with client_for(manager) as client:
        approval = await client.post(f"/api/v1/sdk/candidates/{candidate_id}/approval", json={"request_id": str(uuid4()), "note": "证据齐全"})
        assert approval.status_code == 201, approval.text
        published = await client.post(f"/api/v1/sdk/candidates/{candidate_id}/release", json={
            "request_id": str(uuid4()), "channel": "stable", "rollback_uri": "https://example.test/rollback",
            "released_at": datetime.now(timezone.utc).isoformat(),
        })
        assert published.status_code == 201, published.text
        trace = await client.get(f"/api/v1/sdk/candidates/{candidate_id}")
        assert trace.status_code == 200
        assert trace.json()["releases"][0]["artifact_sha256s"] == ["c" * 64, "d" * 64]
