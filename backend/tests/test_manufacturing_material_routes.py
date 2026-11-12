"""Exact authorization and unavailable-host behavior for material workbench reads."""
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


PATHS = (
    "/projects/{project_id}/material-task-references",
    "/material-control/overview",
    "/projects/{project_id}/material-control",
    "/projects/{project_id}/material-tasks",
    "/material-control/tasks",
    "/projects/{project_id}/material-control/assignees",
    "/projects/{project_id}/work-orders/{work_order_id}/mes-materials",
)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("role", list(Role))
def test_material_reads_use_exact_existing_workflow_identity_policy(path, role):
    assert ("GET", path) in WORKFLOW_ROUTES
    policy = AuthorizationService(_NoObservationGrants())
    actor = AuthContext(uuid4(), role, uuid4(), False)
    route = "/api/v1/manufacturing/workflow" + path
    assert policy.decide(None, "GET", route, ()).status_code == 401
    assert policy.decide(actor, "GET", route, ()).allowed
    assert not policy.decide(actor, "POST", route, ()).allowed
    assert not policy.decide(actor, "GET", route + "/extra", ()).allowed


def test_unavailable_material_reads_authenticate_before_invalid_query(tmp_path, monkeypatch):
    monkeypatch.delenv("PLATFORM_MANUFACTURING_ROOT", raising=False)
    auth = FakeAuth()
    app = FastAPI()
    app.include_router(build_manufacturing_router())
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        authorization=AuthorizationService(_NoObservationGrants()),
        routes=app.routes,
        public_assets=frozenset(),
    )
    client = TestClient(app, base_url="https://agent.example.test")
    for path in PATHS:
        route = "/api/v1/manufacturing/workflow" + path.replace("{project_id}", "invalid")
        response = client.get(route + "?limit=invalid")
        assert response.status_code == 401
        client.cookies.set(auth.cookie_name, "valid-cookie")
        response = client.get(route + "?limit=invalid")
        assert response.status_code == 503
        assert response.headers["cache-control"] == "private, no-store"
        client.cookies.clear()


@pytest.mark.parametrize("role,stale", [(Role.MANAGEMENT_VIEWER, False), (Role.MEMBER, True)])
def test_reference_read_preserves_trusted_read_only_identity(role, stale):
    from types import SimpleNamespace
    from app.manufacturing import authorize_workflow_identity
    actor = AuthContext(uuid4(), role, uuid4(), stale)
    policy = AuthorizationService(_NoObservationGrants())
    path = "/api/v1/manufacturing/workflow/projects/{project_id}/material-task-references"
    assert policy.decide(actor, "GET", path, ()).allowed
    identity = authorize_workflow_identity(SimpleNamespace(state=SimpleNamespace(auth_context=actor)))
    assert identity["read_only"] is True
    assert identity["user_id"] == str(actor.internal_user_id)


def test_old_workflow_reserves_reference_read_before_legacy_catchall(tmp_path, monkeypatch):
    package = tmp_path / "src/manufacturing_platform"
    package.mkdir(parents=True)
    assets = tmp_path / "webui/dist"
    assets.mkdir(parents=True)
    for name in ("manufacturing.js", "manufacturing.css"):
        (assets / name).write_text("fixture")
    (package / "__init__.py").write_text("""
from fastapi import APIRouter
WORKFLOW_API_VERSION = 1
def build_router(**kwargs):
    r = APIRouter(prefix='/api/v1/manufacturing')
    @r.get('/{path:path}')
    def catchall(path): return {'legacy_catchall': True}
    return r
def build_workflow_router(*, database_path, authority_database_path, assets_dir, authenticate, resolve_subject, release, dataset):
    r = APIRouter(prefix='/api/v1/manufacturing/workflow')
    @r.get('/access')
    def access(): return {'available': True}
    return r
""")
    for key, value in {"ROOT": str(tmp_path), "DATABASE_PATH": str(tmp_path / "legacy.db"),
                       "WORKFLOW_DATABASE_PATH": str(tmp_path / "domain.db"),
                       "AUTHORITY_DATABASE_PATH": str(tmp_path / "authority.db"),
                       "DATASET_ID": "reference-test", "DATASET_MODE": "test", "DATASET_LABEL": "Test"}.items():
        monkeypatch.setenv("PLATFORM_MANUFACTURING_" + key, value)
    auth = FakeAuth()
    app = FastAPI()
    app.include_router(build_manufacturing_router(resolve_workflow_subject=lambda uid: True))
    app.add_middleware(IdentitySecurityMiddleware, auth=auth,
                       authorization=AuthorizationService(_NoObservationGrants()), routes=app.routes, public_assets=frozenset())
    client = TestClient(app, base_url="https://agent.example.test")
    path = "/api/v1/manufacturing/workflow/projects/invalid/material-task-references"
    assert client.get(path).status_code == 401
    client.cookies.set(auth.cookie_name, "valid-cookie")
    response = client.get(path + "?limit=invalid")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
    assert "legacy_catchall" not in response.text
    assert client.get("/api/v1/manufacturing/workflow/access").json() == {"available": True}
