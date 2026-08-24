from __future__ import annotations

from uuid import uuid4

from app.agent_brain.context_policy import BrainContextPolicy
from app.agent_brain.tool_protocol import DelegateTaskCall


def _call(*, attachment_refs=()):
    return DelegateTaskCall(
        agent_id="hr-bot",
        objective="判断候选人",
        context_excerpt=("岗位需要视觉经验",),
        constraints=("不联系候选人",),
        attachment_refs=attachment_refs,
        expected_output="给出判断和证据",
        public_reason="需要专业判断",
    )


def test_child_agent_receives_only_explicit_excerpt_and_allowed_attachments() -> None:
    allowed, denied = uuid4(), uuid4()
    policy = BrainContextPolicy(attachment_authorized=lambda ref: ref == allowed)
    context = policy.build_task_context(
        _call(attachment_refs=(allowed, denied))
    )

    assert "岗位需要视觉经验" in context.serialized
    assert "其他轮敏感内容" not in context.serialized
    assert str(allowed) in context.serialized
    assert str(denied) not in context.serialized
    assert context.omissions[0].reason == "not_authorized"


def test_long_brain_context_has_explicit_model_visible_truncation_marker() -> None:
    policy = BrainContextPolicy(max_brain_bytes=1024)
    context = policy.build_brain_context(
        tuple({"role": "user", "content": str(index) + "长" * 300} for index in range(8))
    )

    assert context.omissions[0].reason == "context_truncated"
    assert context.messages[0]["role"] == "system"
    assert "上下文截断" in context.messages[0]["content"]
    assert len(context.serialized.encode("utf-8")) <= 1024
