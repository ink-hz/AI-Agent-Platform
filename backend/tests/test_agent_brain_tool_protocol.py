from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agent_brain.loop_models import (
    AgentTaskStatus,
    BrainLoopStatus,
    BrainStepStatus,
    NormalizedTaskResult,
)
from app.agent_brain.tool_protocol import (
    BRAIN_TOOL_SCHEMAS,
    BrainToolBatch,
    DelegateTaskCall,
    ProtocolViolation,
    SubmitAnswerCall,
    ToolLimits,
    parse_tool_batch,
    stable_runtime_id,
)


def _delegate_block(
    index: int,
    *,
    attachment_refs: list[str] | None = None,
    public_reason: str = "需要专业 Agent 处理",
) -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": f"toolu_{index}",
        "name": "delegate_task",
        "input": {
            "agent_id": "hr-bot",
            "objective": f"判断候选人 {index} 的岗位匹配度",
            "context_excerpt": ["岗位要求视觉技术和硬件产品经验"],
            "constraints": ["不联系候选人"],
            "attachment_refs": attachment_refs or [],
            "expected_output": "判断、证据、风险和面试问题",
            "public_reason": public_reason,
        },
    }


def _submit_block(
    *,
    task_ids: list[str] | None = None,
    attachment_refs: list[str] | None = None,
    outcome: str = "resolved",
    answer: str = "已完成。",
) -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": "toolu_submit",
        "name": "submit_answer",
        "input": {
            "answer_markdown": answer,
            "outcome": outcome,
            "used_task_ids": task_ids or [],
            "attachment_refs": attachment_refs or [],
            "public_reason": "整合已有结果并正式交付",
        },
    }


def test_delegate_batch_accepts_first_four_and_pairs_every_call() -> None:
    batch = parse_tool_batch(
        [_delegate_block(index) for index in range(6)],
        ToolLimits(max_parallel_tasks=4),
    )

    assert isinstance(batch, BrainToolBatch)
    assert batch.kind == "delegate_tasks"
    assert [call.accepted for call in batch.calls] == [True] * 4 + [False] * 2
    assert [call.result_status for call in batch.calls[-2:]] == [
        "rejected_over_parallel_limit",
        "rejected_over_parallel_limit",
    ]
    assert [call.tool_index for call in batch.calls] == list(range(6))
    assert all(isinstance(call.call, DelegateTaskCall) for call in batch.calls)


def test_zero_tool_use_discards_free_text_and_is_a_protocol_violation() -> None:
    with pytest.raises(ProtocolViolation) as error:
        parse_tool_batch(
            [{"type": "text", "text": "这是不能作为交付的草稿"}],
            ToolLimits(),
        )

    assert error.value.code == "zero_tool_use"
    assert "草稿" not in str(error.value)


@pytest.mark.parametrize(
    ("blocks", "code"),
    [
        (
            [
                {
                    "type": "tool_use",
                    "id": "toolu_bad",
                    "name": "cancel_task",
                    "input": {"public_reason": "取消"},
                }
            ],
            "unknown_tool",
        ),
        ([_delegate_block(1), _delegate_block(1)], "duplicate_tool_call_id"),
        (
            [
                _delegate_block(1),
                {
                    "type": "tool_use",
                    "id": "toolu_agents",
                    "name": "list_agents",
                    "input": {"public_reason": "查看可用 Agent"},
                },
            ],
            "mixed_tool_batch",
        ),
    ],
)
def test_unknown_duplicate_and_mixed_tools_fail_with_stable_codes(
    blocks: list[dict[str, object]], code: str
) -> None:
    with pytest.raises(ProtocolViolation) as error:
        parse_tool_batch(blocks, ToolLimits())
    assert error.value.code == code


@pytest.mark.parametrize("reason", ["", "   ", "界" * 171, "bad\ud800"])
def test_public_reason_must_be_nonempty_valid_utf8_and_at_most_512_bytes(
    reason: str,
) -> None:
    with pytest.raises(ProtocolViolation) as error:
        parse_tool_batch([_delegate_block(1, public_reason=reason)], ToolLimits())
    assert error.value.code == "invalid_tool_input"


