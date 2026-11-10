"""Host whitelist and optional native-release compatibility boundaries."""

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.manufacturing import build_manufacturing_router
from app.manufacturing_paths import WORKFLOW_ROUTES, PRODUCTION_TOOLS_ROUTES
from test_dingtalk_auth_api import FakeAuth, _NoObservationGrants

PATHS = {
    "/production-tools/status",
    "/production-tools/projects",
    "/production-tools/projects/{source_project_id}/status",
    "/production-tools/projects/{source_project_id}/observations",
    "/production-tools/projects/{source_project_id}/catalog/{kind}",
    "/production-tools/projects/{source_project_id}/evidence",
    "/production-tools/projects/{source_project_id}/evidence/{source_log_id}/content",
    "/production-tools/projects/{source_project_id}/statistics",
    "/production-tools/projects/{source_project_id}/snapshots/{snapshot_id}",
}
BASE = "/api/v1/manufacturing/workflow"


PROJECT = "/production-tools/projects/{source_project_id}"
DRAFT = PROJECT + "/investigations"
ROUTES = {("GET", path) for path in PATHS} | {
    ("GET", "/production-tools/overview"),
    ("GET", PROJECT + "/analysis"),
    ("GET", PROJECT + "/availability"),
    ("GET", DRAFT),
    ("POST", DRAFT),
    ("GET", DRAFT + "/{investigation_id}"),
    ("PUT", DRAFT + "/{investigation_id}"),
    ("GET", DRAFT + "/{investigation_id}/resolve"),
    ("POST", DRAFT + "/{investigation_id}/handoffs"),
    ("GET", "/production-tools/handoffs/{handoff_id}"),
    ("GET", "/production-tools/handoffs/{handoff_id}/resolve"),
    ("POST", "/production-tools/handoffs/{handoff_id}/fork"),
}


def test_exact_registry_matches_released_router(tmp_path, monkeypatch):
    import os
    from fastapi.routing import APIRoute

    monkeypatch.syspath_prepend(
        str(Path(os.environ["TEST_MANUFACTURING_RELEASE_ROOT"]) / "src")
    )
    from manufacturing_platform import build_workflow_router

    router = build_workflow_router(
        database_path=str(tmp_path / "domain"),
        authority_database_path=str(tmp_path / "authority"),
        assets_dir=str(tmp_path),
        authenticate=lambda request, write=False: {},
        resolve_subject=lambda uid: True,
        release="test",
        dataset={"id": "test", "mode": "test", "label": "Test"},
    )
    released = {
        (method, route.path.removeprefix(BASE))
        for route in router.routes
        if isinstance(route, APIRoute) and "/production-tools/" in route.path
        for method in route.methods
    }
    assert len(ROUTES) == 21
    assert PRODUCTION_TOOLS_ROUTES == released == ROUTES
    assert {
        (method, path)
        for method, path in WORKFLOW_ROUTES
        if "/production-tools/" in path
    } == ROUTES
    service = AuthorizationService(_NoObservationGrants())
    for method, path in ROUTES:
        assert service.decide(None, method, BASE + path, ()).status_code == 401
        for role in (Role.MEMBER, Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER):
            actor = AuthContext(uuid4(), role, uuid4(), False)
            # Exact identity routing is followed by native administrator authorization.
            assert service.decide(actor, method, BASE + path, ()).allowed
            assert not service.decide(actor, method, BASE + path + "/extra", ()).allowed
        assert not service.decide(actor, "DELETE", BASE + path, ()).allowed


