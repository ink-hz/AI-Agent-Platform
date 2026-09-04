from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

PositionSource = Literal["official_site", "manual"]
OfficialStatus = Literal["active", "stale", "suspected_inactive", "inactive"]
InternalStatus = Literal["draft", "active", "archived"]
DraftSource = Literal["historical_conversation", "new_conversation"]
DraftState = Literal["proposed", "confirmed", "merged", "dismissed"]
BindingKind = Literal[
    "created_in_position",
    "draft_confirmed",
    "draft_merged",
    "historical_exact",
    "manual_correction",
]

_JOB_ID = re.compile(r"(?:J[0-9]{4,12}|JOBAD:[0-9]{1,20})\Z")
_POSITION_SOURCES = {"official_site", "manual"}
_OFFICIAL_STATUSES = {"active", "stale", "suspected_inactive", "inactive"}
_INTERNAL_STATUSES = {"draft", "active", "archived"}
_DRAFT_SOURCES = {"historical_conversation", "new_conversation"}
_DRAFT_STATES = {"proposed", "confirmed", "merged", "dismissed"}
_BINDING_KINDS = {
    "created_in_position",
    "draft_confirmed",
    "draft_merged",
    "historical_exact",
    "manual_correction",
}


def _uuid(value: UUID, message: str) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError(message)
    return value


def _text(value: str, *, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise ValueError(message)
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\0" in normalized:
        raise ValueError(message)
    return normalized


def _optional_text(
    value: str | None, *, maximum: int, message: str
) -> str | None:
    if value is None:
        return None
    return _text(value, maximum=maximum, message=message)


def _strings(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > 100:
        raise ValueError("position locations invalid")
    return tuple(
        _text(value, maximum=500, message="position locations invalid")
        for value in values
    )


def _json_object(value: dict[str, object], maximum: int, message: str):
    if type(value) is not dict:
        raise ValueError(message)
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if len(raw.encode("utf-8")) > maximum:
        raise ValueError(message)
    return value


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("position timestamp invalid")
    return value


def _row_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("row version invalid")
    return value


@dataclass(frozen=True, slots=True)
class PositionRecord:
    position_id: UUID
    owner_id: UUID
    source_kind: PositionSource
    official_job_id: str | None
    title: str
    department: str | None
    locations: tuple[str, ...]
    official_status: OfficialStatus | None
    internal_status: InternalStatus
    source_version: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.position_id, "position identifiers invalid")
        _uuid(self.owner_id, "position identifiers invalid")
        if self.source_kind not in _POSITION_SOURCES:
            raise ValueError("position source invalid")
        job_id = self.official_job_id
        if job_id is not None:
            if not isinstance(job_id, str):
                raise ValueError("official job id invalid")
            job_id = job_id.strip().upper()
            if _JOB_ID.fullmatch(job_id) is None:
                raise ValueError("official job id invalid")
        if (
            self.source_kind == "official_site"
            and (job_id is None or self.official_status not in _OFFICIAL_STATUSES)
        ) or (
            self.source_kind == "manual"
            and (job_id is not None or self.official_status is not None)
        ):
            raise ValueError("position source fields invalid")
        if self.internal_status not in _INTERNAL_STATUSES:
            raise ValueError("position internal status invalid")
        object.__setattr__(self, "official_job_id", job_id)
        object.__setattr__(
            self, "title", _text(self.title, maximum=500, message="position title invalid")
        )
        object.__setattr__(
            self,
            "department",
            _optional_text(
                self.department, maximum=500, message="position department invalid"
            ),
        )
        object.__setattr__(self, "locations", _strings(self.locations))
        if self.source_version is not None:
            object.__setattr__(
                self,
                "source_version",
                _text(
                    self.source_version,
                    maximum=256,
                    message="position source version invalid",
                ),
            )
        _row_version(self.row_version)
        _aware(self.created_at)
        _aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class PositionDraftRecord:
    draft_id: UUID
    owner_id: UUID
    source_kind: DraftSource
    source_key: str
    source_conversation_id: UUID | None
    title: str
    proposal: dict[str, object]
    evidence: dict[str, object]
    discovery_rule_version: str
    state: DraftState
    resolved_position_id: UUID | None
    row_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.draft_id, "draft identifiers invalid")
        _uuid(self.owner_id, "draft identifiers invalid")
        if self.source_conversation_id is not None:
            _uuid(self.source_conversation_id, "draft identifiers invalid")
        if self.resolved_position_id is not None:
            _uuid(self.resolved_position_id, "draft identifiers invalid")
        if self.source_kind not in _DRAFT_SOURCES:
            raise ValueError("draft source invalid")
        if self.state not in _DRAFT_STATES:
            raise ValueError("draft state invalid")
        resolved = self.resolved_position_id is not None
        if resolved != (self.state in {"confirmed", "merged"}):
            raise ValueError("draft resolution invalid")
        object.__setattr__(self, "source_key", _text(
            self.source_key, maximum=256, message="draft source key invalid"
        ))
        object.__setattr__(self, "title", _text(
            self.title, maximum=500, message="position title invalid"
        ))
        _json_object(self.proposal, 131072, "draft proposal invalid")
        _json_object(self.evidence, 65536, "draft evidence invalid")
        object.__setattr__(self, "discovery_rule_version", _text(
            self.discovery_rule_version, maximum=128,
            message="draft rule version invalid",
        ))
        _row_version(self.row_version)
        _aware(self.created_at)
        _aware(self.updated_at)


