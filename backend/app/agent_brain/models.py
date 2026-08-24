from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.fleet.catalog import AgentCatalog


CALLABLE_AGENT_IDS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
)


class AgentCapabilityCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    domain_group: str = Field(min_length=1, max_length=128)
    mission: str = Field(min_length=1, max_length=1_024)
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=16)
    exclusions: tuple[str, ...] = Field(min_length=1, max_length=16)
    example_tasks: tuple[str, ...] = Field(min_length=1, max_length=8)
    required_inputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    accepted_input_types: tuple[Literal["text"], ...] = ("text",)
    output_types: tuple[Literal["text"], ...] = ("text",)
    supports_attachments_in: bool = False
    supports_attachments_out: bool = False
    supports_evidence: bool
    supports_streaming: bool
    supports_cancellation: bool
    supports_idempotency: bool
    max_duration_seconds: int = Field(ge=1, le=300)
    data_classification: Literal["internal"] = "internal"
    adapter_id: str = Field(min_length=1, max_length=128)
    adapter_kind: str = Field(
        default="metabot_local", pattern=r"^[a-z][a-z0-9_]{0,63}$"
    )
    adapter_config_version: int = Field(default=1, gt=0)
    output_contract: Literal["normalized_task_result_v1"] = (
        "normalized_task_result_v1"
    )
    capability_version: int = Field(gt=0)


class _CapabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1, max_length=128)
    mission: str = Field(min_length=1, max_length=1_024)
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=16)
    exclusions: tuple[str, ...] = Field(min_length=1, max_length=16)
    example_tasks: tuple[str, ...] = Field(min_length=1, max_length=8)
    required_inputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    accepted_input_types: tuple[Literal["text"], ...] = ("text",)
    output_types: tuple[Literal["text"], ...] = ("text",)
    supports_attachments_in: bool = False
    supports_attachments_out: bool = False
    supports_evidence: bool
    supports_streaming: bool
    supports_cancellation: bool
    supports_idempotency: bool
    max_duration_seconds: int = Field(ge=1, le=300)
    data_classification: Literal["internal"] = "internal"
    adapter_id: str = Field(min_length=1, max_length=128)
    adapter_kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    adapter_config_version: int = Field(gt=0)
    output_contract: Literal["normalized_task_result_v1"]
    capability_version: int = Field(gt=0)

    @field_validator(
        "capabilities", "exclusions", "example_tasks", "required_inputs"
    )
    @classmethod
    def _non_empty_unique_text(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("capability entries must be non-empty text")
        normalized = tuple(value.strip() for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("capability entries must be unique")
        return normalized


class _CapabilityDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: tuple[_CapabilitySpec, ...] = Field(min_length=1)


def _read_document(path: Path) -> _CapabilityDocument:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return _CapabilityDocument.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, TypeError):
        raise ValueError("capability catalog invalid") from None


def load_capability_cards(
    path: str | Path | None = None,
    *,
    fleet_catalog: AgentCatalog | None = None,
) -> tuple[AgentCapabilityCard, ...]:
    """Load and fully validate the immutable public capability catalog."""

    selected_path = (
        Path(path) if path is not None else Path(__file__).with_name("capabilities.yaml")
    )
    document = _read_document(selected_path)
    ids = tuple(spec.agent_id for spec in document.agents)
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate capability Agent ID")
    if set(ids) != set(CALLABLE_AGENT_IDS):
        raise ValueError("capability Agent IDs must match callable allowlist")

    specs = {spec.agent_id: spec for spec in document.agents}
    catalog = fleet_catalog or AgentCatalog.default()
    cards: list[AgentCapabilityCard] = []
    for agent_id in CALLABLE_AGENT_IDS:
        profile = catalog.profile(agent_id, agent_id)
        if profile.id != agent_id or profile.visibility != "business":
            raise ValueError("callable Agent missing from business catalog")
        spec = specs[agent_id]
        values = spec.model_dump()
        cards.append(
            AgentCapabilityCard(
                display_name=profile.name,
                domain_group=profile.domain,
                **values,
            )
        )
    return tuple(cards)
