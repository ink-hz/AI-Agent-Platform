from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.adapters.base import AdapterRegistry
from app.agent_brain.adapters.reference import ReferenceAdapter
from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from app.agent_brain.loop_runtime import BrainLoopRuntime
from app.agent_brain.model_adapter import (
    BrainModelManifest,
    BrainModelResponse,
    BrainRequestBuilder,
    BrainUsage,
    ThinkingDelta,
)
from app.agent_brain.prompt import BrainSystemPrompt
from app.agent_brain.conversation_repository import ConversationRepository
from app.agent_brain.repository import MissionRepository
from app.execution_relay.content_crypto import SealedContent
from test_agent_brain_loop_repository import loop_database, loop_repository, seeded_loop
from test_control_plane_migration import control_database


ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "deploy/cloud/brain-model.release.json"
PROMPT_PATH = ROOT / "backend/app/agent_brain/prompts/brain_v1.md"


class ScriptedModel:
    def __init__(self, response) -> None:
        self.responses = list(response) if isinstance(response, list) else [response]
        self.calls = 0
        self.requests = []

    def complete(self, request, *, on_thinking_delta=None):
        self.calls += 1
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if on_thinking_delta is not None:
            for index, block in enumerate(response.content_blocks):
                if block.get("type") == "thinking" and block.get("thinking"):
                    on_thinking_delta(
                        ThinkingDelta(
                            index,
                            1,
                            block["thinking"],
                            response.provider_request_id,
                        )
                    )
        return response


def _response(
    name: str, arguments: dict[str, object], *, thinking: str = ""
) -> BrainModelResponse:
    return BrainModelResponse(
        provider_request_id=f"msg_{name}_{uuid4()}",
        content_blocks=(
            {
                "type": "thinking",
                "thinking": thinking,
                "signature": "signed",
            },
            {
                "type": "tool_use",
                "id": f"toolu_{name}",
                "name": name,
                "input": arguments,
            },
        ),
        stop_reason="tool_use",
        stop_details=None,
        usage=BrainUsage(input_tokens=100, output_tokens=20),
    )


def _list_agents_response() -> BrainModelResponse:
    return _response("list_agents", {"public_reason": "查看可用专业 Agent"})


def _delegate_response() -> BrainModelResponse:
    return _response(
        "delegate_task",
        {
            "agent_id": "reference-agent",
            "objective": "生成一份确定性验证结果",
            "context_excerpt": ["验证 durable loop"],
            "constraints": ["不得联网"],
            "attachment_refs": [],
            "expected_output": "返回确定性完成结果",
            "public_reason": "需要 Reference Agent 验证任务链路",
        },
    )


def _submit_response() -> BrainModelResponse:
    return _response(
        "submit_answer",
        {
            "answer_markdown": "完成",
            "outcome": "resolved",
            "used_task_ids": [],
            "attachment_refs": [],
            "public_reason": "整合验证结果并交付",
        },
    )


def _request_user_response() -> BrainModelResponse:
    return _response(
        "request_user_input",
        {
            "question": "需要确认岗位级别",
            "public_reason": "岗位级别决定候选人范围",
        },
    )


@dataclass(frozen=True)
class Decision:
    allowed: bool = True
    reason_code: str = "allowed"
    capability_version: int = 1
    adapter_kind: str = "reference"
    grant_ids: tuple = ()
    directory_generation_id: object = None
    effective_decision_hash: bytes = b"a" * 32


class FakeRegistry:
    def __init__(self, *, allowed: bool = True, reason_code: str = "allowed"):
        self.allowed = allowed
        self.reason_code = reason_code

    def list_for_user(self, _user_id):
        return (
            {
                "agent_id": "reference-agent",
                "display_name": "Reference Agent",
                "adapter_kind": "reference",
                "capability_version": 1,
                "availability": "healthy",
            },
        )

    def authorize_task(self, _user_id, agent_id, expected_capability_version):
        assert agent_id == "reference-agent"
        assert expected_capability_version == 1
        return Decision(allowed=self.allowed, reason_code=self.reason_code)


class UnavailableListRegistry(FakeRegistry):
    def list_for_user(self, _user_id):
        raise AgentUseAuthorizationUnavailable()


def _runtime(repository, response=None, *, registry=None, model=None):
    manifest = BrainModelManifest.load(MANIFEST_PATH)
    prompt = BrainSystemPrompt.load(
        PROMPT_PATH,
        expected_sha256=manifest.system_prompt_sha256,
    )
    adapters = AdapterRegistry()
    adapters.register("reference", ReferenceAdapter())
    return BrainLoopRuntime(
        repository=repository,
        model=model or (ScriptedModel(response) if response is not None else None),
        request_builder=BrainRequestBuilder(manifest),
        system_prompt=prompt,
        runtime_registry=registry or FakeRegistry(),
        adapters=adapters,
        worker_id="test-brain-worker",
        lease_seconds=45,
    )


