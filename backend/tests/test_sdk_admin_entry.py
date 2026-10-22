"""Entry paths may bootstrap login; content remains authenticated."""

import pytest

from app.control_plane.auth import _safe_local_return_path
from app.control_plane.middleware import is_public_request


@pytest.mark.parametrize("path", [
    "/sdk-admin", "/sdk-admin/", "/sdk-admin/products", "/sdk-admin/delivery",
    "/sdk-admin/diagnostics", "/sdk-admin/knowledge",
    "/sdk-admin/knowledge/KB-PROP-OB_PROP_COLOR_GAIN_INT",
])
def test_sdk_admin_login_return_and_static_shell(path):
    assert _safe_local_return_path(path)
    assert is_public_request("GET", path, "/")
    assert is_public_request("GET", "/preview" + path, "/preview")
    assert not is_public_request("POST", path, "/")


@pytest.mark.parametrize("path", [
    "/sdk-admin-evil", "/sdk-admin/other", "/sdk-admin/knowledge/..",
    "/sdk-admin/knowledge/%2e%2e", "/sdk-admin/knowledge/KB%2fAPI",
    "/sdk-admin/knowledge/KB-API-001/extra", "/sdk-admin//",
    "/sdk-admin/knowledge/" + "a" * 161,
])
def test_noncanonical_management_paths_do_not_gain_login_or_public_access(path):
    assert not _safe_local_return_path(path)
    assert not is_public_request("GET", path, "/")


@pytest.mark.parametrize("path", [
    "/api/v1/sdk-admin/access", "/api/v1/sdk-admin/catalog",
    "/api/v1/sdk-admin/documents", "/api/v1/sdk-admin/documents/KB-API-001",
])
def test_sdk_admin_content_api_is_never_a_public_shell(path):
    assert not is_public_request("GET", path, "/")
    assert not is_public_request("GET", "/preview" + path, "/preview")


def test_application_factory_keeps_shell_public_and_data_behind_current_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.control_plane.models import AuthContext, Role
    from app.sdk_workbench.plan_service import PlanService
    from test_dingtalk_auth_api import FakeAuth, _app

    auth = FakeAuth()
    auth.context = AuthContext(auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False)
    members = SimpleNamespace(role="viewer")
    members.role_for = lambda actor: members.role
    app = _app(tmp_path, monkeypatch, auth)
    app.state.sdk_plan_service = PlanService(members, None)
    app.state.sdk_admin_repository = SimpleNamespace(catalog=lambda: {"fixture": "sdk-content"})
    client = TestClient(app)
    assert client.get("/sdk-admin/knowledge/KB-API-001").status_code == 200
    assert client.get("/sdk-admin-evil").status_code == 401
    assert client.get("/api/v1/sdk-admin/catalog").status_code == 401
    client.cookies.set(auth.cookie_name, "valid-cookie")
    assert client.get("/api/v1/sdk-admin/access").json() == {"allowed": True}
    response = client.get("/api/v1/sdk-admin/catalog")
    assert response.status_code == 200
    assert response.json() == {"fixture": "sdk-content"}
    members.role = None
    assert client.get("/api/v1/sdk-admin/catalog").status_code == 403
    members.role = "viewer"
    auth.revoked = True
    assert client.get("/api/v1/sdk-admin/catalog").status_code == 401
