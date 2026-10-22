from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.plan_service import PlanService
from app.sdk_admin.repository import SdkAdminRepository
from app.sdk_admin.routes import build_sdk_admin_router


ACTOR = UUID("11111111-1111-4111-8111-111111111111")
PREFIX = "/api/v1/sdk-admin"
NO_STORE = "private, no-store"


class Members:
    def __init__(self, role):
        self.role = role

    def role_for(self, actor):
        assert actor == ACTOR
        return self.role


def document(document_id, title, body, *, kind="knowledge", group="01-system", state="accepted_static"):
    return {
        "id": document_id, "title": title, "kind": kind, "group": group,
        "level": "L2", "baseline_id": "locked-test", "content_state": state,
        "runtime_validation": "not_run", "source_path": f"sources/{document_id}.md",
        "source_sha256": "a" * 64, "review_status": "independently_reviewed",
        "file": f"documents/{document_id}.md", "sha256": hashlib.sha256(body.encode()).hexdigest(),
        "conditions": {"platform": "Linux", "runtime": "not_run"}, "links": {},
    }


def package(tmp_path):
    root = tmp_path / "release"
    (root / "documents").mkdir(parents=True)
    bodies = {"KB-SYS-001": "# 系统架构\n正文包含 depthbuffer 和 ＵＳＢ。", "KB-INDEX-SYS": "# 系统目录",
              "DOC-REPORT": "# 分析记录\n发布流程正文"}
    docs = [document("KB-SYS-001", "系统架构", bodies["KB-SYS-001"]),
            document("KB-INDEX-SYS", "系统目录", bodies["KB-INDEX-SYS"], state="navigation"),
            document("DOC-REPORT", "分析记录", bodies["DOC-REPORT"], kind="analysis", group="analysis", state="phase_report")]
    docs[0]["links"] = {"../index.md#系统": {"document_id": "KB-INDEX-SYS", "fragment": "系统"}}
    manifest = {"schema_version": 1, "release_id": "test-release", "baseline_id": "locked-test",
                "generated_at": "2026-10-22T00:00:00Z", "documents": docs,
                "sources": [{"id": "gitlab", "label": "内部开发", "state": "not_connected", "description": "待核验"}],
                "ai": {"available": False, "reason": "本期未接通"}, "featured_ids": ["KB-SYS-001"]}
    for row in docs:
        (root / row["file"]).write_text(bodies[row["id"]], encoding="utf-8")
    write_manifest(root, manifest)
    return root, manifest


def write_manifest(root, manifest):
    data = json.dumps(manifest, ensure_ascii=False).encode()
    (root / "manifest.json").write_bytes(data)
    (root / "manifest.sha256").write_text(hashlib.sha256(data).hexdigest() + "\n")


def app_for(root=None, *, sdk_role="viewer", role=Role.MEMBER, anonymous=False, stale=False, service=True, injected=True):
    app = FastAPI()
    if service:
        app.state.sdk_plan_service = PlanService(Members(sdk_role), None)
    if root is not None and injected:
        app.state.sdk_admin_repository = SdkAdminRepository(root)

    @app.middleware("http")
    async def identity(request, call_next):
        if not anonymous:
            request.state.auth_context = AuthContext(ACTOR, role, uuid4(), stale)
        return await call_next(request)

    app.include_router(build_sdk_admin_router())
    return app


