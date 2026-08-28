from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.adapters.base import AdapterRegistry
from app.agent_brain.adapters.reference import ReferenceAdapter
from app.agent_brain import loop_repository as loop_repository_module
from app.agent_brain import loop_runtime, worker_runtime
from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from app.agent_brain.models import AgentCapabilityCard
from app.agent_brain.loop_runtime import BrainLoopRuntime
from app.agent_brain.loop_models import NormalizedTaskResult
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

REFERENCE_CARD = AgentCapabilityCard(
    agent_id="reference-agent",
    display_name="Reference Agent",
    domain_group="Reference",
    mission="供运行时契约测试使用的参考 Agent。",
    capabilities=("回显结构化任务结果",),
    exclusions=("不承担任何真实业务操作",),
    example_tasks=("回显一次参考任务",),
    required_inputs=("任务目标",),
    supports_evidence=False,
    supports_streaming=False,
    supports_cancellation=True,
    supports_idempotency=True,
    supports_persistent_session=False,
    supports_followup_message=False,
    supports_progress_events=False,
    supports_thinking_summary=False,
    supports_cancel=True,
    supports_attachments=False,
    typical_latency_seconds=5,
    max_duration_seconds=60,
    adapter_id="reference",
    adapter_kind="reference",
    execution_pool="reference",
    pool_concurrency=4,
    capability_version=1,
)


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