def test_task_and_attachment_references_must_belong_to_current_loop() -> None:
    owned_task = uuid4()
    foreign_task = uuid4()
    owned_attachment = uuid4()
    foreign_attachment = uuid4()
    limits = ToolLimits(
        allowed_task_ids=frozenset({owned_task}),
        allowed_attachment_refs=frozenset({owned_attachment}),
    )

    accepted = parse_tool_batch(
        [
            _submit_block(
                task_ids=[str(owned_task)],
                attachment_refs=[str(owned_attachment)],
            )
        ],
        limits,
    )
    submit = accepted.calls[0].call
    assert isinstance(submit, SubmitAnswerCall)
    assert submit.used_task_ids == (owned_task,)
    assert submit.attachment_refs == (owned_attachment,)

    for block in (
        _submit_block(task_ids=[str(foreign_task)]),
        _submit_block(attachment_refs=[str(foreign_attachment)]),
        _delegate_block(1, attachment_refs=[str(foreign_attachment)]),
    ):
        with pytest.raises(ProtocolViolation) as error:
            parse_tool_batch([block], limits)
        assert error.value.code == "reference_not_owned"


@pytest.mark.parametrize(
    "outcome", ["complete", "partial", "refused", "", "RESOLVED"]
)
def test_submit_answer_accepts_only_the_exact_outcome_enum(outcome: str) -> None:
    with pytest.raises(ProtocolViolation) as error:
        parse_tool_batch([_submit_block(outcome=outcome)], ToolLimits())
    assert error.value.code == "invalid_tool_input"


def test_answer_and_total_argument_utf8_limits_are_enforced() -> None:
    with pytest.raises(ProtocolViolation) as answer_error:
        parse_tool_batch(
            [_submit_block(answer="中" * 22)],
            ToolLimits(max_answer_bytes=64),
        )
    assert answer_error.value.code == "invalid_tool_input"

    with pytest.raises(ProtocolViolation) as argument_error:
        parse_tool_batch(
            [_delegate_block(1)],
            ToolLimits(max_tool_argument_bytes=32),
        )
    assert argument_error.value.code == "tool_arguments_too_large"


def test_stable_runtime_ids_are_deterministic_distinct_and_validate_shape() -> None:
    loop_id = UUID("00000000-0000-4000-8000-000000000001")
    first = stable_runtime_id(loop_id, 2, 3, "task")

    assert first == stable_runtime_id(loop_id, 2, 3, "task")
    assert first != stable_runtime_id(loop_id, 2, 3, "delivery")
    assert first != stable_runtime_id(loop_id, 2, 4, "task")
    for values in ((0, 0, "task"), (1, -1, "task"), (1, 0, "other")):
        with pytest.raises(ValueError, match="runtime identity invalid"):
            stable_runtime_id(loop_id, *values)


def test_public_tool_schema_contains_exactly_four_tools_and_no_cancel() -> None:
    assert [schema["name"] for schema in BRAIN_TOOL_SCHEMAS] == [
        "list_agents",
        "delegate_task",
        "request_user_input",
        "submit_answer",
    ]
    rendered = repr(BRAIN_TOOL_SCHEMAS)
    assert "cancel_task" not in rendered
    assert "public_reason" in rendered


def test_runtime_models_are_frozen_and_use_exact_status_values() -> None:
    assert {status.value for status in BrainLoopStatus} >= {
        "queued",
        "waiting_agents",
        "waiting_user",
        "completing",
        "interrupted",
    }
    assert {status.value for status in BrainStepStatus} >= {
        "leased",
        "requesting_model",
        "waiting_tool_results",
    }
    assert {status.value for status in AgentTaskStatus} >= {
        "timed_out",
        "unavailable",
    }

    result = NormalizedTaskResult(
        status="completed",
        summary="候选人具备跨阶段能力组合",
        deliverables=("人才判断",),
        evidence=("外企英文环境",),
        limitations=(),
        attachment_refs=(),
    )
    with pytest.raises(ValidationError):
        result.summary = "被篡改"


def test_tool_models_forbid_unknown_fields_and_non_string_scalars() -> None:
    block = _delegate_block(1)
    tool_input = block["input"]
    assert isinstance(tool_input, dict)
    tool_input["unexpected"] = True
    with pytest.raises(ProtocolViolation) as extra_error:
        parse_tool_batch([block], ToolLimits())
    assert extra_error.value.code == "invalid_tool_input"

    block = _delegate_block(2)
    tool_input = block["input"]
    assert isinstance(tool_input, dict)
    tool_input["objective"] = 123
    with pytest.raises(ProtocolViolation) as strict_error:
        parse_tool_batch([block], ToolLimits())
    assert strict_error.value.code == "invalid_tool_input"
