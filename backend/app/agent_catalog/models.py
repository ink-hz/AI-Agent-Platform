from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


InteractionMode = Literal["direct_chat", "brain_delegation", "external_workspace"]

CANONICAL_AGENT_IDS = (
    "hr-bot",
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
    "ai-fae-agent": "https://fae.orbbec.com.cn/",
}


class AgentCatalogCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
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
    accepted_input_types: tuple[Literal["text"], ...] = ("text",)
    output_types: tuple[Literal["text"], ...] = ("text",)
    supports_attachments_in: bool = False
    supports_attachments_out: bool = False
    supports_evidence: bool = True
    supports_streaming: bool = True
    supports_cancellation: bool = True
    supports_idempotency: bool = True
    max_duration_seconds: int = Field(default=300, ge=1, le=300)
    data_classification: Literal["internal"] = "internal"
    output_contract: Literal["normalized_task_result_v1"] = "normalized_task_result_v1"
    capability_version: int = Field(gt=0)
    authorization_policy: Literal["agent_grant"] = "agent_grant"

    @field_validator(
        "capabilities", "exclusions", "required_inputs", "example_tasks", "interaction_modes"
    )
    @classmethod
    def _unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("catalog list values must be non-empty and unique")
        return normalized

    @model_validator(mode="after")
    def _validate_delivery_contract(self) -> "AgentCatalogCard":
        modes = set(self.interaction_modes)
        external_only = modes == {"external_workspace"}
        if external_only:
            if self.adapter_kind is not None or self.adapter_id is not None:
                raise ValueError("external workspace Agent cannot declare an Adapter")
            if self.workspace_url != _WORKSPACE_URLS.get(self.agent_id):
                raise ValueError("external workspace URL is not allowlisted")
        else:
            if "external_workspace" in modes or not modes <= {"direct_chat", "brain_delegation"}:
                raise ValueError("interaction mode combination invalid")
            if self.adapter_kind is None or self.adapter_id is None or self.workspace_url is not None:
                raise ValueError("direct or delegated Agent requires only an Adapter")
        return self

    @property
    def dispatchable(self) -> bool:
        return "brain_delegation" in self.interaction_modes


class _CatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: tuple[AgentCatalogCard, ...] = Field(min_length=1)


def load_agent_catalog(path: str | Path | None = None) -> tuple[AgentCatalogCard, ...]:
    selected = Path(path) if path is not None else Path(__file__).with_name("catalog.yaml")
    try:
        payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
        document = _CatalogDocument.model_validate(payload)
        ids = tuple(card.agent_id for card in document.agents)
        if ids != CANONICAL_AGENT_IDS or len(ids) != len(set(ids)):
            raise ValueError
        return document.agents
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, TypeError, ValueError):
        raise ValueError("Agent Catalog invalid") from None
