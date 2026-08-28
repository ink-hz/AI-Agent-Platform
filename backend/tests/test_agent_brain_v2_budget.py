from __future__ import annotations

# Pytest fixtures are imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from app.agent_brain.action_models import (
    ActionProposal,
    proposal_digest,
    stable_action_id,
)
from app.agent_brain.action_service import ActionCommandService
from app.agent_brain.model_adapter import ProviderRefused, ProviderUnavailable
from test_agent_brain_live_repository import live_database, seeded_live_task
from test_agent_brain_loop_repository import loop_database, loop_repository, seeded_loop
from test_agent_brain_loop_runtime import (
    Decision,
    FakeRegistry,
    ScriptedModel,
    _ready_tool_results,
    _runtime,
    _submit_response,
)
from test_control_plane_migration import control_database


class ExistingTaskRegistry(FakeRegistry):
    def authorize_task(self, _user_id, agent_id, expected_capability_version):
        return Decision(
            capability_version=expected_capability_version,
            adapter_kind="reference",
        )


def _propose_pending_action(live_database, seeded_live_task):
    environment, codec, owner_id, _conversation_id, _turn_id = live_database
    _collaboration, repository, loop_id, task_id, _conversation_id = seeded_live_task
    parameters = {"draft_id": "draft-budget", "channel": "voc"}
    action_id = stable_action_id(task_id, 1)
    digest = proposal_digest(
        platform_task_id=task_id,
        action_seq=1,
        action_kind="voc.submit",
        parameters=parameters,
    )
    worker = ActionCommandService(
        environment["urls"]["platform_brain_worker"],
        content_codec=codec,
        dsn_purpose="brain",
    )
    app = ActionCommandService(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        dsn_purpose="app",
    )
    worker.propose(
        ActionProposal(
            action_id=action_id,
            platform_task_id=task_id,
            action_seq=1,
            action_kind="voc.submit",
            summary="提交 VOC 草稿",
            impact="将写入正式 VOC 记录",
            parameters=parameters,
            action_digest=digest,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
            execution_timeout_seconds=300,
        )
    )
    return environment, owner_id, repository, loop_id, action_id, digest, app


@pytest.mark.postgres
def test_pending_action_rejects_submit_without_protocol_retry(
    live_database, seeded_live_task
) -> None:
    environment, _owner_id, repository, loop_id, action_id, _digest, _app = (
        _propose_pending_action(live_database, seeded_live_task)
    )
    model = ScriptedModel(_submit_response())
    runtime = _runtime(repository, model=model, registry=ExistingTaskRegistry())

    assert runtime.advance_one() is True
    assert model.calls == 1
    with psycopg.connect(environment["admin"]) as connection:
        loop_state = connection.execute(
            "select status,protocol_retry_count,terminal_at from "
            "platform_brain.brain_loops where loop_id=%s",
            (loop_id,),
        ).fetchone()
        action_state = connection.execute(
            "select status from platform_brain.agent_task_actions where action_id=%s",
            (action_id,),
        ).fetchone()[0]
    assert loop_state == ("running", 0, None)
    assert action_state == "pending"
    assert _ready_tool_results(repository, environment, loop_id)[-1] == {
        "status": "rejected",
        "reason": "pending_action_requires_resolution",
        "required_next_action": "await_agent_events",
    }


@pytest.mark.postgres
@pytest.mark.parametrize("forced_by", ["task_count", "step_count", "deadline"])
def test_forced_pending_waits_then_submits(
    live_database, seeded_live_task, forced_by
) -> None:
    environment, owner_id, repository, loop_id, action_id, digest, app = (
        _propose_pending_action(live_database, seeded_live_task)
    )
    with psycopg.connect(environment["admin"]) as connection:
        if forced_by == "task_count":
            connection.execute(
                "update platform_brain.brain_loops set max_tasks=task_count "
                "where loop_id=%s",
                (loop_id,),
            )
        elif forced_by == "step_count":
            connection.execute(
                "update platform_brain.brain_loops set max_steps=2 where loop_id=%s",
                (loop_id,),
            )
        else:
            connection.execute(
                "update platform_brain.brain_loops set "
                "active_elapsed_ms=active_budget_ms,active_deadline_at="
                "clock_timestamp()-interval '1 second' where loop_id=%s",
                (loop_id,),
            )
    model = ScriptedModel(_submit_response())
    runtime = _runtime(repository, model=model, registry=ExistingTaskRegistry())

    assert runtime.advance_one() is True
    assert model.calls == 0
    with psycopg.connect(environment["admin"]) as connection:
        paused = connection.execute(
            "select loop.status,turn.status,loop.active_started_at,"
            "loop.active_deadline_at,loop.intervention_expires_at "
            "from platform_brain.brain_loops loop join "
            "platform_control.conversation_turns turn on turn.turn_id=loop.turn_id "
            "where loop.loop_id=%s",
            (loop_id,),
        ).fetchone()
    assert paused[0:4] == ("waiting_confirmation", "waiting_confirmation", None, None)
    assert paused[4] is not None
    assert repository.lease_step("other-brain-worker", lease_seconds=45) is None

    assert app.confirm(owner_id, action_id, digest).status == "confirmed"
    assert runtime.advance_one() is True
    assert model.calls == 1
    assert model.requests[-1].tool_choice == {
        "type": "tool",
        "name": "submit_answer",
    }
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select status from platform_brain.brain_loops where loop_id=%s",
            (loop_id,),
        ).fetchone()[0] == "completed"


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
