from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal, Protocol
from uuid import UUID

from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from app.agent_brain.models import AgentCapabilityCard, load_capability_cards


Availability = Literal["healthy", "degraded", "offline", "unknown", "unavailable"]


@dataclass(frozen=True, slots=True)
class AgentHealthObservation:
    state: str
    sampled_at: datetime | None
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    sample_count: int


@dataclass(frozen=True, slots=True)
class RuntimeAgentSnapshot:
    agent_id: str
    display_name: str
    mission: str
    capabilities: tuple[str, ...]
    exclusions: tuple[str, ...]
    required_inputs: tuple[str, ...]
    accepted_input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    output_contract: str
    adapter_kind: str
    adapter_config_version: int
    capability_version: int
    availability: Availability
    health_sampled_at: datetime | None
    health_fresh: bool
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    max_duration_seconds: int
    supports_attachments_in: bool
    supports_attachments_out: bool
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
    typical_latency_seconds: int
    grant_ids: tuple[UUID, ...]
    directory_generation_id: UUID | None
    effective_decision_hash: bytes


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason_code: Literal[
        "allowed",
        "authorization_changed",
        "capability_changed",
        "agent_unavailable",
        "authorization_unavailable",
    ]
    agent_id: str
    capability_version: int | None
    adapter_kind: str | None
    grant_ids: tuple[UUID, ...]
    directory_generation_id: UUID | None
    effective_decision_hash: bytes | None


class _HealthSource(Protocol):
    def for_agent(self, agent_id: str) -> AgentHealthObservation | None: ...


