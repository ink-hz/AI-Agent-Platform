"""API canary engineering: real local identity/PG; no production or real model."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

SCRIPT = (
    Path(__file__).parents[2]
    / "artifacts/2026-09-13-hr-launch/production/api_canary.py"
)


def canary():
    assert SCRIPT.exists(), "bounded API canary is missing"
    spec = importlib.util.spec_from_file_location("api_canary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def credentials(tmp_path, **changes):
    value = {
        "owner_id": str(uuid4()),
        "session_cookie": "private-session",
        "csrf": "private-csrf",
        "public_origin": "https://localhost",
        "api_base_url": "https://localhost",
    }
    value.update(changes)
    path = tmp_path / "private.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def test_private_config_requires_absolute_regular_0600_and_same_https_origin(tmp_path):
    h = canary()
    path = credentials(tmp_path)
    assert h.load_config(path)["api_base_url"] == "https://localhost"
    path.chmod(0o644)
    with pytest.raises(h.CanaryError):
        h.load_config(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(h.CanaryError):
        h.load_config(link)
    for change in (
        {"api_base_url": "https://other.invalid"},
        {"csrf": "x\r\nX: leak"},
        {"public_origin": "http://localhost"},
        {"session_cookie": ""},
    ):
        with pytest.raises(h.CanaryError):
            h.load_config(credentials(tmp_path, **change))
    with pytest.raises(h.CanaryError):
        h.load_config(Path("relative.json"))


def test_unknown_upload_acceptance_is_journaled_and_never_retried(tmp_path):
    import httpx

    h = canary()
    calls = []

    def lose(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-session must never escape")

    client = httpx.Client(transport=httpx.MockTransport(lose))
    runner = h.Canary(h.load_config(credentials(tmp_path)), tmp_path / "run", client)
    with pytest.raises(h.CanaryError, match="outcome_unknown"):
        runner.mutate(
            "upload_begin",
            "/api/v1/attachments/uploads",
            {"original_name": "synthetic.txt"},
            (201,),
        )
    key = runner.ledger["operations"]["upload_begin"]["key"]
    assert str(__import__("uuid").UUID(key)) == key
    with pytest.raises(h.CanaryError, match="outcome_unknown"):
        runner.mutate(
            "upload_begin",
            "/api/v1/attachments/uploads",
            {"original_name": "synthetic.txt"},
            (201,),
        )
    assert len(calls) == 1
    evidence = (tmp_path / "run" / "ledger.json").read_text()
    assert "private-session" not in evidence and "private-csrf" not in evidence
    assert (
        json.loads(evidence)["operations"]["upload_begin"]["status"]
        == "outcome_unknown"
    )


@pytest.fixture
def real_api(tmp_path, request):
    """Real HTTP/auth/PG/runtime, local login exchange/store and ScriptModel only."""
    import asyncio

    from app.attachments.derivatives import DerivativeBuilder
    from app.attachments.scanner import TrustedInternalScanner
    from app.attachments.validation import AttachmentValidator
    from app.attachments.worker import AttachmentProcessor
    from app.attachments.worker_runtime import AttachmentProcessingRepository
    from app.control_plane.auth import SystemHealthAuditWriter
    from app.control_plane.authorization import (
        AuthorizationRepository,
        AuthorizationService,
    )
    from app.control_plane.middleware import IdentitySecurityMiddleware
    from app.control_plane.routes_auth import build_auth_router
    from app.hr_agent.config import load_hr_agent_settings
    from app.hr_agent.readiness import HrReadiness
    from app.hr_agent.runtime import run_work
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests.helpers.hr_history_replay import environment
    from tests.test_hr_agent_runtime import ScriptModel, answer, tool

    with environment(tmp_path / "runtime") as env:
        repo = env["repo"]
        root = tmp_path / "runtime/config"
        budget = {
            **repo.settings.budget_profile,
            "limits": {
                "model_calls": 32,
                "total_tokens": 600000,
                "active_seconds": 900,
            },
        }
        (root / "budget_profile_file.json").write_text(json.dumps(budget))
        (root / "provider_profile_file.json").write_text(
            json.dumps(repo.settings.provider_profile)
        )
        loaded_env = {
            "PLATFORM_HR_AGENT_ENABLED": "1",
            "PLATFORM_EXECUTION_RELAY_ENABLED": "0",
            "PLATFORM_HR_AGENT_WORK_DIR": str(repo.settings.work_dir),
            "PLATFORM_HR_AGENT_KNOWLEDGE_DIR": str(repo.settings.knowledge_dir),
            "PLATFORM_RELEASE_SHA": "a" * 40,
        }
        for key in (
            "CONTENT_KEYRING_FILE",
            "PROVIDER_PROFILE_FILE",
            "BUDGET_PROFILE_FILE",
            "DIAGNOSTIC_PROFILE_FILE",
        ):
            loaded_env["PLATFORM_HR_AGENT_" + key] = str(root / (key.lower() + ".json"))
        repo.settings = load_hr_agent_settings(loaded_env)
        auth = env["client"].app.user_middleware[0].kwargs["auth"]
        app = FastAPI()
        app.include_router(env["client"].app.router)
        app.include_router(
            build_auth_router(
                auth,
                static_dir=str(tmp_path),
                public_assets=frozenset(),
                detailed_health=dict,
            )
        )
        app.state.conversation_attachment_upload_service = env[
            "client"
        ].app.state.conversation_attachment_upload_service
        app.state.hr_readiness = HrReadiness(
            repo.settings, loaded_env, env["database"].connection
        )
        app.state.system_health_audit = SystemHealthAuditWriter(
            env["database"].dsn.replace(
                "user=platform_control_app", "user=platform_audit_append"
            )
        )
        app.add_middleware(
            IdentitySecurityMiddleware,
            auth=auth,
            public_assets=frozenset(),
            authorization=AuthorizationService(
                AuthorizationRepository(env["database"].dsn)
            ),
            routes=tuple(app.router.routes),
        )
        processor = AttachmentProcessor(
            repository=AttachmentProcessingRepository(
                env["database"].dsn.replace(
                    "user=platform_control_app", "user=platform_brain_worker"
                ),
                content_codec=repo.codec,
            ),
            object_store=env["store"],
            validator=AttachmentValidator(),
            scanner=TrustedInternalScanner(),
            derivatives=DerivativeBuilder(),
            worker_id="canary-engineering-attachment",
        )
        with TestClient(app, base_url="https://localhost") as client:

            class ScheduledClient:
                defer = False
                deferred = None
                lose_upload_once = False
                lose_work_once = False

                def finish_deferred(self):
                    model, fence = self.deferred
                    run_work(repo, model, env["resources"], fence)
                    self.deferred = None

                def request(self, method, url, **kwargs):
                    response = client.request(method, url, **kwargs)
                    if method == "POST" and response.status_code in (200, 201, 202):
                        if url.endswith("/complete"):
                            for _ in range(8):
                                if not asyncio.run(processor.process_next()):
                                    break
                        if url.endswith(("/works", "/inputs")):
                            work = response.json()
                            fence = repo.claim("canary-engineering", 60)
                            if fence:
                                assert str(fence.work_id) == work["work_id"]
                                refs = kwargs["json"]["references"]
                                old = next(
                                    (ref for ref in refs if ref["kind"] == "result"),
                                    None,
                                )
                                args = {
                                    "kind": "research",
                                    "title": "合成 API 验收成果",
                                    "body": "纯合成工程成果，不表示模型质量。",
                                    "objects": [],
                                    "source_refs": refs,
                                    "preceding_refs": [old] if old else [],
                                    "result_id": old["id"] if old else None,
                                    "expected_revision": old["revision"]
                                    if old
                                    else None,
                                    "base_standard_ref": None,
                                    "changes": [],
                                    "basis": [
                                        {
                                            "kind": "user_temporary",
                                            "input_revision": work["input_revision"],
                                            "ref": None,
                                        }
                                    ],
                                }
                                model = ScriptModel(
                                    [
                                        *[
                                            tool("read_resource", {"ref": ref})
                                            for ref in refs
                                        ],
                                        tool("save_result", args),
                                        answer("合成成果已保存。"),
                                    ]
                                )
                                if self.defer:
                                    self.deferred = (model, fence)
                                else:
                                    run_work(repo, model, env["resources"], fence)
                    if (
                        self.lose_upload_once and url.endswith("/attachments/uploads")
                    ) or (self.lose_work_once and url.endswith("/works")):
                        self.lose_upload_once = self.lose_work_once = False
                        import httpx

                        raise httpx.ReadTimeout("injected after real server acceptance")
                    return response

            env.update(
                client=client,
                scheduled=ScheduledClient(),
                auth=auth,
                loaded_env=loaded_env,
                config_path=credentials(
                    tmp_path,
                    owner_id=str(env["owner"]),
                    session_cookie=env["client"].cookies.get(auth.cookie_name),
                    csrf=env["headers"]["X-CSRF-Token"],
                ),
            )
            yield env
            import os
            import shutil

            evidence_root = os.environ.get("HR_CANARY_ENGINEERING_EVIDENCE_DIR")
            if evidence_root:
                evidence = Path(evidence_root) / request.node.name
                evidence.mkdir(parents=True, exist_ok=False)
                for ledger in tmp_path.rglob("ledger.json"):
                    target = evidence / (ledger.parent.name + ".json")
                    shutil.copyfile(ledger, target)
                    target.chmod(0o600)


@pytest.mark.postgres
def test_actual_http_upload_saved_revision_same_work_replay_and_safe_evidence(
    real_api, tmp_path
):
    h = canary()
    config = h.load_config(real_api["config_path"])
    runner = h.Canary(config, tmp_path / "canary-run", real_api["scheduled"])
    result = runner.run()
    assert result["status"] == "completed"
    assert len(result["turns"]) == 2
    first, second = result["turns"]
    assert first["work"]["work_id"] == second["work"]["work_id"]
    assert second["work"]["input_revision"] == 2
    assert first["results"][0]["ref"]["id"] == second["results"][0]["ref"]["id"]
    assert (
        first["results"][0]["ref"]["revision"]
        != second["results"][0]["ref"]["revision"]
    )
    assert result["material"]["original_ref"]["revision"] == h.sha(runner.synthetic())
    assert result["budget_evidence"]["source"] == "authenticated_configuration_api"
    assert result["budget_evidence"]["limits"]["total_tokens"] == 600000
    work_submits = [
        row["status"]
        for row in result["http"]
        if row["method"] == "POST" and row["path"] == "/api/hr/agent/works"
    ]
    assert work_submits == [201, 200]
    assert result["restart_observation"] == "not_requested"
    text = (runner.directory / "ledger.json").read_text()
    assert config["session_cookie"] not in text and config["csrf"] not in text
    assert '"csrf_token"' not in text and '"mobile"' not in text
    with real_api["database"].admin_connection() as connection:
        assert (
            connection.execute(
                "select count(*) from platform_control.audit_events where event_type='system_health_read_completed'"
            ).fetchone()[0]
            >= 1
        )
        assert (
            connection.execute(
                "select count(*) from platform_hr_agent.works"
            ).fetchone()[0]
            == 1
        )
    before = len(runner.ledger["http"])
    resumed = h.Canary(config, runner.directory, real_api["scheduled"], resume=True)
    resumed.observe("after_external_restart")
    assert all(row["method"] == "GET" for row in resumed.ledger["http"][before:])
    assert resumed.ledger["restart_observation"] == "not_requested"


@pytest.mark.postgres
def test_owner_csrf_origin_and_live_readiness_fail_before_any_canary_write(
    real_api, tmp_path
):
    h = canary()
    config = h.load_config(real_api["config_path"])
    for index, altered in enumerate(
        ({**config, "session_cookie": "invalid"}, {**config, "owner_id": str(uuid4())})
    ):
        runner = h.Canary(altered, tmp_path / f"deny-{index}", real_api["client"])
        with pytest.raises(h.CanaryError):
            runner.run()
        assert not runner.ledger["operations"]
    runner = h.Canary(config, tmp_path / "drift", real_api["client"])
    Path(real_api["loaded_env"]["PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE"]).write_text(
        "{}"
    )
    with pytest.raises(h.CanaryError):
        runner.run()
    assert not runner.ledger["operations"]
    # Direct negative HTTP proves server boundaries, not a client-side fake.
    for csrf, origin in (
        ("wrong", config["public_origin"]),
        (config["csrf"], "https://wrong.invalid"),
    ):
        response = real_api["client"].post(
            "/api/v1/attachments/uploads",
            json={},
            headers={
                "Cookie": "__Host-platform_session=" + config["session_cookie"],
                "X-CSRF-Token": csrf,
                "Origin": origin,
            },
        )
        assert response.status_code == 403


@pytest.mark.postgres
@pytest.mark.parametrize("lost", ["upload", "work"])
def test_lost_real_http_acceptance_resume_never_duplicates_owned_resources(
    real_api, tmp_path, lost
):
    h = canary()
    config = h.load_config(real_api["config_path"])
    client = real_api["scheduled"]
    setattr(client, f"lose_{lost}_once", True)
    runner = h.Canary(config, tmp_path / "lost-run", client)
    with pytest.raises(h.CanaryError, match="outcome_unknown"):
        runner.run()
    resumed = h.Canary(config, runner.directory, client, resume=True)
    if lost == "upload":
        assert not runner.ledger.get("attachment_id")
        with pytest.raises(h.CanaryError, match="outcome_unknown"):
            resumed.run()
    else:
        assert resumed.run()["status"] == "completed"
    with real_api["database"].admin_connection() as connection:
        assert (
            connection.execute(
                "select count(*) from platform_attachments.attachments"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute(
            "select count(*) from platform_hr_agent.works"
        ).fetchone()[0] == (1 if lost == "work" else 0)


@pytest.mark.postgres
def test_running_pause_and_resume_are_observations_not_invented_restart_proof(
    real_api, tmp_path
):
    h = canary()
    config = h.load_config(real_api["config_path"])
    client = real_api["scheduled"]
    client.defer = True
    runner = h.Canary(config, tmp_path / "paused", client)
    report = runner.run(pause_at_running=True)
    assert report["status"] == "paused_for_external_restart"
    assert report["observations"][-1]["work"]["state"] == "running"
    client.finish_deferred()  # Real claimed runtime; this is NOT a process-restart test.
    client.defer = False
    resumed = h.Canary(config, runner.directory, client, resume=True)
    report = resumed.run()
    assert report["status"] == "completed"
    assert report["restart_observation"] == "running_window_observed"
    assert "restart_passed" not in json.dumps(report)
    new = h.Canary(config, tmp_path / "missed", client)
    assert (
        new.run(pause_at_running=True)["restart_observation"] == "restart_window_missed"
    )


def test_missing_budget_limits_and_deadline_fail_closed(tmp_path):
    import httpx

    h = canary()
    config = h.load_config(credentials(tmp_path))
    values = {
        "/api/v1/account": {
            "internal_user_id": config["owner_id"],
            "role": "platform_owner",
            "hard_stale_read_only": False,
        },
        "/api/v1/manage/hr-readiness": {
            "ready": True,
            "phase": "cloud",
            "new_admission_enabled": True,
            "release_sha": "a" * 40,
            "configuration_sha256": "b" * 64,
            "runtime_identity_sha256": "c" * 64,
            "knowledge_sha256": "d" * 64,
            "knowledge_release": "local-contract",
        },
        "/api/hr/agent/configuration": {"budget_profile": "only-an-id"},
    }
    # Metadata edge injection only; real endpoint/auth/readiness integration above.
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=values[r.url.path])
        )
    )
    runner = h.Canary(config, tmp_path / "limits", client)
    with pytest.raises(h.CanaryError, match="budget_limits_unproven"):
        runner.run()
    assert not runner.ledger["operations"]
    runner.ledger["deadline"] = 0
    with pytest.raises(h.CanaryError, match="deadline_exhausted"):
        runner.run()
    assert not runner.ledger["operations"]
