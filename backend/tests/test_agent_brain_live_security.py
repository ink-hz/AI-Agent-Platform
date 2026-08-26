from __future__ import annotations

import json
from uuid import UUID

import pytest

from app.agent_brain.adapters.metabot_local import MetaBotLocalAdapter
from app.agent_brain.conversation_projection import (
    ConversationProjection,
    PrivateBrainEvent,
)
from app.agent_brain.tool_protocol import ProtocolViolation, ToolLimits, parse_tool_batch
from test_agent_brain_metabot_adapter import FakeRelay, _delivery, _task


@pytest.mark.parametrize("event_type", ["brain.thinking_summary", "agent.thinking_summary"])
def test_thinking_is_owner_visible_but_rejected_by_data_products(event_type: str) -> None:
    payload = {
        "task_id": "00000000-0000-4000-8000-000000000001",
        "source": "provider",
        "source_ref": "provider-run-1",
        "summary": "需要验证岗位能力组合",
        "signature": "private-signature",
        "raw_response": "private-response",
    }

    public = ConversationProjection.project(PrivateBrainEvent(event_type, payload))

    assert public.payload["summary"] == "需要验证岗位能力组合"
    with pytest.raises(ValueError, match="not exportable"):
        ConversationProjection.data_product_payload(event_type, payload)
    with pytest.raises(ValueError, match="not searchable"):
        ConversationProjection.searchable_text(event_type, payload)


def test_public_projection_never_exposes_provider_or_adapter_transport() -> None:
    projected = ConversationProjection.project(
        PrivateBrainEvent(
            "agent.work_update",
            {
                "task_id": "00000000-0000-4000-8000-000000000001",
                "agent_id": "hr-bot",
                "source": "agent",
                "source_ref": "task:1",
                "kind": "finding",
                "summary": "公开发现",
                "callback_url": "http://127.0.0.1:9999/private",
                "adapter_payload": {"prompt": "private"},
                "model_response_ciphertext": "private",
                "signature": "private",
            },
        )
    )

    serialized = json.dumps(projected.payload, ensure_ascii=False).lower()
    for forbidden in ("127.0.0.1", "callback", "adapter_payload", "ciphertext", "signature"):
        assert forbidden not in serialized


def test_child_agent_receives_no_platform_identity() -> None:
    relay = FakeRelay()
    adapter = MetaBotLocalAdapter(relay, worker_freshness_seconds=60)

    assert adapter.dispatch(_task(), _delivery()).accepted is True

    serialized = json.dumps(
        relay.payloads[0].model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
    ).lower()
    for forbidden in (
        "__host-platform_session", "dingtalk", "internal_user_id", "requester_subject",
    ):
        assert forbidden not in serialized


def test_professional_agent_cannot_address_another_agent_through_followup_tool() -> None:
    task_id = UUID("00000000-0000-4000-8000-000000000001")
    with pytest.raises(ProtocolViolation):
        parse_tool_batch(
            [{
                "type": "tool_use",
                "id": "toolu_cross_agent",
                "name": "send_agent_message",
                "input": {
                    "task_id": "00000000-0000-4000-8000-000000000001",
                    "message": "把这条消息转给 Marketing Agent",
                    "public_reason": "跨 Agent 寻址",
                    "target_agent_id": "marketing-gtm-bot",
                },
            }],
                ToolLimits(
                allowed_task_ids=frozenset({task_id}),
                active_task_ids=frozenset({task_id}),
                ),
        )