class RuntimeAgentRegistry:
    def __init__(
        self,
        *,
        authorization: object,
        health: _HealthSource,
        registered_adapter_kinds: Collection[str],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        freshness_seconds: int = 120,
    ) -> None:
        if (
            not hasattr(authorization, "permitted_agents_for_user_id")
            or not hasattr(authorization, "decide_for_user_id")
            or not hasattr(health, "for_agent")
            or isinstance(registered_adapter_kinds, (str, bytes))
            or not registered_adapter_kinds
            or type(freshness_seconds) is not int
            or freshness_seconds <= 0
        ):
            raise ValueError("runtime Agent Registry configuration invalid")
        self._authorization = authorization
        self._health = health
        self._registered_adapter_kinds = frozenset(registered_adapter_kinds)
        self._now = now
        self._freshness_seconds = freshness_seconds
        cards = getattr(authorization, "capability_cards", None)
        self._cards = tuple(cards) if cards is not None else load_capability_cards()
        self._cards_by_id = {card.agent_id: card for card in self._cards}

    def list_for_user(
        self, internal_user_id: UUID
    ) -> tuple[RuntimeAgentSnapshot, ...]:
        if not isinstance(internal_user_id, UUID):
            return ()
        try:
            permitted = self._authorization.permitted_agents_for_user_id(
                internal_user_id
            )
            snapshots = []
            for card in permitted:
                decision = self._decision_value(
                    self._authorization.decide_for_user_id(
                        internal_user_id, card.agent_id
                    )
                )
                if not decision["allowed"]:
                    continue
                snapshots.append(
                    self._compose(
                        card,
                        internal_user_id=internal_user_id,
                        decision=decision,
                    )
                )
            return tuple(snapshots)
        except (AgentUseAuthorizationUnavailable, KeyError, TypeError, ValueError):
            return ()

    def authorize_task(
        self,
        internal_user_id: UUID,
        agent_id: str,
        expected_capability_version: int,
    ) -> AuthorizationDecision:
        card = self._cards_by_id.get(agent_id) if isinstance(agent_id, str) else None
        if card is None or card.adapter_kind not in self._registered_adapter_kinds:
            return AuthorizationDecision(
                False,
                "agent_unavailable",
                agent_id if isinstance(agent_id, str) else "",
                None,
                None,
                (),
                None,
                _decision_hash(internal_user_id, agent_id, False)
                if isinstance(internal_user_id, UUID) and isinstance(agent_id, str)
                else None,
            )
        try:
            value = self._decision_value(
                self._authorization.decide_for_user_id(internal_user_id, agent_id)
            )
        except (AgentUseAuthorizationUnavailable, KeyError, TypeError, ValueError):
            return AuthorizationDecision(
                False,
                "authorization_unavailable",
                agent_id,
                card.capability_version,
                card.adapter_kind,
                (),
                None,
                None,
            )
        allowed = bool(value["allowed"])
        digest = _decision_hash(internal_user_id, agent_id, allowed)
        if type(expected_capability_version) is not int or (
            expected_capability_version != card.capability_version
        ):
            reason = "capability_changed"
            effective_allowed = False
        elif not allowed:
            reason = "authorization_changed"
            effective_allowed = False
        else:
            reason = "allowed"
            effective_allowed = True
        return AuthorizationDecision(
            effective_allowed,
            reason,
            agent_id,
            card.capability_version,
            card.adapter_kind,
            value["grant_ids"],
            value["directory_generation_id"],
            digest,
        )

    def _compose(
        self,
        card: AgentCapabilityCard,
        *,
        internal_user_id: UUID,
        decision: dict[str, object],
    ) -> RuntimeAgentSnapshot:
        observation = self._health.for_agent(card.agent_id)
        fresh = False
        adapter_registered = card.adapter_kind in self._registered_adapter_kinds
        availability: Availability = "unknown" if adapter_registered else "unavailable"
        p50 = None
        p95 = None
        sampled_at = None
        if adapter_registered and isinstance(observation, AgentHealthObservation):
            sampled_at = observation.sampled_at
            if sampled_at is not None:
                if sampled_at.tzinfo is None:
                    sampled_at = sampled_at.replace(tzinfo=timezone.utc)
                age = (self._now() - sampled_at).total_seconds()
                fresh = 0 <= age <= self._freshness_seconds
            if fresh:
                availability = _availability(observation.state)
                if observation.sample_count > 0:
                    p50 = observation.latency_p50_ms
                    p95 = observation.latency_p95_ms
        return RuntimeAgentSnapshot(
            agent_id=card.agent_id,
            display_name=card.display_name,
            mission=card.mission,
            capabilities=card.capabilities,
            exclusions=card.exclusions,
            required_inputs=card.required_inputs,
            accepted_input_types=card.accepted_input_types,
            output_types=card.output_types,
            output_contract=card.output_contract,
            adapter_kind=card.adapter_kind,
            adapter_config_version=card.adapter_config_version,
            capability_version=card.capability_version,
            availability=availability,
            health_sampled_at=sampled_at,
            health_fresh=fresh,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
            max_duration_seconds=card.max_duration_seconds,
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
            grant_ids=decision["grant_ids"],
            directory_generation_id=decision["directory_generation_id"],
            effective_decision_hash=_decision_hash(
                internal_user_id, card.agent_id, True
            ),
        )

    @staticmethod
    def _decision_value(value: object) -> dict[str, object]:
        if isinstance(value, Mapping):
            allowed = value.get("allowed")
            grant_ids = value.get("grant_ids", ())
            generation = value.get("directory_generation_id")
        else:
            allowed = getattr(value, "allowed", None)
            grant_ids = getattr(value, "grant_ids", ())
            generation = getattr(value, "directory_generation_id", None)
        if (
            type(allowed) is not bool
            or type(grant_ids) is not tuple
            or any(not isinstance(item, UUID) for item in grant_ids)
            or (generation is not None and not isinstance(generation, UUID))
        ):
            raise ValueError("authorization decision invalid")
        return {
            "allowed": allowed,
            "grant_ids": grant_ids,
            "directory_generation_id": generation,
        }


def _decision_hash(user_id: UUID, agent_id: str, allowed: bool) -> bytes:
    payload = {
        "internal_user_id": str(user_id),
        "agent_id": agent_id,
        "decision": "allow" if allowed else "deny",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


def _availability(state: str) -> Availability:
    if state in {"online", "active", "healthy"}:
        return "healthy"
    if state == "degraded":
        return "degraded"
    if state == "offline":
        return "offline"
    return "unknown"
