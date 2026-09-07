from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from .models import OutputWriteGrantPayload, TaskAttachmentGrantPayload

_ENVELOPE_BYTES = 1024 * 1024
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CALLBACK_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_MIME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+/[A-Za-z0-9!#$%&'*+.^_`|~-]+\Z")


def _bounded_text(value: str, *, maximum: int, multiline: bool = False) -> str:
    allowed_controls = {"\n", "\t"} if multiline else set()
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(
            ord(character) < 32 and character not in allowed_controls
            for character in value
        )
    ):
        raise ValueError("v5 contract text invalid")
    return value


class V5ContractError(ValueError):
    """Stable validation failure that never includes the rejected envelope."""


@dataclass(frozen=True)
class FeishuMessageIdentity:
    tenant_id: str
    app_id: str
    bot_id: str
    message_id: str

    def __post_init__(self) -> None:
        try:
            for value, maximum in (
                (self.tenant_id, 256),
                (self.app_id, 256),
                (self.bot_id, 128),
                (self.message_id, 512),
            ):
                _bounded_text(value, maximum=maximum)
        except (TypeError, ValueError):
            raise V5ContractError("v5 intake identity invalid") from None


class PermissionScopeV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    principal_ref: str = Field(alias="principalRef", min_length=1, max_length=256)
    conversation_id: UUID = Field(alias="conversationId")
    agent_id: Literal["hr-bot"] = Field(alias="agentId")
    tool_policy: Literal["default", "none"] = Field(alias="toolPolicy")


class CoreChatCommandV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    contract_version: Literal["core_chat_collaboration_v5"] = Field(
        alias="contractVersion"
    )
    run_id: UUID = Field(alias="runId")
    command_id: UUID = Field(alias="commandId")
    attempt_id: UUID = Field(alias="attemptId")
    attempt_no: int = Field(alias="attemptNo", ge=1, le=_MAX_SAFE_INTEGER)
    lease_epoch: int = Field(alias="leaseEpoch", ge=1, le=_MAX_SAFE_INTEGER)
    turn_id: UUID = Field(alias="turnId")
    turn_seq: int = Field(alias="turnSeq", ge=1, le=_MAX_SAFE_INTEGER)
    command_seq: int = Field(alias="commandSeq", ge=1, le=_MAX_SAFE_INTEGER)
    conversation_id: UUID = Field(alias="conversationId")
    trigger_message_id: UUID = Field(alias="triggerMessageId")
    principal_ref: str = Field(alias="principalRef", min_length=1, max_length=256)
    target_bot: Literal["hr-bot"] = Field(alias="targetBot")
    prompt: str = Field(min_length=1, max_length=131072)
    context_mode: Literal["frozen_prompt"] = Field(alias="contextMode")
    context_hash: str = Field(alias="contextHash", min_length=64, max_length=64)
    command_hash: str = Field(alias="commandHash", min_length=64, max_length=64)
    event_callback_url: str = Field(
        alias="eventCallbackUrl", min_length=1, max_length=2048, repr=False
    )
    task_session_id: str = Field(alias="taskSessionId", min_length=1, max_length=256)
    result_mode: Literal["public_markdown"] = Field(alias="resultMode")
    permission_scope: PermissionScopeV5 = Field(alias="permissionScope")
    input_attachment_grants: tuple[TaskAttachmentGrantPayload, ...] = Field(
        default=(), alias="inputAttachmentGrants", max_length=32, repr=False
    )
    output_write_grant: OutputWriteGrantPayload | None = Field(
        default=None, alias="outputWriteGrant", repr=False
    )
    retry_of: UUID | None = Field(default=None, alias="retryOf")

    @model_validator(mode="after")
    def _valid_command(self) -> CoreChatCommandV5:
        scope = self.permission_scope
        callback = urlsplit(self.event_callback_url)
        callback_parts = callback.path.split("/")
        callback_matches = (
            len(callback_parts) == 4
            and callback_parts[:2] == ["", "callbacks"]
            and callback_parts[2] == str(self.run_id)
            and _CALLBACK_TOKEN.fullmatch(callback_parts[3]) is not None
        )
        attachment_ids = tuple(
            grant.attachment_id for grant in self.input_attachment_grants
        )
        if (
            scope.principal_ref != self.principal_ref
            or scope.conversation_id != self.conversation_id
            or scope.agent_id != self.target_bot
            or self.task_session_id
            != f"platform:{self.conversation_id}:{self.target_bot}"
            or _SHA256.fullmatch(self.context_hash) is None
            or _SHA256.fullmatch(self.command_hash) is None
            or callback.scheme != "http"
            or callback.hostname != "127.0.0.1"
            or callback.port is None
            or callback.username is not None
            or callback.password is not None
            or bool(callback.query)
            or bool(callback.fragment)
            or not callback_matches
            or len(set(attachment_ids)) != len(attachment_ids)
            or self.retry_of in {self.command_id, self.attempt_id}
        ):
            raise ValueError("v5 command invalid")
        if self.output_write_grant is not None and (
            self.output_write_grant.task_id != self.run_id
            or self.output_write_grant.agent_id != self.target_bot
        ):
            raise ValueError("v5 command invalid")
        return self