def configured_release(
    tmp_path,
    monkeypatch,
    *,
    native=True,
    factory=True,
    malformed=False,
    protocol=2,
    factory_arity=2,
):
    root = tmp_path / "release"
    package = root / "src/manufacturing_platform"
    package.mkdir(parents=True)
    assets = root / "webui/dist"
    assets.mkdir(parents=True)
    for name in ("manufacturing.js", "manufacturing.css"):
        (assets / name).touch()
    source = """from fastapi import APIRouter
WORKFLOW_API_VERSION = 1
received = {}
def build_router(**kw):
    router = APIRouter(prefix='/api/v1/manufacturing')
    @router.get('/{path:path}')
    def catchall(path): return {'legacy_catchall': True}
    return router
"""
    if protocol is not None:
        source += f"NATIVE_PRODUCTION_PROTOCOL = {protocol!r}\n"
    if factory:
        source += """def validate_snapshot_path(path, protected_paths=()):
    from pathlib import Path
    received['protected'] = tuple(protected_paths)
    if not Path(path).is_absolute(): raise ValueError('private')
    for other in protected_paths:
        p, q = Path(path), Path(other)
        if p.resolve() == q.resolve() or (p.exists() and q.exists() and p.samefile(q)): raise ValueError('private')
"""
        arguments = "config_path, snapshot_path" + (
            ", dataset" if factory_arity == 3 else ""
        )
        source += f"def build_native_config({arguments}):\n    received['factory'] = (config_path, snapshot_path)\n"
        source += (
            "    raise ValueError('SECRET endpoint')\n"
            if malformed
            else "    return 'native-config'\n"
        )
    keyword = ", production_tools=None" if native else ""
    source += f"""def build_workflow_router(*, database_path, authority_database_path, assets_dir, authenticate, resolve_subject, release, dataset{keyword}):
    router = APIRouter(prefix='/api/v1/manufacturing/workflow')
    @router.get('/access')
    def access(): return {{'workflow_available': True}}
"""
    if native:
        source += "    received['config'] = production_tools\n"
        source += "    @router.get('/production-tools/status')\n    def status(): return {'configured': production_tools is not None}\n"
    source += "    return router\n"
    (package / "__init__.py").write_text(source)
    env = {
        "ROOT": str(root),
        "DATABASE_PATH": str(tmp_path / "legacy.db"),
        "WORKFLOW_DATABASE_PATH": str(tmp_path / "domain.db"),
        "AUTHORITY_DATABASE_PATH": str(tmp_path / "authority.db"),
        "DATASET_ID": "test",
        "DATASET_MODE": "test",
        "DATASET_LABEL": "Test",
    }
    for key, value in env.items():
        monkeypatch.setenv("PLATFORM_MANUFACTURING_" + key, value)
    monkeypatch.delenv(
        "PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_CONFIG_PATH", raising=False
    )
    monkeypatch.delenv(
        "PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_SNAPSHOT_PATH", raising=False
    )
    return env


def client():
    auth = FakeAuth()
    auth.context = AuthContext(
        uuid4(), Role.PLATFORM_ADMIN, auth.context.session_id, False
    )
    app = FastAPI()
    app.include_router(
        build_manufacturing_router(resolve_workflow_subject=lambda uid: True)
    )
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        authorization=AuthorizationService(_NoObservationGrants()),
        routes=app.routes,
        public_assets=frozenset(),
    )
    return TestClient(app, base_url="https://agent.example.test"), auth


def loaded(env):
    import hashlib, sys

    return sys.modules[
        "_platform_manufacturing_"
        + hashlib.sha256(str(Path(env["ROOT"]).resolve()).encode()).hexdigest()[:24]
    ]


@pytest.mark.parametrize(
    "native,factory,protocol,factory_arity",
    [
        (False, True, 2, 2),
        (True, False, 2, 2),
        (True, True, None, 2),
        (True, True, 1, 2),
        (True, True, 2, 3),
    ],
)
def test_old_release_nine_routes_unavailable_before_legacy(
    tmp_path, monkeypatch, native, factory, protocol, factory_arity
):
    configured_release(
        tmp_path,
        monkeypatch,
        native=native,
        factory=factory,
        protocol=protocol,
        factory_arity=factory_arity,
    )
    api, auth = client()
    for path in PATHS:
        url = BASE + path.format(
            source_project_id="10001",
            kind="software",
            source_log_id=1,
            snapshot_id=uuid4(),
        )
        assert api.get(url).status_code == 401
        api.cookies.set(auth.cookie_name, "valid-cookie")
        result = api.get(url)
        assert result.status_code == 503, result.text
        assert "legacy_catchall" not in result.text
        api.cookies.clear()
    api.cookies.set(auth.cookie_name, "valid-cookie")
    assert api.get(BASE + "/access").json()["workflow_available"] is True


