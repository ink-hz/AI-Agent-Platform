from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from .models import AgentVisibility, NewOperationalEvent, RuleState
from .repository import OperationsRepository


class RuntimeObservation(BaseModel):
    agent_id: str
    agent_name: str
    agent_visibility: AgentVisibility
    source_kind: str
    state: Literal[
        "active", "online", "degraded", "offline", "checking", "unknown"
    ]
    observed_at: datetime


class SyncObservation(BaseModel):
    source_kind: Literal["fae", "admin"]
    status: Literal["running", "succeeded", "failed"]
    completed_at: datetime | None
    observed_at: datetime
    last_success_at: datetime | None


class DataAccessObservation(BaseModel):
    source_name: str
    available: bool
    observed_at: datetime


_SYNC_AGENTS = {
    "fae": "ai-fae-agent",
    "admin": "ai-admin-agent",
}
_SYNC_STALE_AFTER = timedelta(hours=36)


class OperationsRuleEngine:
    def __init__(self, repository: OperationsRepository) -> None:
        self._repository = repository

    def evaluate_runtime(
        self, observations: list[RuntimeObservation], now: datetime
    ) -> None:
        for observation in observations:
            normalized = self._normalize_runtime(observation.state)
            if normalized is None:
                continue

            rule_key = f"runtime:{observation.agent_id}"
            current = self._repository.get_rule_state(rule_key)
            value = current.value if current is not None else {}
            candidate = value.get("candidate")
            count = int(value.get("count", 0)) + 1 if candidate == normalized else 1
            stable = value.get("stable")

            if count >= 2:
                stable = normalized

            self._repository.put_rule_state(
                RuleState(
                    rule_key=rule_key,
                    value={"candidate": normalized, "count": count, "stable": stable},
                    updated_at=now,
                )
            )

            if count < 2:
                continue
            if normalized == "healthy":
                self._resolve_runtime(observation, count, now)
            else:
                self._open_runtime(observation, normalized, count, now)

    def evaluate_sync(
        self, observations: list[SyncObservation], now: datetime
    ) -> None:
        for observation in observations:
            stale = (
                observation.last_success_at is not None
                and now - observation.last_success_at > _SYNC_STALE_AFTER
            )
            self._repository.put_rule_state(
                RuleState(
                    rule_key=f"sync:{observation.source_kind}",
                    value={
                        "status": observation.status,
                        "completed_at": self._optional_timestamp(
                            observation.completed_at
                        ),
                        "last_success_at": self._optional_timestamp(
                            observation.last_success_at
                        ),
                        "stale": stale,
                    },
                    updated_at=now,
                )
            )

            fingerprint = f"sync:{observation.source_kind}:unavailable"
            agent_id = _SYNC_AGENTS[observation.source_kind]
            facts = {
                "status": observation.status,
                "stale": stale,
                "completed_at": self._optional_timestamp(observation.completed_at),
                "last_success_at": self._optional_timestamp(
                    observation.last_success_at
                ),
            }
            if observation.status == "succeeded":
                self._repository.resolve_active(
                    fingerprint=fingerprint,
                    resolved_at=now,
                    recovery_title=f"{agent_id} synchronization recovered",
                    recovery_summary="The remote synchronization completed successfully.",
                    recovery_facts=facts,
                )
            elif observation.status == "failed" or stale:
                self._repository.upsert_active(
                    NewOperationalEvent(
                        agent_id=agent_id,
                        agent_visibility="business",
                        event_type="remote_sync_unavailable",
                        event_family="data",
                        severity="attention",
                        title=f"{agent_id} synchronization is unavailable",
                        summary=self._sync_summary(observation.status, stale),
                        source_kind=observation.source_kind,
                        occurred_at=now,
                        facts=facts,
                        target_kind="agent",
                        target_id=agent_id,
                        target_path=f"/agents/{agent_id}",
                        fingerprint=fingerprint,
                    )
                )

    def evaluate_data_access(
        self, observations: list[DataAccessObservation], now: datetime
    ) -> None:
        for observation in observations:
            self._repository.put_rule_state(
                RuleState(
                    rule_key=f"data:{observation.source_name}",
                    value={"available": observation.available},
                    updated_at=now,
                )
            )
            fingerprint = f"data:{observation.source_name}:unavailable"
            facts = {"available": observation.available}
            if observation.available:
                self._repository.resolve_active(
                    fingerprint=fingerprint,
                    resolved_at=now,
                    recovery_title=f"{observation.source_name} data access recovered",
                    recovery_summary="The required business-data source is readable again.",
                    recovery_facts=facts,
                )
            else:
                is_flywheel = observation.source_name == "flywheel"
                self._repository.upsert_active(
                    NewOperationalEvent(
                        agent_id=None,
                        agent_visibility="business",
                        event_type="business_data_unavailable",
                        event_family="data",
                        severity="attention",
                        title=f"{observation.source_name} business data is unavailable",
                        summary="A required local business-data source could not be read.",
                        source_kind=observation.source_name,
                        occurred_at=now,
                        facts=facts,
                        target_kind="sessions" if is_flywheel else None,
                        target_id=None,
                        target_path="/sessions" if is_flywheel else None,
                        fingerprint=fingerprint,
                    )
                )

    @staticmethod
    def _normalize_runtime(state: str) -> str | None:
        if state in ("active", "online"):
            return "healthy"
        if state in ("degraded", "offline"):
            return state
        return None

    def _open_runtime(
        self,
        observation: RuntimeObservation,
        normalized: str,
        count: int,
        now: datetime,
    ) -> None:
        severity = "critical" if normalized == "offline" else "attention"
        self._repository.upsert_active(
            NewOperationalEvent(
                agent_id=observation.agent_id,
                agent_visibility=observation.agent_visibility,
                event_type=f"runtime_{normalized}",
                event_family="runtime",
                severity=severity,
                title=f"{observation.agent_name} is {normalized}",
                summary=(
                    f"Two consecutive runtime observations reported {normalized}."
                ),
                source_kind=observation.source_kind,
                occurred_at=now,
                facts={"state": normalized, "observations": count},
                target_kind="agent",
                target_id=observation.agent_id,
                target_path=f"/agents/{observation.agent_id}",
                fingerprint=f"runtime:{observation.agent_id}:unavailable",
            )
        )

    def _resolve_runtime(
        self, observation: RuntimeObservation, count: int, now: datetime
    ) -> None:
        self._repository.resolve_active(
            fingerprint=f"runtime:{observation.agent_id}:unavailable",
            resolved_at=now,
            recovery_title=f"{observation.agent_name} recovered",
            recovery_summary="Runtime returned to a healthy state.",
            recovery_facts={"state": "healthy", "observations": count},
        )

    @staticmethod
    def _optional_timestamp(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _sync_summary(status: str, stale: bool) -> str:
        if status == "failed" and stale:
            return "The latest synchronization failed and its data is stale."
        if status == "failed":
            return "The latest synchronization failed."
        return "The last successful synchronization is more than 36 hours old."