PositionSummary = PositionRecord


@dataclass(frozen=True, slots=True)
class PositionDetail:
    position: PositionRecord
    conversation_count: int
    material_count: int
    artifact_count: int
    conversation_ids: tuple[UUID, ...] = ()
    material_attachment_ids: tuple[UUID, ...] = ()
    artifact_ids: tuple[UUID, ...] = ()
    artifact_attachment_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.position, PositionRecord) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                self.conversation_count,
                self.material_count,
                self.artifact_count,
            )
        ):
            raise ValueError("position detail invalid")
        for values in (
            self.conversation_ids, self.material_attachment_ids,
            self.artifact_ids, self.artifact_attachment_ids,
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(value, UUID) for value in values
            ) or len(values) != len(set(values)):
                raise ValueError("position detail identifiers invalid")


@dataclass(frozen=True, slots=True)
class CreateManualPosition:
    owner_id: UUID
    position_id: UUID
    client_request_id: UUID
    title: str
    department: str | None = None
    locations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.position_id, self.client_request_id):
            _uuid(value, "position identifiers invalid")
        object.__setattr__(self, "title", _text(
            self.title, maximum=500, message="position title invalid"
        ))
        object.__setattr__(self, "department", _optional_text(
            self.department, maximum=500, message="position department invalid"
        ))
        object.__setattr__(self, "locations", _strings(self.locations))


@dataclass(frozen=True, slots=True)
class ProjectOfficialPosition:
    owner_id: UUID
    position_id: UUID
    client_request_id: UUID
    official_job_id: str
    title: str
    department: str | None
    locations: tuple[str, ...]
    official_status: OfficialStatus
    source_version: str
    content_hash: str
    source_synced_at: datetime

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.position_id, self.client_request_id):
            _uuid(value, "position identifiers invalid")
        if not isinstance(self.official_job_id, str):
            raise ValueError("official job id invalid")
        job_id = self.official_job_id.strip().upper()
        if _JOB_ID.fullmatch(job_id) is None:
            raise ValueError("official job id invalid")
        if self.official_status not in _OFFICIAL_STATUSES:
            raise ValueError("position official status invalid")
        if not isinstance(self.content_hash, str) or re.fullmatch(
            r"[a-f0-9]{64}", self.content_hash
        ) is None:
            raise ValueError("position content hash invalid")
        if (
            not isinstance(self.source_synced_at, datetime)
            or self.source_synced_at.tzinfo is None
        ):
            raise ValueError("position source sync time invalid")
        object.__setattr__(self, "official_job_id", job_id)
        object.__setattr__(self, "title", _text(
            self.title, maximum=500, message="position title invalid"
        ))
        object.__setattr__(self, "department", _optional_text(
            self.department, maximum=500, message="position department invalid"
        ))
        object.__setattr__(self, "locations", _strings(self.locations))
        object.__setattr__(self, "source_version", _text(
            self.source_version, maximum=256, message="position source version invalid"
        ))


@dataclass(frozen=True, slots=True)
class ProposePositionDraft:
    owner_id: UUID
    draft_id: UUID
    client_request_id: UUID
    source_kind: DraftSource
    source_key: str
    source_conversation_id: UUID | None
    title: str
    proposal: dict[str, object]
    evidence: dict[str, object]
    discovery_rule_version: str

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.draft_id, self.client_request_id):
            _uuid(value, "draft identifiers invalid")
        if self.source_conversation_id is not None:
            _uuid(self.source_conversation_id, "draft identifiers invalid")
        if self.source_kind not in _DRAFT_SOURCES:
            raise ValueError("draft source invalid")
        object.__setattr__(self, "source_key", _text(
            self.source_key, maximum=256, message="draft source key invalid"
        ))
        object.__setattr__(self, "title", _text(
            self.title, maximum=500, message="position title invalid"
        ))
        _json_object(self.proposal, 131072, "draft proposal invalid")
        _json_object(self.evidence, 65536, "draft evidence invalid")
        object.__setattr__(self, "discovery_rule_version", _text(
            self.discovery_rule_version, maximum=128,
            message="draft rule version invalid",
        ))


