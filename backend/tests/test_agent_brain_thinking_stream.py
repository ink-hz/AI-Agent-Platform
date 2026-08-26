from __future__ import annotations

import json
from pathlib import Path

import httpx
import psycopg
import pytest

from app.agent_brain.anthropic_adapter import AnthropicMessagesAdapter
from app.agent_brain.model_adapter import (
    BrainModelManifest,
    BrainRequestBuilder,
    ProviderInterrupted,
    ThinkingDelta,
)
from test_agent_brain_loop_repository import loop_database, loop_repository, seeded_loop
from test_agent_brain_loop_runtime import _response, _runtime
from test_control_plane_migration import control_database


MANIFEST = Path(__file__).parents[2] / "deploy/cloud/brain-model.release.json"


def _events(*events: dict[str, object]) -> bytes:
    return "".join(
        f"data: {json.dumps(event)}\n\n" for event in events
    ).encode()


def _request():
    return BrainRequestBuilder(BrainModelManifest.load(MANIFEST)).build(
        messages=({"role": "user", "content": "分析需求"},),
        step_seq=1,
        system_prompt="stable system prompt " * 40,
    )


def test_adapter_streams_provider_summary_before_returning_tool_commit() -> None:
    stream = _events(
        {"type": "message_start", "message": {"id": "msg_live", "usage": {}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "需要先拆分"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "任务。"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "list_agents",
                "input": {},
            },
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 10},
        },
        {"type": "message_stop"},
    )
    adapter = AnthropicMessagesAdapter(
        base_url="https://gateway.example",
        api_key="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=stream))
        ),
    )
    seen: list[ThinkingDelta] = []

    response = adapter.complete(_request(), on_thinking_delta=seen.append)

    assert [(item.block_index, item.delta_seq, item.text) for item in seen] == [
        (0, 1, "需要先拆分"),
        (0, 2, "任务。"),
    ]
    assert all(item.provider_run_ref == "msg_live" for item in seen)
    assert response.content_blocks[0]["signature"] == "sig"


def test_interrupted_stream_keeps_emitted_summary_and_never_retries() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=_events(
                {"type": "message_start", "message": {"id": "msg_partial", "usage": {}}},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking", "thinking": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "已开始分析"},
                },
                {"type": "broken_private_event"},
            ),
        )

    adapter = AnthropicMessagesAdapter(
        base_url="https://gateway.example",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    seen: list[ThinkingDelta] = []

    with pytest.raises(ProviderInterrupted):
        adapter.complete(_request(), on_thinking_delta=seen.append)

    assert [item.text for item in seen] == ["已开始分析"]
    assert calls == 1


@pytest.mark.postgres
def test_runtime_persists_and_completes_provider_summary(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    response = _response(
        "list_agents",
        {"public_reason": "查看可用专业 Agent"},
        thinking="需要先确认有哪些专业 Agent。",
    )

    assert _runtime(loop_repository, response).advance_one() is True

    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select summary_ciphertext,summary.status,provider_run_ref from "
            "platform_brain.brain_thinking_summaries summary join "
            "platform_brain.brain_steps step on step.step_id=summary.step_id "
            "where step.loop_id=%s",
            (loop_id,),
        ).fetchone()
    assert row is not None
    assert row[1:] == ("completed", response.provider_request_id)
    assert "需要先确认".encode() not in bytes(row[0])


class _InterruptedModel:
    def complete(self, _request, *, on_thinking_delta=None):
        assert on_thinking_delta is not None
        on_thinking_delta(ThinkingDelta(0, 1, "已开始分析。", "msg_interrupted"))
        raise ProviderInterrupted()


@pytest.mark.postgres
def test_runtime_marks_partial_summary_interrupted_without_tool_call(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop

    assert _runtime(loop_repository, model=_InterruptedModel()).advance_one() is True

    with psycopg.connect(environment["admin"]) as connection:
        status = connection.execute(
            "select summary.status from platform_brain.brain_thinking_summaries summary "
            "join platform_brain.brain_steps step on step.step_id=summary.step_id "
            "where step.loop_id=%s",
            (loop_id,),
        ).fetchone()[0]
        tool_count = connection.execute(
            "select count(*) from platform_brain.brain_tool_calls call join "
            "platform_brain.brain_steps step on step.step_id=call.step_id "
            "where step.loop_id=%s",
            (loop_id,),
        ).fetchone()[0]
    assert status == "interrupted"
    assert tool_count == 0
