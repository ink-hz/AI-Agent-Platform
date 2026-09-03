from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

InteractionMode = Literal["direct_chat", "brain_delegation", "external_workspace"]
AgentContentType = Literal["text", "image", "pdf", "office"]

CANONICAL_AGENT_IDS = (
    "hr-bot",
    "voc",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
    "ai-admin-agent",
    "ai-fae-agent",
)

_WORKSPACE_URLS = {
    "ai-admin-agent": "/office/?view=services",
    "ai-fae-agent": "/fae/",
    "voc": "/voc/",
}


class AgentAttachmentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_file_bytes: int = Field(gt=0, le=50 * 1024 * 1024)
    max_files_per_message: int = Field(gt=0, le=5)
    max_bytes_per_message: int = Field(gt=0, le=50 * 1024 * 1024)
    max_files_per_conversation: int = Field(gt=0, le=50)
    max_bytes_per_conversation: int = Field(gt=0, le=500 * 1024 * 1024)

    @model_validator(mode="after")
    def _validate_totals(self) -> AgentAttachmentLimits:
        if (
            self.max_bytes_per_message < self.max_file_bytes
            or self.max_files_per_conversation < self.max_files_per_message
            or self.max_bytes_per_conversation < self.max_bytes_per_message
        ):
            raise ValueError("attachment limits invalid")
        return self


class AgentCatalogCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    persona_subtitle: str | None = Field(default=None, min_length=1, max_length=160)
    domain_group: str = Field(min_length=1, max_length=128)
    mission: str = Field(min_length=1, max_length=1_024)
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=16)
    exclusions: tuple[str, ...] = Field(min_length=1, max_length=16)
    required_inputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    example_tasks: tuple[str, ...] = Field(min_length=1, max_length=8)
    interaction_modes: tuple[InteractionMode, ...] = Field(min_length=1, max_length=3)
    workspace_url: str | None = None
    adapter_id: str | None = Field(default=None, max_length=128)
    adapter_kind: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    adapter_config_version: int = Field(default=1, gt=0)
    execution_pool: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    pool_concurrency: int | None = Field(default=None, ge=1, le=16)
    accepted_input_types: tuple[AgentContentType, ...] = Field(
        default=("text",), min_length=1, max_length=4
    )
    output_types: tuple[AgentContentType, ...] = Field(
        default=("text",), min_length=1, max_length=4
    )
    supports_attachments_in: bool = False
    supports_attachments_out: bool = False
    attachment_limits: AgentAttachmentLimits | None = None
    supports_evidence: bool = True
    supports_streaming: bool = True
    supports_cancellation: bool = True
    supports_idempotency: bool = True
    supports_persistent_session: bool
    supports_followup_message: bool
    supports_progress_events: bool
    supports_thinking_summary: bool
    supports_cancel: bool
    supports_attachments: bool
    typical_latency_seconds: int = Field(ge=1, le=600)
    max_duration_seconds: int = Field(default=300, ge=1, le=300)
    data_classification: Literal["internal"] = "internal"
    output_contract: Literal["normalized_task_result_v1"] = "normalized_task_result_v1"
    capability_version: int = Field(gt=0)
    authorization_policy: Literal["agent_grant"] = "agent_grant"

    @field_validator(
        "capabilities",
        "exclusions",
        "required_inputs",
        "example_tasks",
        "interaction_modes",
        "accepted_input_types",
        "output_types",
    )
    @classmethod
    def _unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized) or len(normalized) != len(
            set(normalized)
        ):
            raise ValueError("catalog list values must be non-empty and unique")
        return normalized

    @model_validator(mode="after")
    def _validate_delivery_contract(self) -> AgentCatalogCard:
        if self.accepted_input_types[0] != "text" or self.output_types[0] != "text":
            raise ValueError("text must be the first content type")
        has_attachment_input = any(
            value != "text" for value in self.accepted_input_types
        )
        has_attachment_output = any(value != "text" for value in self.output_types)
        if (
            self.supports_attachments_in is not has_attachment_input
            or self.supports_attachments_out is not has_attachment_output
            or self.supports_attachments
            is not (has_attachment_input or has_attachment_output)
            or (self.attachment_limits is None) is not (not has_attachment_input)
        ):
            raise ValueError("attachment capability contract invalid")
        modes = set(self.interaction_modes)
        external_only = modes == {"external_workspace"}
        has_external_workspace = "external_workspace" in modes
        is_callable = bool(modes & {"direct_chat", "brain_delegation"})
        if has_external_workspace:
            if modes not in (
                {"external_workspace"},
                {"external_workspace", "brain_delegation"},
            ):
                raise ValueError("interaction mode combination invalid")
            if self.workspace_url != _WORKSPACE_URLS.get(self.agent_id):
                raise ValueError("external workspace URL is not allowlisted")
        elif self.workspace_url is not None:
            raise ValueError("direct or delegated Agent cannot declare a workspace URL")
        if external_only:
            if self.adapter_kind is not None or self.adapter_id is not None:
                raise ValueError("external workspace Agent cannot declare an Adapter")
            if self.execution_pool is not None or self.pool_concurrency is not None:
                raise ValueError("external workspace Agent has no execution pool")
            if any(
                (
                    self.supports_persistent_session,
                    self.supports_followup_message,
                    self.supports_progress_events,
                    self.supports_thinking_summary,
                    self.supports_cancel,
                    self.supports_attachments,
                )
            ):
                raise ValueError(
                    "external workspace cannot declare Adapter capabilities"
                )
        elif is_callable:
            if self.adapter_kind is None or self.adapter_id is None:
                raise ValueError("direct or delegated Agent requires an Adapter")
            if self.execution_pool is None or self.pool_concurrency is None:
                # The Brain schedules against the pool's real capacity, so an Agent
                # that can be dispatched must say which executor it contends for.
                raise ValueError("delegated Agent requires an execution pool")
        else:
            raise ValueError("interaction mode combination invalid")
        return self

    @property
    def dispatchable(self) -> bool:
        return "brain_delegation" in self.interaction_modes


class _CatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: tuple[AgentCatalogCard, ...] = Field(min_length=1)


def _require_consistent_pools(cards: tuple[AgentCatalogCard, ...]) -> None:
    """Reject a Catalog where one execution pool declares two capacities.

    The Brain admits work against the pool's declared concurrency. If two cards in
    the same pool disagree, the Brain plans against whichever card it read last and
    the pool silently queues behind it.
    """

    declared: dict[str, int] = {}
    for card in cards:
        if card.execution_pool is None or card.pool_concurrency is None:
            continue
        existing = declared.setdefault(card.execution_pool, card.pool_concurrency)
        if existing != card.pool_concurrency:
            raise ValueError("execution pool concurrency conflict")


def load_agent_catalog(path: str | Path | None = None) -> tuple[AgentCatalogCard, ...]:
    selected = (
        Path(path) if path is not None else Path(__file__).with_name("catalog.yaml")
    )
    try:
        payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
        document = _CatalogDocument.model_validate(payload)
        ids = tuple(card.agent_id for card in document.agents)
        if ids != CANONICAL_AGENT_IDS or len(ids) != len(set(ids)):
            raise ValueError
        _require_consistent_pools(document.agents)
        return document.agents
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        raise ValueError("Agent Catalog invalid") from None
