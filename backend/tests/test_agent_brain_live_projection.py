from __future__ import annotations

from app.agent_brain.conversation_projection import (
    ConversationProjection,
    PrivateBrainEvent,
)


def test_thinking_projection_removes_provider_internals() -> None:
    public = ConversationProjection.project(
        PrivateBrainEvent(
            "agent.thinking_summary",
            {
                "task_id": "00000000-0000-0000-0000-000000000001",
                "agent_id": "hr-bot",
                "source": "provider",
                "source_ref": "run_opaque",
                "summary": "需要验证能力组合。",
                "signature": "secret",
                "raw_response": "secret",
            },
        )
    )

    assert public.payload["summary"] == "需要验证能力组合。"
    assert public.payload["source_ref"] == "run_opaque"
    assert "signature" not in public.payload
    assert "raw_response" not in public.payload


def test_invalid_thinking_projects_stable_platform_fact() -> None:
    assert ConversationProjection.public_payload(
        "brain.thinking_summary", {"signature": "private"}
    ) == {"status": "public_event_unavailable"}
