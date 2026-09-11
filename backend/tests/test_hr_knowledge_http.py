"""Authenticated HTTP with real PG identity/message persistence; no model call."""

from uuid import uuid4
from pathlib import Path
import psycopg

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_hr_direct_worker_progress import (
    attempt_repository as attempt_repository,
    control_database as control_database,
    conversation_database as conversation_database,
    direct_database as direct_database,
    repository as repository,
    worker_conversation as worker_conversation,
)
from test_hr_reference_knowledge import (
    source_repo as source_repo,
    _commit_knowledge,
    _git,
    _resource,
)
from tests.helpers.hr_web_loop import WebLoop

from app.agent_brain.authorization import AgentUseAuthorization
from app.agent_brain.conversation_routes import (
    ConversationCursorCodec,
    build_conversation_router,
)
from app.agent_brain.conversation_service import ConversationCommandService
from app.control_plane.auth import AuthSecrets, DingTalkWebAuth, WebSessionRepository
from app.control_plane.authorization import (
    AuthorizationRepository,
    AuthorizationService,
)
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.hr.reference_knowledge import HrKnowledgeRepository
from app.hr.reference_knowledge_release import build_release


@pytest.mark.postgres
def test_authorized_browse_and_selected_resource_turn_survive_refresh(
    source_repo, tmp_path, direct_database, repository, worker_conversation
):
    from app.hr.reference_knowledge_routes import build_hr_knowledge_router

    commit = _commit_knowledge(source_repo)
    releases = tmp_path / "releases"
    build_release(source_repo, commit, releases)
    knowledge = HrKnowledgeRepository(releases, "/agent/releases", commit)
    environment, owner, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set local role platform_control_owner")
        connection.execute(
            (
                Path(__file__).parents[1]
                / "control_migrations/pending/hr_web_result_recovery.sql"
            ).read_text()
        )
    identity = WebLoop(environment, owner, worker_conversation.conversation_id)
    database = environment["urls"]["platform_control_app"]
    secrets = AuthSecrets(b"w" * 32, key_version=1)

    async def no_login(_code):
        raise RuntimeError("External login disabled in fixture")

    auth = DingTalkWebAuth(
        repository=WebSessionRepository(database, secrets=secrets),
        secrets=secrets,
        qr_login=no_login,
        in_client_login=None,
        environment="production",
        route_prefix="/",
        public_base_url="https://localhost",
        app_key="owned-fixture",
    )
    repository.worker_direct_enabled = True
    authorization = AgentUseAuthorization(database)
    app = FastAPI()
    app.include_router(build_hr_knowledge_router(knowledge, authorization))
    app.include_router(
        build_conversation_router(
            repository,
            authorization,
            command_service=ConversationCommandService(
                repository, v2_enabled=False, hr_knowledge_repository=knowledge
            ),
            cursor_codec=ConversationCursorCodec(secrets),
            session_revalidator=auth.authenticate,
            session_cookie_name=auth.cookie_name,
        )
    )
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        authorization=AuthorizationService(AuthorizationRepository(database)),
        routes=tuple(app.router.routes),
    )
    try:
        with TestClient(app, base_url="https://localhost") as anonymous:
            assert anonymous.get("/api/hr/knowledge").status_code == 401
        with TestClient(
            app,
            base_url="https://localhost",
            cookies={
                "__Host-platform_session": identity.token,
                "__Host-platform_csrf": identity.csrf,
            },
            headers={"Origin": "https://localhost", "X-CSRF-Token": identity.csrf},
        ) as client:
            response = client.get("/api/hr/knowledge")
            assert response.status_code == 200, response.text
            resource = response.json()["resources"][0]
            detail = client.get(f"/api/hr/knowledge/{commit}/{resource['id']}")
            assert detail.status_code == 200
            assert detail.json()["markdown"]
            assert "/agent/releases" not in detail.text
            selected = {k: resource[k] for k in ["id", "revision", "sha256"]}
            selected["source_commit"] = commit
            path = (
                f"/api/v1/conversations/{worker_conversation.conversation_id}/messages"
            )
            body = {"text": "请结合此方法分析", "user_selected_resources": [selected]}
            key = str(uuid4())
            denied = client.post(
                path,
                json=body,
                headers={"Idempotency-Key": key, "X-CSRF-Token": "wrong"},
            )
            assert denied.status_code == 403
            submitted = client.post(path, json=body, headers={"Idempotency-Key": key})
            assert submitted.status_code == 201, submitted.text
            replay = client.post(path, json=body, headers={"Idempotency-Key": key})
            assert replay.status_code == 200, replay.text
            restored = client.get(path)
            assert restored.status_code == 200, restored.text
            assert restored.json()["items"][-1]["user_selected_resources"] == [selected]
            # A newly active release must not replace the explicitly selected old one.
            changed_file = (
                source_repo / "bots/hr/knowledge/recruiting" / f"{selected['id']}.md"
            )
            changed_file.write_text(
                _resource(selected["id"], revision=2, marker="new revision")
            )
            _git(source_repo, "add", ".")
            _git(source_repo, "commit", "-m", "new active revision")
            new_commit = _git(source_repo, "rev-parse", "HEAD")
            build_release(source_repo, new_commit, releases)
            from app.agent_brain.conversation_context import ConversationContextBuilder
            from uuid import UUID

            context = ConversationContextBuilder(
                repository,
                hr_knowledge_repository=HrKnowledgeRepository(
                    releases, "/agent/releases", new_commit
                ),
            ).build_direct(
                worker_conversation.conversation_id,
                UUID(submitted.json()["turn"]["turn_id"]),
            )
            assert context.hr_reference_knowledge["source_commit"] == commit
            assert context.hr_reference_knowledge["user_selected_resources"] == [
                selected
            ]
            changed = {**selected, "revision": 2}
            mismatch = client.post(
                path,
                json={**body, "user_selected_resources": [changed]},
                headers={"Idempotency-Key": key},
            )
            assert mismatch.status_code in (409, 422)
            created = client.post(
                "/api/v1/agents/hr-bot/conversations",
                json=body,
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert created.status_code == 201, created.text
            assert created.json()["message"]["user_selected_resources"] == [selected]
            rejected = client.post(
                "/api/v1/conversations",
                json=body,
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert rejected.status_code == 422, rejected.text
            with psycopg.connect(environment["admin"]) as connection:
                connection.execute(
                    "delete from platform_control.agent_use_grants where agent_id='hr-bot' and target_internal_user_id=%s",
                    (owner,),
                )
            assert client.get("/api/hr/knowledge").status_code == 403
    finally:
        identity.close()
