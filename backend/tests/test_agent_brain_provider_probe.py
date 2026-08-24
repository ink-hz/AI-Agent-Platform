from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_brain.model_adapter import (
    BrainModelResponse,
    BrainUsage,
)
from app.agent_brain.provider_probe import (
    ProviderCapabilityError,
    run_probe,
)


MANIFEST = Path(__file__).parents[2] / "deploy/cloud/brain-model.release.json"


class FakeProvider:
    def __init__(self, *, honor_forced: bool = True, readable_thinking: bool = False):
        self.honor_forced = honor_forced
        self.readable_thinking = readable_thinking
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if request.tool_choice and not self.honor_forced:
            blocks = ({"type": "text", "text": "free text"},)
        else:
            tool_name = request.tool_choice["name"] if request.tool_choice else "list_agents"
            blocks = (
                {
                    "type": "thinking",
                    "thinking": "readable" if self.readable_thinking else "",
                    "signature": "sig",
                },
                {"type": "tool_use", "id": "toolu_probe", "name": tool_name, "input": {}},
            )
        return BrainModelResponse(
            provider_request_id=f"msg_{len(self.requests)}",
            content_blocks=blocks,
            stop_reason="tool_use",
            stop_details=None,
            usage=BrainUsage(
                input_tokens=100,
                output_tokens=10,
                cache_creation_input_tokens=100,
                cache_read_input_tokens=100,
            ),
        )


def test_provider_probe_fails_when_forced_tool_choice_is_not_honored() -> None:
    with pytest.raises(ProviderCapabilityError, match="forced_tool_choice"):
        run_probe(
            MANIFEST,
            system_prompt="synthetic stable prompt",
            provider=FakeProvider(honor_forced=False),
        )


def test_provider_probe_rejects_readable_thinking_in_omitted_mode() -> None:
    with pytest.raises(ProviderCapabilityError, match="omitted_thinking"):
        run_probe(
            MANIFEST,
            system_prompt="synthetic stable prompt",
            provider=FakeProvider(readable_thinking=True),
        )


def test_provider_probe_records_required_capabilities_and_effort_profiles() -> None:
    provider = FakeProvider()
    evidence = run_probe(
        MANIFEST,
        system_prompt="synthetic stable prompt",
        provider=provider,
    )

    assert evidence["supported"] == {
        "streaming": True,
        "forced_tool_choice": True,
        "omitted_thinking": True,
        "mid_conversation_system": True,
        "one_hour_cache": True,
        "one_million_context": True,
    }
    assert evidence["efforts"] == ["medium", "high", "xhigh"]
    assert evidence["stable_cache_ttl"] == "1h"
    assert evidence["rolling_cache_ttl"] == "5m"
    assert all(request.model_id == "claude-opus-5" for request in provider.requests)