@dataclass(frozen=True, slots=True)
class ConfirmPositionDraft:
    owner_id: UUID
    draft_id: UUID
    position_id: UUID
    client_request_id: UUID
    expected_row_version: int

    def __post_init__(self) -> None:
        for value in (
            self.owner_id, self.draft_id, self.position_id, self.client_request_id
        ):
            _uuid(value, "draft identifiers invalid")
        _row_version(self.expected_row_version)


@dataclass(frozen=True, slots=True)
class MergePositionDraft:
    owner_id: UUID
    draft_id: UUID
    target_position_id: UUID
    client_request_id: UUID
    expected_row_version: int

    def __post_init__(self) -> None:
        for value in (
            self.owner_id,
            self.draft_id,
            self.target_position_id,
            self.client_request_id,
        ):
            _uuid(value, "draft identifiers invalid")
        _row_version(self.expected_row_version)


@dataclass(frozen=True, slots=True)
class DismissPositionDraft:
    owner_id: UUID
    draft_id: UUID
    client_request_id: UUID
    expected_row_version: int

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.draft_id, self.client_request_id):
            _uuid(value, "draft identifiers invalid")
        _row_version(self.expected_row_version)


@dataclass(frozen=True, slots=True)
class PositionConversationBinding:
    owner_id: UUID
    position_id: UUID
    conversation_id: UUID
    client_request_id: UUID
    binding_kind: BindingKind
    previous_position_id: UUID | None
    created_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.owner_id,
            self.position_id,
            self.conversation_id,
            self.client_request_id,
        ):
            _uuid(value, "conversation binding identifiers invalid")
        if self.previous_position_id is not None:
            _uuid(
                self.previous_position_id,
                "conversation binding identifiers invalid",
            )
        if self.binding_kind not in _BINDING_KINDS:
            raise ValueError("binding kind invalid")
        _aware(self.created_at)


@dataclass(frozen=True, slots=True)
class PositionMaterialRecord:
    owner_id: UUID
    position_id: UUID
    attachment_id: UUID
    client_request_id: UUID
    active: bool
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.owner_id, self.position_id, self.attachment_id,
            self.client_request_id,
        ):
            _uuid(value, "material identifiers invalid")
        if type(self.active) is not bool:
            raise ValueError("material state invalid")
        _aware(self.created_at)
        _aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class BindPositionConversation:
    owner_id: UUID
    position_id: UUID
    conversation_id: UUID
    client_request_id: UUID
    binding_kind: BindingKind

    def __post_init__(self) -> None:
        for value in (
            self.owner_id,
            self.position_id,
            self.conversation_id,
            self.client_request_id,
        ):
            _uuid(value, "conversation binding identifiers invalid")
        if self.binding_kind not in _BINDING_KINDS:
            raise ValueError("binding kind invalid")


@dataclass(frozen=True, slots=True)
class CorrectPositionConversationBinding:
    owner_id: UUID
    conversation_id: UUID
    previous_position_id: UUID
    new_position_id: UUID
    client_request_id: UUID
    reason: str

    def __post_init__(self) -> None:
        for value in (
            self.owner_id,
            self.conversation_id,
            self.previous_position_id,
            self.new_position_id,
            self.client_request_id,
        ):
            _uuid(value, "conversation binding identifiers invalid")
        if self.previous_position_id == self.new_position_id:
            raise ValueError("binding correction positions invalid")
        object.__setattr__(
            self,
            "reason",
            _text(
                self.reason,
                maximum=1000,
                message="binding correction reason invalid",
            ),
        )


@dataclass(frozen=True, slots=True)
class PromotePositionMaterial:
    owner_id: UUID
    position_id: UUID
    attachment_id: UUID
    client_request_id: UUID

    def __post_init__(self) -> None:
        values = (
            self.owner_id,
            self.position_id,
            self.attachment_id,
            self.client_request_id,
        )
        if any(not isinstance(value, UUID) for value in values) or len(set(values)) < 4:
            raise ValueError("material identifiers invalid")
