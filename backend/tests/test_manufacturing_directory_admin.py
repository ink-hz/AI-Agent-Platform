"""Enterprise-directory (花名通讯录) keyed manufacturing membership and 制造管理员.

The host only supplies read-only directory resolvers; the manufacturing release
decides roles. The central policy still keeps legacy/native manufacturing routes
platform-admin only.
"""
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.manufacturing import WorkflowDirectory, build_manufacturing_router
from test_manufacturing_workflow_bridge import FakeDirectory

APP_DSN = "postgresql://platform_control_app@localhost/agent_platform_control"


class Recorder:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.calls = list(rows), error, []

    def __call__(self, url, **kwargs):
        recorder = self

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, args):
                recorder.calls.append((url, kwargs, sql, args))
                if recorder.error:
                    raise recorder.error
                return SimpleNamespace(fetchall=lambda: recorder.rows)

        return Connection()


def test_directory_reads_only_non_secret_columns_read_only():
    linked, pending, gone = uuid4(), uuid4(), uuid4()
    internal = uuid4()
    rows = [
        {"row_kind": "member", "directory_member_id": linked, "internal_user_id": internal,
         "display_name": "叶开", "status": "active", "departments": ["制造中心"]},
        {"row_kind": "member", "directory_member_id": pending, "internal_user_id": None,
         "display_name": "望北", "status": "active", "departments": None},
        {"row_kind": "member", "directory_member_id": gone, "internal_user_id": None,
         "display_name": "离职", "status": "inactive", "departments": []},
        {"row_kind": "metadata", "directory_member_id": None},
    ]
    connect = Recorder(rows)
    directory = WorkflowDirectory(APP_DSN, connect=connect)
    assert "platform_control_app" not in repr(directory)
    found = directory.search(" 叶 ", 20)
    assert found == [
        {"directory_member_id": str(linked), "internal_user_id": str(internal), "display_name": "叶开",
         "departments": ["制造中心"], "linked": True},
        {"directory_member_id": str(pending), "internal_user_id": None, "display_name": "望北",
         "departments": [], "linked": False},
    ]
    url, kwargs, sql, args = connect.calls[0]
    assert url == APP_DSN and "default_transaction_read_only=on" in kwargs["options"]
    for secret in ("encrypted_provider_id", "real_name", "ciphertext", "nonce", "dingtalk"):
        assert secret not in sql
    assert "read_office_recipient_directory_v54" in sql
    assert args == ("search", "叶", [], False, 20, None, [], [])
    resolved = directory.resolve(directory_member_ids=[pending], internal_user_ids=[internal])
    assert {p["directory_member_id"] for p in resolved} == {str(linked), str(pending)}
    assert connect.calls[1][3] == ("resolve", "", [], False, 1, None, [pending], [internal])
    assert directory.resolve(directory_member_ids=[], internal_user_ids=[]) == []
    assert len(connect.calls) == 2


@pytest.mark.parametrize("query,limit", [("", 20), ("x" * 65, 20), ("叶", 0), ("叶", 51), ("叶", True), (None, 20)])
def test_directory_search_bounds(query, limit):
    connect = Recorder()
    with pytest.raises(ValueError):
        WorkflowDirectory(APP_DSN, connect=connect).search(query, limit)
    assert connect.calls == []


def test_directory_resolve_bounds_and_failures_fail_closed():
    connect = Recorder(error=RuntimeError("password=secret"))
    directory = WorkflowDirectory(APP_DSN, connect=connect)
    with pytest.raises(ValueError):
        directory.resolve(directory_member_ids=[uuid4() for _ in range(150)], internal_user_ids=[uuid4() for _ in range(51)])
    with pytest.raises(ValueError):
        directory.resolve(directory_member_ids=["not-a-uuid"], internal_user_ids=[])
    with pytest.raises(OSError) as error:
        directory.resolve(directory_member_ids=[uuid4()], internal_user_ids=[])
    assert "secret" not in str(error.value) and error.value.__cause__ is None
    with pytest.raises(ValueError):
        WorkflowDirectory("postgresql://platform_control_owner@localhost/agent_platform_control", connect=connect)


