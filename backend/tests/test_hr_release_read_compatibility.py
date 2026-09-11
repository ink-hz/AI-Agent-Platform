"""Release assembly guard: Loop is additive to deployed HR read APIs."""

import base64
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.hr_agent.access import HrAccess
from app.hr_agent.service import HrAgentService
from tests.hr_agent_support import hr_agent_database, make_hr_settings
from tests.test_hr_position_api import _SecurityAuth


class _Knowledge:
    def list_resources(self):
        return []

    def read_resource(self, *_args):
        return None


@pytest.fixture(scope="module")
def database():
    with hr_agent_database() as db:
        yield db


def test_loop_keeps_deployed_result_and_knowledge_reads_when_old_worker_is_off(
    tmp_path, monkeypatch, database
):
    from app import main
    from app.config import load_config

    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}')
    dsn_file = tmp_path / "control-dsn"
    dsn_file.write_text(database.dsn)
    dsn_file.chmod(0o600)
    keyring = tmp_path / "content-keyring.json"
    keyring.write_text(json.dumps({
        "purpose": "platform-content-encryption", "active_version": 1,
        "keys": {"1": base64.b64encode(b"r" * 32).decode()},
    }))
    keyring.chmod(0o600)
    authorization = SimpleNamespace(
        decide_for_user_id=lambda *_: SimpleNamespace(allowed=True),
        permitted_catalog_for_user_id=lambda *_: (),
    )
    settings = make_hr_settings(tmp_path / "loop")
    config = replace(
        load_config(),
        execution_relay_enabled=True,
        direct_agent_enabled=False,
        agent_brain_enabled=False,
        hr_web_worker_enabled=False,
        content_encryption_keyring_file=str(keyring),
        hr_agent_settings=settings,
        control_plane=replace(
            load_config().control_plane,
            control_database_url_file=str(dsn_file), audit_database_url_file="",
        ),
    )
    monkeypatch.setattr(main, "load_config", lambda: config)
    from app.hr.tool_service import HrToolError
    def missing_result(_service, _owner_id, _result_id):
        raise HrToolError("not_found", "missing")
    monkeypatch.setattr(main.HrToolService, "result", missing_result)
    included = []
    original_include = main.FastAPI.include_router
    def track_include(app, router, *args, **kwargs):
        included.append(router)
        return original_include(app, router, *args, **kwargs)
    monkeypatch.setattr(main.FastAPI, "include_router", track_include)
    app = main.create_app(
        registry_path=str(registry), cluster_contract_path=str(contract),
        start_poller=False, identity_auth=_SecurityAuth(uuid4()),
        agent_use_authorization=authorization,
        hr_knowledge_repository=_Knowledge(),
        hr_agent_service=HrAgentService(None, HrAccess(authorization), ready=False),
    )

    assert config.execution_relay_enabled is True
    assert app.state.execution_relay_repository is not None
    assembled_routes = [route for router in included for route in router.routes]
    methods_by_path = {
        route.path: set(route.methods or ()) for route in assembled_routes if hasattr(route, "methods")
    }
    assert "GET" in methods_by_path["/api/v1/hr/results/{result_id}"]
    assert "GET" in methods_by_path["/api/v1/hr/conversations/{conversation_id}/results"]
    assert "GET" in methods_by_path["/api/hr/knowledge"]
    assert "GET" in methods_by_path["/api/hr/knowledge/{source_commit}/{resource_id}"]
    assert "GET" in methods_by_path["/api/hr/agent/results"]
    assert "GET" in methods_by_path["/api/hr/agent/results/{result_id}/revisions/{revision}"]
    assert not any(
        path.startswith((
            "/api/v1/execution-worker/hr/v6",
            "/api/v1/execution-worker/hr/v7",
        ))
        for path in methods_by_path
    )

    legacy_result_route = next(
        route for route in assembled_routes
        if route.path == "/api/v1/hr/results/{result_id}"
    )
    dependency_names = {
        dependency.call.__name__ for dependency in legacy_result_route.dependant.dependencies
    }
    assert "require_hr_access" in dependency_names

    result_router = next(
        router for router in included
        if any(route.path == "/api/v1/hr/results/{result_id}" for route in router.routes)
    )
    assert result_router in included
    result_path = f"/api/v1/hr/results/{uuid4()}"
    client = TestClient(app)
    assert client.get(result_path).status_code == 401
    client.cookies.set("session", "valid")
    client.cookies.set("csrf", "csrf-token")
    response = client.get(result_path)
    assert response.status_code == 404, response.text


def test_loop_allows_complete_immutable_legacy_knowledge_configuration(
    tmp_path, monkeypatch
):
    from app import config as config_module

    settings = make_hr_settings(tmp_path / "loop")
    legacy_root = tmp_path / "legacy"
    legacy_agent = legacy_root / "bots" / "hr"
    legacy_agent.mkdir(parents=True)
    monkeypatch.setattr(
        config_module, "load_hr_agent_settings", lambda _environment: settings,
    )
    monkeypatch.setenv("PLATFORM_HR_WEB_WORKER_ENABLED", "0")
    monkeypatch.setenv("PLATFORM_HR_KNOWLEDGE_ROOT", str(legacy_root))
    monkeypatch.setenv("PLATFORM_HR_KNOWLEDGE_AGENT_ROOT", str(legacy_agent))
    monkeypatch.setenv("PLATFORM_HR_KNOWLEDGE_COMMIT", "a" * 40)

    loaded = config_module.load_config()

    assert loaded.hr_agent_settings.enabled is True
    assert loaded.hr_web_worker_enabled is False
    assert loaded.hr_knowledge_commit == "a" * 40
