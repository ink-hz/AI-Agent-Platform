from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.agent_brain.anthropic_adapter import AnthropicMessagesAdapter
from app.agent_brain.model_adapter import (
    BrainModelManifest,
    BrainRequestBuilder,
    ProviderInterrupted,
    ProviderRefused,
)
from app.agent_brain.tool_protocol import BRAIN_TOOL_SCHEMAS


MANIFEST = Path(__file__).parents[2] / "deploy/cloud/brain-model.release.json"


def _events(*events: dict[str, object]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        for event in events
    ).encode()


def _tool_stream(*, stop_reason: str = "tool_use", thinking: str = "") -> bytes:
    return _events(
        {
            "type": "message_start",
            "message": {
                "id": "msg_123",
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 400,
                    "cache_read_input_tokens": 1200,
                },
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": thinking},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig_abc"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "submit_answer",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"answer_markdown":"完成","outcome":"resolved",'
                '"used_task_ids":[],"attachment_refs":[],"public_reason":"交付"}',
            },
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason},
            "usage": {"output_tokens": 88},
        },
        {"type": "message_stop"},
    )


def _request(*, forced: bool = False):
    manifest = BrainModelManifest.load(MANIFEST)
    return BrainRequestBuilder(manifest).build(
        messages=({"role": "user", "content": "介绍一下你自己"},),
        step_seq=1,
        system_prompt="stable system prompt " * 40,
        tool_choice=(
            {"type": "tool", "name": "submit_answer"} if forced else None
        ),
    )


def test_adapter_sends_adaptive_thinking_cache_and_tools() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_tool_stream(thinking="需要先判断是否委派。"),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = AnthropicMessagesAdapter(
        base_url="https://gateway.example",
        api_key="provider-secret",
        client=client,
    )

    response = adapter.complete(_request())

    body = bodies[0]
    assert body["model"] == "claude-opus-5"
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "high"}
    assert body["max_tokens"] == 65536
    assert body["stream"] is True
    assert {"temperature", "top_p", "top_k", "fallbacks"}.isdisjoint(body)
    assert response.usage.cache_read_input_tokens == 1200
    assert response.content_blocks[0] == {
        "type": "thinking",
        "thinking": "需要先判断是否委派。",
        "signature": "sig_abc",
    }
    assert all(block.get("type") != "thinking" for block in response.public_blocks)
    assert "provider-secret" not in repr(adapter)
    assert "provider-secret" not in repr(response)


def test_adapter_uses_explicit_bearer_auth_for_fae_gateway() -> None:
    captured: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers)
        return httpx.Response(200, content=_tool_stream())

    adapter = AnthropicMessagesAdapter(
        base_url="https://cc.nexcor.ai",
        api_key="fae-provider-token",
        auth_scheme="bearer",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert adapter.complete(_request()).stop_reason == "tool_use"
    assert captured[0]["authorization"] == "Bearer fae-provider-token"
    assert "x-api-key" not in captured[0]


def test_adapter_rejects_unknown_auth_scheme() -> None:
    with pytest.raises(ValueError, match="configuration invalid"):
        AnthropicMessagesAdapter(
            base_url="https://gateway.example",
            api_key="secret",
            auth_scheme="automatic",
            client=httpx.Client(transport=httpx.MockTransport(lambda _: None)),
        )


def test_forced_submission_changes_only_tool_choice_not_tool_bytes() -> None:
    normal = _request()
    forced = _request(forced=True)

    assert normal.tools_json == forced.tools_json
    assert normal.tools == forced.tools
    assert normal.tool_choice is None
    assert forced.tool_choice == {"type": "tool", "name": "submit_answer"}


def test_request_builder_uses_four_ordered_cache_breakpoints() -> None:
    manifest = BrainModelManifest.load(MANIFEST)
    request = BrainRequestBuilder(manifest).build(
        messages=(
            {"role": "user", "content": "开始"},
            {
                "role": "system",
                "content": "capability-version=7",
                "cache_anchor": "capability",
            },
            {"role": "assistant", "content": "继续"},
        ),
        step_seq=2,
        system_prompt="stable prompt " * 100,
    )

    body = request.provider_body()
    breakpoints = request.cache_breakpoints
    assert len(breakpoints) == 4
    assert [point.ttl for point in breakpoints] == ["1h", "1h", "5m", "5m"]
    assert [schema["name"] for schema in body["tools"]] == [
        schema["name"] for schema in BRAIN_TOOL_SCHEMAS
    ]
    assert list(body)[:3] == ["model", "max_tokens", "stream"]
    assert body["tools"][-1]["cache_control"]["ttl"] == "1h"
    assert body["system"][-1]["cache_control"]["ttl"] == "1h"


def test_refusal_is_not_retried_or_parsed_as_zero_tool_use() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = _events(
            {
                "type": "message_start",
                "message": {"id": "msg_refused", "usage": {"input_tokens": 0}},
            },
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "refusal",
                    "stop_details": {"category": "cyber"},
                },
                "usage": {"output_tokens": 0},
            },
            {"type": "message_stop"},
        )
        return httpx.Response(200, content=content)

    adapter = AnthropicMessagesAdapter(
        base_url="https://gateway.example",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderRefused) as error:
        adapter.complete(_request())
    assert error.value.category == "cyber"
    assert calls == 1


def test_only_failure_before_first_stream_event_is_retried() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, content=b"private upstream body")
        return httpx.Response(200, content=_tool_stream())

    adapter = AnthropicMessagesAdapter(
        base_url="https://gateway.example",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert adapter.complete(_request()).stop_reason == "tool_use"
    assert calls == 2


def test_stream_failure_after_first_event_is_not_retried() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=_events(
                {
                    "type": "message_start",
                    "message": {"id": "msg_partial", "usage": {}},
                },
                {"type": "unknown_after_start", "secret": "raw-private"},
            ),
        )

    adapter = AnthropicMessagesAdapter(
        base_url="https://gateway.example",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderInterrupted) as error:
        adapter.complete(_request())
    assert calls == 1
    assert "raw-private" not in repr(error.value)
