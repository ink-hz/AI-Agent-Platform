from __future__ import annotations

# Imported fixtures intentionally become fixtures in this module.
# ruff: noqa: F401,F811
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_agent_brain_conversation_api import _app, _write_credentials
from test_agent_brain_conversation_repository import conversation_database, repository
from test_control_plane_migration import control_database


@pytest.mark.postgres
def test_terminal_activity_is_unread_until_owner_marks_last_seen_event(
    conversation_database,
    repository,
) -> None:
    environment, owner, other = conversation_database
    started = repository.start(
        owner, uuid4(), "搜索候选人", mode="direct_agent", direct_agent_id="hr-bot"
    )
    with psycopg.connect(environment["admin"]) as connection:
        event_seq = connection.execute(
            "select coalesce(max(seq),0)+1 from platform_control.conversation_events "
            "where conversation_id=%s",
            (started.conversation.conversation_id,),
        ).fetchone()[0]
        connection.execute(
            "insert into platform_control.conversation_events("
            "event_id,conversation_id,seq,turn_id,mission_id,event_type,"
            "payload_ciphertext,encryption_key_version) values "
            "(%s,%s,%s,%s,%s,'brain.answer_submitted',%s,1)",
            (
                uuid4(),
                started.conversation.conversation_id,
                event_seq,
                started.turn.turn_id,
                started.mission.mission_id,
                b"e" * 29,
            ),
        )

    listed = repository.list_for_owner(owner, direct_agent_id="hr-bot")
    assert listed[0].unread is True
    assert listed[0].activity_status == "accepted"

    app, auth, _ = _app(owner, repository)
    response = TestClient(app).post(
        f"/api/v1/conversations/{started.conversation.conversation_id}/read-state",
        **_write_credentials(auth),
        json={"last_seen_event_seq": event_seq},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "conversation_id": str(started.conversation.conversation_id),
        "last_read_message_seq": event_seq,
        "last_read_at": response.json()["last_read_at"],
    }
    assert repository.list_for_owner(owner, direct_agent_id="hr-bot")[0].unread is False

    foreign_app, foreign_auth, _ = _app(other, repository)
    denied = TestClient(foreign_app).post(
        f"/api/v1/conversations/{started.conversation.conversation_id}/read-state",
        **_write_credentials(foreign_auth),
        json={"last_seen_event_seq": event_seq},
    )
    assert denied.status_code == 404

    replay = repository.mark_read(owner, started.conversation.conversation_id, 1)
    assert replay.last_read_message_seq == event_seq
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "delete from platform_attachments.conversation_read_state where "
            "conversation_id=%s",
            (started.conversation.conversation_id,),
        )
