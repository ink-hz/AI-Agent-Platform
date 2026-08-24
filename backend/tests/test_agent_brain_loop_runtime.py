from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.adapters.base import AdapterRegistry
from app.agent_brain.adapters.reference import ReferenceAdapter
from app.agent_brain.loop_runtime import BrainLoopRuntime
from app.agent_brain.model_adapter import (
    BrainModelManifest,
    BrainModelResponse,
    BrainRequestBuilder,
    BrainUsage,
)
from app.agent_brain.prompt import BrainSystemPrompt
from app.execution_relay.content_crypto import SealedContent
from test_agent_brain_loop_repository import loop_database, loop_repository, seeded_loop
from test_control_plane_migration import control_database


ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "deploy/cloud/brain-model.release.json"
PROMPT_PATH = ROOT / "backend/app/agent_brain/prompts/brain_v1.md"


class ScriptedModel:
    def __init__(self, response: BrainModelResponse) -> None:
        self.response = response
        self.calls = 0

    def complete(self, _request):
        self.calls += 1
        return self.response


def _response(name: str, arguments: dict[str, object]) -> BrainModelResponse:
    return BrainModelResponse(
        provider_request_id=f"msg_{name}_{uuid4()}",
        content_blocks=(
            {
                "type": "thinking",
                "thinking": "",
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
        return Decision()


def _runtime(repository, response=None):
    manifest = BrainModelManifest.load(MANIFEST_PATH)
    prompt = BrainSystemPrompt.load(
        PROMPT_PATH,
        expected_sha256=manifest.system_prompt_sha256,
    )
    adapters = AdapterRegistry()
    adapters.register("reference", ReferenceAdapter())
    return BrainLoopRuntime(
        repository=repository,
        model=ScriptedModel(response) if response is not None else None,
        request_builder=BrainRequestBuilder(manifest),
        system_prompt=prompt,
        runtime_registry=FakeRegistry(),
        adapters=adapters,
        worker_id="test-brain-worker",
        lease_seconds=45,
    )


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
