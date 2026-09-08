"""Actual app/factory composition over the owned authenticated PG fixture.

Only external login and object storage are substituted. No terminal state or
model response is manufactured; this test checks startup, intake and projection
ownership, while the process harness separately checks actual model transport.
"""

# ruff: noqa: PLC0414
import base64
import json
import os

import pytest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_hr_web_reliable_loop import (
    attempt_repository as attempt_repository,
)
from test_hr_web_reliable_loop import (
    control_database as control_database,
)
from test_hr_web_reliable_loop import (
    conversation_database as conversation_database,
)
from test_hr_web_reliable_loop import (
    direct_database as direct_database,
)
from test_hr_web_reliable_loop import (
    repository as repository,
)
from test_hr_web_reliable_loop import (
    web_loop as web_loop,
)
from test_hr_web_reliable_loop import (
    worker_conversation as worker_conversation,
)

from app import main as app_main
from app.agent_brain.direct_worker import DirectWorker
from app.attachments.citation_service import CitationRepository, CitationService
from app.attachments.result_artifact_recovery import ArtifactRecovery
from app.config import load_config
from app.control_plane.auth import AuthSecrets, DingTalkWebAuth, WebSessionRepository
from app.hr.position_package_projection import PositionPackageProjector
from tests.helpers.hr_recruiting_loop import configure_recruiting_api
from tests.helpers.hr_web_loop import _codec


@pytest.mark.parametrize("with_knowledge", [False, True])
def test_actual_app_owns_snapshot_context_and_projection_but_not_direct_execution(
    web_loop, monkeypatch, tmp_path, with_knowledge
):
    database_url = web_loop.environment["urls"]["platform_control_app"]
    database_file = tmp_path / "database-url"
    database_file.write_text(database_url)
    database_file.chmod(0o600)
    keyring = tmp_path / "content-keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "purpose": "platform-content-encryption",
                "active_version": 4,
                "keys": {
                    str(n): base64.b64encode(str(n).encode() * 32).decode()
                    for n in (3, 4)
                },
            }
        )
    )
    keyring.chmod(0o600)
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n")
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "bots": [
                    {
                        "name": "hr-bot",
                        "model": "claude-opus-4-8",
                        "instance": {"pm2Name": "metabot-hr", "apiPort": 9101},
                    }
                ]
            }
        )
    )
    config = load_config()
    config = replace(
        config,
        execution_relay_enabled=True,
        direct_agent_enabled=True,
        hr_web_worker_enabled=True,
        agent_brain_enabled=False,
        content_encryption_keyring_file=str(keyring),
        static_dir=str(Path(__file__).parents[2] / "webui/dist"),
        control_plane=replace(
            config.control_plane,
            control_database_url_file=str(database_file),
            audit_database_url_file="",
        ),
    )
    knowledge_commit = None
    if with_knowledge:
        from test_hr_reference_knowledge import _commit_knowledge, _git
        from app.hr.reference_knowledge_release import build_release
        source = tmp_path / "knowledge-source"
        source.mkdir()
        _git(source, "init", "-q")
        _git(source, "config", "user.name", "Test")
        _git(source, "config", "user.email", "test@example.com")
        knowledge_commit = _commit_knowledge(source)
        release_root = tmp_path / "knowledge-releases"
        build_release(source, knowledge_commit, release_root)
        config = replace(config, hr_knowledge_root=str(release_root), hr_knowledge_agent_root="/agent/releases", hr_knowledge_commit=knowledge_commit)
    monkeypatch.setattr(app_main, "load_config", lambda: config)
    monkeypatch.setenv("HR_WEB_FIXTURE_ROOT", str(tmp_path))
    monkeypatch.setenv("PLATFORM_HR_INTELLIGENCE_ROOT", str(tmp_path / "intelligence"))
    secrets = AuthSecrets(b"w" * 32, key_version=1)

    async def no_external_login(_code):
        raise RuntimeError("Owned fixture has no external login")

    auth = DingTalkWebAuth(
        repository=WebSessionRepository(database_url, secrets=secrets),
        secrets=secrets,
        qr_login=no_external_login,
        in_client_login=None,
        environment="production",
        route_prefix="/",
        public_base_url="https://localhost",
        app_key="owned-web-fixture",
    )
    attachments = FastAPI()
    configure_recruiting_api(attachments, database_url)
    services = {
        name: getattr(attachments.state, name)
        for name in (
            "conversation_attachment_upload_service",
            "conversation_attachment_download_service",
            "task_attachment_grant_service",
            "artifact_output_service",
        )
    }
    app = app_main.create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        identity_auth=auth,
        **services,
        citation_service=CitationService(
            CitationRepository(database_url, content_codec=_codec())
        ),
    )
    worker = app.state.direct_worker_factory()
    assert isinstance(worker, DirectWorker)
    assert isinstance(worker.artifact_recovery, ArtifactRecovery)
    assert worker.adapter.attachment_grants is services["task_attachment_grant_service"]
    assert isinstance(app.state.hr_position_package_projector, PositionPackageProjector)
    assert app.state.hr_task_context_provider is not None
    assert app.state.hr_panorama_context_provider is not None
    if with_knowledge:
        assert worker.adapter.context_builder._hr_knowledge_repository is app.state.hr_knowledge_repository
    try:
        with TestClient(
            app,
            base_url="https://localhost",
            cookies={
                "__Host-platform_session": web_loop.token,
                "__Host-platform_csrf": web_loop.csrf,
            },
            headers={"Origin": "https://localhost", "X-CSRF-Token": web_loop.csrf},
        ) as client:
            path = f"/api/v1/conversations/{web_loop.conversation_id}"
            submitted = client.post(
                path + "/messages",
                json={"text": "介绍一下你自己"},
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert submitted.status_code in (200, 201), submitted.text
            turn_id = submitted.json()["turn"]["turn_id"]
            if with_knowledge:
                from uuid import UUID
                assert client.get("/api/hr/knowledge").status_code == 200
                context = worker.adapter.context_builder.build_direct(UUID(web_loop.conversation_id) if isinstance(web_loop.conversation_id, str) else web_loop.conversation_id, UUID(turn_id))
                assert context.hr_reference_knowledge["source_commit"] == knowledge_commit

            snapshot = client.get(path + "/snapshot", params={"turn_id": turn_id})
            assert snapshot.status_code == 200, snapshot.text
            assert snapshot.json()["answer"] is None
            # No ready runtime: the independent worker must hold, not silently
            # fall back to the API's legacy orchestrator or start a model.
            worker.tick()
            assert (
                client.get(path + "/snapshot", params={"turn_id": turn_id}).json()[
                    "answer"
                ]
                is None
            )
            if control := os.environ.get("HR_WEB_BROWSER_CONTROL"):
                from tests.helpers.hr_browser_preview import preview_until_released

                preview_until_released(client, control, web_loop.csrf)
    finally:
        worker.close()
