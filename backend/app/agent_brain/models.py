from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_catalog import load_agent_catalog


CALLABLE_AGENT_IDS = (
    "hr-bot",
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
    supports_persistent_session: bool
    supports_followup_message: bool
    supports_progress_events: bool
    supports_thinking_summary: bool
    supports_cancel: bool
    supports_attachments: bool
    typical_latency_seconds: int = Field(ge=1, le=600)
    max_duration_seconds: int = Field(ge=1, le=300)
    data_classification: Literal["internal"] = "internal"
    adapter_id: str = Field(min_length=1, max_length=128)
    adapter_kind: str = Field(
        default="metabot_local", pattern=r"^[a-z][a-z0-9_]{0,63}$"
    )
    adapter_config_version: int = Field(default=1, gt=0)
    execution_pool: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    pool_concurrency: int = Field(ge=1, le=16)
    output_contract: Literal["normalized_task_result_v1"] = (
        "normalized_task_result_v1"
    )
    capability_version: int = Field(gt=0)


def load_capability_cards(
    path: str | Path | None = None,
) -> tuple[AgentCapabilityCard, ...]:
    """Project the canonical product Catalog into Brain-delegatable cards."""

    catalog = load_agent_catalog(path)
    delegated = tuple(card for card in catalog if card.dispatchable)
    if tuple(card.agent_id for card in delegated) != CALLABLE_AGENT_IDS:
        raise ValueError("capability Agent IDs must match callable allowlist")
    projected: list[AgentCapabilityCard] = []
    for card in delegated:
        if card.adapter_id is None or card.adapter_kind is None:
            raise ValueError("delegated Agent requires an Adapter")
        projected.append(
            AgentCapabilityCard(
                agent_id=card.agent_id,
                display_name=card.display_name,
                domain_group=card.domain_group,
                mission=card.mission,
                capabilities=card.capabilities,
                exclusions=card.exclusions,
                example_tasks=card.example_tasks,
                required_inputs=card.required_inputs,
                accepted_input_types=card.accepted_input_types,
                output_types=card.output_types,
                supports_attachments_in=card.supports_attachments_in,
                supports_attachments_out=card.supports_attachments_out,
                supports_evidence=card.supports_evidence,
                supports_streaming=card.supports_streaming,
                supports_cancellation=card.supports_cancellation,
                supports_idempotency=card.supports_idempotency,
                supports_persistent_session=card.supports_persistent_session,
                supports_followup_message=card.supports_followup_message,
                supports_progress_events=card.supports_progress_events,
                supports_thinking_summary=card.supports_thinking_summary,
                supports_cancel=card.supports_cancel,
                supports_attachments=card.supports_attachments,
                typical_latency_seconds=card.typical_latency_seconds,
                max_duration_seconds=card.max_duration_seconds,
                data_classification=card.data_classification,
                adapter_id=card.adapter_id,
                adapter_kind=card.adapter_kind,
                adapter_config_version=card.adapter_config_version,
                execution_pool=card.execution_pool,
                pool_concurrency=card.pool_concurrency,
                output_contract=card.output_contract,
                capability_version=card.capability_version,
            )
        )
    return tuple(projected)