async def get(app, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(PREFIX + path, **kwargs)
    assert response.headers.get("cache-control") == NO_STORE
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/access", "/catalog", "/documents?q=depthbuffer", "/documents/KB-SYS-001"])
async def test_anonymous_and_nonmembers_cannot_obtain_content_or_counts(tmp_path, path):
    root, _ = package(tmp_path)
    assert (await get(app_for(root, anonymous=True), path)).status_code == 401
    denied = await get(app_for(root, sdk_role=None), path)
    assert denied.status_code == (200 if path == "/access" else 403)
    assert denied.json() == ({"allowed": False} if path == "/access" else {"detail": "sdk_access_required"})


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_role", ["viewer", "contributor", "reviewer", "release_manager"])
async def test_all_sdk_read_roles_can_read_even_when_hard_stale(tmp_path, sdk_role):
    root, _ = package(tmp_path)
    app = app_for(root, sdk_role=sdk_role, stale=True)
    assert (await get(app, "/access")).json() == {"allowed": True}
    assert (await get(app, "/catalog")).status_code == 200
    assert (await get(app, "/documents?q=depthbuffer")).json()["total"] == 1
    assert (await get(app, "/documents/KB-SYS-001")).status_code == 200


@pytest.mark.asyncio
async def test_owner_reads_without_sdk_service_but_platform_admin_does_not(tmp_path):
    root, _ = package(tmp_path)
    assert (await get(app_for(root, service=False, role=Role.PLATFORM_OWNER), "/catalog")).status_code == 200
    assert (await get(app_for(root, service=False, role=Role.PLATFORM_ADMIN), "/catalog")).status_code == 403
    assert (await get(app_for(root, service=False), "/access")).json() == {"allowed": False}


@pytest.mark.asyncio
async def test_catalog_detail_full_body_search_filters_and_stable_pagination(tmp_path):
    root, manifest = package(tmp_path)
    app = app_for(root)
    catalog = (await get(app, "/catalog")).json()
    assert catalog["counts"] == {"knowledge_pages": 1, "knowledge_navigation": 1, "documents": 3}
    assert catalog["sources"] == manifest["sources"]
    assert catalog["ai"]["available"] is False
    assert catalog["manifest_sha256"] == (root / "manifest.sha256").read_text().strip()
    assert next(row for row in catalog["groups"] if row["id"] == "01-system")["title"] == "系统架构"
    detail = (await get(app, "/documents/KB-SYS-001")).json()
    assert "depthbuffer" in detail["markdown"]
    assert detail["links"] == manifest["documents"][0]["links"]
    assert detail["conditions"] == manifest["documents"][0]["conditions"]
    assert "file" not in detail
    for query in ("DEPTHBUFFER", "usb", "kb-sys-001", "系统架构"):
        found = (await get(app, "/documents", params={"q": query, "kind": "knowledge", "group": "01-system"})).json()
        assert [row["id"] for row in found["items"]] == ["KB-SYS-001"]
        assert "markdown" not in found["items"][0]
    pages = [(await get(app, "/documents", params={"offset": n, "limit": 1})).json() for n in range(4)]
    assert [page["items"][0]["id"] for page in pages[:3]] == sorted(row["id"] for row in manifest["documents"])
    assert pages[3]["items"] == []
    assert all(page["total"] == 3 and page["limit"] == 1 for page in pages)


@pytest.mark.asyncio
async def test_catalog_labels_actual_knowledge_groups_and_preserves_unknown_groups(tmp_path):
    root, manifest = package(tmp_path)
    groups = [("02-public-api", "公共API"), ("03-runtime", "运行时"), ("04-pipeline", "Pipeline"),
              ("14-scenarios", "场景与排障"), ("_meta", "机器索引与语义证据"),
              ("future-group", "知识专题（future-group）")]
    for index, (group, _) in enumerate(groups):
        body = f"# 精准分组资料 {index}"
        row = document(f"KB-GROUP-{index}", f"资料 {index}", body, group=group)
        manifest["documents"].append(row)
        (root / row["file"]).write_text(body)
    write_manifest(root, manifest)
    app = app_for(root)
    catalog = (await get(app, "/catalog")).json()
    returned_groups = {row["id"]: row for row in catalog["groups"]}
    assert returned_groups["01-system"] == {"id": "01-system", "title": "系统架构", "count": 2}
    assert returned_groups["analysis"] == {"id": "analysis", "title": "现状分析", "count": 1}
    for group, title in groups:
        assert returned_groups[group] == {"id": group, "title": title, "count": 1}
        filtered = (await get(app, "/documents", params={"group": group})).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["group"] == group


@pytest.mark.asyncio
async def test_access_works_without_package_and_environment_root_is_supported(tmp_path, monkeypatch):
    monkeypatch.delenv("SDK_ADMIN_CONTENT_ROOT", raising=False)
    app = app_for()
    assert (await get(app, "/access")).json() == {"allowed": True}
    assert (await get(app, "/catalog")).json() == {"detail": "sdk_admin_content_unavailable"}
    assert (await get(app, "/catalog")).status_code == 503
    root, _ = package(tmp_path)
    monkeypatch.setenv("SDK_ADMIN_CONTENT_ROOT", str(root))
    assert (await get(app, "/catalog")).status_code == 200
    monkeypatch.setenv("SDK_ADMIN_CONTENT_ROOT", str(tmp_path / "absent"))
    assert (await get(app_for(root), "/catalog")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"q": "x" * 201}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": "bad"}])
async def test_query_bounds_have_private_error_responses(tmp_path, params):
    root, _ = package(tmp_path)
    assert (await get(app_for(root), "/documents", params=params)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["digest", "body", "missing", "duplicate", "id", "escape", "symlink", "manifest_symlink", "links"])
async def test_corrupt_packages_fail_closed_without_path_leak(tmp_path, mutation):
    root, manifest = package(tmp_path)
    if mutation == "digest":
        (root / "manifest.sha256").write_text("f" * 64)
    elif mutation == "body":
        (root / manifest["documents"][0]["file"]).write_text("tampered")
    elif mutation == "missing":
        (root / manifest["documents"][0]["file"]).unlink()
    elif mutation == "symlink":
        body = root / manifest["documents"][0]["file"]
        target = tmp_path / "outside.md"
        target.write_bytes(body.read_bytes())
        body.unlink()
        body.symlink_to(target)
    elif mutation == "manifest_symlink":
        target = tmp_path / "outside.json"
        target.write_bytes((root / "manifest.json").read_bytes())
        (root / "manifest.json").unlink()
        (root / "manifest.json").symlink_to(target)
    else:
        if mutation == "duplicate":
            manifest["documents"].append(manifest["documents"][0])
        elif mutation == "id":
            manifest["documents"][0]["id"] = "../escape"
        elif mutation == "links":
            manifest["documents"][0]["links"]["x"] = {"document_id": "unknown", "fragment": ""}
        else:
            manifest["documents"][0]["file"] = "../outside.md"
        write_manifest(root, manifest)
    response = await get(app_for(root), "/catalog")
    assert response.status_code == 503
    assert response.json() == {"detail": "sdk_admin_content_unavailable"}
    assert str(tmp_path) not in response.text


@pytest.mark.asyncio
async def test_unknown_invalid_ids_and_snapshot_cannot_be_changed_by_disk_mutation(tmp_path):
    root, _ = package(tmp_path)
    app = app_for(root)
    before = (await get(app, "/documents/KB-SYS-001")).json()
    (root / "documents/KB-SYS-001.md").write_text("new secret")
    assert (await get(app, "/documents/KB-SYS-001")).json() == before
    assert (await get(app, "/documents?q=secret")).json()["total"] == 0
    assert (await get(app, "/documents?q=depthbuffer")).json()["total"] == 1
    for path in ("/documents/UNKNOWN", "/documents/.bad", "/documents/a%2Fb", "/documents/a%5Cb", "/unknown"):
        assert (await get(app, path)).status_code == 404


@pytest.mark.asyncio
async def test_sdk_service_takes_priority_and_permissions_are_not_cached(tmp_path):
    root, _ = package(tmp_path)
    app = app_for(root)
    members = Members(None)
    app.state.sdk_service = PlanService(members, None)
    assert (await get(app, "/catalog")).status_code == 403
    members.role = "reviewer"
    assert (await get(app, "/catalog")).status_code == 200
    members.role = None
    assert (await get(app, "/documents/KB-SYS-001")).status_code == 403
    assert (await get(app, "/documents?q=depthbuffer")).status_code == 403


@pytest.mark.asyncio
async def test_service_failures_and_malformed_roles_fail_closed(tmp_path):
    root, _ = package(tmp_path)
    app = app_for(root)

    class Broken:
        def me(self, actor):
            raise RuntimeError(f"private {root}")

    app.state.sdk_service = Broken()
    failed = await get(app, "/catalog")
    assert failed.status_code == 503
    assert failed.json() == {"detail": "sdk_admin_access_unavailable"}

    class Invalid:
        def me(self, actor):
            return {"role": []}

    app.state.sdk_service = Invalid()
    assert (await get(app, "/catalog")).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["root_symlink", "parent_symlink", "documents_symlink", "nan", "json_duplicate"])
async def test_all_release_structure_must_be_safe_before_any_content_is_served(tmp_path, mutation):
    root, manifest = package(tmp_path)
    if mutation == "root_symlink":
        linked = tmp_path / "linked"
        linked.symlink_to(root, target_is_directory=True)
        root = linked
    elif mutation == "parent_symlink":
        linked = tmp_path / "linked-parent"
        linked.symlink_to(tmp_path, target_is_directory=True)
        root = linked / "release"
    elif mutation == "documents_symlink":
        target = tmp_path / "other-documents"
        (root / "documents").rename(target)
        (root / "documents").symlink_to(target, target_is_directory=True)
    elif mutation == "nan":
        manifest["documents"][0]["conditions"]["invalid"] = float("nan")
        write_manifest(root, manifest)
    else:
        data = (root / "manifest.json").read_bytes().replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
        (root / "manifest.json").write_bytes(data)
        (root / "manifest.sha256").write_text(hashlib.sha256(data).hexdigest())
    for path in ("/catalog", "/documents?q=depthbuffer", "/documents/KB-SYS-001"):
        response = await get(app_for(root), path)
        assert response.status_code == 503
        assert response.json() == {"detail": "sdk_admin_content_unavailable"}
