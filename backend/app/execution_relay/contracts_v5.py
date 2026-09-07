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
_UUID_WIRE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
_CALLBACK_UUID_WIRE = re.compile(_UUID_WIRE.pattern, re.IGNORECASE)
_CALLBACK_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_MIME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+/[A-Za-z0-9!#$%&'*+.^_`|~-]+\Z")
_V5_DATETIME_WIRE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ]"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:[Zz]|[+-][0-9]{2}:?[0-9]{2})\Z"
)


def _bounded_identifier(value: str, *, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("v5 contract text invalid")
    return value


def _bounded_content(value: str, *, maximum: int) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("v5 contract content invalid")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError("v5 contract content invalid")
    for index, character in enumerate(value):
        codepoint = ord(character)
        if character in {"\n", "\t"}:
            continue
        if character == "\r" and index + 1 < len(value) and value[index + 1] == "\n":
            continue
        if codepoint < 32 or codepoint == 127:
            raise ValueError("v5 contract content invalid")
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
                _bounded_identifier(value, maximum=maximum)
        except (TypeError, ValueError):
            raise V5ContractError("v5 intake identity invalid") from None


class _TurnIntakeIdentityV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=False,
        hide_input_in_errors=True,
    )

    tenant_id: str = Field(alias="tenantId", min_length=1, max_length=256)
    app_id: str = Field(alias="appId", min_length=1, max_length=256)
    bot_id: Literal["hr-bot"] = Field(alias="botId")
    message_id: str = Field(alias="messageId", min_length=1, max_length=512)


class _TurnIntakeSenderV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=False,
        hide_input_in_errors=True,
    )

    open_id: str = Field(alias="openId", min_length=1, max_length=512)
    union_id: str | None = Field(alias="unionId", min_length=1, max_length=512)


class _TurnIntakeAttachmentV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=False,
        hide_input_in_errors=True,
    )

    attachment_id: UUID = Field(alias="attachmentId")
    index: int = Field(ge=0, le=31)
    sha256_hex: str = Field(alias="sha256", pattern=r"^[0-9a-f]{64}$")


