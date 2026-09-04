from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

CandidateDraftState = Literal[
    "pending", "processing", "ready", "failed", "confirmed", "dismissed"
]
CandidateDocumentStatus = Literal["active", "erased"]
PositionCandidateStatus = Literal["active", "archived"]
AnalysisKind = Literal[
    "resume_extract", "match", "candidate_interview_plan", "comparison"
]
FeedbackKind = Literal["accepted", "rejected", "correction"]
CandidateProcessingAttemptState = Literal[
    "processing", "completed", "failed", "expired"
]

_DRAFT_STATES = {
    "pending", "processing", "ready", "failed", "confirmed", "dismissed"
}
_DOCUMENT_STATUSES = {"active", "erased"}
_POSITION_CANDIDATE_STATUSES = {"active", "archived"}
_ANALYSIS_KINDS = {
    "resume_extract", "match", "candidate_interview_plan", "comparison"
}
_FEEDBACK_KINDS = {"accepted", "rejected", "correction"}
_PROCESSING_ATTEMPT_STATES = {"processing", "completed", "failed", "expired"}
_FORBIDDEN_FACT_KEYS = {
    "age",
    "birth_date",
    "date_of_birth",
    "disability",
    "ethnicity",
    "gender",
    "health",
    "marital_status",
    "nationality",
    "onboarding",
    "offer_status",
    "pipeline_stage",
    "political_affiliation",
    "pregnancy",
    "race",
    "religion",
    "sexual_orientation",
    "storage_key",
    "storage_path",
    "object_key",
    "object_ref",
    "object_ref_ciphertext",
    "immutable_locator",
    "ats",
    "ats_id",
    "interview_schedule",
    "automatic_rejection",
    "beisen",
    "boss_zhipin",
    "liepin",
}
_CANDIDATE_FACT_KEYS = {
    "stable_name", "summary", "contact", "education", "experiences",
    "projects", "skills", "certifications", "languages", "awards",
    "publications", "unknowns", "sources",
}


def _uuid(value: UUID, message: str = "candidate identifiers invalid") -> UUID:
    if not isinstance(value, UUID):
        raise ValueError(message)
    return value


def _optional_uuid(
    value: UUID | None, message: str = "candidate identifiers invalid"
) -> UUID | None:
    if value is not None:
        _uuid(value, message)
    return value


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("candidate timestamp invalid")
    return value


def _positive(value: int, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
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


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).strip().lower() in _FORBIDDEN_FACT_KEYS
            or _contains_forbidden_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _json_object(
    value: dict[str, object], *, maximum: int, message: str,
    reject_protected: bool = False,
) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(message)
    if reject_protected and _contains_forbidden_key(value):
        raise ValueError("candidate facts contain forbidden fields")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError(message)
    return value


def _candidate_facts(value: dict[str, object]) -> dict[str, object]:
    validated = _json_object(
        value, maximum=262144, message="candidate facts invalid",
        reject_protected=True,
    )
    if any(str(key).strip().lower() not in _CANDIDATE_FACT_KEYS for key in value):
        raise ValueError("candidate facts invalid")
    return validated


def _json_objects(
    values: tuple[dict[str, object], ...], *, maximum: int, message: str,
    reject_protected: bool = False,
) -> tuple[dict[str, object], ...]:
    if not isinstance(values, tuple) or len(values) > 500:
        raise ValueError(message)
    try:
        encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if len(encoded.encode("utf-8")) > maximum or any(
        type(value) is not dict for value in values
    ):
        raise ValueError(message)
    if reject_protected and _contains_forbidden_key(values):
        raise ValueError("analysis evidence contains forbidden fields")
    return values


def _uuid_tuple(
    values: tuple[UUID, ...], *, minimum: int = 0, maximum: int = 100,
    message: str = "candidate identifiers invalid",
) -> tuple[UUID, ...]:
    if (
        not isinstance(values, tuple)
        or not minimum <= len(values) <= maximum
        or any(not isinstance(value, UUID) for value in values)
        or len(values) != len(set(values))
    ):
        raise ValueError(message)
    return values


