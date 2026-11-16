from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.control_plane.models import AuthContext, Role
from app.manufacturing import authorize_workflow_identity
from fastapi import HTTPException


def _real_release(tmp_path, monkeypatch):
    import os
    import shutil
    from pathlib import Path

    configured = os.environ.get("TEST_MANUFACTURING_RELEASE_ROOT")
    if not configured:
        pytest.skip("set TEST_MANUFACTURING_RELEASE_ROOT for real sibling integration")
    root = tmp_path / "release"
    shutil.copytree(
        Path(configured) / "src/manufacturing_platform",
        root / "src/manufacturing_platform",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    assets = root / "webui/dist"
    assets.mkdir(parents=True)
    for name in ("manufacturing.js", "manufacturing.css"):
        shutil.copyfile(Path(configured) / "webui/dist" / name, assets / name)
    for key, value in {
        "ROOT": str(root),
        "DATABASE_PATH": str(tmp_path / "legacy.db"),
        "WORKFLOW_DATABASE_PATH": str(tmp_path / "domain.db"),
        "AUTHORITY_DATABASE_PATH": str(tmp_path / "authority.db"),
        "DATASET_ID": "test",
        "DATASET_MODE": "test",
        "DATASET_LABEL": "Test",
    }.items():
        monkeypatch.setenv("PLATFORM_MANUFACTURING_" + key, value)
    return root


def test_actual_create_app_uses_injected_real_policy_and_trusted_clock(tmp_path, monkeypatch):
    import json
    import sqlite3
    from datetime import UTC, datetime

    from app.control_plane.authorization import AuthorizationService
    from app.control_plane.middleware import IdentitySecurityMiddleware
    from app.main import create_app
    from fastapi.testclient import TestClient
    from test_dingtalk_auth_api import FakeAuth, _NoObservationGrants

    _real_release(tmp_path, monkeypatch)
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<main>SHELL</main>", encoding="utf-8")
    (static / ".vite").mkdir()
    (static / ".vite/manifest.json").write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("PLATFORM_STATIC_DIR", str(static))
    (tmp_path / "registry.yaml").write_text("version: 1\nagents: []\n", encoding="utf-8")
    (tmp_path / "contract.json").write_text(json.dumps({"bots": []}), encoding="utf-8")
    auth = FakeAuth()
    auth.hard_stale_audit = lambda *args: None
    owner = auth.context.internal_user_id
    member = uuid4()
    auth.repository = SimpleNamespace(
        is_active_internal_user=lambda uid: uid in {owner, member},
        active_internal_user_profiles=lambda ids: [],
    )
    fixed = datetime(2026, 11, 1, 8, 30, tzinfo=UTC)
    app = create_app(
        registry_path=str(tmp_path / "registry.yaml"),
        cluster_contract_path=str(tmp_path / "contract.json"),
        start_poller=False,
        identity_auth=auth,
        manufacturing_business_clock=lambda: fixed,
    )
    middleware = next(v for v in app.user_middleware if v.cls is IdentitySecurityMiddleware)
    middleware.kwargs["authorization"] = AuthorizationService(_NoObservationGrants())
    client = TestClient(app, base_url=auth.public_base_url)
    wf = "/api/v1/manufacturing/workflow"
    client.cookies.set(auth.cookie_name, "valid-cookie")
    headers = {"Origin": auth.public_base_url, "X-CSRF-Token": auth.csrf, "X-User-ID": str(member)}
    grant = {"roles": ["production"], "active": True}
    response = client.put(f"{wf}/members/{member}", headers=headers, json=grant)
    assert response.status_code == 200, response.text
    assert response.json()["updated_at"] == fixed.isoformat()
    with sqlite3.connect(f"file:{tmp_path / 'domain.db'}?mode=ro", uri=True) as db:
        assert db.execute("SELECT updated_by FROM member_roles WHERE user_id=?", (str(member),)).fetchone() == (str(owner),)
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, False)
    assert client.get(wf + "/me").json()["roles"] == ["production"]
    assert client.get("/api/v1/manufacturing/health").status_code == 403
    assert client.get(wf + "/unknown-policy-template").status_code == 403
    assert client.put(f"{wf}/members/{member}", headers=headers, json=grant).status_code == 403
    auth.context = AuthContext(owner, Role.PLATFORM_OWNER, auth.context.session_id, True)
    assert client.put(f"{wf}/members/{member}", headers=headers, json=grant).status_code == 503
    auth.revoked = True
    assert client.get(wf + "/me").status_code == 401
    client.close()


