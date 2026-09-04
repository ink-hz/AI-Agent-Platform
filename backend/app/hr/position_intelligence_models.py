from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


ContextState = Literal["draft", "confirmed", "superseded"]
CONTEXT_MODULES = frozenset(
    {
        "mission",
        "jd",
        "jr",
        "competencies",
        "talent_profile",
        "sourcing_strategy",
        "interview_standard",
        "unknowns",
    }
)
TASK_KINDS = frozenset(
    {
        "jd",
        "jr",
        "talent_profile",
        "sourcing_strategy",
        "position_interview_plan",
        "candidate_match",
        "candidate_interview_plan",
        "candidate_comparison",
        "freeform",
    }
)
_JOB_ID = re.compile(r"J[0-9]{4,12}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


def _uuid(value: UUID, message: str = "position intelligence identifiers invalid") -> UUID:
    if not isinstance(value, UUID):
        raise ValueError(message)
    return value


def _optional_uuid(value: UUID | None, message: str = "position intelligence identifiers invalid") -> UUID | None:
    if value is not None:
        _uuid(value, message)
    return value


def _text(value: str, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise ValueError(message)
    normalized = value.strip()
    if not normalized or "\0" in normalized or len(normalized) > maximum:
        raise ValueError(message)
    return normalized


def _optional_text(value: str | None, maximum: int, message: str) -> str | None:
    return None if value is None else _text(value, maximum, message)


def _aware(value: datetime, message: str = "position intelligence timestamp invalid") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(message)
    return value


def _positive(value: int, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(message)
    return value


def _uuids(values: tuple[UUID, ...], message: str) -> tuple[UUID, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > 100
        or any(not isinstance(value, UUID) for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError(message)
    return values


def _object(value: dict[str, object], maximum: int, message: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(message)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError(message)
    return value


def _modules(value: dict[str, object]) -> dict[str, object]:
    _object(value, 512 * 1024, "context modules invalid")
    if not value or not set(value).issubset(CONTEXT_MODULES):
        raise ValueError("context modules invalid")
    for module in value.values():
        if type(module) is not dict:
            raise ValueError("context modules invalid")
    return value


@dataclass(frozen=True, slots=True)
class OfficialPositionVersion:
    official_position_version_id: UUID
    owner_id: UUID
    position_id: UUID
    official_job_id: str
    title: str
    department: str | None
    locations: tuple[str, ...]
    category: str
    subcategory: str | None
    headcount: int
    degree: str | None
    employment_type: str
    salary: str
    duty: str
    requirement: str
    source_version: str
    source_changed_at: datetime
    content_hash: str
    first_observed_at: datetime
    last_observed_at: datetime
    official_status: str
    status_reason: str
    evidence: dict[str, object]
    created_at: datetime

    def __post_init__(self) -> None:
        for value in (self.official_position_version_id, self.owner_id, self.position_id):
            _uuid(value)
        if not isinstance(self.official_job_id, str):
            raise ValueError("official job id invalid")
        job_id = self.official_job_id.strip().upper()
        if _JOB_ID.fullmatch(job_id) is None:
            raise ValueError("official job id invalid")
        object.__setattr__(self, "official_job_id", job_id)
        object.__setattr__(self, "title", _text(self.title, 500, "official title invalid"))
        object.__setattr__(self, "department", _optional_text(self.department, 500, "official department invalid"))
        if not isinstance(self.locations, tuple) or len(self.locations) > 100:
            raise ValueError("official locations invalid")
        object.__setattr__(self, "locations", tuple(_text(item, 500, "official locations invalid") for item in self.locations))
        object.__setattr__(self, "category", _text(self.category, 500, "official category invalid"))
        object.__setattr__(self, "subcategory", _optional_text(self.subcategory, 500, "official subcategory invalid"))
        _positive(self.headcount, "official headcount invalid")
        for name, maximum in (
            ("degree", 500), ("employment_type", 500), ("salary", 1000),
            ("duty", 131072), ("requirement", 131072), ("source_version", 256),
            ("official_status", 64), ("status_reason", 1000),
        ):
            value = getattr(self, name)
            if name == "degree":
                object.__setattr__(self, name, _optional_text(value, maximum, f"{name} invalid"))
            else:
                object.__setattr__(self, name, _text(value, maximum, f"{name} invalid"))
        if _SHA256.fullmatch(self.content_hash) is None:
            raise ValueError("official content hash invalid")
        for value in (self.source_changed_at, self.first_observed_at, self.last_observed_at, self.created_at):
            _aware(value)
        if self.first_observed_at > self.last_observed_at:
            raise ValueError("official observation range invalid")
        _object(self.evidence, 65536, "official evidence invalid")


@dataclass(frozen=True, slots=True)
class PositionContextVersion:
    context_version_id: UUID
    owner_id: UUID
    position_id: UUID
    version_number: int
    state: ContextState
    modules: dict[str, object]
    summary: str
    official_version_id: UUID | None
    base_context_version_id: UUID | None
    source_conversation_id: UUID | None
    source_turn_id: UUID | None
    source_artifact_version_id: UUID | None
    source_material_attachment_ids: tuple[UUID, ...]
    agent_id: str | None
    model_version: str | None
    created_by: UUID
    confirmed_by: UUID | None
    created_at: datetime
    confirmed_at: datetime | None
    row_version: int

    def __post_init__(self) -> None:
        for value in (self.context_version_id, self.owner_id, self.position_id, self.created_by):
            _uuid(value)
        for value in (
            self.official_version_id, self.base_context_version_id,
            self.source_conversation_id, self.source_turn_id,
            self.source_artifact_version_id, self.confirmed_by,
        ):
            _optional_uuid(value)
        _positive(self.version_number, "context version number invalid")
        _positive(self.row_version, "row version invalid")
        if self.state not in {"draft", "confirmed", "superseded"}:
            raise ValueError("context state invalid")
        _modules(self.modules)
        object.__setattr__(self, "summary", _text(self.summary, 32768, "context summary invalid"))
        _uuids(self.source_material_attachment_ids, "context material identifiers invalid")
        object.__setattr__(self, "agent_id", _optional_text(self.agent_id, 128, "context agent invalid"))
        object.__setattr__(self, "model_version", _optional_text(self.model_version, 160, "context model version invalid"))
        _aware(self.created_at)
        if self.confirmed_at is not None:
            _aware(self.confirmed_at)
        confirmed = self.state in {"confirmed", "superseded"}
        if confirmed != (self.confirmed_by is not None and self.confirmed_at is not None):
            raise ValueError("context confirmation invalid")
        if self.source_turn_id is not None and self.source_conversation_id is None:
            raise ValueError("context source invalid")


@dataclass(frozen=True, slots=True)
class CreateContextDraft:
    owner_id: UUID
    context_version_id: UUID
    position_id: UUID
    base_context_version_id: UUID | None
    official_version_id: UUID | None
    modules: dict[str, object]
    summary: str
    client_request_id: UUID
    source_conversation_id: UUID | None = None
    source_turn_id: UUID | None = None
    source_artifact_version_id: UUID | None = None
    source_material_attachment_ids: tuple[UUID, ...] = ()
    agent_id: str | None = None
    model_version: str | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.context_version_id, self.position_id, self.client_request_id):
            _uuid(value)
        for value in (self.base_context_version_id, self.official_version_id, self.source_conversation_id, self.source_turn_id, self.source_artifact_version_id):
            _optional_uuid(value)
        _modules(self.modules)
        object.__setattr__(self, "summary", _text(self.summary, 32768, "context summary invalid"))
        _uuids(self.source_material_attachment_ids, "context material identifiers invalid")
        if self.source_turn_id is not None and self.source_conversation_id is None:
            raise ValueError("context source invalid")
        object.__setattr__(self, "agent_id", _optional_text(self.agent_id, 128, "context agent invalid"))
        object.__setattr__(self, "model_version", _optional_text(self.model_version, 160, "context model version invalid"))
        creator = self.owner_id if self.created_by is None else self.created_by
        _uuid(creator)
        object.__setattr__(self, "created_by", creator)


@dataclass(frozen=True, slots=True)
class ConfirmContextModules:
    owner_id: UUID
    position_id: UUID
    draft_context_version_id: UUID
    client_request_id: UUID
    expected_current_context_version_id: UUID | None
    expected_draft_row_version: int
    module_names: tuple[str, ...]
    confirmed_by: UUID

    def __post_init__(self) -> None:
        for value in (
            self.owner_id, self.position_id, self.draft_context_version_id,
            self.client_request_id, self.confirmed_by,
        ):
            _uuid(value)
        _optional_uuid(self.expected_current_context_version_id)
        _positive(self.expected_draft_row_version, "row version invalid")
        if (
            not isinstance(self.module_names, tuple)
            or not self.module_names
            or len(set(self.module_names)) != len(self.module_names)
            or not set(self.module_names).issubset(CONTEXT_MODULES)
        ):
            raise ValueError("confirmed modules invalid")


@dataclass(frozen=True, slots=True)
class PositionTaskRecord:
    task_record_id: UUID
    owner_id: UUID
    position_id: UUID
    task_kind: str
    official_version_id: UUID | None
    context_version_id: UUID | None
    material_attachment_ids: tuple[UUID, ...]
    candidate_id: UUID | None
    position_candidate_id: UUID | None
    document_attachment_ids: tuple[UUID, ...]
    human_feedback_ids: tuple[UUID, ...]
    conversation_id: UUID
    turn_id: UUID
    output_artifact_version_id: UUID | None
    draft_context_version_id: UUID | None
    canonical_sha256: str
    prompt_context: str
    client_request_id: UUID
    created_at: datetime

    def __post_init__(self) -> None:
        for value in (self.task_record_id, self.owner_id, self.position_id, self.conversation_id, self.turn_id, self.client_request_id):
            _uuid(value)
        for value in (self.official_version_id, self.context_version_id, self.candidate_id, self.position_candidate_id, self.output_artifact_version_id, self.draft_context_version_id):
            _optional_uuid(value)
        if self.task_kind not in TASK_KINDS:
            raise ValueError("HR task kind invalid")
        for values in (self.material_attachment_ids, self.document_attachment_ids, self.human_feedback_ids):
            _uuids(values, "task input identifiers invalid")
        if _SHA256.fullmatch(self.canonical_sha256) is None:
            raise ValueError("task context hash invalid")
        object.__setattr__(self, "prompt_context", _text(self.prompt_context, 131072, "task prompt context invalid"))
        _aware(self.created_at)


@dataclass(frozen=True, slots=True)
class HrPositionContextEnvelope:
    position_id: UUID
    official_version_id: UUID | None
    context_version_id: UUID | None
    task_kind: str
    material_attachment_ids: tuple[UUID, ...]
    candidate_id: UUID | None
    position_candidate_id: UUID | None
    document_attachment_ids: tuple[UUID, ...]
    human_feedback_ids: tuple[UUID, ...]
    prompt_context: str
    canonical_sha256: str

    def __post_init__(self) -> None:
        _uuid(self.position_id)
        for value in (self.official_version_id, self.context_version_id, self.candidate_id, self.position_candidate_id):
            _optional_uuid(value)
        if self.task_kind not in TASK_KINDS:
            raise ValueError("HR task kind invalid")
        for values in (self.material_attachment_ids, self.document_attachment_ids, self.human_feedback_ids):
            _uuids(values, "task input identifiers invalid")
        object.__setattr__(self, "prompt_context", _text(self.prompt_context, 131072, "task prompt context invalid"))
        if _SHA256.fullmatch(self.canonical_sha256) is None:
            raise ValueError("task context hash invalid")