def _submit_response(*, thinking: str = "") -> BrainModelResponse:
    return _response(
        "submit_answer",
        {
            "answer_markdown": "完成",
            "outcome": "resolved",
            "used_task_ids": [],
            "attachment_refs": [],
            "public_reason": "整合验证结果并交付",
        },
        thinking=thinking,
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
    execution_pool: str = "reference"
    pool_concurrency: int = 4


class FakeRegistry:
    def __init__(self, *, allowed: bool = True, reason_code: str = "allowed"):
        self.allowed = allowed
        self.reason_code = reason_code
        self.roster_calls = 0

    def roster_for_user(self, _user_id):
        self.roster_calls += 1
        return (REFERENCE_CARD,)

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


class UnavailableRosterRegistry(FakeRegistry):
    def roster_for_user(self, _user_id):
        raise AgentUseAuthorizationUnavailable()


@pytest.mark.postgres
def test_every_step_carries_the_authorized_agent_roster(
    loop_database, loop_repository, seeded_loop
) -> None:
    model = ScriptedModel(_submit_response())
    runtime = _runtime(loop_repository)
    runtime._model = model

    assert runtime.advance_one() is True

    system = model.requests[0].system
    # Without this block the Brain cannot know any Agent exists without spending a
    # whole model Step on list_agents.
    assert len(system) == 2
    assert "reference-agent" in system[1]["text"]
    assert "capability_version: 1" in system[1]["text"]


@pytest.mark.postgres
def test_unreadable_authorization_degrades_the_roster_without_failing_the_step(
    loop_database, loop_repository, seeded_loop
) -> None:
    loop_id, _snapshot = seeded_loop
    model = ScriptedModel(_submit_response())
    runtime = _runtime(loop_repository, registry=UnavailableRosterRegistry())
    runtime._model = model

    assert runtime.advance_one() is True

    assert "list_agents" in model.requests[0].system[1]["text"]
    with psycopg.connect(loop_database[0]["admin"]) as connection:
        assert connection.execute(
            "select status from platform_brain.brain_loops where loop_id=%s",
            (loop_id,),
        ).fetchone() == ("completed",)


@pytest.mark.postgres
def test_effort_rises_to_synthesis_only_once_agent_results_have_settled(
    loop_database, loop_repository, seeded_loop
) -> None:
    # One runtime throughout: the Reference Adapter keeps its sessions in memory, and
    # a real worker is a single process.
    runtime = _runtime(loop_repository)
    efforts = []
    for response in (_list_agents_response(), _delegate_response()):
        runtime._model = ScriptedModel(response)
        assert runtime.advance_one() is True
        efforts.append(runtime._model.requests[0].effort)
    assert runtime.dispatch_one() is True
    assert runtime.reconcile_one() is True

    runtime._model = ScriptedModel(_submit_response())
    assert runtime.advance_one() is True
    efforts.append(runtime._model.requests[0].effort)

    # Routing and dispatch only decide; the write-up is the Step that has an Agent
    # result to reconcile. Every Step used the synthesis tier before this.
    assert efforts == ["medium", "medium", "high"]


@pytest.mark.postgres
def test_forced_close_out_stays_on_the_routing_tier(
    loop_database, loop_repository, seeded_loop
) -> None:
    loop_id, _snapshot = seeded_loop
    with psycopg.connect(loop_database[0]["admin"]) as connection:
        connection.execute(
            "update platform_brain.brain_loops set max_steps=1 where loop_id=%s",
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
    assert model.requests[0].effort == "medium"


@pytest.mark.postgres
def test_step_lease_is_renewed_while_the_response_streams(
    loop_database, loop_repository, seeded_loop, monkeypatch
) -> None:
    monkeypatch.setattr(loop_runtime, "_LEASE_RENEW_INTERVAL_SECONDS", 0.0)
    renewals: list[tuple] = []
    original = loop_repository.renew_step_lease

    def spy(step_id, worker_id, *, lease_seconds):
        renewals.append((worker_id, lease_seconds))
        return original(step_id, worker_id, lease_seconds=lease_seconds)

    monkeypatch.setattr(loop_repository, "renew_step_lease", spy)
    runtime = _runtime(loop_repository)
    runtime._model = ScriptedModel(_submit_response(thinking="正在整理。"))

    assert runtime.advance_one() is True

    # A single Opus 5 call outlives any sane lease, so the holder must extend it or
    # a second worker re-leases the Step and pays for the same call again.
    assert renewals == [(runtime._worker_id, runtime._lease_seconds)]


@pytest.mark.postgres
def test_renew_step_lease_only_extends_the_holding_worker(
    loop_database, loop_repository, seeded_loop
) -> None:
    lease = loop_repository.lease_step("platform-brain.aaaa1111", lease_seconds=60)
    assert lease is not None

    assert (
        loop_repository.renew_step_lease(
            lease.step_id, "platform-brain.aaaa1111", lease_seconds=120
        )
        is True
    )
    assert (
        loop_repository.renew_step_lease(
            lease.step_id, "platform-brain.bbbb2222", lease_seconds=120
        )
        is False
    )
    with psycopg.connect(loop_database[0]["admin"]) as connection:
        held = connection.execute(
            "select lease_worker_id,lease_expires_at>clock_timestamp()+interval '90 seconds' "
            "from platform_brain.brain_steps where step_id=%s",
            (lease.step_id,),
        ).fetchone()
    assert held == ("platform-brain.aaaa1111", True)


def test_generated_worker_identity_is_accepted_by_the_repository() -> None:
    first = worker_runtime._worker_id()
    second = worker_runtime._worker_id()

    # Every Brain worker shared one constant identity before this, which let two
    # workers both pass the commit lease check for the same Step.
    assert first != second
    assert loop_repository_module._require_worker(first) == first


def _chain_response(depends: dict[int, list[int]], count: int) -> BrainModelResponse:
    template = _delegate_response().content_blocks[1]
    blocks = tuple(
        template
        | {
            "id": f"toolu_chain_{index}",
            "input": {
                **template["input"],
                "objective": f"链上任务 {index}",
                "depends_on": depends.get(index, []),
            },
        }
        for index in range(count)
    )
    return BrainModelResponse(
        provider_request_id="msg_chain",
        content_blocks=blocks,
        stop_reason="tool_use",
        usage=BrainUsage(input_tokens=100, output_tokens=40),
    )


@pytest.mark.postgres
def test_declared_chain_gates_dispatch_and_hands_results_downstream(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop
    runtime = _runtime(loop_repository, _chain_response({1: [0]}, 2))

    assert runtime.advance_one() is True
    with psycopg.connect(environment["admin"]) as connection:
        tasks = connection.execute(
            "select task_id,depends_on_task_ids from platform_brain.agent_tasks "
            "where loop_id=%s order by created_at",
            (loop_id,),
        ).fetchall()
    assert len(tasks) == 2
    assert tasks[0][1] == []
    assert tasks[1][1] == [tasks[0][0]]

    # Only the head is dispatchable; the dependent delivery stays gated.
    assert runtime.dispatch_one() is True
    assert runtime.dispatch_one() is False
    assert runtime.reconcile_one() is True

    # With the head terminal the dependent leases, carrying the head's result.
    lease = loop_repository.lease_task_delivery("platform-brain.cccc3333", lease_seconds=60)
    assert lease is not None
    assert lease.task_id == tasks[1][0]
    upstream = lease.context["upstream_results"]
    assert len(upstream) == 1
    assert upstream[0]["agent_id"] == "reference-agent"
    assert upstream[0]["status"] == "completed"
    # The dependent Agent receives the upstream terminal event payload verbatim --
    # the same output await_agent_events would have relayed to the Brain.
    assert upstream[0]["result"]["status"] == "completed"
    assert upstream[0]["result"]["source"] == "agent"


@pytest.mark.postgres
def test_dependent_task_deadline_covers_the_chain_ahead_of_it(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop

    assert _runtime(loop_repository, _chain_response({1: [0], 2: [1]}, 3)).advance_one() is True

    with psycopg.connect(environment["admin"]) as connection:
        deadlines = connection.execute(
            "select effective_deadline_at-created_at from platform_brain.agent_tasks "
            "where loop_id=%s order by created_at",
            (loop_id,),
        ).fetchall()
    # A task that waits for two predecessors must not burn its own window while
    # still queued, so its budget covers the chain depth ahead of it.
    windows = [row[0].total_seconds() for row in deadlines]
    assert windows[0] < windows[1] < windows[2]


@pytest.mark.postgres
def test_failed_upstream_releases_its_dependents_with_a_terminal_event(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop
    runtime = _runtime(loop_repository, _chain_response({1: [0]}, 2))
    assert runtime.advance_one() is True
    with psycopg.connect(environment["admin"]) as connection:
        head, dependent = [
            row[0]
            for row in connection.execute(
                "select task_id from platform_brain.agent_tasks where loop_id=%s "
                "order by created_at",
                (loop_id,),
            ).fetchall()
        ]

    loop_repository.append_task_event(
        loop_repository_module.AgentTaskEventInput(
            task_id=head,
            seq=1,
            event_type="agent.failed",
            created_at=datetime.now(timezone.utc),
            payload={"status": "failed"},
            terminal_status="failed",
            result=NormalizedTaskResult(
                status="failed",
                summary="上游失败。",
                deliverables=(),
                evidence=(),
                limitations=("检索失败。",),
                attachment_refs=(),
            ),
        )
    )

    assert loop_repository.terminalize_blocked_tasks(limit=10) == 1
    # Idempotent: the dependent is already terminal on a second pass.
    assert loop_repository.terminalize_blocked_tasks(limit=10) == 0

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select status from platform_brain.agent_tasks where task_id=%s",
            (dependent,),
        ).fetchone() == ("unavailable",)
        terminal = connection.execute(
            "select event_type from platform_brain.agent_task_events "
            "where task_id=%s order by seq desc limit 1",
            (dependent,),
        ).fetchone()
    # A dependent that can never be dispatched must still reach a terminal event, or
    # a Brain awaiting it would wait until the Turn budget ran out.
    assert terminal == ("agent.unavailable",)


@pytest.mark.postgres
def test_task_past_its_deadline_is_terminalized_by_the_reaper(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop
    assert _runtime(loop_repository, _delegate_response()).advance_one() is True
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_brain.agent_tasks set effective_deadline_at="
            "clock_timestamp()-interval '1 second' where loop_id=%s",
            (loop_id,),
        )

    # Nothing enforced effective_deadline_at before this, so a stalled Agent simply
    # held the Turn open until its whole budget was gone.
    assert loop_repository.expire_task_deadlines(limit=10) == 1
    assert loop_repository.expire_task_deadlines(limit=10) == 0

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select status from platform_brain.agent_tasks where loop_id=%s",
            (loop_id,),
        ).fetchone() == ("timed_out",)


class SerialPoolRegistry(FakeRegistry):
    def authorize_task(self, _user_id, agent_id, expected_capability_version):
        return Decision(execution_pool="metabot_local", pool_concurrency=1)


@pytest.mark.postgres
def test_parallel_delegation_into_a_serial_pool_is_rejected_not_queued(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop

    assert (
        _runtime(
            loop_repository,
            _chain_response({}, 3),
            registry=SerialPoolRegistry(),
        ).advance_one()
        is True
    )

    with psycopg.connect(environment["admin"]) as connection:
        created = connection.execute(
            "select count(*) from platform_brain.agent_tasks where loop_id=%s",
            (loop_id,),
        ).fetchone()[0]
    # Six MetaBot Agents share one executor, so three "parallel" tasks would have
    # queued behind each other while their deadlines ran from the same instant.
    assert created == 1


@pytest.mark.postgres
def test_chained_delegation_into_a_serial_pool_is_admitted(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop

    assert (
        _runtime(
            loop_repository,
            _chain_response({1: [0], 2: [1]}, 3),
            registry=SerialPoolRegistry(),
        ).advance_one()
        is True
    )

    with psycopg.connect(environment["admin"]) as connection:
        created = connection.execute(
            "select count(*) from platform_brain.agent_tasks where loop_id=%s",
            (loop_id,),
        ).fetchone()[0]
    # A chain is serial by construction, so the same three Agents are admissible.
    assert created == 3