@pytest.mark.postgres
def test_waiting_user_pauses_budget_and_resumes_same_turn_once(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, _codec, owner, conversation_id, turn_id = loop_database
    loop_id, _snapshot_id = seeded_loop
    assert _runtime(loop_repository, _request_user_response()).advance_one() is True
    with psycopg.connect(environment["admin"]) as connection:
        waiting = connection.execute(
            "select status,active_elapsed_ms,active_started_at,active_deadline_at,"
            "waiting_user_expires_at from platform_brain.brain_loops where loop_id=%s",
            (loop_id,),
        ).fetchone()
    assert waiting[0] == "waiting_user"
    assert waiting[1] < 900_000
    assert waiting[2] is None and waiting[3] is None
    assert waiting[4] is not None

    request_id = uuid4()
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=loop_repository.content_codec,
        mission_repository=MissionRepository(
            environment["urls"]["platform_control_app"],
            content_codec=loop_repository.content_codec,
        ),
    )
    resumed = conversations.resume_waiting_user_v2(
        owner, conversation_id, request_id, "高级工程师"
    )
    replay = conversations.resume_waiting_user_v2(
        owner, conversation_id, request_id, "高级工程师"
    )
    assert resumed.message.message_id == replay.message.message_id == request_id
    messages = loop_repository.reconstruct_messages(loop_id)
    tool_results = [
        block
        for message in messages
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    assert "高级工程师" in tool_results[0]["content"]
    with pytest.raises(Exception):
        conversations.resume_waiting_user_v2(
            owner, conversation_id, uuid4(), "另一个答案"
        )


@pytest.mark.postgres
def test_three_task_batch_creates_only_one_resume_step(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop
    blocks = tuple(
        _delegate_response().content_blocks[1] | {
            "id": f"toolu_batch_{index}",
            "input": {
                **_delegate_response().content_blocks[1]["input"],
                "objective": f"任务 {index}",
            },
        }
        for index in range(3)
    )
    response = BrainModelResponse(
        provider_request_id="msg_batch",
        content_blocks=blocks,
        stop_reason="tool_use",
        usage=BrainUsage(input_tokens=100, output_tokens=40),
    )
    assert _runtime(loop_repository, response).advance_one() is True
    while _runtime(loop_repository).dispatch_one():
        pass
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_brain.brain_steps where loop_id=%s "
            "and status='queued'",
            (loop_id,),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_reference_adapter_slice_survives_worker_recreation(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop

    assert _runtime(loop_repository, _list_agents_response()).advance_one() is True
    assert _runtime(loop_repository, _delegate_response()).advance_one() is True
    assert _runtime(loop_repository).dispatch_one() is True
    assert _runtime(loop_repository, _submit_response()).advance_one() is True

    with psycopg.connect(environment["admin"]) as connection:
        answer_rows = connection.execute(
            "select message.content_ciphertext,message.encryption_key_version,"
            "message.message_id,message.conversation_id "
            "from platform_control.conversation_messages message "
            "join platform_control.conversation_turns turn "
            "on turn.assistant_message_id=message.message_id "
            "join platform_brain.brain_loops loop on loop.turn_id=turn.turn_id "
            "where loop.loop_id=%s",
            (loop_id,),
        ).fetchall()
        state = connection.execute(
            "select loop.status,turn.status,"
            "(select count(*) from platform_brain.agent_tasks where loop_id=%s) "
            "from platform_brain.brain_loops loop "
            "join platform_control.conversation_turns turn on turn.turn_id=loop.turn_id "
            "where loop.loop_id=%s",
            (loop_id, loop_id),
        ).fetchone()
    assert len(answer_rows) == 1
    row = answer_rows[0]
    content = loop_repository.content_codec.unseal_json(
        f"conversation:{row[3]}:message:{row[2]}:content",
        SealedContent(bytes(row[0]), row[1]),
    )
    assert content == {"text": "完成"}
    assert state == ("completed", "completed", 1)


@pytest.mark.postgres
def test_list_agents_reports_authorization_failure_instead_of_zero_agents(
    loop_repository, seeded_loop
) -> None:
    loop_id, _snapshot_id = seeded_loop

    assert _runtime(
        loop_repository,
        _list_agents_response(),
        registry=UnavailableListRegistry(),
    ).advance_one() is True

    tool_results = [
        block
        for message in loop_repository.reconstruct_messages(loop_id)
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    assert '"status":"failed"' in tool_results[0]["content"]
    assert '"reason":"authorization_unavailable"' in tool_results[0]["content"]


def test_adapter_registry_fails_closed_for_duplicate_and_unknown_kinds() -> None:
    registry = AdapterRegistry()
    adapter = ReferenceAdapter()
    registry.register("reference", adapter)
    assert registry.require("reference") is adapter
    assert registry.is_registered("reference") is True
    with pytest.raises(ValueError, match="already registered"):
        registry.register("reference", ReferenceAdapter())
    with pytest.raises(LookupError, match="not registered"):
        registry.require("missing")


@pytest.mark.postgres
def test_live_revocation_fails_loop_before_model(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    _runtime(loop_repository, _delegate_response()).advance_one()
    _runtime(loop_repository).dispatch_one()
    denied_model = ScriptedModel(_submit_response())
    denied = _runtime(
        loop_repository,
        registry=FakeRegistry(allowed=False, reason_code="authorization_changed"),
    )
    denied._model = denied_model
    assert denied.advance_one() is True
    assert denied_model.calls == 0
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select status,reason_code from platform_brain.brain_loops where loop_id=%s",
            (loop_id,),
        ).fetchone() == ("failed", "authorization_changed")
