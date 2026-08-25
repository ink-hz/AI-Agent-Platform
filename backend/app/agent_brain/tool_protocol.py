from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import ClassVar, Literal, TypeAlias
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.agent_brain.loop_models import _require_utf8_text


_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOOL_CALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_RUNTIME_KINDS = frozenset({"tool_call", "task", "delivery"})
_OUTCOMES = frozenset({"resolved", "partially_completed", "safe_abstained"})


class ProtocolViolation(RuntimeError):
    """Stable, non-sensitive failure raised for model protocol violations."""

    _CODES: ClassVar[frozenset[str]] = frozenset(
        {
            "zero_tool_use",
            "invalid_content_block",
            "unknown_tool",
            "duplicate_tool_call_id",
            "mixed_tool_batch",
            "invalid_tool_input",
            "tool_arguments_too_large",
            "reference_not_owned",
            "target_not_allowed",
            "target_not_active",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("protocol violation code invalid")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ToolLimits:
    max_parallel_tasks: int = 4
    max_tool_argument_bytes: int = 128 * 1024
    max_answer_bytes: int = 64 * 1024
    allowed_agent_ids: frozenset[str] | None = None
    allowed_task_ids: frozenset[UUID] | None = None
    active_task_ids: frozenset[UUID] | None = None
    allowed_attachment_refs: frozenset[UUID] | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.max_parallel_tasks <= 32:
            raise ValueError("parallel task limit invalid")
        if not 1 <= self.max_tool_argument_bytes <= 1024 * 1024:
            raise ValueError("tool argument limit invalid")
        if not 1 <= self.max_answer_bytes <= 1024 * 1024:
            raise ValueError("answer limit invalid")
        if self.allowed_agent_ids is not None and any(
            not _valid_agent_id(agent_id) for agent_id in self.allowed_agent_ids
        ):
            raise ValueError("allowed Agent IDs invalid")
        for references in (
            self.allowed_task_ids,
            self.active_task_ids,
            self.allowed_attachment_refs,
        ):
            if references is not None and any(
                not isinstance(reference, UUID) for reference in references
            ):
                raise ValueError("allowed references invalid")
        if (
            self.active_task_ids is not None
            and self.allowed_task_ids is not None
            and not self.active_task_ids.issubset(self.allowed_task_ids)
        ):
            raise ValueError("active Task IDs invalid")


class _StrictToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    public_reason: str

    @field_validator("public_reason")
    @classmethod
    def _public_reason_is_bounded(cls, value: str) -> str:
        _require_utf8_text(value, minimum=1, maximum=512)
        return value


class ListAgentsCall(_StrictToolCall):
    pass


class DelegateTaskCall(_StrictToolCall):
    agent_id: str
    objective: str
    context_excerpt: tuple[str, ...]
    constraints: tuple[str, ...]
    attachment_refs: tuple[UUID, ...]
    expected_output: str

    @field_validator("agent_id")
    @classmethod
    def _agent_id_is_valid(cls, value: str) -> str:
        if not _valid_agent_id(value):
            raise ValueError("Agent ID invalid")
        return value

    @field_validator("objective")
    @classmethod
    def _objective_is_bounded(cls, value: str) -> str:
        _require_utf8_text(value, minimum=1, maximum=8192)
        return value

    @field_validator("expected_output")
    @classmethod
    def _expected_output_is_bounded(cls, value: str) -> str:
        _require_utf8_text(value, minimum=1, maximum=4096)
        return value

    @field_validator("context_excerpt")
    @classmethod
    def _context_is_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _bounded_text_tuple(value, maximum_items=32, maximum_bytes=16384)

    @field_validator("constraints")
    @classmethod
    def _constraints_are_bounded(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _bounded_text_tuple(value, maximum_items=32, maximum_bytes=4096)

    @field_validator("attachment_refs")
    @classmethod
    def _attachments_are_bounded(
        cls, value: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        return _bounded_uuid_tuple(value, maximum_items=32)


class RequestUserInputCall(_StrictToolCall):
    question: str

    @field_validator("question")
    @classmethod
    def _question_is_bounded(cls, value: str) -> str:
        _require_utf8_text(value, minimum=1, maximum=8192)
        return value


class AwaitAgentEventsCall(_StrictToolCall):
    task_ids: tuple[UUID, ...]
    wake_on: tuple[
        Literal["question", "finding", "result", "failed", "timeout"], ...
    ]

    @field_validator("task_ids")
    @classmethod
    def _tasks_are_bounded(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if not value:
            raise ValueError("at least one Task is required")
        return _bounded_uuid_tuple(value, maximum_items=8)

    @field_validator("wake_on")
    @classmethod
    def _wake_kinds_are_exact(
        cls,
        value: tuple[
            Literal["question", "finding", "result", "failed", "timeout"], ...
        ],
    ) -> tuple[
        Literal["question", "finding", "result", "failed", "timeout"], ...
    ]:
        if not value or len(set(value)) != len(value):
            raise ValueError("wake kinds invalid")
        return value


class SendAgentMessageCall(_StrictToolCall):
    task_id: UUID
    message: str

    @field_validator("message")
    @classmethod
    def _message_is_bounded(cls, value: str) -> str:
        _require_utf8_text(value, minimum=1, maximum=16 * 1024)
        return value


class StopAgentTaskCall(_StrictToolCall):
    task_id: UUID
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_is_bounded(cls, value: str) -> str:
        _require_utf8_text(value, minimum=1, maximum=4096)
        return value


class SubmitAnswerCall(_StrictToolCall):
    answer_markdown: str
    outcome: Literal["resolved", "partially_completed", "safe_abstained"]
    used_task_ids: tuple[UUID, ...]
    attachment_refs: tuple[UUID, ...]

    @field_validator("answer_markdown")
    @classmethod
    def _answer_is_nonempty(cls, value: str) -> str:
        _require_utf8_text(value, minimum=1, maximum=1024 * 1024)
        return value

    @field_validator("used_task_ids")
    @classmethod
    def _tasks_are_bounded(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return _bounded_uuid_tuple(value, maximum_items=128)

    @field_validator("attachment_refs")
    @classmethod
    def _attachments_are_bounded(
        cls, value: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        return _bounded_uuid_tuple(value, maximum_items=32)


ToolCall: TypeAlias = (
    ListAgentsCall
    | DelegateTaskCall
    | AwaitAgentEventsCall
    | SendAgentMessageCall
    | StopAgentTaskCall
    | RequestUserInputCall
    | SubmitAnswerCall
)


@dataclass(frozen=True, slots=True)
class ParsedToolCall:
    provider_tool_call_id: str
    tool_index: int
    name: Literal[
        "list_agents",
        "delegate_task",
        "await_agent_events",
        "send_agent_message",
        "stop_agent_task",
        "request_user_input",
        "submit_answer",
    ]
    call: ToolCall
    accepted: bool = True
    result_status: Literal["rejected_over_parallel_limit"] | None = None


@dataclass(frozen=True, slots=True)
class BrainToolBatch:
    kind: Literal[
        "list_agents",
        "delegate_tasks",
        "await_agent_events",
        "agent_messages",
        "stop_agent_task",
        "request_user_input",
        "submit_answer",
    ]
    calls: tuple[ParsedToolCall, ...]


_TOOL_MODELS: dict[str, type[_StrictToolCall]] = {
    "list_agents": ListAgentsCall,
    "delegate_task": DelegateTaskCall,
    "await_agent_events": AwaitAgentEventsCall,
    "send_agent_message": SendAgentMessageCall,
    "stop_agent_task": StopAgentTaskCall,
    "request_user_input": RequestUserInputCall,
    "submit_answer": SubmitAnswerCall,
}


def _tool_schema(name: str, model: type[_StrictToolCall], description: str) -> dict[str, object]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return {"name": name, "description": description, "input_schema": schema}


BRAIN_TOOL_SCHEMAS: tuple[dict[str, object], ...] = (
    _tool_schema(
        "list_agents",
        ListAgentsCall,
        "List currently authorized professional Agents and their availability.",
    ),
    _tool_schema(
        "delegate_task",
        DelegateTaskCall,
        "Delegate one bounded task to one authorized professional Agent.",
    ),
    _tool_schema(
        "await_agent_events",
        AwaitAgentEventsCall,
        "Wait for real events from owned professional-Agent tasks.",
    ),
    _tool_schema(
        "send_agent_message",
        SendAgentMessageCall,
        "Send a bounded follow-up message to one active professional-Agent task.",
    ),
    _tool_schema(
        "stop_agent_task",
        StopAgentTaskCall,
        "Request cancellation of one owned active professional-Agent task.",
    ),
    _tool_schema(
        "request_user_input",
        RequestUserInputCall,
        "Ask one focused question required to continue the current turn.",
    ),
    _tool_schema(
        "submit_answer",
        SubmitAnswerCall,
        "Submit the final user-visible answer and complete the current turn.",
    ),
)


def parse_tool_batch(
    content_blocks: object, limits: ToolLimits
) -> BrainToolBatch:
    if type(content_blocks) not in (list, tuple):
        raise ProtocolViolation("invalid_content_block")

    parsed: list[ParsedToolCall] = []
    seen_provider_ids: set[str] = set()
    for block in content_blocks:
        if type(block) is not dict:
            raise ProtocolViolation("invalid_content_block")
        if block.get("type") != "tool_use":
            continue
        if set(block) != {"type", "id", "name", "input"}:
            raise ProtocolViolation("invalid_content_block")
        provider_id = block.get("id")
        tool_name = block.get("name")
        raw_arguments = block.get("input")
        if (
            type(provider_id) is not str
            or _TOOL_CALL_ID.fullmatch(provider_id) is None
            or type(tool_name) is not str
            or type(raw_arguments) is not dict
        ):
            raise ProtocolViolation("invalid_content_block")
        if provider_id in seen_provider_ids:
            raise ProtocolViolation("duplicate_tool_call_id")
        seen_provider_ids.add(provider_id)
        model = _TOOL_MODELS.get(tool_name)
        if model is None:
            raise ProtocolViolation("unknown_tool")
        if _json_utf8_size(raw_arguments) > limits.max_tool_argument_bytes:
            raise ProtocolViolation("tool_arguments_too_large")
        try:
            arguments = model.model_validate(_normalize_arguments(tool_name, raw_arguments))
        except (TypeError, UnicodeError, ValueError, ValidationError):
            raise ProtocolViolation("invalid_tool_input") from None
        _validate_runtime_limits(arguments, limits)
        parsed.append(
            ParsedToolCall(
                provider_tool_call_id=provider_id,
                tool_index=len(parsed),
                name=tool_name,
                call=arguments,
            )
        )

    if not parsed:
        raise ProtocolViolation("zero_tool_use")
    names = {call.name for call in parsed}
    if len(names) != 1 or (
        next(iter(names)) not in {"delegate_task", "send_agent_message"}
        and len(parsed) != 1
    ):
        raise ProtocolViolation("mixed_tool_batch")

    tool_name = parsed[0].name
    if tool_name == "delegate_task":
        parsed = [
            call
            if index < limits.max_parallel_tasks
            else ParsedToolCall(
                provider_tool_call_id=call.provider_tool_call_id,
                tool_index=call.tool_index,
                name=call.name,
                call=call.call,
                accepted=False,
                result_status="rejected_over_parallel_limit",
            )
            for index, call in enumerate(parsed)
        ]
        kind = "delegate_tasks"
    elif tool_name == "send_agent_message":
        kind = "agent_messages"
    else:
        kind = tool_name
    return BrainToolBatch(kind=kind, calls=tuple(parsed))  # type: ignore[arg-type]


def stable_runtime_id(
    loop_id: UUID, step_seq: int, tool_index: int, kind: str
) -> UUID:
    if (
        type(loop_id) is not UUID
        or type(step_seq) is not int
        or step_seq < 1
        or type(tool_index) is not int
        or tool_index < 0
        or kind not in _RUNTIME_KINDS
    ):
        raise ValueError("runtime identity invalid")
    return uuid5(loop_id, f"{step_seq}:{tool_index}:{kind}")


def _normalize_arguments(name: str, raw: dict[object, object]) -> dict[object, object]:
    normalized = dict(raw)
    tuple_fields: tuple[str, ...] = ()
    uuid_fields: tuple[str, ...] = ()
    if name == "delegate_task":
        tuple_fields = ("context_excerpt", "constraints", "attachment_refs")
        uuid_fields = ("attachment_refs",)
    elif name == "await_agent_events":
        tuple_fields = ("task_ids", "wake_on")
        uuid_fields = ("task_ids",)
    elif name in {"send_agent_message", "stop_agent_task"}:
        normalized["task_id"] = _strict_uuid(normalized.get("task_id"))
    elif name == "submit_answer":
        tuple_fields = ("used_task_ids", "attachment_refs")
        uuid_fields = tuple_fields
    for field in tuple_fields:
        value = normalized.get(field)
        if type(value) is not list:
            raise TypeError
        normalized[field] = tuple(value)
    for field in uuid_fields:
        value = normalized[field]
        assert isinstance(value, tuple)
        normalized[field] = tuple(_strict_uuid(member) for member in value)
    return normalized


def _validate_runtime_limits(arguments: ToolCall, limits: ToolLimits) -> None:
    if isinstance(arguments, DelegateTaskCall):
        if (
            limits.allowed_agent_ids is not None
            and arguments.agent_id not in limits.allowed_agent_ids
        ):
            raise ProtocolViolation("target_not_allowed")
        _require_owned(
            arguments.attachment_refs,
            limits.allowed_attachment_refs,
        )
    elif isinstance(arguments, SubmitAnswerCall):
        try:
            answer_size = len(arguments.answer_markdown.encode("utf-8"))
        except UnicodeError:
            raise ProtocolViolation("invalid_tool_input") from None
        if answer_size > limits.max_answer_bytes:
            raise ProtocolViolation("invalid_tool_input")
        _require_owned(arguments.used_task_ids, limits.allowed_task_ids)
        _require_owned(
            arguments.attachment_refs,
            limits.allowed_attachment_refs,
        )
    elif isinstance(arguments, AwaitAgentEventsCall):
        _require_owned(arguments.task_ids, limits.allowed_task_ids)
    elif isinstance(arguments, (SendAgentMessageCall, StopAgentTaskCall)):
        _require_owned((arguments.task_id,), limits.allowed_task_ids)
        if (
            limits.active_task_ids is not None
            and arguments.task_id not in limits.active_task_ids
        ):
            raise ProtocolViolation("target_not_active")


def _require_owned(
    references: tuple[UUID, ...], allowed: frozenset[UUID] | None
) -> None:
    if allowed is not None and not set(references).issubset(allowed):
        raise ProtocolViolation("reference_not_owned")


def _strict_uuid(value: object) -> UUID:
    if type(value) is not str:
        raise TypeError
    parsed = UUID(value)
    if str(parsed) != value.lower():
        raise ValueError
    return parsed


def _bounded_text_tuple(
    value: tuple[str, ...], *, maximum_items: int, maximum_bytes: int
) -> tuple[str, ...]:
    if len(value) > maximum_items:
        raise ValueError("too many text items")
    for member in value:
        _require_utf8_text(member, minimum=1, maximum=maximum_bytes)
    return value


def _bounded_uuid_tuple(
    value: tuple[UUID, ...], *, maximum_items: int
) -> tuple[UUID, ...]:
    if len(value) > maximum_items or len(set(value)) != len(value):
        raise ValueError("UUID references invalid")
    return value


def _json_utf8_size(value: object) -> int:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return len(rendered.encode("utf-8"))
    except (TypeError, UnicodeError, ValueError):
        raise ProtocolViolation("invalid_tool_input") from None


def _valid_agent_id(value: object) -> bool:
    return type(value) is str and _AGENT_ID.fullmatch(value) is not None


assert set(_TOOL_MODELS) == {
    "list_agents",
    "delegate_task",
    "await_agent_events",
    "send_agent_message",
    "stop_agent_task",
    "request_user_input",
    "submit_answer",
}
assert _OUTCOMES == {"resolved", "partially_completed", "safe_abstained"}