def test_missing_trusted_context_is_unauthenticated():
    with pytest.raises(HTTPException) as error:
        authorize_workflow_identity(
            SimpleNamespace(state=SimpleNamespace()), write=False
        )
    assert error.value.status_code == 401


def test_host_exposes_explicit_workflow_subject_resolver_keyword():
    from app.manufacturing import build_manufacturing_router

    # The host protocol uses this name so main.py can pass the control-plane lookup.
    build_manufacturing_router(resolve_workflow_subject=lambda user_id: False)


@pytest.mark.parametrize("prefix", ["/", "/_preview/dingtalk-r1/"])
def test_real_middleware_scopes_members_and_preserves_legacy(
    tmp_path, monkeypatch, prefix, caplog
):
    import shutil
    from pathlib import Path

    from app.control_plane.authorization import AuthorizationService
    from app.control_plane.middleware import IdentitySecurityMiddleware
    from app.manufacturing import build_manufacturing_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_dingtalk_auth_api import FakeAuth, _NoObservationGrants

    root = tmp_path / "release"
    import os

    configured = os.environ.get("TEST_MANUFACTURING_RELEASE_ROOT")
    if not configured:
        pytest.skip("set TEST_MANUFACTURING_RELEASE_ROOT for real sibling integration")
    source = Path(configured) / "src/manufacturing_platform"
    shutil.copytree(
        source,
        root / "src/manufacturing_platform",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    assets = root / "webui/dist"
    assets.mkdir(parents=True)
    for name in ("manufacturing.js", "manufacturing.css"):
        shutil.copyfile(Path(configured) / "webui/dist" / name, assets / name)
    monkeypatch.setenv("PLATFORM_MANUFACTURING_ROOT", str(root))
    monkeypatch.setenv(
        "PLATFORM_MANUFACTURING_DATABASE_PATH", str(tmp_path / "legacy.db")
    )
    monkeypatch.setenv(
        "PLATFORM_MANUFACTURING_WORKFLOW_DATABASE_PATH", str(tmp_path / "domain.db")
    )
    monkeypatch.setenv(
        "PLATFORM_MANUFACTURING_AUTHORITY_DATABASE_PATH", str(tmp_path / "authority.db")
    )
    monkeypatch.setenv("PLATFORM_MANUFACTURING_DATASET_ID", "test")
    monkeypatch.setenv("PLATFORM_MANUFACTURING_DATASET_MODE", "test")
    monkeypatch.setenv("PLATFORM_MANUFACTURING_DATASET_LABEL", "Test")
    auth = FakeAuth(prefix=prefix)
    owner = auth.context.internal_user_id
    member = uuid4()
    active_subjects = {owner, member}
    app = FastAPI()
    app.include_router(
        build_manufacturing_router(
            resolve_workflow_subject=lambda uid: uid in active_subjects,
            resolve_workflow_profiles=lambda ids: [dict(user_id=str(uid),display_name="Named project member" if uid==member else "Project Owner") for uid in ids if uid in active_subjects],
        ),
        prefix=prefix.rstrip("/"),
    )
    auth.hard_stale_audit = lambda *args: None
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        authorization=AuthorizationService(_NoObservationGrants()),
        routes=app.routes,
        public_assets=frozenset(),
    )
    client = TestClient(app, base_url="https://agent.example.test")
    base = prefix.rstrip("/") + "/api/v1/manufacturing"
    wf = base + "/workflow"

    def domain_files():
        # The release opens its store eagerly; unauthenticated calls must not touch it.
        return {p.name: p.read_bytes() for p in tmp_path.glob("domain.db*")}

    pristine = domain_files()
    assert (
        client.get(wf + "/access", headers={"Cookie": "secret-cookie"}).status_code
        == 401
    )
    events = [
        r.workflow_security for r in caplog.records if hasattr(r, "workflow_security")
    ]
    assert len(events) == 1
    assert events[0]["status"] == 401
    assert "secret" not in str(events)
    assert domain_files() == pristine
    caplog.clear()
    for path in ("/expedites?limit=no", "/handoffs?view=invalid", "/me"):
        response = client.get(wf + path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"
    assert domain_files() == pristine
    caplog.clear()
    for path in ("/production-tools/status", "/production-tools/projects/10001/status"):
        assert client.get(wf + path).status_code == 401
    client.cookies.set(auth.cookie_name, "valid-cookie")
    # Independent source routes work before any manufacturing member exists.
    assert client.get(wf + "/todos").json()["items"] == []
    assert client.get(wf + "/production-tools/status").json()["configured"] is False
    me = client.get(wf + "/me").json()
    assert me["user_id"] == str(owner) and me["configuration_admin"] is True
    assert me["roles"] == ["mc", "iqc", "warehouse", "production", "purchasing"]
    bootstrap = client.get(wf + "/access").json()
    assert bootstrap["allowed"] is True and bootstrap["project_ids"] == []
    assert bootstrap["roles"] == me["roles"]
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, False)
    for path in ("/production-tools/status", "/production-tools/projects",
                 "/production-tools/projects/10001/status"):
        denied = client.get(wf + path)
        assert denied.status_code == 403
        assert denied.json()["code"] == "FORBIDDEN"
    auth.context = AuthContext(owner, Role.PLATFORM_OWNER, auth.context.session_id, False)
    for source_id in ("0", "01", "-1", "1.0", "a", "1" * 21):
        assert client.get(wf + f"/production-tools/projects/{source_id}/status").status_code == 422
    assert client.get(wf + "/production-tools/unknown").status_code in (403, 404)
    assert client.post(wf + "/production-tools/status", json={}).status_code in (403, 404, 405)
    headers = {
        "Origin": "https://agent.example.test",
        "X-CSRF-Token": auth.csrf,
        "X-User-ID": str(member),
    }
    handoff = {
        "operation_id": str(uuid4()), "material": "M-1", "quantity": "1", "unit": "PC",
        "to_user": str(member), "from_location": "WH", "to_location": "LINE",
    }
    assert client.post(wf + "/handoffs", json=handoff).status_code == 403
    assert (
        client.post(
            wf + "/handoffs",
            headers={"Origin": "https://evil.test", "X-CSRF-Token": auth.csrf},
            json=handoff,
        ).status_code
        == 403
    )
    # Accepted writes reach the domain; without a MES source no handoff is created.
    response = client.post(wf + "/handoffs", headers=headers, json=handoff)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "MES_CONTEXT_UNAVAILABLE"
    assert client.get(prefix.rstrip("/") + "/fixture/login").status_code in (403, 404, 405)
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, False)
    denied_access = client.get(wf + "/access").json()
    assert denied_access["allowed"] is False and denied_access["roles"] == []
    assert client.get(wf + "/todos").status_code == 403
    assert (
        client.get(wf + "/assets/manufacturing.js?release=development").status_code
        == 403
    )
    auth.context = AuthContext(
        owner, Role.PLATFORM_OWNER, auth.context.session_id, False
    )
    assert (
        client.get(wf + "/assets/manufacturing.js?release=development").status_code
        == 200
    )
    assert client.get(wf + "/assets/missing?release=development").status_code == 404
    assert client.get(wf + "/unlisted").status_code in (403, 404)
    grant = {"roles": ["production"], "active": True}
    active_subjects.remove(member)
    assert (
        client.put(f"{wf}/members/{member}", headers=headers, json=grant).status_code
        == 404
    )
    active_subjects.add(member)
    granted = client.put(f"{wf}/members/{member}", headers=headers, json=grant)
    assert granted.status_code == 200, granted.text
    assert granted.json()["roles"] == ["production"]
    assert granted.json()["display_name"] == "Named project member"
    import sqlite3
    with sqlite3.connect(f"file:{tmp_path / 'domain.db'}?mode=ro", uri=True) as facts:
        # Spoofed caller identity headers never become the domain actor.
        assert facts.execute("SELECT updated_by FROM member_roles WHERE user_id=?", (str(member),)).fetchall() == [(str(owner),)]
        assert facts.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0
    assert [item["user_id"] for item in client.get(wf + "/members").json()["items"]] == [str(member)]
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, False)
    bootstrap = client.get(wf + "/access").json()
    assert bootstrap["allowed"] is True and bootstrap["roles"] == ["production"]
    assert bootstrap["project_ids"] == []
    assert client.get(wf + "/members").status_code == 403
    assert client.put(f"{wf}/members/{member}", headers=headers, json=grant).status_code == 403
    tools = wf + "/production-tools/projects/10001"
    assert client.get(tools + "/status").status_code == 403
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, True)
    assert client.get(tools + "/status").status_code == 403
    auth.context = AuthContext(uuid4(), Role.MEMBER, auth.context.session_id, False)
    assert client.get(tools + "/status").status_code == 403
    assert client.get(wf + "/todos").status_code == 403
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, False)
    directory = client.get(wf + "/members/directory", params={"role": "production"})
    assert directory.status_code == 200, directory.text
    assert directory.json()["items"] == [
        {"user_id": str(member), "display_name": "Named project member", "roles": ["production"]}
    ]
    assert client.get(wf + "/members/directory", params={"role": "invalid"}).status_code == 422
    for path in ("/expedites", "/handoffs", "/handoffs?view=pending", "/todos"):
        page = client.get(wf + path)
        assert page.status_code == 200, page.text
        assert page.json()["items"] == []
        assert page.headers["cache-control"] == "private, no-store"
    assert client.get(wf + "/expedites", params={"limit": "invalid"}).status_code == 422
    assert client.get(wf + "/handoffs", params={"view": "invalid"}).status_code == 422
    expedite = {"operation_id": str(uuid4()), "shop_order": "WO-1", "material": "M-1",
                "component_key": "1:1", "reason": "Shortage"}
    response = client.post(wf + "/expedites", headers=headers, json=expedite)
    assert response.status_code == 403 and response.json()["code"] == "ROLE_REQUIRED"
    assert client.get(base + "/projects").status_code == 403
    assert client.get(base + "/health").status_code == 403
    assert client.get(wf + "/projects").status_code == 403
    assert (
        client.get(wf + "/assets/manufacturing.js?release=development").status_code
        == 200
    )
    caplog.clear()
    assert client.get(f"{wf}/expedites/999").status_code == 404
    events = [
        r.workflow_security for r in caplog.records if hasattr(r, "workflow_security")
    ]
    assert len(events) == 1  # the library denial is not duplicated by the host
    assert events[0]["code"] == "NOT_FOUND"
    caplog.clear()
    assert client.get(wf + "/unlisted-secret?token=secret").status_code in (403, 404)
    events = [
        r.workflow_security for r in caplog.records if hasattr(r, "workflow_security")
    ]
    assert len(events) == 1
    assert len(str(events[0])) < 1024
    assert "secret" not in str(events[0])
    assert client.delete(wf + "/handoffs").status_code in (403, 404, 405)
    auth.context = AuthContext(
        member, Role.MANAGEMENT_VIEWER, auth.context.session_id, False
    )
    assert client.post(wf + "/handoffs", headers=headers, json=handoff).status_code == 403
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, True)
    assert client.post(wf + "/handoffs", headers=headers, json=handoff).status_code == 503

    # Explicit configuration without a trusted directory resolver fails only the new domain.
    unavailable_app = FastAPI()
    unavailable_app.include_router(
        build_manufacturing_router(), prefix=prefix.rstrip("/")
    )
    unavailable_app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        authorization=AuthorizationService(_NoObservationGrants()),
        routes=unavailable_app.routes,
        public_assets=frozenset(),
    )
    unavailable_client = TestClient(
        unavailable_app, base_url="https://agent.example.test"
    )
    unavailable_client.cookies.set(auth.cookie_name, "valid-cookie")
    auth.context = AuthContext(
        owner, Role.PLATFORM_OWNER, auth.context.session_id, False
    )
    assert unavailable_client.get(wf + "/access").status_code == 503
    assert unavailable_client.get(base + "/access").status_code == 200

    # A path collision is rejected before the legacy database can be opened by workflow.
    legacy_file = tmp_path / "legacy.db"
    legacy_file.write_bytes(b"legacy sentinel")
    monkeypatch.setenv(
        "PLATFORM_MANUFACTURING_WORKFLOW_DATABASE_PATH", str(legacy_file)
    )
    collision_app = FastAPI()
    collision_app.include_router(
        build_manufacturing_router(resolve_workflow_subject=lambda uid: True),
        prefix=prefix.rstrip("/"),
    )
    collision_app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        authorization=AuthorizationService(_NoObservationGrants()),
        routes=collision_app.routes,
        public_assets=frozenset(),
    )
    collision_client = TestClient(collision_app, base_url="https://agent.example.test")
    collision_client.cookies.set(auth.cookie_name, "valid-cookie")
    assert collision_client.get(wf + "/access").status_code == 503
    assert collision_client.get(base + "/access").status_code == 200
    assert legacy_file.read_bytes() == b"legacy sentinel"

