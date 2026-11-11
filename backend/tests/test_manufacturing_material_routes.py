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
