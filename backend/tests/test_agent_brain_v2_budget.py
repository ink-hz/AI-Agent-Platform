from __future__ import annotations

import psycopg
import pytest

from app.agent_brain.model_adapter import ProviderRefused, ProviderUnavailable
from test_agent_brain_loop_repository import loop_database, loop_repository, seeded_loop
from test_agent_brain_loop_runtime import (
    ScriptedModel,
    _runtime,
    _submit_response,
)
from test_control_plane_migration import control_database


@pytest.mark.postgres
def test_task_budget_forces_submit_with_unchanged_tools(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_brain.brain_loops set max_tasks=0 where loop_id=%s",
            (loop_id,),
        )
    model = ScriptedModel(_submit_response())
    runtime = _runtime(loop_repository)
    runtime._model = model

    assert runtime.advance_one() is True
    assert model.requests[0].tool_choice == {
        "type": "tool",
        "name": "submit_answer",
    }
    assert model.requests[0].tools_json == runtime._request_builder.build(
        messages=(), step_seq=1, system_prompt=runtime._system_prompt.text
    ).tools_json


@pytest.mark.postgres
def test_zero_tool_response_retries_once_then_uses_protocol_fallback(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    from app.agent_brain.model_adapter import BrainModelResponse

    invalid = BrainModelResponse(
        provider_request_id="msg_text_only",
        content_blocks=({"type": "text", "text": "未调用工具"},),
        stop_reason="end_turn",
    )
    model = ScriptedModel([invalid, invalid])
    runtime = _runtime(loop_repository)
    runtime._model = model

    assert runtime.advance_one() is True
    assert model.calls == 2
    with psycopg.connect(environment["admin"]) as connection:
        state = connection.execute(
            "select status,reason_code,fallback_used,fallback_kind,"
            "protocol_retry_count from platform_brain.brain_loops where loop_id=%s",
            (loop_id,),
        ).fetchone()
    assert state == (
        "failed",
        "protocol_violation_after_retry",
        True,
        "platform_partial_summary",
        1,
    )


@pytest.mark.postgres
def test_provider_refusal_skips_retry_and_writes_explicit_fallback(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    model = ScriptedModel(ProviderRefused("cyber"))
    runtime = _runtime(loop_repository)
    runtime._model = model

    assert runtime.advance_one() is True
    assert model.calls == 1
    with psycopg.connect(environment["admin"]) as connection:
        state = connection.execute(
            "select status,reason_code,fallback_used from "
            "platform_brain.brain_loops where loop_id=%s",
            (loop_id,),
        ).fetchone()
    assert state == ("failed", "provider_refused", True)


@pytest.mark.postgres
def test_step_budget_forces_submission_failure_with_distinct_reason(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_brain.brain_loops set max_steps=1 where loop_id=%s",
            (loop_id,),
        )
    model = ScriptedModel(ProviderUnavailable())
    runtime = _runtime(loop_repository)
    runtime._model = model

    assert runtime.advance_one() is True
    assert model.requests[0].tool_choice == {
        "type": "tool",
        "name": "submit_answer",
    }
    with psycopg.connect(environment["admin"]) as connection:
        state = connection.execute(
            "select status,reason_code,fallback_used from "
            "platform_brain.brain_loops where loop_id=%s",
            (loop_id,),
        ).fetchone()
    assert state == ("failed", "forced_submission_failed", True)


@pytest.mark.postgres
def test_waiting_user_expiry_is_idempotent_and_explicit(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    from test_agent_brain_loop_runtime import _request_user_response

    assert _runtime(loop_repository, _request_user_response()).advance_one() is True
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_brain.brain_loops set waiting_user_expires_at="
            "clock_timestamp()-interval '1 second' where loop_id=%s",
            (loop_id,),
        )
    assert loop_repository.expire_waiting_users(limit=10) == 1
    assert loop_repository.expire_waiting_users(limit=10) == 0
    with psycopg.connect(environment["admin"]) as connection:
        state = connection.execute(
            "select status,reason_code from platform_brain.brain_loops "
            "where loop_id=%s",
            (loop_id,),
        ).fetchone()
    assert state == ("failed", "user_input_timeout")