@pytest.mark.parametrize('role', list(Role))
@pytest.mark.parametrize('path', ['/me', '/todos', '/members/directory', '/expedites', '/handoffs'])
def test_global_workflow_exact_auth_contract(role, path):
    from app.control_plane.authorization import AuthorizationService
    from app.manufacturing_paths import WORKFLOW_ROUTES
    from test_dingtalk_auth_api import _NoObservationGrants
    assert ('GET', path) in WORKFLOW_ROUTES
    service = AuthorizationService(_NoObservationGrants())
    context = AuthContext(uuid4(), role, uuid4(), False)
    route = '/api/v1/manufacturing/workflow' + path
    assert service.decide(None, 'GET', route, ()).status_code == 401
    assert service.decide(context, 'GET', route, ()).allowed
    assert service.decide(context, 'POST', route, ()).allowed is (('POST', path) in WORKFLOW_ROUTES)
    assert not service.decide(context, 'DELETE', route, ()).allowed
    assert not service.decide(context, 'GET', route + '/extra', ()).allowed


def test_unavailable_global_routes_authenticate_before_validation(tmp_path, monkeypatch):
    from app.manufacturing import build_manufacturing_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.control_plane.authorization import AuthorizationService
    from app.control_plane.middleware import IdentitySecurityMiddleware
    from test_dingtalk_auth_api import FakeAuth, _NoObservationGrants
    monkeypatch.delenv('PLATFORM_MANUFACTURING_ROOT', raising=False)
    auth = FakeAuth()
    app = FastAPI()
    app.include_router(build_manufacturing_router())
    app.add_middleware(IdentitySecurityMiddleware, auth=auth, authorization=AuthorizationService(_NoObservationGrants()), routes=app.routes, public_assets=frozenset())
    client = TestClient(app, base_url='https://agent.example.test')
    for path in ('/expedites?limit=no&status=invalid', '/handoffs?limit=no&view=invalid', '/expedites/invalid'):
        url = '/api/v1/manufacturing/workflow' + path
        response = client.get(url)
        assert response.status_code == 401
        client.cookies.set(auth.cookie_name, 'valid-cookie')
        response = client.get(url)
        assert response.status_code == 503
        assert response.headers['cache-control'] == 'private, no-store'
        client.cookies.clear()