class ExecutionRecoveryV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    evidence_complete: bool = Field(alias="evidenceComplete")
    tool_effect: Literal["unknown", "read_only", "write"] = Field(alias="toolEffect")
    has_output: bool = Field(alias="hasOutput")
    executor_stopped: bool = Field(alias="executorStopped")
    executor_stop_proof_ref: str | None = Field(
        default=None, alias="executorStopProofRef", max_length=256
    )
    replay_used: bool = Field(alias="replayUsed")

    @model_validator(mode="after")
    def _valid_stop_proof(self) -> ExecutionRecoveryV5:
        if self.executor_stopped != (self.executor_stop_proof_ref is not None):
            raise ValueError("v5 execution recovery invalid")
        if self.executor_stop_proof_ref is not None:
            _bounded_text(self.executor_stop_proof_ref, maximum=256)
        return self


class FrozenArtifactIntentV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    task_id: UUID = Field(alias="taskId")
    principal_ref: str = Field(alias="principalRef", min_length=1, max_length=256)
    conversation_id: UUID = Field(alias="conversationId")
    index: int = Field(ge=0, le=19)
    sha256_hex: str = Field(alias="sha256", min_length=64, max_length=64)
    mime_type: str = Field(alias="mimeType", min_length=3, max_length=255)
    size_bytes: int = Field(alias="sizeBytes", gt=0, le=250 * 1024 * 1024)
    display_name: str = Field(alias="displayName", min_length=1, max_length=1024)
    opaque_spool_ref: str = Field(
        alias="opaqueSpoolRef", min_length=7, max_length=256, repr=False
    )

    @model_validator(mode="after")
    def _valid_artifact(self) -> FrozenArtifactIntentV5:
        if (
            _SHA256.fullmatch(self.sha256_hex) is None
            or _MIME.fullmatch(self.mime_type) is None
            or "/" in self.display_name
            or "\\" in self.display_name
            or not self.opaque_spool_ref.startswith("spool:")
            or "/" in self.opaque_spool_ref
            or "\\" in self.opaque_spool_ref
        ):
            raise ValueError("v5 artifact intent invalid")
        _bounded_text(self.principal_ref, maximum=256)
        _bounded_text(self.display_name, maximum=1024)
        _bounded_text(self.opaque_spool_ref, maximum=256)
        return self


class ResultEventPayloadV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    source: Literal["agent_runtime"]
    source_ref: str = Field(alias="sourceRef", min_length=1, max_length=256)
    terminal: Literal["completed"]
    public_answer_markdown: str = Field(
        alias="publicAnswerMarkdown", min_length=1, max_length=131072
    )
    artifact_intents: tuple[FrozenArtifactIntentV5, ...] = Field(
        default=(), alias="artifactIntents", max_length=20
    )
    execution_recovery: ExecutionRecoveryV5 = Field(alias="executionRecovery")

    @model_validator(mode="after")
    def _valid_result(self) -> ResultEventPayloadV5:
        _bounded_text(self.source_ref, maximum=256)
        _bounded_text(self.public_answer_markdown, maximum=131072, multiline=True)
        indexes = tuple(intent.index for intent in self.artifact_intents)
        if (
            not self.execution_recovery.has_output
            or len(set(indexes)) != len(indexes)
            or indexes != tuple(range(len(indexes)))
        ):
            raise ValueError("v5 result invalid")
        return self


