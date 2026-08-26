from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.agent_brain.models import load_capability_cards
from app.agent_brain.runtime_registry import (
    AgentHealthObservation,
    RuntimeAgentRegistry,
)


NOW = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
USER_ID = uuid4()


class FakeAuthorization:
    def __init__(self) -> None:
        self.allowed = {"hr-bot"}
        self.generation = uuid4()

    def permitted_agents_for_user_id(self, _user_id):
        return tuple(
            card for card in load_capability_cards() if card.agent_id in self.allowed
        )

    def decide_for_user_id(self, _user_id, agent_id):
        return {
            "allowed": agent_id in self.allowed,
            "grant_ids": (uuid4(),) if agent_id in self.allowed else (),
            "directory_generation_id": self.generation,
        }


class FakeHealth:
    def __init__(self, observations=None) -> None:
        self.observations = observations or {}

    def for_agent(self, agent_id):
        return self.observations.get(agent_id)


def _registry(authorization=None, health=None, registered=None):
    return RuntimeAgentRegistry(
        authorization=authorization or FakeAuthorization(),
        health=health or FakeHealth(),
        registered_adapter_kinds=registered or {"metabot_local", "reference"},
        now=lambda: NOW,
        freshness_seconds=120,
    )


def test_directory_generation_change_does_not_change_effective_hash() -> None:
    authorization = FakeAuthorization()
    registry = _registry(authorization=authorization)
    first = registry.list_for_user(USER_ID)
    authorization.generation = uuid4()
    second = registry.list_for_user(USER_ID)

    assert first[0].directory_generation_id != second[0].directory_generation_id
    assert first[0].effective_decision_hash == second[0].effective_decision_hash


def test_capability_change_rejects_new_task_without_revoking_loop() -> None:
    registry = _registry()

    decision = registry.authorize_task(
        USER_ID,
        "hr-bot",
        expected_capability_version=999,
    )

    assert decision.allowed is False
    assert decision.reason_code == "capability_changed"
    assert decision.effective_decision_hash is not None


def test_genuine_allow_to_deny_changes_effective_hash() -> None:
    authorization = FakeAuthorization()
    registry = _registry(authorization=authorization)
    allowed = registry.authorize_task(USER_ID, "hr-bot", 2)
    authorization.allowed.clear()
    denied = registry.authorize_task(USER_ID, "hr-bot", 2)

    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason_code == "authorization_changed"
    assert allowed.effective_decision_hash != denied.effective_decision_hash


def test_list_keeps_authorized_agent_with_missing_adapter_as_unavailable() -> None:
    authorization = FakeAuthorization()
    authorization.allowed = {"hr-bot"}
    registry = _registry(
        authorization=authorization,
        registered={"reference"},
    )

    listed = registry.list_for_user(USER_ID)
    assert tuple(item.agent_id for item in listed) == ("hr-bot",)
    assert listed[0].availability == "unavailable"
    assert listed[0].health_fresh is False
    unknown = registry.authorize_task(USER_ID, "missing-bot", 1)
    assert unknown.allowed is False
    assert unknown.reason_code == "agent_unavailable"


def test_health_states_staleness_and_unknown_latency_are_explicit() -> None:
    observations = {
        "hr-bot": AgentHealthObservation(
            state="online",
            sampled_at=NOW - timedelta(seconds=20),
            latency_p50_ms=120,
            latency_p95_ms=420,
            sample_count=30,
        )
    }
    item = _registry(health=FakeHealth(observations)).list_for_user(USER_ID)[0]
    assert item.availability == "healthy"
    assert item.health_fresh is True
    assert item.latency_p50_ms == 120
    assert item.latency_p95_ms == 420

    observations["hr-bot"] = AgentHealthObservation(
        state="degraded",
        sampled_at=NOW - timedelta(minutes=10),
        latency_p50_ms=None,
        latency_p95_ms=None,
        sample_count=0,
    )
    stale = _registry(health=FakeHealth(observations)).list_for_user(USER_ID)[0]
    assert stale.availability == "unknown"
    assert stale.health_fresh is False
    assert stale.latency_p50_ms is None
    assert stale.latency_p95_ms is None


def test_snapshot_exposes_adapter_and_public_capability_contract() -> None:
    item = _registry().list_for_user(USER_ID)[0]

    assert item.adapter_kind == "metabot_local"
    assert item.adapter_config_version == 1
    assert item.accepted_input_types == ("text",)
    assert item.output_contract == "normalized_task_result_v1"
    assert item.supports_idempotency is True
    assert item.max_duration_seconds == 300
    assert item.supports_persistent_session is True
    assert item.supports_followup_message is True
    assert item.supports_progress_events is True
    assert item.supports_thinking_summary is True
    assert item.supports_cancel is True
    assert item.supports_attachments is False
    assert item.typical_latency_seconds == 90