@pytest.mark.parametrize(
    "mode",
    [
        "enabled",
        "missing",
        "half",
        "malformed",
        "legacy_alias",
        "domain_alias",
        "authority_alias",
        "secret_alias",
        "relative",
    ],
)
def test_config_boundary_preserves_workflow(tmp_path, monkeypatch, mode, caplog):
    env = configured_release(tmp_path, monkeypatch, malformed=mode == "malformed")
    config = tmp_path / "private.json"
    config.write_text("{}")
    config.chmod(0o600)
    snapshot = tmp_path / "snapshots.db"
    protected = [
        env["DATABASE_PATH"],
        env["WORKFLOW_DATABASE_PATH"],
        env["AUTHORITY_DATABASE_PATH"],
        str(config),
    ]
    targets = {
        "legacy_alias": 0,
        "domain_alias": 1,
        "authority_alias": 2,
        "secret_alias": 3,
    }
    if mode in targets:
        import os

        original = Path(protected[targets[mode]])
        original.touch(exist_ok=True)
        os.link(original, snapshot)
    if mode != "missing":
        monkeypatch.setenv(
            "PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_CONFIG_PATH", str(config)
        )
    if mode not in {"missing", "half"}:
        monkeypatch.setenv(
            "PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_SNAPSHOT_PATH",
            "relative.db" if mode == "relative" else str(snapshot),
        )
    api, auth = client()
    api.cookies.set(auth.cookie_name, "valid-cookie")
    assert api.get(BASE + "/access").json()["workflow_available"] is True
    result = api.get(BASE + "/production-tools/status")
    assert result.status_code == 200
    assert result.json()["configured"] is (mode == "enabled")
    module = loaded(env)
    if mode == "enabled":
        assert module.received["config"] == "native-config"
        assert set(module.received["protected"]) == set(protected + [str(snapshot)])
        assert module.received["factory"] == (str(config), str(snapshot))
    else:
        assert module.received["config"] is None
    assert "SECRET" not in result.text
    assert "SECRET" not in caplog.text


def test_whole_workflow_unavailable_authenticates_nine_routes(monkeypatch):
    monkeypatch.delenv("PLATFORM_MANUFACTURING_ROOT", raising=False)
    api, auth = client()
    for path in PATHS:
        url = BASE + path.format(
            source_project_id="10001",
            kind="software",
            source_log_id=1,
            snapshot_id=uuid4(),
        )
        assert api.get(url).status_code == 401
        api.cookies.set(auth.cookie_name, "valid-cookie")
        assert api.get(url).status_code == 503
        api.cookies.clear()


def test_release_missing_native_routes_still_reserves_exact_paths(
    tmp_path, monkeypatch
):
    configured_release(tmp_path, monkeypatch)
    api, auth = client()
    api.cookies.set(auth.cookie_name, "valid-cookie")
    result = api.get(BASE + "/production-tools/projects/10001/observations")
    assert result.status_code == 503 and "legacy_catchall" not in result.text


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink"])
@pytest.mark.parametrize(
    "target", ["legacy", "domain", "authority", "snapshot", "config"]
)
def test_derived_investigation_path_isolated_before_factory(
    tmp_path, monkeypatch, alias_kind, target
):
    env = configured_release(tmp_path, monkeypatch)
    config = tmp_path / "private.json"
    config.write_text("{}")
    snapshot = tmp_path / "snapshots.db"
    derived = Path(str(snapshot) + ".investigations.sqlite3")
    key = {
        "legacy": "DATABASE_PATH",
        "domain": "WORKFLOW_DATABASE_PATH",
        "authority": "AUTHORITY_DATABASE_PATH",
    }.get(target)
    protected = Path(env[key]) if key else config if target == "config" else snapshot
    if alias_kind == "direct":
        if key:
            monkeypatch.setenv("PLATFORM_MANUFACTURING_" + key, str(derived))
        elif target == "config":
            config = derived
        else:
            # A derived name cannot equal the snapshot string, but it can resolve to it.
            derived.symlink_to(snapshot)
    else:
        protected.touch(exist_ok=True)
        if alias_kind == "symlink":
            derived.symlink_to(protected)
        else:
            derived.hardlink_to(protected)
    config.write_text("{}")
    monkeypatch.setenv(
        "PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_CONFIG_PATH", str(config)
    )
    monkeypatch.setenv(
        "PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_SNAPSHOT_PATH", str(snapshot)
    )
    api, auth = client()
    api.cookies.set(auth.cookie_name, "valid-cookie")
    assert api.get(BASE + "/access").json()["workflow_available"] is True
    assert api.get(BASE + "/production-tools/status").json()["configured"] is False
    assert "factory" not in loaded(env).received


