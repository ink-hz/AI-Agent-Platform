from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.hr.context import HrPositionScope
from app.hr.models import CreateManualPosition, ProposePositionDraft
from app.hr.repository import HrPositionRepository, HrUnavailable
from test_agent_brain_api import _write_credentials
from test_agent_brain_conversation_api import _app
from test_agent_brain_conversation_repository import conversation_database, repository
from test_control_plane_migration import control_database  # noqa: F401


@pytest.fixture
def hr_conversation_database(conversation_database):
    environment, _, _ = conversation_database
    yield conversation_database
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute("delete from platform_hr.position_binding_events")
        admin.execute("delete from platform_hr.position_conversations")
        admin.execute("delete from platform_hr.position_drafts")


def _post(client, auth, agent_id, request_id, payload):
    credentials = _write_credentials(auth)
    return client.post(
        f"/api/v1/agents/{agent_id}/conversations",
        json=payload,
        headers={
            **credentials["headers"],
            "Idempotency-Key": str(request_id),
        },
        cookies=credentials["cookies"],
    )


@pytest.mark.postgres
def test_hr_conversation_is_returned_only_after_position_binding_exists(
    hr_conversation_database, repository
) -> None:
    environment, owner_id, _ = hr_conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "高级结构工程师")
    )
    scope = HrPositionScope(positions)
    app, auth, _ = _app(owner_id, repository, hr_position_scope=scope)
    client = TestClient(app)
    request_id = uuid4()

    first = _post(client, auth, "hr-bot", request_id, {
        "text": "建立岗位工作区",
        "position_id": str(position.position_id),
    })
    replay = _post(client, auth, "hr-bot", request_id, {
        "text": "建立岗位工作区",
        "position_id": str(position.position_id),
    })

    assert first.status_code == 201
    assert replay.status_code == 200
    conversation_id = UUID(first.json()["conversation"]["conversation_id"])
    assert scope.for_conversation(owner_id, conversation_id) == position.position_id
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_hr.position_conversations "
            "where conversation_id=%s", (conversation_id,),
        ).fetchone() == (1,)


def test_non_hr_conversation_rejects_position_scope_fields() -> None:
    owner_id = uuid4()
    app, auth, _ = _app(owner_id, object(), hr_position_scope=object())
    response = _post(TestClient(app), auth, "ai-fae-agent", uuid4(), {
        "text": "不应接受岗位字段", "position_id": str(uuid4())
    })

    assert response.status_code == 422
    assert response.json() == {"detail": "conversation request invalid"}


@pytest.mark.postgres
def test_hr_conversation_can_resume_an_existing_position_draft(
    hr_conversation_database, repository
) -> None:
    environment, owner_id, _ = hr_conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    draft = positions.propose_draft(ProposePositionDraft(
        owner_id, uuid4(), uuid4(), "new_conversation", "request:new-role",
        None, "机器人结构工程师", {}, {"message_seq": 1}, "interactive-v1",
    ))
    app, auth, _ = _app(
        owner_id, repository, hr_position_scope=HrPositionScope(positions)
    )

    response = _post(TestClient(app), auth, "hr-bot", uuid4(), {
        "text": "继续完善岗位定义",
        "position_draft_id": str(draft.draft_id),
    })

    assert response.status_code == 201
    conversation_id = UUID(response.json()["conversation"]["conversation_id"])
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select source_conversation_id from platform_hr.position_drafts "
            "where draft_id=%s", (draft.draft_id,),
        ).fetchone() == (conversation_id,)


@pytest.mark.postgres
def test_transient_binding_failure_retries_same_conversation_without_duplication(
    hr_conversation_database, repository
) -> None:
    environment, owner_id, _ = hr_conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "光学设计工程师")
    )
    delegate = HrPositionScope(positions)

    class FlakyScope:
        calls = 0

        def bind_conversation(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise HrUnavailable("temporary")
            return delegate.bind_conversation(*args, **kwargs)

    scope = FlakyScope()
    app, auth, _ = _app(owner_id, repository, hr_position_scope=scope)
    client = TestClient(app)
    request_id = uuid4()
    payload = {"text": "岗位分析", "position_id": str(position.position_id)}

    assert _post(client, auth, "hr-bot", request_id, payload).status_code == 503
    recovered = _post(client, auth, "hr-bot", request_id, payload)

    assert recovered.status_code == 200
    conversation_id = UUID(recovered.json()["conversation"]["conversation_id"])
    assert delegate.for_conversation(owner_id, conversation_id) == position.position_id
