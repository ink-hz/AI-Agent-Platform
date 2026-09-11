"""Knowledge context uses the real persisted turn and existing frozen binding."""

import json
from uuid import uuid4

import pytest
from test_hr_direct_worker_progress import (
    attempt_repository as attempt_repository,
    control_database as control_database,
    conversation_database as conversation_database,
    direct_database as direct_database,
    repository as repository,
    scheduler as scheduler,
    worker_conversation as worker_conversation,
    worker_turn as worker_turn,
)
from app.agent_brain.conversation_context import (
    ConversationContextBuilder,
    _context_size,
)


class References:
    def __init__(self):
        self.commit = "a" * 40

    def prompt_context(self, selections=()):
        return {
            "source_commit": self.commit,
            "index": "岗位任务与情境",
            "agent_release_path": "/test/releases/" + self.commit,
            "user_selected_resources": list(selections),
        }


@pytest.mark.postgres
def test_index_is_in_actual_frozen_prompt_and_retry_does_not_rebuild(
    scheduler, repository, worker_turn, attempt_repository
):
    _, adapter, _ = scheduler
    references = References()
    adapter.context_builder = ConversationContextBuilder(
        repository, hr_knowledge_repository=references
    )
    context = adapter.context_builder.build_direct(
        worker_turn.conversation.conversation_id, worker_turn.turn.turn_id
    )
    assert context.hr_reference_knowledge["source_commit"] == "a" * 40
    without = _context_size(
        context.summary,
        context.messages,
        context.hr_position_context,
        context.hr_panorama_context,
        context.hr_workflow_contract,
    )
    assert context.estimated_utf8_bytes > without
    lease = attempt_repository.claim_due(uuid4(), 60)
    first = adapter.prepare(lease)
    document = first.frozen.document
    assert (
        json.loads(document["prompt"])["hr_reference_knowledge"]
        == context.hr_reference_knowledge
    )
    references.commit = "b" * 40
    assert adapter.prepare(lease) == first


@pytest.mark.postgres
def test_legacy_turn_has_no_knowledge_injection(repository, conversation_database):
    _, owner, _ = conversation_database
    turn = repository.start(
        owner, uuid4(), "一般问题", mode="direct_agent", direct_agent_id="hr-bot"
    )
    context = ConversationContextBuilder(
        repository, hr_knowledge_repository=References()
    ).build_direct(turn.conversation.conversation_id, turn.turn.turn_id)
    assert context.hr_reference_knowledge is None
