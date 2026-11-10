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
from app.manufacturing_paths import WORKFLOW_ROUTES
from test_dingtalk_auth_api import FakeAuth, _NoObservationGrants

ROOT = "/projects/{project_id}/production-tools"
PATHS = {
    ROOT + suffix
    for suffix in (
        "/status",
        "/observations",
        "/catalog/{kind}",
        "/evidence",
        "/evidence/{source_log_id}/content",
        "/statistics",
        "/snapshots/{snapshot_id}",
    )
}
BASE = "/api/v1/manufacturing/workflow"


def test_exact_read_whitelist():
    assert {
        (method, path)
        for method, path in WORKFLOW_ROUTES
        if "/production-tools/" in path
    } == {("GET", path) for path in PATHS}
    service = AuthorizationService(_NoObservationGrants())
    member = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
    for path in PATHS:
        assert service.decide(None, "GET", BASE + path, ()).status_code == 401
        assert service.decide(member, "GET", BASE + path, ()).allowed
        assert not service.decide(member, "POST", BASE + path, ()).allowed
        assert not service.decide(member, "GET", BASE + path + "/extra", ()).allowed


def configured_release(
    tmp_path, monkeypatch, *, native=True, factory=True, malformed=False
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
    if factory:
        source += """def validate_snapshot_path(path, protected_paths=()):
    from pathlib import Path
    received['protected'] = tuple(protected_paths)
    if not Path(path).is_absolute(): raise ValueError('private')
    for other in protected_paths:
        p, q = Path(path), Path(other)
        if p.resolve() == q.resolve() or (p.exists() and q.exists() and p.samefile(q)): raise ValueError('private')
def build_native_config(config_path, snapshot_path, dataset):
    received['factory'] = (config_path, snapshot_path, dataset)
"""
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
        source += "    @router.get('/projects/{project_id}/production-tools/status')\n    def status(project_id): return {'configured': production_tools is not None}\n"
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
    auth.context = AuthContext(uuid4(), Role.MEMBER, auth.context.session_id, False)
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


@pytest.mark.parametrize("native,factory", [(False, True), (True, False)])
def test_old_release_seven_routes_unavailable_before_legacy(
    tmp_path, monkeypatch, native, factory
):
    configured_release(tmp_path, monkeypatch, native=native, factory=factory)
    api, auth = client()
    for path in PATHS:
        url = BASE + path.format(
            project_id=uuid4(), kind="software", source_log_id=1, snapshot_id=uuid4()
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
def test_config_boundary_preserves_workflow(tmp_path, monkeypatch, mode):
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
    result = api.get(BASE + f"/projects/{uuid4()}/production-tools/status")
    assert result.status_code == 200
    assert result.json()["configured"] is (mode == "enabled")
    module = loaded(env)
    if mode == "enabled":
        assert module.received["config"] == "native-config"
        assert set(module.received["protected"]) == set(protected)
        assert module.received["factory"][2] == {
            "id": "test",
            "mode": "test",
            "label": "Test",
        }
    else:
        assert module.received["config"] is None
    assert "SECRET" not in result.text


def test_whole_workflow_unavailable_authenticates_seven_routes(monkeypatch):
    monkeypatch.delenv("PLATFORM_MANUFACTURING_ROOT", raising=False)
    api, auth = client()
    for path in PATHS:
        url = BASE + path.format(
            project_id=uuid4(), kind="software", source_log_id=1, snapshot_id=uuid4()
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
    result = api.get(BASE + f"/projects/{uuid4()}/production-tools/observations")
    assert result.status_code == 503 and "legacy_catchall" not in result.text