@pytest.mark.parametrize("signature,supported", [
    ("", False), (", **kwargs", False), (", search_directory=None", False),
    (", search_directory=None, resolve_directory=None", True),
])
def test_directory_resolvers_require_explicit_factory_support(tmp_path, monkeypatch, signature, supported):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    package = tmp_path / "src/manufacturing_platform"
    package.mkdir(parents=True)
    assets = tmp_path / "webui/dist"
    assets.mkdir(parents=True)
    for name in ("manufacturing.js", "manufacturing.css"):
        (assets / name).write_text("fixture")
    body = "from fastapi import APIRouter\nWORKFLOW_API_VERSION=1\ndef build_router(**kwargs): return APIRouter()\n"
    body += f"def build_workflow_router(*,database_path,authority_database_path,assets_dir,authenticate,resolve_subject,release,dataset{signature}):\n"
    if "**kwargs" in signature:
        body += " assert 'search_directory' not in kwargs and 'resolve_directory' not in kwargs\n"
    flag = "resolve_directory is not None and search_directory is not None" if supported else "False"
    body += " r=APIRouter(prefix='/api/v1/manufacturing/workflow')\n @r.get('/access')\n def access():return {'directory': " + flag + "}\n return r\n"
    (package / "__init__.py").write_text(body)
    for key, value in {"ROOT": str(tmp_path), "DATABASE_PATH": str(tmp_path / "legacy.db"),
                       "WORKFLOW_DATABASE_PATH": str(tmp_path / "domain.db"),
                       "AUTHORITY_DATABASE_PATH": str(tmp_path / "authority.db"),
                       "DATASET_ID": "dir", "DATASET_MODE": "test", "DATASET_LABEL": "Dir"}.items():
        monkeypatch.setenv("PLATFORM_MANUFACTURING_" + key, value)
    directory = FakeDirectory(set())
    app = FastAPI()
    app.include_router(build_manufacturing_router(
        resolve_workflow_subject=lambda uid: True,
        search_workflow_directory=directory.search, resolve_workflow_directory=directory.resolve))
    response = TestClient(app).get("/api/v1/manufacturing/workflow/access")
    assert response.status_code == 200, response.text
    assert response.json() == {"directory": supported}


