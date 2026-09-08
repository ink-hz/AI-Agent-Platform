"""Persisted selected knowledge, without changing the conversation text."""

from dataclasses import replace
from uuid import uuid4

import pytest
from test_agent_brain_conversation_repository import (
    conversation_database as conversation_database,
    repository as repository,
)
from test_control_plane_migration import (
    control_database as control_database,
)

from app.agent_brain.conversation_models import (
    ConversationTurnSubmission,
    normalize_turn_submission,
)
from app.agent_brain.conversation_repository import ConversationRepositoryConflict
from app.agent_brain.conversation_routes import ConversationTextBody, _message_payload
from app.agent_brain.conversation_service import ConversationCommandService

SELECTION = {
    "source_commit": "a" * 40,
    "id": "job-and-context",
    "revision": 1,
    "sha256": "b" * 64,
}


def test_http_submission_preserves_selection_without_rewriting_user_text():
    body = ConversationTextBody.model_validate(
        {"text": "帮我分析岗位", "user_selected_resources": [SELECTION]}
    )
    selected = normalize_turn_submission(body.submission())
    assert selected.text == "帮我分析岗位"
    assert selected.user_selected_resources == (SELECTION,)


@pytest.mark.postgres
def test_selection_round_trips_encrypted_message_and_idempotency(
    repository, conversation_database
):
    _, owner, _ = conversation_database
    request = uuid4()
    selected = ConversationTurnSubmission(
        "帮我分析岗位", user_selected_resources=(SELECTION,)
    )
    first = repository.start(
        owner, request, selected, mode="direct_agent", direct_agent_id="hr-bot"
    )
    assert _message_payload(first.message)["user_selected_resources"] == [SELECTION]
    repeated = repository.start(
        owner, request, selected, mode="direct_agent", direct_agent_id="hr-bot"
    )
    assert not repeated.created
    assert repeated.message.user_selected_resources == (SELECTION,)
    changed = replace(
        selected, user_selected_resources=({**SELECTION, "sha256": "c" * 64},)
    )
    with pytest.raises(ConversationRepositoryConflict):
        repository.start(
            owner, request, changed, mode="direct_agent", direct_agent_id="hr-bot"
        )


@pytest.mark.postgres
def test_disabled_knowledge_rejected_before_creating_turn(
    repository, conversation_database
):
    _, owner, _ = conversation_database
    command = ConversationCommandService(repository, v2_enabled=False)
    with pytest.raises(ValueError, match="knowledge"):
        command.start(
            owner,
            uuid4(),
            ConversationTurnSubmission(
                "帮我分析岗位", user_selected_resources=(SELECTION,)
            ),
            mode="direct_agent",
            direct_agent_id="hr-bot",
        )