class RunHeartbeatPayloadV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    source: Literal["executor"]
    executor_ref: str = Field(alias="executorRef", min_length=1, max_length=256)
    observed_at: AwareDatetime = Field(alias="observedAt")
    visibility: Literal["private"]


class ErrorEventPayloadV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    source: Literal["agent_runtime"]
    source_ref: str = Field(alias="sourceRef", min_length=1, max_length=256)
    terminal: Literal["failed"]
    error_code: str = Field(alias="errorCode", min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4096)


class CancelledEventPayloadV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    source: Literal["agent_runtime"]
    source_ref: str = Field(alias="sourceRef", min_length=1, max_length=256)
    terminal: Literal["cancelled"]
    reason_code: str = Field(alias="reasonCode", min_length=1, max_length=128)


class InterruptedEventPayloadV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    source: Literal["agent_runtime"]
    source_ref: str = Field(alias="sourceRef", min_length=1, max_length=256)
    terminal: Literal["interrupted"]
    reason_code: str = Field(alias="reasonCode", min_length=1, max_length=128)
    execution_recovery: ExecutionRecoveryV5 = Field(alias="executionRecovery")


class RawProgressPayloadV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    source: Literal["provider", "agent_sdk", "agent_runtime"]
    source_ref: str = Field(alias="sourceRef", min_length=1, max_length=256)
    visibility: Literal["private"]
    kind: Literal[
        "state",
        "question",
        "file",
        "log",
        "thinking_summary",
        "work_update",
        "agent_message",
        "artifact",
    ]
    text: str = Field(min_length=1, max_length=16384)

    @model_validator(mode="after")
    def _valid_private_progress(self) -> RawProgressPayloadV5:
        _bounded_text(self.source_ref, maximum=256)
        _bounded_text(self.text, maximum=16384, multiline=True)
        return self


class CoreChatEventV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    contract_version: Literal["core_chat_collaboration_v5"] = Field(
        alias="contractVersion"
    )
    run_id: UUID = Field(alias="runId")
    command_id: UUID = Field(alias="commandId")
    attempt_id: UUID = Field(alias="attemptId")
    lease_epoch: int = Field(alias="leaseEpoch", ge=1, le=_MAX_SAFE_INTEGER)
    seq: int = Field(gt=0, le=_MAX_SAFE_INTEGER)
    event_type: Literal[
        "run_heartbeat", "result", "error", "cancelled", "interrupted", "raw_progress"
    ] = Field(alias="type")
    created_at: AwareDatetime = Field(alias="createdAt")
    payload: (
        RunHeartbeatPayloadV5
        | ResultEventPayloadV5
        | ErrorEventPayloadV5
        | CancelledEventPayloadV5
        | InterruptedEventPayloadV5
        | RawProgressPayloadV5
    )

    @model_validator(mode="after")
    def _bounded_envelope(self) -> CoreChatEventV5:
        expected_payload = {
            "run_heartbeat": RunHeartbeatPayloadV5,
            "result": ResultEventPayloadV5,
            "error": ErrorEventPayloadV5,
            "cancelled": CancelledEventPayloadV5,
            "interrupted": InterruptedEventPayloadV5,
            "raw_progress": RawProgressPayloadV5,
        }[self.event_type]
        if not isinstance(self.payload, expected_payload):
            raise ValueError("v5 event payload invalid")  # noqa: TRY004
        if len(self.model_dump_json(by_alias=True).encode("utf-8")) > _ENVELOPE_BYTES:
            raise ValueError("v5 event invalid")
        return self


class CallbackAckV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )

    status: Literal["accepted", "duplicate", "gap", "conflict"]
    run_id: UUID = Field(alias="runId")
    accepted_through: int = Field(
        alias="acceptedThrough", ge=0, le=_MAX_SAFE_INTEGER - 1
    )
    expected_seq: int = Field(alias="expectedSeq", ge=1, le=_MAX_SAFE_INTEGER)

    @model_validator(mode="after")
    def _contiguous_cursor(self) -> CallbackAckV5:
        if self.expected_seq != self.accepted_through + 1:
            raise ValueError("v5 callback acknowledgement invalid")
        return self


