"""Public source metadata; native work and execution records are not chat turns.

This registry describes data location and access semantics. It does not probe
runtime health, decrypt content, or grant access to the linked workspaces.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.fleet.catalog import AgentCatalog
from .models import AgentSourceStatus

Mode = Literal["cloud_direct", "office", "native"]


class DataSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    name: str
    agent_id: str | None
    source_kind: str
    intended_mode: Mode
    mode: Mode | None
    mode_label: str
    unit: Literal["session", "work", "execution"]
    permission_scope: Literal["observation_scope", "owner_scope", "business_scope"]
    workspace_url: str
    persistence: str
    source_observed_at: datetime | None = None
    source_status: AgentSourceStatus | None = None
    runtime_status: Literal["active", "inactive"] | None = None
    feedback_availability: Literal["available", "unavailable"] = "unavailable"
    review_availability: Literal["available", "unavailable"] = "unavailable"


_LABELS = {"cloud_direct": "云端直连", "office": "办公室发布", "native": "无需同步"}
_CLOUD = (
    ("ai-fae-agent", "FAE", "fae"),
    ("ai-daq-fae-agent", "DAQ", "fae"),
    ("ai-admin-agent", "ADMIN", "admin"),
    ("voc", "VOC", "voc"),
)


def public_source_registry(
    states: Mapping[str, AgentSourceStatus], *, catalog: AgentCatalog | None = None,
) -> tuple[DataSource, ...]:
    """Project only proven source watermarks; static catalog entries imply no health."""
    records = []
    for agent_id, name, family in _CLOUD:
        status = states.get(agent_id)
        mode = status.mode if status else None
        records.append(DataSource(
            source_id=agent_id, name=name, agent_id=agent_id, source_kind=family,
            intended_mode="cloud_direct", mode=mode,
            mode_label=_LABELS[mode] if mode else "未验证",
            unit="session", permission_scope="observation_scope",
            workspace_url=f"/admin/sessions?agent_id={agent_id}",
            persistence="platform_replica",
            source_observed_at=status.last_source_success_at if status else None,
            source_status=status,
            feedback_availability="unavailable" if family == "voc" else "available",
            review_availability="unavailable" if family == "voc" else "available",
        ))
    records.extend((
        DataSource(
            source_id="native-hr", name="HR", agent_id="hr-bot", source_kind="hr",
            intended_mode="native", mode="native", mode_label=_LABELS["native"],
            unit="work", permission_scope="owner_scope", workspace_url="/hr/",
            persistence="platform_hr_agent",
        ),
        DataSource(
            source_id="native-sdk", name="SDK", agent_id=None, source_kind="sdk",
            intended_mode="native", mode="native", mode_label=_LABELS["native"],
            unit="execution", permission_scope="business_scope", workspace_url="/sdk/",
            persistence="sdk/zentao",
        ),
        DataSource(
            source_id="office-metabot", name="MetaBot", agent_id=None, source_kind="metabot",
            intended_mode="office", mode="office", mode_label=_LABELS["office"],
            unit="session", permission_scope="observation_scope",
            workspace_url="/admin/sessions?source_kind=metabot", persistence="office",
        ),
    ))
    for profile in (catalog or AgentCatalog.default()).all_profiles():
        if not profile.id.startswith("marketing-"):
            continue
        status = states.get(profile.id)
        records.append(DataSource(
            source_id=profile.id, name=profile.name, agent_id=profile.id,
            source_kind="metabot", intended_mode="office",
            mode=status.mode if status else "office",
            mode_label=_LABELS[status.mode] if status else _LABELS["office"],
            unit="session", permission_scope="observation_scope", persistence="office",
            workspace_url=f"/admin/sessions?agent_id={profile.id}",
            source_observed_at=status.last_source_success_at if status else None,
            source_status=status,
        ))
    return tuple(records)
