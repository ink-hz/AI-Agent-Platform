from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.hr_agent.access import HrAccess
from app.hr_agent.repository import HrAgentRepository
from app.hr_agent.routes import build_hr_agent_router
from app.hr_agent.service import HrAgentService
from tests.hr_agent_support import hr_agent_database, make_hr_settings
from tests.test_hr_position_api import _SecurityAuth


@pytest.fixture(scope="module")
def database():
    with hr_agent_database() as db:
        yield db


@pytest.fixture
def secured(tmp_path, database):
    with database.admin_connection() as conn:
        conn.execute(
            "TRUNCATE platform_hr_agent.threads, platform_hr_agent.operations CASCADE"
        )
    owner = uuid4()
    settings = make_hr_settings(tmp_path)
    access = HrAccess(
        SimpleNamespace(decide_for_user_id=lambda *_: SimpleNamespace(allowed=True))
    )
    repo = HrAgentRepository(
        database.connection,
        settings.create_codec(),
        settings=settings,
        scope_validator=lambda owner, objects, refs, work: access.authorize_scope(
            owner, objects, refs, work_id=work
        ),
    )
    app = FastAPI()
    app.state.hr_agent_service = HrAgentService(repo, access)
    app.include_router(build_hr_agent_router(app.state.hr_agent_service))
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=_SecurityAuth(owner),
        public_assets=frozenset(),
        authorization=AuthorizationService(SimpleNamespace(permits=lambda *_: False)),
        routes=tuple(app.router.routes),
    )
    client = TestClient(app)
    client.cookies.set("session", "valid")
    client.cookies.set("csrf", "csrf-token")
    headers = {
        "Origin": "https://agent.example.test",
        "X-CSRF-Token": "csrf-token",
        "Idempotency-Key": str(uuid4()),
    }
    return client, headers, repo, owner


def body():
    return {
        "thread_id": None,
        "text": "校准公开岗位",
        "objects": [],
        "references": [],
        "budget_profile": "test",
    }


def test_authenticated_submit_persists_and_replays(secured):
    client, headers, repo, owner = secured
    response = client.post("/api/hr/agent/works", json=body(), headers=headers)
    assert response.status_code == 201, response.text
    view = response.json()
    assert repo.get_work(owner, view["work_id"]) == view
    assert (
        client.post("/api/hr/agent/works", json=body(), headers=headers).status_code
        == 200
    )
    assert (
        client.post(
            "/api/hr/agent/works", json={**body(), "text": "different"}, headers=headers
        ).status_code
        == 409
    )
    assert client.get("/api/hr/agent/works/" + view["work_id"]).status_code == 200
    assert "no-store" in response.headers["cache-control"]


