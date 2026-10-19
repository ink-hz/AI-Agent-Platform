"""Opt-in SDK workbench flow against disposable PostgreSQL and local ZenTao."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.evidence_repository import EvidenceRepository
from app.sdk_workbench.evidence_service import EvidenceService, MODULE_PATHS
from app.sdk_workbench.repository import SdkRepository
from app.sdk_workbench.routes import build_sdk_router
from app.sdk_workbench.service import SdkService
from app.sdk_workbench.zentao import ZenTaoClient
from test_control_plane_migration import control_database


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.live_zentao,
    pytest.mark.skipif(os.getenv("SDK_LIVE_ACCEPT") != "1", reason="isolated SDK live trial is opt-in"),
]


def _git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def _manifest(source: Path) -> dict:
    modules = {}
    for name in sorted(MODULE_PATHS):
        if name.startswith("OpenOrbbecViewer/"):
            tree = _git(source / "OpenOrbbecViewer", "ls-tree", "HEAD", "third_party/OpenOrbbecSDK")
        else:
            tree = _git(source, "ls-tree", "HEAD", name)
        fields = tree.split()
        assert len(fields) >= 4 and fields[1] == "commit", name
        modules[name] = fields[2]
    return {
        "packaging_sha": _git(source, "rev-parse", "HEAD"),
        "sdk_version": "2.6.2",
        "script_path": "OpenOrbbecSDK/CMakeLists.txt",
        "modules": modules,
    }


@pytest.mark.asyncio
async def test_real_sdk_platform_requirement_to_blocked_release(control_database) -> None:
    required = (
        "SDK_LIVE_ZENTAO_URL", "SDK_LIVE_ZENTAO_ACCOUNT", "SDK_LIVE_ZENTAO_PASSWORD_FILE",
        "SDK_LIVE_PRODUCT_ID", "SDK_LIVE_PRODUCT_NAME", "SDK_LIVE_PROJECT_ID",
        "SDK_LIVE_EXECUTION_ID", "SDK_LIVE_EXECUTION_NAME", "SDK_LIVE_ANCHOR_TASK_ID",
        "SDK_LIVE_SOURCE_DIR", "SDK_LIVE_ARTIFACT_PATH", "SDK_LIVE_HARDWARE_BLOCKER_PATH",
    )
    missing = [name for name in required if not os.getenv(name)]
    assert not missing, f"live SDK settings missing: {', '.join(missing)}"
    parsed = urlparse(os.environ["SDK_LIVE_ZENTAO_URL"])
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
    password_file = Path(os.environ["SDK_LIVE_ZENTAO_PASSWORD_FILE"])
    assert password_file.is_file() and not password_file.is_symlink() and password_file.stat().st_mode & 0o077 == 0
    source = Path(os.environ["SDK_LIVE_SOURCE_DIR"]).resolve(strict=True)
    artifact = Path(os.environ["SDK_LIVE_ARTIFACT_PATH"]).resolve(strict=True)
    assert artifact.is_file() and artifact.stat().st_size > 0
    blocker_report = Path(os.environ["SDK_LIVE_HARDWARE_BLOCKER_PATH"]).resolve(strict=True)
    assert blocker_report.is_file() and blocker_report.stat().st_size > 0
    assert "未连接 Orbbec 相机" in blocker_report.read_text(encoding="utf-8")
    manifest = _manifest(source)
    artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    environment = control_database["environments"]["production"]
    owner, contributor, reviewer, manager = (uuid4() for _ in range(4))
    with psycopg.connect(environment["admin"]) as connection:
        for actor, label in ((owner, "Owner"), (contributor, "Contributor"), (reviewer, "Reviewer"), (manager, "Manager")):
            connection.execute(
                "insert into platform_control.internal_users(internal_user_id,display_name,status) values (%s,%s,'active')",
                (actor, f"SDK Trial {label}"),
            )

    zentao = ZenTaoClient(
        os.environ["SDK_LIVE_ZENTAO_URL"], os.environ["SDK_LIVE_ZENTAO_ACCOUNT"],
        os.environ["SDK_LIVE_ZENTAO_PASSWORD_FILE"],
    )
    # A loopback URL can be an SSH tunnel to production. Confirm the entire
    # disposable product/project/execution chain before any platform writes.
    product_id = int(os.environ["SDK_LIVE_PRODUCT_ID"])
    project_id = int(os.environ["SDK_LIVE_PROJECT_ID"])
    execution_id = int(os.environ["SDK_LIVE_EXECUTION_ID"])
    try:
        product = (await zentao._request("GET", f"products/{product_id}"))["product"]
        project = (await zentao._request("GET", f"projects/{project_id}"))["project"]
        execution = (await zentao._request("GET", f"executions/{execution_id}"))["execution"]
        anchor = await zentao.get_task(int(os.environ["SDK_LIVE_ANCHOR_TASK_ID"]))
        anchor_story = await zentao.get_story(int(anchor["story"]))
        assert os.environ["SDK_LIVE_PRODUCT_NAME"].startswith("SDK-V1-ACCEPT-")
        assert os.environ["SDK_LIVE_EXECUTION_NAME"].startswith("SDK-V1-ACCEPT-")
        assert product["name"] == os.environ["SDK_LIVE_PRODUCT_NAME"]
        assert str(product["id"]) == str(product_id)
        assert product["acl"] == "private"
        assert project["name"].startswith("SDK-V1-ACCEPT-") and str(project["id"]) == str(project_id)
        assert execution["name"] == os.environ["SDK_LIVE_EXECUTION_NAME"]
        assert str(execution["id"]) == str(execution_id)
        assert str(execution["project"]) == str(project_id)
        assert str(anchor["execution"]) == str(execution_id)
        assert str(anchor["name"]).startswith("SDK-V1-ACCEPT-")
        assert str(anchor_story["product"]) == str(product_id)
        assert str(anchor_story["title"]).startswith("SDK-V1-ACCEPT-")
    except BaseException:
        await zentao.close()
        raise
    repository = SdkRepository(environment["urls"]["platform_control_app"])
    evidence = EvidenceRepository(environment["urls"]["platform_control_app"])
    app = FastAPI()
    app.state.sdk_service = SdkService(
        repository, zentao,
        product_id=product_id,
        execution_id=execution_id,
    )
    app.state.sdk_evidence_service = EvidenceService(
        repository, zentao, evidence, product_id=product_id,
    )

    @app.middleware("http")
    async def trial_identity(request, call_next):
        actor = request.headers.get("x-trial-actor")
        if actor:
            request.state.auth_context = AuthContext(
                owner if actor == "owner" else {"contributor": contributor, "reviewer": reviewer, "manager": manager}[actor],
                Role.PLATFORM_OWNER if actor == "owner" else Role.MEMBER,
                uuid4(), False,
            )
        return await call_next(request)

    app.include_router(build_sdk_router())
    marker = uuid4().hex[:12]
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://sdk-local") as client:
            for actor, role in ((contributor, "contributor"), (reviewer, "reviewer"), (manager, "release_manager")):
                response = await client.post("/api/v1/sdk/members", headers={"x-trial-actor": "owner"}, json={
                    "request_id": str(uuid4()), "internal_user_id": str(actor), "role": role,
                })
                assert response.status_code == 201, response.text

            req = {
                "request_id": str(uuid4()), "title": f"SDK-V1-ACCEPT-{marker} 深度和彩色采集稳定性",
                "scenario": "Gemini 深度与彩色同步采集 60 秒",
                "device_platform": "Gemini / macOS arm64；本地无设备，硬件待测",
                "acceptance": "帧时间戳单调，采集连续 60 秒，无崩溃且丢帧率达标",
            }
            headers = {"x-trial-actor": "contributor"}
            created = await client.post("/api/v1/sdk/requirements", headers=headers, json=req)
            assert created.status_code == 201, created.text
            story_id = created.json()["id"]
            replay = await client.post("/api/v1/sdk/requirements", headers=headers, json=req)
            assert replay.status_code == 201 and replay.json() == created.json()
            detail = await client.get(f"/api/v1/sdk/requirements/{story_id}", headers=headers)
            assert detail.status_code == 200 and detail.json()["story"]["status"] == "active"

            before_review = await client.post(f"/api/v1/sdk/requirements/{story_id}/tasks", headers=headers, json={
                "request_id": str(uuid4()), "name": f"SDK-V1-ACCEPT-{marker} 提前任务",
                "description": "平台评审前不得写入禅道",
            })
            assert before_review.status_code == 409 and before_review.json()["detail"] == "sdk_requirement_review_required"

            review = await client.post(f"/api/v1/sdk/requirements/{story_id}/reviews", headers={"x-trial-actor": "reviewer"}, json={
                "request_id": str(uuid4()), "decision": "approved", "reason": "场景和验收条件明确；真机结果仍待提交",
            })
            assert review.status_code == 201, review.text
            task = await client.post(f"/api/v1/sdk/requirements/{story_id}/tasks", headers=headers, json={
                "request_id": str(uuid4()), "name": f"SDK-V1-ACCEPT-{marker} 核对采集路径",
                "description": "完成真机运行并提交日志、单测和硬件报告",
            })
            assert task.status_code == 201, task.text

            candidate = await client.post("/api/v1/sdk/candidates", headers=headers, json={
                "request_id": str(uuid4()), "story_id": story_id, "task_ids": [task.json()["id"]],
                "manifest": manifest, "targets": [{"os": "macos", "arch": "arm64"}],
            })
            assert candidate.status_code == 201, candidate.text
            candidate_id = candidate.json()["id"]
            build = await client.post(f"/api/v1/sdk/candidates/{candidate_id}/builds", headers=headers, json={
                "request_id": str(uuid4()), "os": "macos", "arch": "arm64", "status": "success",
                "command": "cmake --build /tmp/sdk-live-openorbbecsdk-20261019/build-arm64 --parallel 4",
                "log_uri": "local:///tmp/sdk-live-openorbbecsdk-20261019/build-arm64.log",
                "artifact_name": artifact.name, "artifact_sha256": artifact_sha,
                "artifact_size": artifact.stat().st_size, "artifact_uri": f"local://{artifact}",
            })
            assert build.status_code == 201, build.text
            blocked = await client.post(f"/api/v1/sdk/artifacts/{build.json()['id']}/tests", headers={"x-trial-actor": "reviewer"}, json={
                "request_id": str(uuid4()), "suite": "hardware", "status": "blocked",
                "passed": 0, "failed": 0, "skipped": 0,
                "report_uri": f"local://{blocker_report}",
                "device": "未连接 Orbbec 相机", "firmware": "未读取（设备缺席）",
            })
            assert blocked.status_code == 201, blocked.text
            persisted = await client.get(f"/api/v1/sdk/candidates/{candidate_id}", headers=headers)
            assert persisted.status_code == 200, persisted.text
            assert persisted.json()["builds"][0]["artifact_sha256"] == artifact_sha
            assert persisted.json()["tests"][0]["status"] == "blocked"
            gate = await client.get(f"/api/v1/sdk/candidates/{candidate_id}/gate", headers=headers)
            assert gate.status_code == 200, gate.text
            assert gate.json()["ready"] is False
            assert set(gate.json()["reasons"]) == {"macos/arm64:unit_not_passed", "macos/arm64:hardware_not_passed"}
            approval = await client.post(f"/api/v1/sdk/candidates/{candidate_id}/approval", headers={"x-trial-actor": "manager"}, json={
                "request_id": str(uuid4()), "note": "待真实单测和硬件报告",
            })
            assert approval.status_code == 409 and approval.json()["detail"] == "sdk_gate_not_ready"
            release = await client.post(f"/api/v1/sdk/candidates/{candidate_id}/release", headers={"x-trial-actor": "manager"}, json={
                "request_id": str(uuid4()), "channel": "trial", "rollback_uri": "local:///not-released",
                "released_at": "2026-10-19T08:00:00+08:00",
            })
            assert release.status_code == 409 and release.json()["detail"] == "sdk_approval_required"
            print(json.dumps({
                "story_id": story_id, "task_id": task.json()["id"], "candidate_id": candidate_id,
                "build_id": build.json()["id"], "artifact_sha256": artifact_sha,
                "hardware_test_id": blocked.json()["id"], "hardware_status": "blocked",
                "gate_ready": False, "gate_reasons": gate.json()["reasons"],
            }, ensure_ascii=False, sort_keys=True))
    finally:
        await zentao.close()
