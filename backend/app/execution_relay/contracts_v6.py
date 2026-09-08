"""Strict HR v6 boundary: frozen intent, scoped tools and registered results.

Lifecycle/grant invariants reuse the existing execution value types. This module
never accepts a v5 envelope and never projects business data from answer text.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .contracts_v5 import (
    CancelledEventPayloadV5, CoreChatCommandV5, CoreChatEventV5,
    ErrorEventPayloadV5, InterruptedEventPayloadV5, RawProgressPayloadV5,
    ResultEventPayloadV5, RunHeartbeatPayloadV5, canonical_command_bytes,
    core_chat_command_document as lifecycle_command_document,
    _bounded_content, _bounded_identifier, _V5_DATETIME_WIRE,
)

CONTRACT_VERSION = "core_chat_collaboration_v6"
MAX_ENVELOPE_BYTES = 1024 * 1024
SHA256 = r"^[0-9a-f]{64}$"
IDENTIFIER = r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$"
UUID_WIRE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
ToolName = Literal["hr.read_context", "hr.submit_result", "hr.confirm_standard"]
ResultSchemaId = Literal["hr.analysis.v1", "hr.standard-proposal.v1", "hr.candidate-analysis.v1"]
ResourceKind = Literal["official_position", "confirmed_standard", "material", "intelligence"]


class V6ContractError(ValueError):
    """Public validation errors must not include input or capability secrets."""


class StrictValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True,
                              populate_by_name=True, hide_input_in_errors=True)


class HrTurnScope(StrictValue):
    position_id: UUID | None = Field(alias="positionId")
    position_candidate_ids: tuple[UUID, ...] = Field(alias="positionCandidateIds", max_length=10)
    attachment_ids: tuple[UUID, ...] = Field(alias="attachmentIds", max_length=32)

    @model_validator(mode="after")
    def _scope(self):
        if (len(set(self.position_candidate_ids)) != len(self.position_candidate_ids)
            or len(set(self.attachment_ids)) != len(self.attachment_ids)
            or (self.position_id is None and self.position_candidate_ids)):
            raise ValueError("HR turn scope invalid")
        return self


class HrRolePackageRef(StrictValue):
    team_commit: str = Field(alias="teamCommit", pattern=r"^[0-9a-f]{40}$")
    catalog_release: str = Field(alias="catalogRelease", pattern=IDENTIFIER)
    manifest_sha256: str = Field(alias="manifestSha256", pattern=SHA256)


class HrMethodSelection(StrictValue):
    scenario_id: str | None = Field(alias="scenarioId", pattern=IDENTIFIER)
    method_ids: tuple[Annotated[str, Field(pattern=IDENTIFIER)], ...] = Field(alias="methodIds", min_length=1, max_length=3)
    catalog_release: str = Field(alias="catalogRelease", pattern=IDENTIFIER)

    @model_validator(mode="after")
    def _unique(self):
        if len(set(self.method_ids)) != len(self.method_ids):
            raise ValueError("method selection invalid")
        return self


class HrBusinessToolGrant(StrictValue):
    grant_id: UUID = Field(alias="grantId")
    bearer_token: str = Field(alias="bearerToken", pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)
    expires_at: AwareDatetime = Field(alias="expiresAt")


class CoreChatCommandV6(CoreChatCommandV5):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True,
                              populate_by_name=True, hide_input_in_errors=True)
    contract_version: Literal["core_chat_collaboration_v6"] = Field(alias="contractVersion")
    context_mode: Literal["frozen_intent_with_tools"] = Field(alias="contextMode")
    scope: HrTurnScope
    role_package: HrRolePackageRef = Field(alias="rolePackage")
    method_selection: HrMethodSelection | None = Field(alias="methodSelection")
    tool_capabilities: tuple[ToolName, ...] = Field(alias="toolCapabilities", max_length=3)
    business_tool_grant: HrBusinessToolGrant | None = Field(alias="businessToolGrant", repr=False)

    @model_validator(mode="after")
    def _v6_scope(self):
        _bounded_content(self.prompt, maximum=131072)
        _bounded_identifier(self.principal_ref, maximum=256)
        if (len(set(self.tool_capabilities)) != len(self.tool_capabilities)
            or bool(self.tool_capabilities) != (self.business_tool_grant is not None)
            or (self.tool_capabilities and self.permission_scope.tool_policy != "default")
            or (self.method_selection is not None
                and self.method_selection.catalog_release != self.role_package.catalog_release)
            or any(grant.attachment_id not in self.scope.attachment_ids for grant in self.input_attachment_grants)):
            raise ValueError("v6 command scope invalid")
        secrets = [grant.bearer_token for grant in self.input_attachment_grants]
        if self.output_write_grant is not None:
            secrets.append(self.output_write_grant.bearer_token)
        if self.business_tool_grant is not None:
            secrets.append(self.business_tool_grant.bearer_token)
        if any(secret in self.prompt for secret in secrets):
            raise ValueError("v6 prompt contains private capability")
        return self


class HrResultRef(StrictValue):
    result_id: UUID = Field(alias="resultId")
    schema_id: ResultSchemaId = Field(alias="schemaId")
    content_sha256: str = Field(alias="contentSha256", pattern=SHA256)


class ResultEventPayloadV6(ResultEventPayloadV5):
    result_refs: tuple[HrResultRef, ...] = Field(alias="resultRefs", max_length=20)

    @model_validator(mode="after")
    def _unique_results(self):
        if len({ref.result_id for ref in self.result_refs}) != len(self.result_refs):
            raise ValueError("v6 result references invalid")
        return self


class CoreChatEventV6(CoreChatEventV5):
    contract_version: Literal["core_chat_collaboration_v6"] = Field(alias="contractVersion")
    payload: (RunHeartbeatPayloadV5 | ResultEventPayloadV6 | ErrorEventPayloadV5
              | CancelledEventPayloadV5 | InterruptedEventPayloadV5 | RawProgressPayloadV5)


class HrSourceRef(StrictValue):
    """A recorded read, not an arbitrary unverified model-provided URL."""
    read_id: UUID = Field(alias="readId")
    content_sha256: str = Field(alias="contentSha256", pattern=SHA256)


class HrMethodStep(StrictValue):
    method_id: str = Field(alias="methodId", pattern=IDENTIFIER)
    step_id: str = Field(alias="stepId", pattern=IDENTIFIER)
    status: Literal["completed", "skipped", "blocked", "failed"]
    evidence: str = Field(min_length=1, max_length=8192)


class HrAnalysisResult(StrictValue):
    schema_id: Literal["hr.analysis.v1"] = Field(alias="schemaId")
    title: str = Field(min_length=1, max_length=256)
    markdown: str = Field(min_length=1, max_length=131072)
    source_refs: tuple[HrSourceRef, ...] = Field(alias="sourceRefs", max_length=100)
    method_steps: tuple[HrMethodStep, ...] = Field(alias="methodSteps", max_length=100)


class HrStandardChange(StrictValue):
    change_id: str = Field(alias="changeId", pattern=IDENTIFIER)
    module: Literal["mission", "jd", "jr", "competencies", "talent_profile", "sourcing_strategy", "interview_standard", "unknowns"]
    markdown: str = Field(min_length=1, max_length=32768)


class HrStandardProposal(StrictValue):
    schema_id: Literal["hr.standard-proposal.v1"] = Field(alias="schemaId")
    title: str = Field(min_length=1, max_length=256)
    base_context_version_id: UUID | None = Field(alias="baseContextVersionId")
    changes: tuple[HrStandardChange, ...] = Field(min_length=1, max_length=8)
    source_refs: tuple[HrSourceRef, ...] = Field(alias="sourceRefs", max_length=100)

    @model_validator(mode="after")
    def _unique_changes(self):
        if (len({c.change_id for c in self.changes}) != len(self.changes)
            or len({c.module for c in self.changes}) != len(self.changes)):
            raise ValueError("standard proposal changes invalid")
        return self


class HrCandidateEvidence(StrictValue):
    position_candidate_id: UUID = Field(alias="positionCandidateId")
    dimension: str = Field(min_length=1, max_length=256)
    evidence: str = Field(min_length=1, max_length=8192)
    assessment: Literal["supported", "uncertain", "contradicted"]
    verification: str = Field(min_length=1, max_length=8192)


class HrCandidateAnalysis(StrictValue):
    schema_id: Literal["hr.candidate-analysis.v1"] = Field(alias="schemaId")
    title: str = Field(min_length=1, max_length=256)
    context_version_id: UUID = Field(alias="contextVersionId")
    position_candidate_ids: tuple[UUID, ...] = Field(alias="positionCandidateIds", min_length=1, max_length=10)
    evidence: tuple[HrCandidateEvidence, ...] = Field(min_length=1, max_length=100)
    markdown: str = Field(min_length=1, max_length=131072)
    source_refs: tuple[HrSourceRef, ...] = Field(alias="sourceRefs", max_length=100)
    method_steps: tuple[HrMethodStep, ...] = Field(alias="methodSteps", max_length=100)

    @model_validator(mode="after")
    def _candidate_evidence(self):
        if (len(set(self.position_candidate_ids)) != len(self.position_candidate_ids)
            or any(item.position_candidate_id not in self.position_candidate_ids for item in self.evidence)):
            raise ValueError("candidate evidence scope invalid")
        return self


HrResult = Annotated[HrAnalysisResult | HrStandardProposal | HrCandidateAnalysis, Field(discriminator="schema_id")]


class HrReadContextRequest(StrictValue):
    tool: Literal["hr.read_context"]
    operation_id: UUID = Field(alias="operationId")
    resource_kind: ResourceKind = Field(alias="resourceKind")
    resource_id: UUID | None = Field(alias="resourceId")
    read_mode: Literal["recorded", "reverify"] = Field(alias="readMode")
    version_ref: str | None = Field(alias="versionRef", min_length=1, max_length=256)

    @model_validator(mode="after")
    def _material_identity(self):
        if self.resource_kind == "material" and self.resource_id is None:
            raise ValueError("material identity required")
        return self


class HrSubmitResultRequest(StrictValue):
    tool: Literal["hr.submit_result"]
    operation_id: UUID = Field(alias="operationId")
    result: HrResult


class HrConfirmStandardRequest(StrictValue):
    tool: Literal["hr.confirm_standard"]
    operation_id: UUID = Field(alias="operationId")
    proposal_result_id: UUID = Field(alias="proposalResultId")
    proposal_content_sha256: str = Field(alias="proposalContentSha256", pattern=SHA256)
    confirmation_message_id: UUID = Field(alias="confirmationMessageId")
    expected_context_version_id: UUID | None = Field(alias="expectedContextVersionId")
    selected_change_ids: tuple[Annotated[str, Field(pattern=IDENTIFIER)], ...] = Field(alias="selectedChangeIds", min_length=1, max_length=8)

    @model_validator(mode="after")
    def _selection(self):
        if len(set(self.selected_change_ids)) != len(self.selected_change_ids):
            raise ValueError("duplicate confirmation selection")
        return self


HrToolRequest = Annotated[HrReadContextRequest | HrSubmitResultRequest | HrConfirmStandardRequest, Field(discriminator="tool")]
_TOOL_ADAPTER = TypeAdapter(HrToolRequest)


class HrToolFailure(StrictValue):
    status: Literal["error"]
    code: Literal["permission_expired", "scope_mismatch", "version_conflict", "idempotency_conflict",
                  "source_unavailable", "result_unregistered", "confirmation_required"]
    retryable: bool
    message: str = Field(min_length=1, max_length=1024)


class HrReadContextReply(StrictValue):
    status: Literal["ok"]
    tool: Literal["hr.read_context"]
    read_id: UUID = Field(alias="readId")
    resource_kind: ResourceKind = Field(alias="resourceKind")
    resource_id: UUID = Field(alias="resourceId")
    version_ref: str = Field(alias="versionRef", min_length=1, max_length=256)
    content_sha256: str = Field(alias="contentSha256", pattern=SHA256)
    verification: Literal["current", "recorded", "degraded"]
    content_text: str = Field(alias="contentText", min_length=1, max_length=262144)


class HrSubmitResultReply(StrictValue):
    status: Literal["ok"]
    tool: Literal["hr.submit_result"]
    result_ref: HrResultRef = Field(alias="resultRef")


class HrConfirmStandardReply(StrictValue):
    status: Literal["ok"]
    tool: Literal["hr.confirm_standard"]
    context_version_id: UUID = Field(alias="contextVersionId")
    confirmed_by: UUID = Field(alias="confirmedBy")
    confirmed_at: AwareDatetime = Field(alias="confirmedAt")
    selected_change_ids: tuple[str, ...] = Field(alias="selectedChangeIds", min_length=1, max_length=8)


def core_chat_context_hash(command: CoreChatCommandV6) -> str:
    wire = command.model_dump(mode="json", by_alias=True)
    document = {key: wire[key] for key in ("prompt", "scope", "rolePackage", "methodSelection")}
    return hashlib.sha256(canonical_command_bytes(document)).hexdigest()


def core_chat_command_hash(command: CoreChatCommandV6) -> str:
    # Credentials and lease epoch can renew; their authority is checked on use.
    # Frozen capability *names*, data scope, role package and intent cannot change.
    document = lifecycle_command_document(command)
    wire = command.model_dump(mode="json", by_alias=True)
    for key in ("scope", "rolePackage", "methodSelection", "toolCapabilities"):
        document[key] = wire[key]
    return hashlib.sha256(canonical_command_bytes(document)).hexdigest()


def _strict_wire(raw: Any, parsed: Any) -> None:
    """Reject aliases, omitted defaults and UUID/date spellings on public wire."""
    if isinstance(parsed, BaseModel):
        names = {field.alias or name: name for name, field in type(parsed).model_fields.items()}
        if type(raw) is not dict or set(raw) != set(names):
            raise ValueError("wire shape invalid")
        for alias, name in names.items():
            _strict_wire(raw[alias], getattr(parsed, name))
    elif isinstance(parsed, (tuple, list)):
        if type(raw) is not list or len(raw) != len(parsed):
            raise ValueError("wire array invalid")
        for source, target in zip(raw, parsed):
            _strict_wire(source, target)
    elif isinstance(parsed, UUID):
        if type(raw) is not str or UUID_WIRE.fullmatch(raw) is None:
            raise ValueError("wire UUID invalid")
    elif isinstance(parsed, datetime):
        if type(raw) is not str or _V5_DATETIME_WIRE.fullmatch(raw) is None:
            raise ValueError("wire datetime invalid")


def _parse(value: Any, parser: Any, label: str):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_ENVELOPE_BYTES:
            raise ValueError("wire size invalid")
        parsed = parser(encoded)
        _strict_wire(value, parsed)
        return parsed
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise V6ContractError(f"v6 {label} invalid") from None


def parse_v6_command(value: Any) -> CoreChatCommandV6:
    command = _parse(value, CoreChatCommandV6.model_validate_json, "command")
    if (core_chat_context_hash(command) != command.context_hash
        or core_chat_command_hash(command) != command.command_hash):
        raise V6ContractError("v6 command invalid")
    return command


def parse_v6_event(value: Any) -> CoreChatEventV6:
    return _parse(value, CoreChatEventV6.model_validate_json, "event")


def parse_v6_tool_request(value: Any) -> HrToolRequest:
    return _parse(value, _TOOL_ADAPTER.validate_json, "tool request")


def contract_schemas() -> dict[str, dict[str, Any]]:
    """Publish exactly the boundary models; consumers never hand-edit copies."""
    schemas = {
        "command.schema.json": CoreChatCommandV6.model_json_schema(by_alias=True),
        "callback.schema.json": CoreChatEventV6.model_json_schema(by_alias=True),
        "business-tools.schema.json": _TOOL_ADAPTER.json_schema(by_alias=True),
    }
    definitions = schemas["business-tools.schema.json"]["$defs"]
    for reply in (HrToolFailure, HrReadContextReply, HrSubmitResultReply, HrConfirmStandardReply):
        schema = reply.model_json_schema(by_alias=True)
        definitions.update(schema.pop("$defs", {}))
        definitions[reply.__name__] = schema

    def normalize(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
            if node.get("format") == "uuid":
                node["pattern"] = UUID_WIRE.pattern.replace("\\Z", "$")
            node.pop("default", None)
            for item in node.values():
                normalize(item)
        elif isinstance(node, list):
            for item in node:
                normalize(item)

    for name, schema in schemas.items():
        normalize(schema)
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://agent.orbbec.com.cn/contracts/hr-execution/v6/{name}"
        schema["$comment"] = "Generated from contracts_v6.py. Runtime also checks cross-field identity, scope, canonical hashes and UTF-8 limits."
    return schemas