def _strings(
    values: tuple[str, ...], *, maximum_items: int, maximum_text: int, message: str
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > maximum_items:
        raise ValueError(message)
    return tuple(
        _text(value, maximum=maximum_text, message=message) for value in values
    )


@dataclass(frozen=True, slots=True)
class CandidateDraft:
    draft_id: UUID
    owner_id: UUID
    position_id: UUID
    attachment_id: UUID
    batch_request_id: UUID
    client_request_id: UUID
    state: CandidateDraftState
    extracted_facts: dict[str, object]
    identity_candidates: tuple[UUID, ...]
    error_code: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.draft_id, self.owner_id, self.position_id, self.attachment_id,
            self.batch_request_id, self.client_request_id,
        ):
            _uuid(value)
        if self.state not in _DRAFT_STATES:
            raise ValueError("candidate draft state invalid")
        _candidate_facts(self.extracted_facts)
        _uuid_tuple(
            self.identity_candidates, maximum=100,
            message="identity candidates invalid",
        )
        if (self.state == "failed") != (self.error_code is not None):
            raise ValueError("draft error state invalid")
        object.__setattr__(self, "error_code", _optional_text(
            self.error_code, maximum=128, message="draft error code invalid"
        ))
        _positive(self.row_version, "row version invalid")
        _aware(self.created_at)
        _aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class CandidateDraftProcessingAttempt:
    attempt_id: UUID
    owner_id: UUID
    draft_id: UUID
    position_id: UUID
    attachment_id: UUID
    draft_client_request_id: UUID
    worker_id: str
    execution_job_id: UUID | None
    conversation_id: UUID | None
    turn_id: UUID | None
    state: CandidateProcessingAttemptState
    starting_row_version: int
    claimed_row_version: int
    claimed_at: datetime
    lease_expires_at: datetime
    execution_attached_at: datetime | None
    finished_at: datetime | None
    terminal_request_id: UUID | None

    def __post_init__(self) -> None:
        for value in (
            self.attempt_id, self.owner_id, self.draft_id, self.position_id,
            self.attachment_id, self.draft_client_request_id,
        ):
            _uuid(value)
        _optional_uuid(self.execution_job_id)
        _optional_uuid(self.conversation_id)
        _optional_uuid(self.turn_id)
        _optional_uuid(self.terminal_request_id)
        if len({
            self.execution_job_id is None,
            self.conversation_id is None,
            self.turn_id is None,
        }) != 1:
            raise ValueError("processing attempt turn identity invalid")
        object.__setattr__(self, "worker_id", _text(
            self.worker_id, maximum=64, message="processing worker invalid"
        ))
        if self.state not in _PROCESSING_ATTEMPT_STATES:
            raise ValueError("processing attempt state invalid")
        _positive(self.starting_row_version, "row version invalid")
        _positive(self.claimed_row_version, "row version invalid")
        _aware(self.claimed_at)
        _aware(self.lease_expires_at)
        if self.execution_attached_at is not None:
            _aware(self.execution_attached_at)
        if (self.execution_job_id is None) != (self.execution_attached_at is None):
            raise ValueError("processing attempt execution identity invalid")
        if self.finished_at is not None:
            _aware(self.finished_at)
        if (self.state == "processing") != (self.finished_at is None):
            raise ValueError("processing attempt terminal state invalid")
        if self.state == "expired" and self.terminal_request_id is not None:
            raise ValueError("processing attempt terminal state invalid")


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: UUID
    owner_id: UUID
    stable_name: str
    facts: dict[str, object]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.candidate_id)
        _uuid(self.owner_id)
        object.__setattr__(self, "stable_name", _text(
            self.stable_name, maximum=500, message="candidate name invalid"
        ))
        _candidate_facts(self.facts)
        _aware(self.created_at)
        _aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class CandidateDocument:
    document_id: UUID
    owner_id: UUID
    candidate_id: UUID
    attachment_id: UUID
    source_draft_id: UUID
    document_kind: Literal["resume"]
    version_number: int
    content_sha256: str
    status: CandidateDocumentStatus
    created_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.document_id, self.owner_id, self.candidate_id,
            self.attachment_id, self.source_draft_id,
        ):
            _uuid(value)
        if self.document_kind != "resume":
            raise ValueError("candidate document kind invalid")
        _positive(self.version_number, "document version invalid")
        if (
            not isinstance(self.content_sha256, str)
            or len(self.content_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.content_sha256)
        ):
            raise ValueError("document content hash invalid")
        if self.status not in _DOCUMENT_STATUSES:
            raise ValueError("candidate document status invalid")
        _aware(self.created_at)