@pytest.mark.parametrize("prefix", ["/", "/_preview/dingtalk-r1/"])
def test_manufacturing_admin_through_real_policy(tmp_path, monkeypatch, prefix):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.control_plane.authorization import AuthorizationService
    from app.control_plane.middleware import IdentitySecurityMiddleware
    from app.control_plane.models import AuthContext, Role
    from test_dingtalk_auth_api import FakeAuth, _NoObservationGrants

    configured = os.environ.get("TEST_MANUFACTURING_RELEASE_ROOT")
    if not configured:
        pytest.skip("set TEST_MANUFACTURING_RELEASE_ROOT for real sibling integration")
    root = tmp_path / "release"
    shutil.copytree(Path(configured) / "src/manufacturing_platform", root / "src/manufacturing_platform",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (root / "webui/dist").mkdir(parents=True)
    for name in ("manufacturing.js", "manufacturing.css"):
        shutil.copyfile(Path(configured) / "webui/dist" / name, root / "webui/dist" / name)
    for key, value in {"ROOT": str(root), "DATABASE_PATH": str(tmp_path / "legacy.db"),
                       "WORKFLOW_DATABASE_PATH": str(tmp_path / "domain.db"),
                       "AUTHORITY_DATABASE_PATH": str(tmp_path / "authority.db"),
                       "DATASET_ID": "test", "DATASET_MODE": "test", "DATASET_LABEL": "Test"}.items():
        monkeypatch.setenv("PLATFORM_MANUFACTURING_" + key, value)
    auth = FakeAuth(prefix=prefix)
    auth.hard_stale_audit = lambda *args: None
    owner = auth.context.internal_user_id
    session = auth.context.session_id
    admin, plain = uuid4(), uuid4()
    active = {owner, admin, plain}
    directory = FakeDirectory(active, {admin: "叶开", plain: "李工", owner: "苍渊"})
    # 叶开 is assigned by directory member id before ever logging in.
    directory.remap[admin] = uuid4()
    directory.unlinked.add(admin)
    app = FastAPI()
    app.include_router(build_manufacturing_router(
        resolve_workflow_subject=lambda uid: uid in active,
        search_workflow_directory=directory.search,
        resolve_workflow_directory=directory.resolve,
    ), prefix=prefix.rstrip("/"))
    app.add_middleware(IdentitySecurityMiddleware, auth=auth,
                       authorization=AuthorizationService(_NoObservationGrants()),
                       routes=app.routes, public_assets=frozenset())
    client = TestClient(app, base_url="https://agent.example.test")
    client.cookies.set(auth.cookie_name, "valid-cookie")
    base = prefix.rstrip("/") + "/api/v1/manufacturing"
    wf = base + "/workflow"
    headers = {"Origin": "https://agent.example.test", "X-CSRF-Token": auth.csrf}

    found = client.get(wf + "/members/search", params={"q": "叶"}).json()["items"]
    assert found == [{"directory_member_id": str(directory.remap[admin]), "display_name": "叶开",
                      "departments": ["制造中心"], "linked": False}]
    granted = client.put(f"{wf}/members/{directory.remap[admin]}", headers=headers,
                         json={"roles": ["admin"], "active": True})
    assert granted.status_code == 200, granted.text
    assert granted.json()["roles"] == ["admin"] and granted.json()["linked"] is False
    assert client.put(f"{wf}/members/{plain}", headers=headers,
                      json={"roles": ["mc"], "active": True}).status_code == 200

    def as_user(uid, read_only=False):
        auth.context = AuthContext(uid, Role.MEMBER, session, read_only)

    as_user(admin)
    assert client.get(wf + "/access").json()["manufacturing_admin"] is False  # not linked yet
    directory.unlinked.discard(admin)  # first login links the directory entry
    access = client.get(wf + "/access").json()
    assert access["manufacturing_admin"] is True and access["workflow_access"] is True
    assert access["configuration_admin"] is False and access["legacy_access"] is False
    assert client.get(wf + "/members").status_code == 200
    assert client.get(wf + "/members/search", params={"q": "李"}).status_code == 200
    assert client.get(wf + "/production-tools/status").json()["configured"] is False
    assert client.get(wf + "/production-tools/projects/10001/status").status_code == 200
    assert client.get(wf + "/assets/manufacturing.js?release=development").status_code == 200
    promoted = client.put(f"{wf}/members/{plain}", headers=headers, json={"roles": ["mc", "admin"], "active": True})
    assert promoted.status_code == 200 and promoted.json()["roles"] == ["mc", "admin"]
    client.put(f"{wf}/members/{plain}", headers=headers, json={"roles": ["mc"], "active": True})
    # Central policy unchanged: legacy and native manufacturing routes stay platform-admin only.
    assert client.get(base + "/health").status_code == 403
    assert client.get(base + "/access").status_code == 403

    as_user(admin, read_only=True)
    assert client.get(wf + "/members").status_code == 200
    assert client.put(f"{wf}/members/{plain}", headers=headers,
                      json={"roles": ["mc"], "active": True}).status_code == 503

    as_user(plain)
    assert client.get(wf + "/access").json()["roles"] == ["mc"]
    for path in ("/members", "/members/search?q=x", "/production-tools/status"):
        assert client.get(wf + path).status_code == 403, path

    directory.down = True
    as_user(admin)
    access = client.get(wf + "/access").json()
    assert access["manufacturing_admin"] is False and access["allowed"] is False
    assert client.get(wf + "/members").status_code == 403
    directory.down = False

    directory.active.discard(admin)  # left the company / disabled in the directory
    assert client.get(wf + "/me").json()["roles"] == []
    assert client.get(wf + "/members").status_code == 403