def test_partial_release_reserves_methods_without_intercepting_released_get(
    tmp_path, monkeypatch
):
    env = configured_release(tmp_path, monkeypatch)
    init = Path(env["ROOT"]) / "src/manufacturing_platform/__init__.py"
    source = init.read_text()
    pos = source.rindex("    return router")
    source = (
        source[:pos]
        + "    @router.get('/production-tools/projects/{source_project_id}/investigations')\n    def drafts(source_project_id): return {'released_get': True}\n    @router.get('/production-tools/projects/{source_project_id}/investigations/{investigation_id}')\n    def draft(source_project_id,investigation_id): return {'released_get': True}\n"
        + source[pos:]
    )
    init.write_text(source)
    api, auth = client()
    api.cookies.set(auth.cookie_name, "valid-cookie")
    api.cookies.set(auth.csrf_cookie_name, auth.csrf)
    headers = {"Origin": auth.public_base_url, "X-CSRF-Token": auth.csrf}
    for method, path in [
        ("POST", "/production-tools/projects/1/investigations"),
        ("PUT", "/production-tools/projects/1/investigations/" + str(uuid4())),
    ]:
        assert api.get(BASE + path).json() == {"released_get": True}
        response = api.request(method, BASE + path, headers=headers, json={})
        assert (
            response.status_code == 503
            and "no-store" in response.headers["cache-control"]
        )
        assert api.request(method, BASE + path, json={}).status_code == 403
        auth.context = AuthContext(
            auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False
        )
        assert (
            api.request(method, BASE + path, headers=headers, json={}).status_code
            == 403
        )
        auth.context = AuthContext(
            auth.context.internal_user_id,
            Role.PLATFORM_ADMIN,
            auth.context.session_id,
            False,
        )


def test_unavailable_production_routes_keep_admin_current_role_readonly_and_private_headers(
    monkeypatch,
):
    monkeypatch.delenv("PLATFORM_MANUFACTURING_ROOT", raising=False)
    for key in (
        "WORKFLOW_DATABASE_PATH",
        "AUTHORITY_DATABASE_PATH",
        "DATASET_ID",
        "DATASET_MODE",
        "DATASET_LABEL",
    ):
        monkeypatch.delenv("PLATFORM_MANUFACTURING_" + key, raising=False)
    api, auth = client()
    api.cookies.set(auth.cookie_name, "valid-cookie")
    api.cookies.set(auth.csrf_cookie_name, auth.csrf)
    headers = {"Origin": auth.public_base_url, "X-CSRF-Token": auth.csrf}
    for method, path in ROUTES:
        path = BASE + path.format(
            source_project_id="1",
            kind="software",
            source_log_id="1",
            snapshot_id=uuid4(),
            investigation_id=uuid4(),
            handoff_id=uuid4(),
        )
        response = api.request(
            method, path, headers=headers, json={} if method != "GET" else None
        )
        assert (
            response.status_code == 503
            and "no-store" in response.headers["cache-control"]
        )
        auth.context = AuthContext(
            auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False
        )
        assert api.request(method, path, headers=headers).status_code == 403
        auth.context = AuthContext(
            auth.context.internal_user_id,
            Role.PLATFORM_ADMIN,
            auth.context.session_id,
            False,
        )
    auth.context = AuthContext(
        auth.context.internal_user_id,
        Role.PLATFORM_ADMIN,
        auth.context.session_id,
        True,
    )
    path = BASE + "/production-tools/projects/1/investigations"
    auth.hard_stale_audit = lambda *_args: None  # Synthetic audit boundary.
    readonly_response = api.get(path)
    assert readonly_response.status_code == 503
    assert readonly_response.json() == {"detail": "Workflow unavailable"}
    assert api.post(path, headers=headers, json={}).status_code == 503
    assert api.get(BASE + "/access").status_code == 200


def test_unavailable_namespace_does_not_classify_dynamic_object_id_as_production(
    monkeypatch,
):
    monkeypatch.delenv("PLATFORM_MANUFACTURING_ROOT", raising=False)
    api, auth = client()
    api.cookies.set(auth.cookie_name, "valid-cookie")
    auth.context = AuthContext(
        auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False
    )
    response = api.get(BASE + "/projects/1/systems/erp/facts/production-tools/object")
    assert response.status_code == 503, response.text