@dataclass(frozen=True, slots=True)
class PositionCandidate:
    position_candidate_id: UUID
    owner_id: UUID
    position_id: UUID
    candidate_id: UUID
    context_version_id: UUID
    source_draft_id: UUID
    status: PositionCandidateStatus
    row_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.position_candidate_id, self.owner_id, self.position_id,
            self.candidate_id, self.context_version_id, self.source_draft_id,
        ):
            _uuid(value)
        if self.status not in _POSITION_CANDIDATE_STATUSES:
            raise ValueError("position candidate status invalid")
        _positive(self.row_version, "row version invalid")
        _aware(self.created_at)
        _aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class ConfirmedCandidate:
    candidate: Candidate
    document: CandidateDocument
    position_candidate: PositionCandidate

    def __post_init__(self) -> None:
        if (
            not isinstance(self.candidate, Candidate)
            or not isinstance(self.document, CandidateDocument)
            or not isinstance(self.position_candidate, PositionCandidate)
            or self.candidate.owner_id != self.document.owner_id
            or self.candidate.owner_id != self.position_candidate.owner_id
            or self.candidate.candidate_id != self.document.candidate_id
            or self.candidate.candidate_id != self.position_candidate.candidate_id
            or self.document.source_draft_id
            != self.position_candidate.source_draft_id
        ):
            raise ValueError("confirmed candidate scope invalid")


@dataclass(frozen=True, slots=True)
class CandidateAnalysisVersion:
    analysis_version_id: UUID
    owner_id: UUID
    position_candidate_id: UUID
    position_id: UUID
    candidate_id: UUID
    context_version_id: UUID
    version_number: int
    analysis_kind: AnalysisKind
    document_ids: tuple[UUID, ...]
    feedback_ids: tuple[UUID, ...]
    result: dict[str, object]
    evidence: tuple[dict[str, object], ...]
    unknowns: tuple[str, ...]
    conflicts: tuple[str, ...]
    verification_questions: tuple[str, ...]
    agent_version: str
    model_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.analysis_version_id, self.owner_id, self.position_candidate_id,
            self.position_id, self.candidate_id, self.context_version_id,
        ):
            _uuid(value)
        _positive(self.version_number, "analysis version invalid")
        if self.analysis_kind not in _ANALYSIS_KINDS:
            raise ValueError("analysis kind invalid")
        _uuid_tuple(
            self.document_ids, minimum=1, maximum=100,
            message="analysis documents required",
        )
        _uuid_tuple(self.feedback_ids, maximum=500, message="feedback scope invalid")
        try:
            _json_object(
                self.result, maximum=524288, message="analysis result invalid",
                reject_protected=True,
            )
        except ValueError as error:
            if "forbidden" in str(error):
                raise ValueError("analysis result contains forbidden fields") from None
            raise
        _json_objects(
            self.evidence, maximum=524288, message="analysis evidence invalid",
            reject_protected=True,
        )
        for field in (self.unknowns, self.conflicts, self.verification_questions):
            _strings(
                field, maximum_items=500, maximum_text=4000,
                message="analysis text collection invalid",
            )
        object.__setattr__(self, "agent_version", _text(
            self.agent_version, maximum=128, message="agent version invalid"
        ))
        object.__setattr__(self, "model_version", _text(
            self.model_version, maximum=128, message="model version invalid"
        ))
        _aware(self.created_at)


@dataclass(frozen=True, slots=True)
class HumanFeedback:
    feedback_id: UUID
    owner_id: UUID
    position_candidate_id: UUID
    analysis_version_id: UUID
    feedback_kind: FeedbackKind
    conclusion_key: str
    correction: str | None
    reason: str
    created_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.feedback_id, self.owner_id, self.position_candidate_id,
            self.analysis_version_id,
        ):
            _uuid(value)
        if self.feedback_kind not in _FEEDBACK_KINDS:
            raise ValueError("feedback kind invalid")
        object.__setattr__(self, "conclusion_key", _text(
            self.conclusion_key, maximum=256, message="feedback conclusion invalid"
        ))
        object.__setattr__(self, "correction", _optional_text(
            self.correction, maximum=8000, message="feedback correction invalid"
        ))
        if self.feedback_kind == "correction" and self.correction is None:
            raise ValueError("feedback correction required")
        if self.feedback_kind != "correction" and self.correction is not None:
            raise ValueError("feedback correction invalid")
        object.__setattr__(self, "reason", _text(
            self.reason, maximum=4000, message="feedback reason invalid"
        ))
        _aware(self.created_at)


@dataclass(frozen=True, slots=True)
class CandidateEnvelopeFragment:
    candidate_id: UUID
    position_candidate_id: UUID
    context_version_id: UUID
    document_ids: tuple[UUID, ...]
    document_attachment_ids: tuple[UUID, ...]
    human_feedback_ids: tuple[UUID, ...]
    prompt_context: str

    def __post_init__(self) -> None:
        for value in (
            self.candidate_id, self.position_candidate_id, self.context_version_id
        ):
            _uuid(value)
        _uuid_tuple(
            self.document_ids, minimum=1, maximum=100,
            message="document scope invalid",
        )
        _uuid_tuple(
            self.document_attachment_ids, minimum=1, maximum=100,
            message="document scope invalid",
        )
        if len(self.document_ids) != len(self.document_attachment_ids):
            raise ValueError("document scope invalid")
        _uuid_tuple(
            self.human_feedback_ids, maximum=100,
            message="feedback scope invalid",
        )
        object.__setattr__(self, "prompt_context", _text(
            self.prompt_context, maximum=65536,
            message="candidate prompt context invalid",
        ))
        if len(self.prompt_context.encode("utf-8")) > 65536:
            raise ValueError("candidate prompt context invalid")


@dataclass(frozen=True, slots=True)
class CreateCandidateDraftBatch:
    owner_id: UUID
    position_id: UUID
    attachment_ids: tuple[UUID, ...]
    client_request_id: UUID

    def __post_init__(self) -> None:
        _uuid(self.owner_id)
        _uuid(self.position_id)
        _uuid(self.client_request_id)
        _uuid_tuple(
            self.attachment_ids, minimum=1, maximum=100,
            message="candidate attachments required",
        )


@dataclass(frozen=True, slots=True)
class ClaimNextCandidateDraft:
    attempt_id: UUID
    worker_id: str
    lease_seconds: int = 300

    def __post_init__(self) -> None:
        _uuid(self.attempt_id)
        object.__setattr__(self, "worker_id", _text(
            self.worker_id, maximum=64, message="processing worker invalid"
        ))
        if (
            isinstance(self.lease_seconds, bool)
            or not isinstance(self.lease_seconds, int)
            or not 30 <= self.lease_seconds <= 900
        ):
            raise ValueError("processing lease invalid")


@dataclass(frozen=True, slots=True)
class AttachCandidateDraftExecution:
    attempt_id: UUID
    worker_id: str
    execution_job_id: UUID
    conversation_id: UUID
    turn_id: UUID

    def __post_init__(self) -> None:
        for value in (
            self.attempt_id, self.execution_job_id,
            self.conversation_id, self.turn_id,
        ):
            _uuid(value)
        object.__setattr__(self, "worker_id", _text(
            self.worker_id, maximum=64, message="processing worker invalid"
        ))


@dataclass(frozen=True, slots=True)
class RetryCandidateDraft:
    owner_id: UUID
    draft_id: UUID
    client_request_id: UUID
    expected_row_version: int

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.draft_id, self.client_request_id):
            _uuid(value)
        _positive(self.expected_row_version, "row version invalid")


@dataclass(frozen=True, slots=True)
class CompleteCandidateDraft:
    owner_id: UUID
    draft_id: UUID
    client_request_id: UUID
    expected_row_version: int
    extracted_facts: dict[str, object]
    identity_candidates: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.draft_id, self.client_request_id):
            _uuid(value)
        _positive(self.expected_row_version, "row version invalid")
        _candidate_facts(self.extracted_facts)
        _uuid_tuple(
            self.identity_candidates, maximum=100,
            message="identity candidates invalid",
        )


@dataclass(frozen=True, slots=True)
class FailCandidateDraft:
    owner_id: UUID
    draft_id: UUID
    client_request_id: UUID
    expected_row_version: int
    error_code: str

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.draft_id, self.client_request_id):
            _uuid(value)
        _positive(self.expected_row_version, "row version invalid")
        object.__setattr__(self, "error_code", _text(
            self.error_code, maximum=128, message="draft error code invalid"
        ))


@dataclass(frozen=True, slots=True)
class ConfirmCandidateDraft:
    owner_id: UUID
    draft_id: UUID
    client_request_id: UUID
    expected_row_version: int
    candidate_id: UUID
    stable_name: str
    confirmed_facts: dict[str, object]
    merge_candidate_id: UUID | None

    def __post_init__(self) -> None:
        for value in (
            self.owner_id, self.draft_id, self.client_request_id, self.candidate_id
        ):
            _uuid(value)
        _optional_uuid(self.merge_candidate_id)
        if self.merge_candidate_id == self.candidate_id:
            raise ValueError("merge target invalid")
        _positive(self.expected_row_version, "row version invalid")
        object.__setattr__(self, "stable_name", _text(
            self.stable_name, maximum=500, message="candidate name invalid"
        ))
        _candidate_facts(self.confirmed_facts)


@dataclass(frozen=True, slots=True)
class CreateCandidateAnalysis:
    owner_id: UUID
    position_candidate_id: UUID
    context_version_id: UUID
    document_ids: tuple[UUID, ...]
    analysis_kind: AnalysisKind
    client_request_id: UUID
    result: dict[str, object]
    evidence: tuple[dict[str, object], ...]
    unknowns: tuple[str, ...]
    conflicts: tuple[str, ...]
    verification_questions: tuple[str, ...]
    agent_version: str
    model_version: str

    def __post_init__(self) -> None:
        for value in (
            self.owner_id, self.position_candidate_id,
            self.context_version_id, self.client_request_id,
        ):
            _uuid(value)
        if self.analysis_kind not in _ANALYSIS_KINDS:
            raise ValueError("analysis kind invalid")
        _uuid_tuple(
            self.document_ids, minimum=1, maximum=100,
            message="analysis documents required",
        )
        try:
            _json_object(
                self.result, maximum=524288, message="analysis result invalid",
                reject_protected=True,
            )
        except ValueError as error:
            if "forbidden" in str(error):
                raise ValueError("analysis result contains forbidden fields") from None
            raise
        _json_objects(
            self.evidence, maximum=524288, message="analysis evidence invalid",
            reject_protected=True,
        )
        for field in (self.unknowns, self.conflicts, self.verification_questions):
            _strings(
                field, maximum_items=500, maximum_text=4000,
                message="analysis text collection invalid",
            )
        object.__setattr__(self, "agent_version", _text(
            self.agent_version, maximum=128, message="agent version invalid"
        ))
        object.__setattr__(self, "model_version", _text(
            self.model_version, maximum=128, message="model version invalid"
        ))


@dataclass(frozen=True, slots=True)
class AppendHumanFeedback:
    owner_id: UUID
    position_candidate_id: UUID
    analysis_version_id: UUID
    feedback_kind: FeedbackKind
    conclusion_key: str
    correction: str | None
    reason: str
    client_request_id: UUID

    def __post_init__(self) -> None:
        for value in (
            self.owner_id, self.position_candidate_id, self.analysis_version_id,
            self.client_request_id,
        ):
            _uuid(value)
        if self.feedback_kind not in _FEEDBACK_KINDS:
            raise ValueError("feedback kind invalid")
        object.__setattr__(self, "conclusion_key", _text(
            self.conclusion_key, maximum=256, message="feedback conclusion invalid"
        ))
        object.__setattr__(self, "correction", _optional_text(
            self.correction, maximum=8000, message="feedback correction invalid"
        ))
        if self.feedback_kind == "correction" and self.correction is None:
            raise ValueError("feedback correction required")
        if self.feedback_kind != "correction" and self.correction is not None:
            raise ValueError("feedback correction invalid")
        object.__setattr__(self, "reason", _text(
            self.reason, maximum=4000, message="feedback reason invalid"
        ))


@dataclass(frozen=True, slots=True)
class ComparePositionCandidates:
    owner_id: UUID
    position_id: UUID
    position_candidate_ids: tuple[UUID, ...]
    context_version_id: UUID
    client_request_id: UUID
    agent_version: str
    model_version: str

    def __post_init__(self) -> None:
        for value in (
            self.owner_id, self.position_id, self.context_version_id,
            self.client_request_id,
        ):
            _uuid(value)
        _uuid_tuple(
            self.position_candidate_ids, minimum=2, maximum=20,
            message="comparison candidates invalid",
        )
        object.__setattr__(self, "agent_version", _text(
            self.agent_version, maximum=128, message="agent version invalid"
        ))
        object.__setattr__(self, "model_version", _text(
            self.model_version, maximum=128, message="model version invalid"
        ))
