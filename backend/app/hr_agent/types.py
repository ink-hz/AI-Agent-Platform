"""Runtime-owned wire validation and immutable execution identities."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

_SCHEMA = json.loads(Path(__file__).with_name("contracts.schema.json").read_text())
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time")
def _rfc3339_datetime(value):
    # jsonschema's optional RFC3339 dependency is deliberately unnecessary here.
    if not isinstance(value, str):
        return True
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
        value,
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        return parsed.tzinfo is not None
    except ValueError:
        return False


_VALIDATORS = {
    name: Draft202012Validator(
        {"$defs": _SCHEMA["$defs"], "$ref": f"#/$defs/{name}"},
        format_checker=_FORMAT_CHECKER,
    )
    for name in _SCHEMA["$defs"]
}


class HrAgentProblem(RuntimeError):
    def __init__(self, problem: dict, http_status: int = 422):
        self.problem = problem
        self.http_status = http_status
        super().__init__(problem["code"])


def problem(code, message=None, retryable=False, details=None, http_status=None):
    return HrAgentProblem(
        {
            "code": code,
            "message": message or code.replace("_", " "),
            "retryable": retryable,
            "details": details or {},
        },
        http_status or 422,
    )


def validate_contract(name: str, value: Any) -> dict:
    if name not in _VALIDATORS or not _VALIDATORS[name].is_valid(value):
        raise problem("invalid_input")
    if (
        name == "ResourceText"
        and not 0 <= value["offset"] <= value["end"] <= value["total_characters"]
    ):
        raise problem("invalid_input")
    return copy.deepcopy(value)


TOOL_INPUTS = {
    "list_resources": "ListResourcesInput",
    "read_resource": "ReadResourceInput",
    "save_note": "SaveNoteInput",
    "save_result": "SaveResultInput",
    "ask_user": "AskUserInput",
}


def validate_tool_arguments(name: str, value: Any) -> dict:
    if name not in TOOL_INPUTS:
        raise problem("invalid_input")
    return validate_contract(TOOL_INPUTS[name], value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda v: (
            str(v) if isinstance(v, UUID) else (_ for _ in ()).throw(TypeError())
        ),
        allow_nan=False,
    )


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


# Wire values are copied and validated at boundaries, never arbitrary database SQL.
WorkInput = AppendInput = WorkView = ExactRef = ObjectRef = Problem = dict
SummaryProvenance = WorkCheckpoint = MaterialView = ResultBasis = dict
ToolOutcome = ResourceText = ResourcePage = ResourceItem = dict
ListResourcesInput = ReadResourceInput = SaveNoteInput = SaveResultInput = (
    AskUserInput
) = dict
ExtendBudgetInput = ResultView = ResultPage = LinkResultInput = LinkReceipt = dict
ThreadPage = WorkPage = MessagePage = EventPage = ProgressEvent = dict
OrdinaryLogRecord = ConfirmInput = StandardView = dict


@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str
    profile_revision: str


@dataclass(frozen=True)
class LeaseFence:
    work_id: UUID
    input_revision: int
    epoch: int
    worker_id: str


@dataclass(frozen=True)
class AuthorizedScope:
    owner_id: UUID
    work_id: UUID | None
    objects: tuple[ObjectRef, ...]
    selected_refs: tuple[ExactRef, ...]


@dataclass(frozen=True)
class ModelContext:
    purpose: str
    messages: tuple[dict, ...]
    tools: tuple[dict, ...]
    dependencies: tuple[ExactRef, ...]
    estimated_input_tokens: int
    input_revision: int
    role: str = ""
    summary_provenance: SummaryProvenance | None = None


@dataclass(frozen=True)
class ModelRequest:
    attempt_id: UUID
    purpose: str
    profile_id: str
    messages: tuple[dict, ...]
    tools: tuple[dict, ...]
    max_output_tokens: int
    deadline_seconds: float


@dataclass(frozen=True)
class ModelEvent:
    type: str
    payload: dict

    def __post_init__(self):
        if self.type not in {
            "text_delta",
            "tool_delta",
            "usage",
            "stop",
        } or not isinstance(self.payload, dict):
            raise ValueError("invalid model event")


@dataclass(frozen=True)
class ToolCall:
    provider_call_id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class Usage:
    raw: dict | None
    input_total: int | None
    output_total: int | None
    quality: str

    def __post_init__(self):
        if self.quality not in {"unknown", "estimated", "reported"}:
            raise ValueError("invalid usage quality")
        if any(
            v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0)
            for v in (self.input_total, self.output_total)
        ):
            raise ValueError("invalid usage amount")


@dataclass(frozen=True)
class ModelReply:
    text: str
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str
    usage: Usage


@dataclass(frozen=True)
class RuntimeAction:
    kind: str
    attempt_id: UUID | None = None
    operation_id: UUID | None = None

    def __post_init__(self):
        if self.kind not in {
            "build_context",
            "resume_prepared",
            "execute_tool",
            "project_answer",
            "project_summary",
            "wait",
            "done",
        }:
            raise ValueError("invalid runtime action")


@dataclass(frozen=True)
class StoredToolOperation:
    operation_id: UUID
    name: str
    arguments: dict
    status: str
    receipt: dict | None


@dataclass(frozen=True)
class ScopedEntry:
    entry_id: UUID
    seq: int
    kind: str
    body: dict
    objects: tuple[ObjectRef, ...]
    source_refs: tuple[ExactRef, ...]
    input_revision: int
    summary_provenance: SummaryProvenance | None = None


@dataclass(frozen=True)
class MaterialText:
    ref: ExactRef
    original_ref: ExactRef
    parser_release: str
    text: str
    coverage_complete: bool


@dataclass(frozen=True)
class DiagnosticIdentity:
    actor_id: UUID
    roles: tuple[str, ...]


@dataclass(frozen=True)
class DiagnosticRecord:
    diagnostic_id: UUID
    work_id: UUID
    expires_at: datetime
    sealed_payload: Any = field(repr=False)


@dataclass(frozen=True)
class ResultQuery:
    thread_id: UUID | None = None
    object_ref: ObjectRef | None = None
    kind: str | None = None
    cursor: str | None = None

    def __post_init__(self):
        if (self.thread_id is None) == (self.object_ref is None):
            raise problem("invalid_input")


class ContextRebuildRequired(RuntimeError):
    pass


class WorkPaused(RuntimeError):
    def __init__(self, view: WorkView):
        self.view = view
        super().__init__("work paused")
