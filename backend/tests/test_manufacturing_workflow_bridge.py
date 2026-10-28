from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.control_plane.models import AuthContext, Role
from app.manufacturing import authorize_workflow_identity
from fastapi import HTTPException


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
        (assets / name).write_text("test")
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
            resolve_workflow_subject=lambda uid: uid in active_subjects
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
    assert not (tmp_path / "domain.db").exists()
    caplog.clear()
    client.cookies.set(auth.cookie_name, "valid-cookie")
    headers = {
        "Origin": "https://agent.example.test",
        "X-CSRF-Token": auth.csrf,
        "X-User-ID": str(member),
    }
    assert client.post(wf + "/projects", json={}).status_code == 403
    assert (
        client.post(
            wf + "/projects",
            headers={"Origin": "https://evil.test", "X-CSRF-Token": auth.csrf},
            json={},
        ).status_code
        == 403
    )
    body = {
        "operation_id": str(uuid4()),
        "name": "Camera",
        "line_id": "line",
        "product": "camera",
        "customer_id": "customer",
        "mode": "internal",
        "unit": {"code": "piece", "scale": 0},
        "quantity": "100",
        "contract": {
            "customer_approval_required": "unknown",
            "customer_release_required": "unknown",
            "acceptance_required": "unknown",
            "customer_revision": "unknown",
            "internal_revision": "unknown",
            "material_ownership_rules": "unknown",
        },
    }
    response = client.post(wf + "/projects", headers=headers, json=body)
    assert response.status_code == 200, response.text
    pid = response.json()["id"]
    assert response.json()["version"] == 1
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, False)
    assert client.get(wf + "/access").json()["allowed"] is False
    assert client.get(f"{wf}/projects/{pid}").status_code == 404
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
    grant = {
        "operation_id": str(uuid4()),
        "expected_version": 1,
        "scope": {"kind": "project", "project_id": pid},
        "roles": ["planner"],
        "qualifications": [],
        "active": True,
        "evidence": "assigned",
    }
    active_subjects.remove(member)
    assert (
        client.put(
            f"{wf}/projects/{pid}/members/{member}", headers=headers, json=grant
        ).status_code
        == 422
    )
    active_subjects.add(member)
    assert (
        client.put(
            f"{wf}/projects/{pid}/members/{member}", headers=headers, json=grant
        ).status_code
        == 200
    )
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, False)
    assert client.get(wf + "/access").json()["project_ids"] == [pid]
    assert client.get(f"{wf}/projects/{pid}").status_code == 200
    assert client.get(base + "/projects").status_code == 403
    assert (
        client.get(wf + "/assets/manufacturing.js?release=development").status_code
        == 200
    )
    caplog.clear()
    assert client.get(f"{wf}/projects/{uuid4()}").status_code == 404
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
    assert client.delete(wf + "/projects").status_code in (403, 404, 405)
    auth.context = AuthContext(
        member, Role.MANAGEMENT_VIEWER, auth.context.session_id, False
    )
    assert client.post(wf + "/projects", headers=headers, json=body).status_code == 403
    auth.context = AuthContext(member, Role.MEMBER, auth.context.session_id, True)
    assert client.post(wf + "/projects", headers=headers, json=body).status_code == 503

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