def test_security_and_unknown_owner_fields_fail_before_submission(secured):
    client, headers, _repo, owner = secured
    assert client.post("/api/hr/agent/works", json=body()).status_code == 403
    assert (
        client.post(
            "/api/hr/agent/works",
            json=body(),
            headers={**headers, "Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/hr/agent/works",
            json={**body(), "owner_id": str(owner)},
            headers=headers,
        ).status_code
        == 422
    )
    client.cookies.clear()
    assert client.get("/api/hr/agent/threads").status_code == 401


def test_standards_are_disabled_without_fake_confirmation(secured):
    client, headers, *_ = secured
    assert (
        client.post(
            f"/api/hr/agent/positions/{uuid4()}/standards/confirm",
            json={},
            headers=headers,
        ).status_code
        == 503
    )


def test_reads_pagination_scope_and_cancel(secured):
    client, headers, _repo, _owner = secured
    view = client.post("/api/hr/agent/works", json=body(), headers=headers).json()
    work = view["work_id"]
    thread = view["thread_id"]
    for path in [
        "/threads",
        f"/threads/{thread}/works",
        f"/works/{work}/messages",
        f"/works/{work}/events",
        f"/results?thread_id={thread}",
    ]:
        response = client.get("/api/hr/agent" + path)
        assert response.status_code == 200, (path, response.text)
    assert (
        client.get(f"/api/hr/agent/works/{work}/messages?limit=201").status_code == 422
    )
    assert client.get(f"/api/hr/agent/works/{work}/events?after=-1").status_code == 422
    assert client.get("/api/hr/agent/results").status_code == 422
    assert (
        client.get(
            f"/api/hr/agent/results?thread_id={thread}&object_kind=position&object_id=p"
        ).status_code
        == 422
    )
    assert client.get("/api/hr/agent/works/" + str(uuid4())).status_code == 404
    assert (
        client.get("/api/hr/agent/threads/" + str(uuid4()) + "/works").status_code
        == 404
    )
    response = client.post(
        f"/api/hr/agent/works/{work}/cancel",
        json={"reason": "user stopped"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "cancelled"


def test_malformed_body_and_idempotency_key(secured):
    client, headers, *_ = secured
    response = client.post("/api/hr/agent/works", content="{", headers=headers)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"
    headers.pop("Idempotency-Key")
    assert (
        client.post("/api/hr/agent/works", json=body(), headers=headers).status_code
        == 422
    )


@pytest.mark.parametrize("schema_ready", [False, True])
def test_actual_app_opt_in_without_relay_requires_schema_and_never_migrates(
    tmp_path, monkeypatch, schema_ready
):
    from dataclasses import replace

    from app import main
    from app.config import load_config

    owner = uuid4()
    settings = make_hr_settings(tmp_path / "hr")
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}')
    from tests.test_hr_agent_context import publication

    publication(settings.knowledge_dir)
    with hr_agent_database(migrate_hr=schema_ready) as db:
        secret = tmp_path / "dsn"
        secret.write_text(db.dsn)
        secret.chmod(0o600)
        config = load_config()
        config = replace(
            config,
            hr_agent_settings=settings,
            execution_relay_enabled=False,
            direct_agent_enabled=False,
            hr_web_worker_enabled=False,
            control_plane=replace(
                config.control_plane,
                control_database_url_file=str(secret),
                audit_database_url_file="",
            ),
        )
        monkeypatch.setattr(main, "load_config", lambda: config)
        app = main.create_app(
            registry_path=str(registry),
            cluster_contract_path=str(contract),
            start_poller=False,
            identity_auth=_SecurityAuth(owner),
            agent_use_authorization=SimpleNamespace(
                decide_for_user_id=lambda *_: SimpleNamespace(allowed=True),
                permitted_catalog_for_user_id=lambda *_: (),
            ),
        )
        client = TestClient(app)
        client.cookies.set("session", "valid")
        client.cookies.set("csrf", "csrf-token")
        headers = {
            "Origin": "https://agent.example.test",
            "X-CSRF-Token": "csrf-token",
            "Idempotency-Key": str(uuid4()),
        }
        response = client.post("/api/hr/agent/works", headers=headers, json=body())
        if schema_ready:
            assert response.status_code == 201, response.text
            assert response.json()["state"] == "queued"
            assert app.state.hr_agent_service.repository.codec is not None
        else:
            assert response.status_code == 503, response.text
            assert response.json()["code"] == "temporarily_unavailable"
            with db.connection() as conn:
                assert conn.execute(
                    "SELECT to_regclass('platform_hr_agent.works')"
                ).fetchone() == (None,)


def test_changed_session_identity_and_hr_entitlement_are_rechecked(secured):
    client, headers, repo, owner = secured
    work = client.post("/api/hr/agent/works", headers=headers, json=body()).json()
    security = client.app.user_middleware[0].kwargs["auth"]
    security.owner_id = uuid4()
    assert client.get("/api/hr/agent/works/" + work["work_id"]).status_code == 404
    security.owner_id = owner
    security.stale = True
    assert (
        client.post(
            "/api/hr/agent/works",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json=body(),
        ).status_code
        == 503
    )
    security.stale = False
    # The service's HR grant boundary can be revoked between HTTP requests.
    service = client.app.state.hr_agent_service
    service.access.agent_use_authorization = SimpleNamespace(
        decide_for_user_id=lambda *_: SimpleNamespace(allowed=False)
    )
    assert client.get("/api/hr/agent/works/" + work["work_id"]).status_code == 403
    assert repo.get_work(owner, work["work_id"])["state"] == "queued"


def test_budget_extension_http_uses_revision_and_nonzero_addition(secured):
    client, headers, _repo, _owner = secured
    work = client.post("/api/hr/agent/works", headers=headers, json=body()).json()
    path = f"/api/hr/agent/works/{work['work_id']}/budget-extensions"
    payload = {
        "expected_budget_revision": work["budget"]["revision"],
        "addition": {"model_calls": 1, "total_tokens": 100, "active_seconds": 1},
        "reason": "继续研究",
    }
    response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert (
        response.json()["budget"]["limits"]["model_calls"]
        == work["budget"]["limits"]["model_calls"] + 1
    )
    assert (
        client.post(
            path, headers={**headers, "Idempotency-Key": str(uuid4())}, json=payload
        ).status_code
        == 409
    )
    payload["addition"] = {"model_calls": 0, "total_tokens": 0, "active_seconds": 0}
    assert (
        client.post(
            path, headers={**headers, "Idempotency-Key": str(uuid4())}, json=payload
        ).status_code
        == 422
    )