class _TurnIntakeEnvelopeV5(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=False,
        hide_input_in_errors=True,
    )

    operation: Literal["turn-intake"]
    request_id: UUID = Field(alias="requestId")
    identity: _TurnIntakeIdentityV5
    sender_identity: _TurnIntakeSenderV5 = Field(alias="senderIdentity")
    chat_id: str = Field(alias="chatId", min_length=1, max_length=512)
    thread_key: str = Field(alias="threadKey", max_length=512)
    conversation_id: UUID = Field(alias="conversationId")
    principal_ref: str = Field(alias="principalRef", min_length=1, max_length=256)
    content_hash: str = Field(alias="contentHash", pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1, max_length=131072)
    attachments: tuple[_TurnIntakeAttachmentV5, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def _valid_request_identity(self) -> _TurnIntakeEnvelopeV5:
        identity = FeishuMessageIdentity(
            self.identity.tenant_id,
            self.identity.app_id,
            self.identity.bot_id,
            self.identity.message_id,
        )
        if self.request_id != feishu_request_id(identity):
            raise ValueError("v5 turn intake invalid")
        return self


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
            _bounded_identifier(self.executor_stop_proof_ref, maximum=256)
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
        _bounded_identifier(self.principal_ref, maximum=256)
        _bounded_identifier(self.display_name, maximum=1024)
        _bounded_identifier(self.opaque_spool_ref, maximum=256)
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
        _bounded_identifier(self.source_ref, maximum=256)
        _bounded_content(self.public_answer_markdown, maximum=131072)
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
        _bounded_identifier(self.source_ref, maximum=256)
        _bounded_content(self.text, maximum=16384)
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


_EVENT_PAYLOAD_MODELS = {
    "run_heartbeat": RunHeartbeatPayloadV5,
    "result": ResultEventPayloadV5,
    "error": ErrorEventPayloadV5,
    "cancelled": CancelledEventPayloadV5,
    "interrupted": InterruptedEventPayloadV5,
    "raw_progress": RawProgressPayloadV5,
}


def _wire_fields(model: type[BaseModel]) -> set[str]:
    return {field.alias or name for name, field in model.model_fields.items()}


def _has_exact_event_wire_shape(value: dict[str, Any]) -> bool:
    if set(value) != _wire_fields(CoreChatEventV5):
        return False
    event_type = value.get("type")
    payload = value.get("payload")
    payload_model = _EVENT_PAYLOAD_MODELS.get(event_type)
    if type(payload) is not dict or payload_model is None:
        return False
    if set(payload) != _wire_fields(payload_model):
        return False
    if event_type == "result":
        artifact_intents = payload.get("artifactIntents")
        recovery = payload.get("executionRecovery")
        if (
            type(artifact_intents) is not list
            or any(
                type(intent) is not dict
                or set(intent) != _wire_fields(FrozenArtifactIntentV5)
                for intent in artifact_intents
            )
            or type(recovery) is not dict
            or set(recovery) != _wire_fields(ExecutionRecoveryV5)
        ):
            return False
    if event_type == "interrupted":
        recovery = payload.get("executionRecovery")
        if (
            type(recovery) is not dict
            or set(recovery) != _wire_fields(ExecutionRecoveryV5)
        ):
            return False
    return True


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


def core_chat_command_document(command: CoreChatCommandV5) -> dict[str, Any]:
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
    return document


def canonical_command_bytes(document: dict[str, Any]) -> bytes:
    """The P01 canonical byte encoding, also used by frozen internal templates."""
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def core_chat_command_hash(command: CoreChatCommandV5) -> str:
    encoded = canonical_command_bytes(core_chat_command_document(command))
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


def turn_intake_content_hash(value: dict[str, Any]) -> str:
    """Hash the frozen business projection of a strict turn-intake wire body."""

    try:
        if type(value) is not dict:
            raise ValueError
        identity = value.get("identity")
        attachments = value.get("attachments")
        if (
            type(identity) is not dict
            or type(attachments) is not list
            or any(type(attachment) is not dict for attachment in attachments)
            or any(
                _UUID_WIRE.fullmatch(uuid_value) is None
                for uuid_value in (
                    value.get("requestId"),
                    value.get("conversationId"),
                    *(attachment.get("attachmentId") for attachment in attachments),
                )
                if type(uuid_value) is str
            )
            or any(
                type(uuid_value) is not str
                for uuid_value in (
                    value.get("requestId"),
                    value.get("conversationId"),
                    *(attachment.get("attachmentId") for attachment in attachments),
                )
            )
        ):
            raise ValueError
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > _ENVELOPE_BYTES:
            raise ValueError
        envelope = _TurnIntakeEnvelopeV5.model_validate_json(encoded, strict=True)
        wire = envelope.model_dump(mode="json", by_alias=True)
        document = {
            key: wire[key]
            for key in (
                "operation",
                "identity",
                "senderIdentity",
                "chatId",
                "threadKey",
                "conversationId",
                "principalRef",
                "text",
                "attachments",
            )
        }
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
    except (TypeError, ValueError, ValidationError):
        raise V5ContractError("v5 turn intake invalid") from None


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
            or any(
                type(expires_at := grant.get("expiresAt")) is not str
                or _V5_DATETIME_WIRE.fullmatch(expires_at) is None
                for grant in input_grants
            )
            or (
                output_grant is not None
                and (
                    type(output_grant) is not dict
                    or set(output_grant) != output_grant_fields
                )
            )
            or any(
                type(uuid_value) is not str
                or _UUID_WIRE.fullmatch(uuid_value) is None
                for uuid_value in (
                    value.get("runId"),
                    value.get("commandId"),
                    value.get("attemptId"),
                    value.get("turnId"),
                    value.get("conversationId"),
                    value.get("triggerMessageId"),
                    permission_scope.get("conversationId"),
                    *(grant.get("attachmentId") for grant in input_grants),
                    *(
                        (output_grant.get("taskId"),)
                        if output_grant is not None
                        else ()
                    ),
                    *((value.get("retryOf"),) if value.get("retryOf") is not None else ()),
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


def parse_v5_event(value: dict[str, Any]) -> CoreChatEventV5:
    """Parse an untrusted camelCase event without exposing rejected content."""

    try:
        if type(value) is not dict or not _has_exact_event_wire_shape(value):
            raise ValueError
        uuid_values = [value.get(key) for key in ("runId", "commandId", "attemptId")]
        if value["type"] == "result":
            uuid_values.extend(
                intent.get(key)
                for intent in value["payload"]["artifactIntents"]
                for key in ("taskId", "conversationId")
            )
        if any(
            type(uuid_value) is not str
            or _CALLBACK_UUID_WIRE.fullmatch(uuid_value) is None
            for uuid_value in uuid_values
        ):
            raise ValueError
        dates = [value.get("createdAt")]
        if value["type"] == "run_heartbeat":
            dates.append(value["payload"].get("observedAt"))
        if any(
            type(date) is not str or _V5_DATETIME_WIRE.fullmatch(date) is None
            for date in dates
        ):
            raise ValueError
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > _ENVELOPE_BYTES:
            raise ValueError
        return CoreChatEventV5.model_validate_json(encoded, strict=True)
    except (TypeError, ValueError, ValidationError):
        raise V5ContractError("v5 event invalid") from None
