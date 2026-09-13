"""Dynamic owner HTTP and real PG checks; test identity, no provider requests."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from app.control_plane.models import AuthContext, Role
from app.hr_agent.config import load_hr_agent_settings
from fastapi.testclient import TestClient
from test_dingtalk_auth_api import FakeAuth, _app
from test_hr_agent_preflight import _environment
from tests.hr_agent_support import hr_agent_database


@pytest.fixture
def configured(tmp_path):
    env = _environment(tmp_path / "runtime", attachments=False)
    env["PLATFORM_RELEASE_SHA"] = "a" * 40
    return load_hr_agent_settings(env), env


def monitor(settings, env, connect):
    assert importlib.util.find_spec("app.hr_agent.readiness") is not None, "dynamic HR readiness is missing"
    from app.hr_agent.readiness import HrReadiness
    return HrReadiness(settings, env, connect)


@pytest.mark.postgres
def test_dynamic_schema_phase_and_integrity_are_rechecked(configured):
    settings, env = configured
    with hr_agent_database(cutover_phase="legacy") as db:
        probe = monitor(settings, env, db.connection)
        before = probe.check()
        assert before["ready"] and before["phase"] == "legacy"
        assert before["new_admission_enabled"] is False
        assert before["configuration_sha256"] == settings.configuration_revision
        assert before["release_sha"] == "a" * 40
        assert len(before["knowledge_sha256"]) == 64
        with db.admin_connection() as c:
            for phase in ("draining_legacy", "cloud"):
                c.execute("select platform_control.transition_hr_execution_cutover_v102(%s,%s)", (phase, uuid4()))
        assert probe.check()["new_admission_enabled"] is True
        with db.admin_connection() as c:
            c.execute("delete from platform_control.schema_migrations where version=104")
        assert probe.check()["ready"] is False
        assert probe.check()["blockers"] == ["database_unavailable"]


@pytest.mark.postgres
@pytest.mark.parametrize("fault", ["profile", "knowledge", "permission", "uninitialized"])
def test_runtime_drift_does_not_become_a_new_loaded_configuration(configured, fault):
    settings, env = configured
    with hr_agent_database(cutover_phase="legacy") as db:
        probe = monitor(settings, env, db.connection)
        assert probe.check()["ready"]
        if fault == "profile":
            path = Path(env["PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE"])
            value = json.loads(path.read_text()); value["revision"] = "changed"
            path.write_text(json.dumps(value))
        elif fault == "knowledge":
            (settings.knowledge_dir / "method.md").write_text("corrupted material")
        else:
            with db.admin_connection() as c:
                c.execute("revoke select on platform_hr_agent.inputs from platform_control_app" if fault == "permission" else "delete from platform_control.hr_execution_cutover")
        result = probe.check()
        assert not result["ready"] and not result["new_admission_enabled"]
        assert result["configuration_sha256"] == settings.configuration_revision
        assert "changed" not in json.dumps(result) and "corrupted material" not in json.dumps(result)


def test_database_exception_is_safe_and_no_new_probe_can_certify_missing_assembly(configured):
    settings, env = configured
    def broken():
        raise psycopg.OperationalError("private DSN and SQL must not escape")
    probe = monitor(settings, env, broken)
    assert probe.check()["blockers"] == ["database_unavailable"]
    assert "private" not in json.dumps(probe.check())


@pytest.mark.postgres
def test_owner_audit_route_reports_live_failures_without_changing_liveness(tmp_path, monkeypatch, configured):
    settings, env = configured
    with hr_agent_database(cutover_phase="legacy") as db:
        probe = monitor(settings, env, db.connection)
        auth = FakeAuth(); app = _app(tmp_path / "app", monkeypatch, auth)
        app.state.hr_readiness = probe
        with TestClient(app) as client:
            client.cookies.set(auth.cookie_name, "valid-cookie")
            assert client.get("/api/v1/manage/hr-readiness").status_code == 503  # audit absent
            audited = []
            app.state.system_health_audit = lambda context: audited.append(context.session_id)
            response = client.get("/api/v1/manage/hr-readiness")
            assert response.status_code == 200 and response.json()["ready"]
            assert response.headers["cache-control"] == "no-store"
            with db.admin_connection() as c:
                c.execute("delete from platform_control.schema_migrations where version=104")
            assert client.get("/api/v1/manage/hr-readiness").status_code == 503
            assert client.get("/api/health").json() == {"status": "ok"}
            assert len(audited) == 2
            auth.context = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
            assert client.get("/api/v1/manage/hr-readiness").status_code == 403
            assert len(audited) == 2