def test_host_allowlist_covers_every_released_workflow_route(tmp_path):
    # The host denies unlisted workflow routes, so a library route missing here
    # surfaces to users as "no access" even for platform owners.
    import importlib.util
    import os
    import sys
    from pathlib import Path

    from app.manufacturing_paths import WORKFLOW_AUTH_ROUTES
    from fastapi.routing import APIRoute

    configured = os.environ.get("TEST_MANUFACTURING_RELEASE_ROOT")
    if not configured:
        pytest.skip("set TEST_MANUFACTURING_RELEASE_ROOT for real sibling integration")
    package = Path(configured) / "src/manufacturing_platform"
    spec = importlib.util.spec_from_file_location(
        "_route_contract_manufacturing", package / "__init__.py",
        submodule_search_locations=[str(package)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        router = module.build_workflow_router(
            database_path=str(tmp_path / "domain.db"),
            authority_database_path=str(tmp_path / "authority.db"),
            assets_dir=str(tmp_path),
            authenticate=lambda request, write: None,
            resolve_subject=lambda user_id: False,
            release={},
            dataset={"id": "test", "mode": "test", "label": "test"},
        )
    finally:
        for name in [n for n in sys.modules if n.startswith(spec.name)]:
            del sys.modules[name]
    released = {
        (method, route.path)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert released and released <= WORKFLOW_AUTH_ROUTES, sorted(released - WORKFLOW_AUTH_ROUTES)
    # Exact: no retired route remains allowlisted (the shell entry is host-owned).
    host_only = {("GET", "/manufacturing"), ("GET", "/manufacturing/")}
    assert WORKFLOW_AUTH_ROUTES - host_only == released, sorted(WORKFLOW_AUTH_ROUTES - host_only - released)
