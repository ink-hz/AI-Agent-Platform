from __future__ import annotations

# Imported fixture names intentionally become pytest fixtures in this module.
# ruff: noqa: F401,F811
from datetime import datetime, timezone
from uuid import UUID, uuid4

import psycopg
import pytest
from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_models import ConversationTurnSubmission
from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.conversation_repository import ConversationRepository
from app.agent_brain.conversation_service import ConversationCommandService
from app.agent_brain.models import load_capability_cards
from app.agent_brain.orchestrator import MissionOrchestrator
from app.attachments.result_projection import ConversationResultProjection
from fastapi.testclient import TestClient
from test_agent_brain_api import _credentials, _write_credentials
from test_agent_brain_conversation_api import _app
from test_agent_brain_conversation_repository import (
    conversation_database,
    repository,
)
from test_agent_brain_orchestrator import ScriptedRelay
from test_control_plane_migration import control_database
from test_conversation_attachment_binding import _ready_attachment


def _recovery_result(status: str, *, resumable: bool) -> dict[str, object]:
    return {
        "contractVersion": "core_chat_collaboration_v4",
        "publicAnswerMarkdown": (
            "搜索通道暂时不可用，已保存当前分析。"
            if status == "unavailable"
            else "已完成检索，当前范围内没有匹配结果。"
        ),
        "citations": [],
        "artifacts": [],
        "completion": "partially_completed",
        "recovery": {
            "status": status,
            "attemptCount": 2,
            "lastAttemptAt": datetime.now(timezone.utc).isoformat(),
            "resumable": resumable,
            "coverageNote": "已检索公开人才渠道。",
        },
    }


def _completed_direct_turn(
    repository: ConversationRepository,
    owner: UUID,
    result: dict[str, object],
    submission: str | ConversationTurnSubmission = "搜索机器人视觉候选人",
):
    commands = ConversationCommandService(repository, v2_enabled=True)
    started = commands.start(
        owner,
        uuid4(),
        submission,
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    relay = ScriptedRelay()
    projection = ConversationProjection(
        repository,
        result_projection=ConversationResultProjection(
            content_codec=repository.content_codec
        ),
    )
    orchestrator = MissionOrchestrator(
        repository._missions,
        relay,
        capability_provider=lambda _owner: load_capability_cards(),
        conversation_context_builder=ConversationContextBuilder(repository),
        conversation_projection=projection,
    )
    assert orchestrator.advance_pending(limit=50) == 1
    run_id = next(iter(relay.payloads))
    relay.terminal_result(run_id, result, event_type="agent.result")
    assert orchestrator.advance_pending(limit=50) == 1
    return started, commands


@pytest.mark.postgres
def test_search_unavailable_is_projected_and_resumes_as_a_linked_new_turn(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    started, commands = _completed_direct_turn(
        repository,
        owner,
        _recovery_result("unavailable", resumable=True),
    )
    original_turn_id = started.turn.turn_id
    original_mission_id = started.mission.mission_id
    client_request_id = uuid4()
    app, auth, _agent_use = _app(
        owner,
        repository,
        command_service=commands,
    )
    client = TestClient(app)

    messages = client.get(
        f"/api/v1/conversations/{started.conversation.conversation_id}/messages",
        cookies=_credentials(auth)["cookies"],
    )
    assert messages.status_code == 200
    assert messages.json()["items"][-1]["search_recovery"] == {
        "status": "unavailable",
        "attempt_count": 2,
        "last_attempt_at": messages.json()["items"][-1]["search_recovery"][
            "last_attempt_at"
        ],
        "resumable": True,
        "coverage_note": "已检索公开人才渠道。",
    }

    def resume():
        return client.post(
            "/api/v1/conversations/"
            f"{started.conversation.conversation_id}/turns/"
            f"{original_turn_id}/resume",
            headers={
                **_write_credentials(auth)["headers"],
                "Idempotency-Key": str(client_request_id),
            },
            cookies=_credentials(auth)["cookies"],
        )

    first = resume()
    replay = resume()
    assert first.status_code == 201, first.text
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["turn"]["retry_of_turn_id"] == str(original_turn_id)
    assert first.json()["message"]["content"] == "搜索机器人视觉候选人"

    new_turn_id = UUID(first.json()["turn"]["turn_id"])
    with psycopg.connect(environment["admin"]) as connection:
        rows = connection.execute(
            "select turn_id,mission_id,retry_of_turn_id from "
            "platform_control.conversation_turns where turn_id in (%s,%s) "
            "order by created_at",
            (original_turn_id, new_turn_id),
        ).fetchall()
    assert rows[0] == (original_turn_id, original_mission_id, None)
    assert rows[1][0] == new_turn_id
    assert rows[1][1] != original_mission_id
    assert rows[1][2] == original_turn_id


@pytest.mark.postgres
def test_search_resume_reuses_original_inputs_without_copying_registered_outputs(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    attachment_id = _ready_attachment(
        environment, repository._attachments, owner, None
    )
    cards = tuple(
        card.model_copy(
            update={
                "supports_attachments_in": True,
                "supports_attachments_out": True,
                "supports_attachments": True,
            }
        )
        if card.agent_id == "hr-bot"
        else card
        for card in load_capability_cards()
    )
    repository = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=repository.content_codec,
        mission_repository=repository._missions,
        attachment_repository=repository._attachments,
        agent_capability_cards=cards,
    )
    started, commands = _completed_direct_turn(
        repository,
        owner,
        _recovery_result("partial", resumable=True),
        ConversationTurnSubmission(
            "搜索机器人视觉候选人",
            (attachment_id,),
            (attachment_id,),
        ),
    )
    client_request_id = uuid4()

    with psycopg.connect(environment["admin"]) as connection:
        before = connection.execute(
            "select count(*) from platform_attachments.artifacts "
            "where conversation_id=%s",
            (started.conversation.conversation_id,),
        ).fetchone()[0]

    resumed = commands.resume_search(
        owner,
        started.conversation.conversation_id,
        started.turn.turn_id,
        client_request_id,
    )

    assert tuple(
        item.attachment_id for item in resumed.message.input_attachments
    ) == (attachment_id,)
    assert resumed.message.active_attachment_ids == (attachment_id,)
    with psycopg.connect(environment["admin"]) as connection:
        bindings = connection.execute(
            "select kind,attachment_id from platform_attachments.bindings "
            "where turn_id=%s order by kind,attachment_id",
            (resumed.turn.turn_id,),
        ).fetchall()
        after = connection.execute(
            "select count(*) from platform_attachments.artifacts "
            "where conversation_id=%s",
            (started.conversation.conversation_id,),
        ).fetchone()[0]
    assert bindings == [("turn_input", attachment_id)]
    assert after == before


@pytest.mark.postgres
def test_no_results_remains_distinct_and_cannot_be_resumed(
    conversation_database,
    repository,
) -> None:
    _environment, owner, _other = conversation_database
    started, commands = _completed_direct_turn(
        repository,
        owner,
        _recovery_result("no_results", resumable=False),
    )
    app, auth, _agent_use = _app(
        owner,
        repository,
        command_service=commands,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/conversations/"
        f"{started.conversation.conversation_id}/turns/{started.turn.turn_id}/resume",
        headers={
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(uuid4()),
        },
        cookies=_credentials(auth)["cookies"],
    )

    assert response.status_code == 409
    messages = repository.messages_after(owner, started.conversation.conversation_id)
    assert messages[-1].search_recovery is not None
    assert messages[-1].search_recovery.status == "no_results"
    assert messages[-1].search_recovery.resumable is False