def core_chat_command_hash(command: CoreChatCommandV5) -> str:
    value = command.model_dump(mode="json", by_alias=True)
    attachments = [
        {
            "index": index,
            "attachmentId": grant["attachmentId"],
            "displayName": grant["displayName"],
            "detectedMime": grant["detectedMime"],
            "sizeBytes": grant["sizeBytes"],
            "sha256": grant["sha256"],
        }
        for index, grant in enumerate(value["inputAttachmentGrants"])
    ]
    output_grant = value["outputWriteGrant"]
    output_scope = (
        {
            key: output_grant[key]
            for key in ("taskId", "agentId", "maxFiles", "maxTotalBytes")
        }
        if output_grant is not None
        else None
    )
    document = {
        key: value[key]
        for key in (
            "contractVersion",
            "runId",
            "commandId",
            "attemptId",
            "attemptNo",
            "turnId",
            "turnSeq",
            "commandSeq",
            "conversationId",
            "triggerMessageId",
            "principalRef",
            "targetBot",
            "prompt",
            "contextMode",
            "contextHash",
            "taskSessionId",
            "resultMode",
            "permissionScope",
            "retryOf",
        )
    }
    document["inputAttachments"] = attachments
    document["outputScope"] = output_scope
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def core_chat_command_replay_status(
    existing: CoreChatCommandV5, incoming: CoreChatCommandV5
) -> Literal["accepted", "duplicate", "conflict"]:
    if existing.command_id != incoming.command_id:
        return "accepted"
    if core_chat_command_hash(existing) == core_chat_command_hash(incoming):
        return "duplicate"
    return "conflict"


def intake_replay_status(
    existing_request_id: UUID,
    existing_content_hash: str,
    incoming_request_id: UUID,
    incoming_content_hash: str,
) -> Literal["accepted", "duplicate", "conflict"]:
    if (
        type(existing_request_id) is not UUID
        or type(incoming_request_id) is not UUID
        or type(existing_content_hash) is not str
        or type(incoming_content_hash) is not str
        or _SHA256.fullmatch(existing_content_hash) is None
        or _SHA256.fullmatch(incoming_content_hash) is None
    ):
        raise V5ContractError("v5 intake identity invalid")
    if existing_request_id != incoming_request_id:
        return "accepted"
    if existing_content_hash == incoming_content_hash:
        return "duplicate"
    return "conflict"


def feishu_request_id(identity: FeishuMessageIdentity) -> UUID:
    """Return the stable, fully scoped Feishu intake identity."""

    if type(identity) is not FeishuMessageIdentity:
        raise V5ContractError("v5 intake identity invalid")
    name = json.dumps(
        [
            "hr-feishu-intake-v1",
            identity.tenant_id,
            identity.app_id,
            identity.bot_id,
            identity.message_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return uuid5(NAMESPACE_URL, name)


def parse_v5_command(value: dict[str, Any]) -> CoreChatCommandV5:
    try:
        expected_fields = {
            field.alias or name
            for name, field in CoreChatCommandV5.model_fields.items()
        }
        permission_fields = {
            field.alias or name
            for name, field in PermissionScopeV5.model_fields.items()
        }
        input_grant_fields = {
            field.alias or name
            for name, field in TaskAttachmentGrantPayload.model_fields.items()
        }
        output_grant_fields = {
            field.alias or name
            for name, field in OutputWriteGrantPayload.model_fields.items()
        }
        permission_scope = value.get("permissionScope") if type(value) is dict else None
        input_grants = (
            value.get("inputAttachmentGrants") if type(value) is dict else None
        )
        output_grant = value.get("outputWriteGrant") if type(value) is dict else None
        if (
            type(value) is not dict
            or set(value) != expected_fields
            or value.get("contractVersion") != "core_chat_collaboration_v5"
            or type(permission_scope) is not dict
            or set(permission_scope) != permission_fields
            or type(input_grants) is not list
            or any(
                type(grant) is not dict or set(grant) != input_grant_fields
                for grant in input_grants
            )
            or (
                output_grant is not None
                and (
                    type(output_grant) is not dict
                    or set(output_grant) != output_grant_fields
                )
            )
        ):
            raise ValueError
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > _ENVELOPE_BYTES:
            raise ValueError
        command = CoreChatCommandV5.model_validate_json(encoded, strict=True)
        if core_chat_command_hash(command) != command.command_hash:
            raise ValueError
        return command
    except (TypeError, ValueError, ValidationError):
        raise V5ContractError("v5 command invalid") from None
