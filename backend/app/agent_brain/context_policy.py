from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable
from uuid import UUID

from app.agent_brain.tool_protocol import DelegateTaskCall


@dataclass(frozen=True, slots=True)
class ContextOmission:
    kind: str
    reason: str


@dataclass(frozen=True, slots=True)
class BrainContext:
    messages: tuple[dict[str, object], ...]
    omissions: tuple[ContextOmission, ...]

    @property
    def serialized(self) -> str:
        return json.dumps(self.messages, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class TaskContext:
    context_excerpt: tuple[str, ...]
    constraints: tuple[str, ...]
    attachment_refs: tuple[UUID, ...]
    expected_output: str
    omissions: tuple[ContextOmission, ...]

    @property
    def serialized(self) -> str:
        return json.dumps(
            {
                "context_excerpt": self.context_excerpt,
                "constraints": self.constraints,
                "attachment_refs": tuple(map(str, self.attachment_refs)),
                "expected_output": self.expected_output,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class BrainContextPolicy:
    def __init__(
        self,
        *,
        max_brain_bytes: int = 96 * 1024,
        attachment_authorized: Callable[[UUID], bool] | None = None,
    ) -> None:
        if type(max_brain_bytes) is not int or max_brain_bytes < 1024:
            raise ValueError("Brain context limit invalid")
        self._max_brain_bytes = max_brain_bytes
        self._attachment_authorized = attachment_authorized or (lambda _ref: True)

    def build_brain_context(
        self, messages: tuple[dict[str, object], ...]
    ) -> BrainContext:
        if type(messages) is not tuple:
            raise ValueError("Brain context messages invalid")
        kept = list(messages)
        omitted = 0
        while kept and _size(tuple(kept)) > self._max_brain_bytes:
            kept.pop(0)
            omitted += 1
        omissions: tuple[ContextOmission, ...] = ()
        if omitted:
            marker = {
                "role": "system",
                "content": f"【上下文截断】更早的 {omitted} 条消息未注入。",
            }
            kept.insert(0, marker)
            while len(kept) > 1 and _size(tuple(kept)) > self._max_brain_bytes:
                kept.pop(1)
                omitted += 1
                marker["content"] = f"【上下文截断】更早的 {omitted} 条消息未注入。"
            omissions = (ContextOmission("conversation", "context_truncated"),)
        return BrainContext(tuple(kept), omissions)

    def build_task_context(self, call: DelegateTaskCall) -> TaskContext:
        if not isinstance(call, DelegateTaskCall):
            raise ValueError("delegate task call required")
        accepted: list[UUID] = []
        omissions: list[ContextOmission] = []
        for attachment_ref in call.attachment_refs:
            if self._attachment_authorized(attachment_ref):
                accepted.append(attachment_ref)
            else:
                omissions.append(ContextOmission("attachment", "not_authorized"))
        return TaskContext(
            context_excerpt=call.context_excerpt,
            constraints=call.constraints,
            attachment_refs=tuple(accepted),
            expected_output=call.expected_output,
            omissions=tuple(omissions),
        )


def _size(messages: tuple[dict[str, object], ...]) -> int:
    return len(
        json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )
