from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

RelayJobKind = Literal["legacy_brain", "direct_agent", "metabot_local"]
RelayResultMode = Literal["internal", "public_markdown"]
CollaborationContract = Literal[
    "core_chat_collaboration_v3", "core_chat_collaboration_v4"
]

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MIME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+/[A-Za-z0-9!#$%&'*+.^_`|~-]+\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MAX_FILE_BYTES = 50 * 1024 * 1024
_MAX_TOTAL_OUTPUT_BYTES = 250 * 1024 * 1024


def _bounded_text(
    value: str,
    *,
    maximum: int,
    allow_empty: bool = False,
    multiline: bool = False,
) -> str:
    allowed_controls = {"\n", "\t"} if multiline else set()
    if type(value) is not str or any(
        ord(character) < 32 and character not in allowed_controls for character in value
    ):
        raise ValueError("collaboration text invalid")
    if value != value.strip() or (not allow_empty and not value):
        raise ValueError("collaboration text invalid")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError("collaboration text invalid")
    return value


class TaskAttachmentGrantPayload(BaseModel):
    """Task-bound, short-lived read capability sent only through relay ciphertext."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    attachment_id: UUID = Field(alias="attachmentId")
    display_name: str = Field(alias="displayName", min_length=1, max_length=1024)
    detected_mime: str = Field(alias="detectedMime", min_length=3, max_length=255)
    size_bytes: int = Field(alias="sizeBytes", gt=0, le=_MAX_FILE_BYTES)
    sha256_hex: str = Field(alias="sha256", min_length=64, max_length=64, repr=False)
    download_url: str = Field(
        alias="downloadUrl", min_length=1, max_length=2048, repr=False
    )
    bearer_token: str = Field(
        alias="bearerToken", min_length=43, max_length=43, repr=False
    )
    expires_at: AwareDatetime = Field(alias="expiresAt")

    @model_validator(mode="after")
    def _valid_grant(self) -> TaskAttachmentGrantPayload:
        if (
            _bounded_text(self.display_name, maximum=1024) != self.display_name
            or "/" in self.display_name
            or "\\" in self.display_name
            or _MIME.fullmatch(self.detected_mime) is None
            or _SHA256.fullmatch(self.sha256_hex) is None
            or _TOKEN.fullmatch(self.bearer_token) is None
            or self.download_url
            != f"/api/v1/execution-worker/attachments/{self.attachment_id}/content"
        ):
            raise ValueError("attachment grant invalid")
        return self


class OutputWriteGrantPayload(BaseModel):
    """Task-bound write capability for registering Platform-owned artifacts."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    task_id: UUID = Field(alias="taskId")
    agent_id: str = Field(alias="agentId", min_length=1, max_length=128)
    upload_url: str = Field(
        alias="uploadUrl", min_length=1, max_length=2048, repr=False
    )
    bearer_token: str = Field(
        alias="bearerToken", min_length=43, max_length=43, repr=False
    )
    max_files: int = Field(alias="maxFiles", gt=0, le=20)
    max_total_bytes: int = Field(
        alias="maxTotalBytes", gt=0, le=_MAX_TOTAL_OUTPUT_BYTES
    )

    @model_validator(mode="after")
    def _valid_grant(self) -> OutputWriteGrantPayload:
        if (
            _IDENTIFIER.fullmatch(self.agent_id) is None
            or _TOKEN.fullmatch(self.bearer_token) is None
            or self.upload_url
            != f"/api/v1/execution-worker/tasks/{self.task_id}/artifacts"
        ):
            raise ValueError("output write grant invalid")
        return self


class CitationPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    citation_key: str = Field(alias="citationKey", min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=4096)
    site: str = Field(min_length=1, max_length=253)
    retrieved_at: AwareDatetime = Field(alias="retrievedAt")
    supports: tuple[str, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _valid_citation(self) -> CitationPayload:
        try:
            parsed = urlsplit(self.url)
            hostname = (
                parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
            )
        except (AttributeError, UnicodeError, ValueError):
            raise ValueError("citation invalid") from None
        if (
            _IDENTIFIER.fullmatch(self.citation_key) is None
            or parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or hostname != self.site.lower().rstrip(".")
            or any(_bounded_text(item, maximum=128) != item for item in self.supports)
        ):
            raise ValueError("citation invalid")
        _bounded_text(self.title, maximum=512)
        return self


class RegisteredArtifactPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    attachment_id: UUID = Field(alias="attachmentId")
    artifact_key: str = Field(alias="artifactKey", min_length=1, max_length=128)
    producer_version_id: str = Field(
        alias="producerVersionId", min_length=1, max_length=160
    )
    display_name: str = Field(alias="displayName", min_length=1, max_length=1024)
    status: Literal["ready", "rejected"]

    @model_validator(mode="after")
    def _registered_only(self) -> RegisteredArtifactPayload:
        if (
            _IDENTIFIER.fullmatch(self.artifact_key) is None
            or self.artifact_key == "bridge-private"
            or "/" in self.producer_version_id
            or "\\" in self.producer_version_id
            or "/" in self.display_name
            or "\\" in self.display_name
        ):
            raise ValueError("registered artifact invalid")
        _bounded_text(self.producer_version_id, maximum=160)
        _bounded_text(self.display_name, maximum=1024)
        return self


class SearchRecoveryPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    status: Literal["unavailable", "no_results", "partial"]
    attempt_count: int = Field(alias="attemptCount", ge=1, le=100)
    last_attempt_at: AwareDatetime = Field(alias="lastAttemptAt")
    resumable: bool
    coverage_note: str | None = Field(
        default=None, alias="coverageNote", max_length=4096
    )

    @field_validator("coverage_note")
    @classmethod
    def _valid_note(cls, value: str | None) -> str | None:
        if value is not None:
            _bounded_text(value, maximum=4096, multiline=True)
        return value


class CollaborationV4Result(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    public_answer_markdown: str = Field(alias="publicAnswerMarkdown", max_length=131072)
    citations: tuple[CitationPayload, ...] = Field(default=(), max_length=50)
    artifacts: tuple[RegisteredArtifactPayload, ...] = Field(default=(), max_length=20)
    completion: Literal["completed", "partially_completed", "failed"]
    recovery: SearchRecoveryPayload | None = None

    @model_validator(mode="after")
    def _valid_result(self) -> CollaborationV4Result:
        _bounded_text(
            self.public_answer_markdown,
            maximum=131072,
            allow_empty=self.completion == "failed",
            multiline=True,
        )
        citation_keys = tuple(item.citation_key for item in self.citations)
        attachment_ids = tuple(item.attachment_id for item in self.artifacts)
        if len(set(citation_keys)) != len(citation_keys) or len(
            set(attachment_ids)
        ) != len(attachment_ids):
            raise ValueError("collaboration result contains duplicates")
        return self


class RequesterSubject(BaseModel):
    """Minimal Platform-verified identity carried outside the user prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    internal_user_id: UUID
    display_name: str = Field(min_length=1, max_length=256, strict=True)

    @field_validator("display_name")
    @classmethod
    def _valid_display_name(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("requester display name invalid")
        return value


class RelayJobPayload(BaseModel):
    run_id: UUID
    conversation_id: UUID
    trigger_message_id: UUID
    agent_id: str
    prompt: str
    max_turns: int = Field(ge=1, le=24)
    job_kind: RelayJobKind = "legacy_brain"
    result_mode: RelayResultMode = "internal"
    requester_subject: RequesterSubject | None = Field(default=None, repr=False)
    collaboration_contract: CollaborationContract | None = None
    task_session_id: str | None = Field(default=None, min_length=16, max_length=256)
    message_kind: Literal["initial", "followup", "stop"] = "initial"
    message_seq: int = Field(default=1, ge=1)
    parent_run_id: UUID | None = None
    input_attachment_grants: tuple[TaskAttachmentGrantPayload, ...] = Field(
        default=(), max_length=32, repr=False
    )
    output_write_grant: OutputWriteGrantPayload | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _valid_collaboration_command(self) -> RelayJobPayload:
        collaboration_values = (
            self.collaboration_contract,
            self.task_session_id,
            self.parent_run_id,
        )
        if self.job_kind == "legacy_brain":
            if (
                any(value is not None for value in collaboration_values)
                or self.input_attachment_grants
                or self.output_write_grant is not None
                or self.message_kind != "initial"
                or self.message_seq != 1
            ):
                raise ValueError("collaboration command requires metabot_local")
            return self
        if self.job_kind == "direct_agent" and (
            all(value is None for value in collaboration_values)
            and not self.input_attachment_grants
            and self.output_write_grant is None
            and self.message_kind == "initial"
            and self.message_seq == 1
        ):
            return self
        if self.job_kind == "direct_agent" and (
            self.message_kind != "initial"
            or self.message_seq != 1
            or self.parent_run_id is not None
        ):
            raise ValueError("direct collaboration command invalid")
        if (
            self.agent_id == "agent-brain-bot"
            or self.collaboration_contract is None
            or self.task_session_id is None
        ):
            raise ValueError("metabot_local collaboration command invalid")
        if self.message_kind == "initial":
            if self.message_seq != 1 or self.parent_run_id is not None:
                raise ValueError("initial collaboration command invalid")
        elif self.message_kind == "followup":
            if self.message_seq <= 1 or self.parent_run_id is None:
                raise ValueError("follow-up collaboration command invalid")
        elif self.parent_run_id is None:
            raise ValueError("stop collaboration command invalid")
        if self.collaboration_contract != "core_chat_collaboration_v4":
            if self.input_attachment_grants or self.output_write_grant is not None:
                raise ValueError("attachment grants require collaboration v4")
            return self
        attachment_ids = tuple(
            grant.attachment_id for grant in self.input_attachment_grants
        )
        if len(set(attachment_ids)) != len(attachment_ids):
            raise ValueError("attachment grants must be unique")
        if self.output_write_grant is not None and (
            self.output_write_grant.agent_id != self.agent_id
            or (
                self.job_kind == "metabot_local"
                and self.output_write_grant.task_id != self.run_id
            )
        ):
            raise ValueError("output write grant subject mismatch")
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, handler) -> dict[str, Any]:
        value = handler(self)
        if self.collaboration_contract != "core_chat_collaboration_v4":
            value.pop("input_attachment_grants", None)
            value.pop("output_write_grant", None)
        return value


class RelayEvent(BaseModel):
    run_id: UUID
    seq: int = Field(gt=0)
    event_type: str
    created_at: AwareDatetime
    payload: dict[str, object]


class RelayLease(BaseModel):
    job_id: UUID
    payload: RelayJobPayload
    lease_expires_at: datetime
    cancel_requested: bool
