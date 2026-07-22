from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .models import (
    AgentVisibility,
    ExecutionObservation,
    LifecycleObservation,
    NewOperationalEvent,
    RuleState,
    UsageObservation,
)
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
_LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


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
            rule_key = f"sync:{observation.source_kind}"
            current = self._repository.get_rule_state(rule_key)
            no_success_since = self._no_success_since(observation, current)
            if observation.status == "succeeded":
                stale = False
            elif observation.last_success_at is not None:
                stale = now - observation.last_success_at > _SYNC_STALE_AFTER
            else:
                assert no_success_since is not None
                stale = now - no_success_since > _SYNC_STALE_AFTER
            self._repository.put_rule_state(
                RuleState(
                    rule_key=rule_key,
                    value={
                        "status": observation.status,
                        "completed_at": self._optional_timestamp(
                            observation.completed_at
                        ),
                        "last_success_at": self._optional_timestamp(
                            observation.last_success_at
                        ),
                        "no_success_since": self._optional_timestamp(
                            no_success_since
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

    def evaluate_usage(
        self,
        observations: list[UsageObservation],
        now: datetime,
        *,
        initializing: bool,
    ) -> None:
        current_hour = self._local_hour(now)
        for observation in sorted(observations, key=lambda item: item.bucket_start):
            bucket_start = self._local_hour(observation.bucket_start)
            self._validate_usage_occurrences(observation, bucket_start)
            observation = observation.model_copy(
                update={"bucket_start": bucket_start}
            )
            milestone_events, milestone_state = self._milestone_mutations(
                observation, now, initializing
            )
            usage_current = self._repository.get_rule_state(
                f"usage:{observation.agent_id}"
            )
            prior_cumulative = (
                int(usage_current.value.get("cumulative_conversations", 0))
                if usage_current is not None
                else 0
            )
            usage_state = RuleState(
                rule_key=f"usage:{observation.agent_id}",
                value={
                    "cumulative_conversations": max(
                        prior_cumulative,
                        observation.cumulative_conversations,
                    )
                },
                updated_at=now,
            )
            self._repository.record_usage(
                observation,
                (
                    self._usage_event(observation, bucket_start)
                    if observation.occurrences
                    else None
                ),
                status=(
                    "historical" if bucket_start < current_hour else "active"
                ),
                milestone_events=milestone_events,
                usage_state=usage_state,
                milestone_state=milestone_state,
                processed_at=now,
            )
        self._repository.expire_active_occurrences("usage", current_hour)

    def evaluate_lifecycle(
        self,
        observations: list[LifecycleObservation],
        now: datetime,
        *,
        initializing: bool,
    ) -> None:
        for observation in observations:
            rule_key = f"lifecycle:{observation.agent_id}"
            current = self._repository.get_rule_state(rule_key)
            events: list[NewOperationalEvent] = []
            if current is None:
                events.extend(self._initial_lifecycle_events(observation))
                live_since = observation.live_since
                last_updated_at = observation.last_updated_at
            else:
                live_since = self._stored_datetime(current, "live_since")
                last_updated_at = self._stored_datetime(current, "last_updated_at")
                if live_since is None:
                    live_since = observation.live_since
                candidate = observation.last_updated_at
                if candidate is not None and (
                    last_updated_at is None or candidate > last_updated_at
                ):
                    if not initializing:
                        events.append(
                            self._lifecycle_event(
                                observation,
                                event_type="deployment_updated",
                                occurred_at=candidate,
                            )
                        )
                    last_updated_at = candidate

            self._repository.record_occurrences(
                events,
                status="historical",
                states=(RuleState(
                    rule_key=rule_key,
                    value={
                        "live_since": self._optional_timestamp(live_since),
                        "last_updated_at": self._optional_timestamp(last_updated_at),
                    },
                    updated_at=now,
                ),),
            )

    def evaluate_execution(
        self, observations: list[ExecutionObservation], now: datetime
    ) -> None:
        grouped: dict[
            tuple[str, str, datetime], list[ExecutionObservation]
        ] = {}
        for observation in observations:
            key = (
                observation.agent_id,
                observation.signal_type,
                self._local_hour(observation.occurred_at),
            )
            grouped.setdefault(key, []).append(observation)

        active = {
            event.fingerprint: event
            for visibility in ("business", "system")
            for event in self._repository.list_active_attention(visibility)
            if event.event_family == "execution"
        }
        current_hour = self._local_hour(now)
        for (agent_id, signal_type, bucket_start), items in sorted(
            grouped.items(), key=lambda item: item[0]
        ):
            fingerprint = (
                f"execution:{agent_id}:{signal_type}:{bucket_start.isoformat()}"
            )
            previous = active.get(fingerprint) or self._repository.get_occurrence(
                fingerprint, bucket_start
            )
            turn_keys = {item.turn_key for item in items}
            if previous is not None:
                turn_keys.update(previous.facts.get("turn_keys", []))
            latest = max(items, key=lambda item: (item.occurred_at, item.turn_key))
            previous_occurred_at = (
                datetime.fromisoformat(previous.facts["last_occurred_at"])
                if previous is not None
                and previous.facts.get("last_occurred_at") is not None
                else None
            )
            previous_latest_key = (
                (previous_occurred_at, previous.facts.get("latest_turn_key", ""))
                if previous_occurred_at is not None
                else None
            )
            latest_key = (latest.occurred_at, latest.turn_key)
            if previous_latest_key is not None and previous_latest_key >= latest_key:
                latest_session_key = previous.facts["latest_session_key"]
                latest_turn_key = previous.facts.get("latest_turn_key", "")
                last_occurred_at = previous_occurred_at
            else:
                latest_session_key = latest.session_key
                latest_turn_key = latest.turn_key
                last_occurred_at = latest.occurred_at
            event = NewOperationalEvent(
                agent_id=agent_id,
                agent_visibility=latest.agent_visibility,
                event_type=signal_type,
                event_family="execution",
                severity="attention",
                title=self._execution_title(latest.agent_name, signal_type),
                summary=(
                    f"{len(turn_keys)} supported {signal_type.replace('_', ' ')} "
                    "signal(s) occurred during this hour."
                ),
                source_kind=latest.source_kind,
                occurred_at=bucket_start,
                facts={
                    "signal_type": signal_type,
                    "count": len(turn_keys),
                    "turn_keys": sorted(turn_keys),
                    "latest_session_key": latest_session_key,
                    "latest_turn_key": latest_turn_key,
                    "last_occurred_at": last_occurred_at.isoformat(),
                },
                target_kind="session",
                target_id=latest_session_key,
                target_path=f"/sessions/{quote(latest_session_key, safe='')}",
                fingerprint=fingerprint,
            )
            self._repository.record_occurrences(
                (event,),
                status=(
                    "historical" if bucket_start < current_hour else "active"
                ),
            )

        self._repository.expire_active_occurrences("execution", current_hour)

    @staticmethod
    def _usage_event(
        observation: UsageObservation,
        bucket_start: datetime,
    ) -> NewOperationalEvent:
        return NewOperationalEvent(
            agent_id=observation.agent_id,
            agent_visibility=observation.agent_visibility,
            event_type="new_conversations",
            event_family="usage",
            severity="info",
            title=f"{observation.agent_name} received new conversations",
            summary="Answered conversations were recorded during this hour.",
            source_kind=observation.source_kind,
            occurred_at=bucket_start,
            facts={
                "cumulative_conversations": observation.cumulative_conversations,
            },
            target_kind="agent",
            target_id=observation.agent_id,
            target_path=f"/agents/{observation.agent_id}",
            fingerprint=(
                f"usage:{observation.agent_id}:conversations:"
                f"{bucket_start.isoformat()}"
            ),
        )

    def _milestone_mutations(
        self, observation: UsageObservation, now: datetime, initializing: bool
    ) -> tuple[list[NewOperationalEvent], RuleState]:
        rule_key = f"milestone:{observation.agent_id}"
        current = self._repository.get_rule_state(rule_key)
        prior = int(current.value.get("reached", 0)) if current is not None else 0
        reached = self._highest_milestone(observation.cumulative_conversations)
        events: list[NewOperationalEvent] = []
        if not initializing:
            for milestone in self._milestones_through(
                observation.cumulative_conversations
            ):
                if milestone <= prior:
                    continue
                events.append(
                    NewOperationalEvent(
                        agent_id=observation.agent_id,
                        agent_visibility=observation.agent_visibility,
                        event_type="conversation_milestone",
                        event_family="usage",
                        severity="info",
                        title=(
                            f"{observation.agent_name} reached {milestone} conversations"
                        ),
                        summary=(
                            f"Cumulative answered conversations reached {milestone}."
                        ),
                        source_kind=observation.source_kind,
                        occurred_at=observation.bucket_start,
                        facts={"milestone": milestone},
                        target_kind="agent",
                        target_id=observation.agent_id,
                        target_path=f"/agents/{observation.agent_id}",
                        fingerprint=f"milestone:{observation.agent_id}:{milestone}",
                    )
                )
        return events, RuleState(
                rule_key=rule_key,
                value={"reached": max(prior, reached)},
                updated_at=now,
            )

    @classmethod
    def _validate_usage_occurrences(
        cls, observation: UsageObservation, bucket_start: datetime
    ) -> None:
        if observation.conversations != len(observation.occurrences):
            raise ValueError("usage conversations must match occurrence identities")
        for occurrence in observation.occurrences:
            if occurrence.agent_id != observation.agent_id:
                raise ValueError("usage occurrence agent does not match its bucket")
            if occurrence.source_kind != observation.source_kind:
                raise ValueError("usage occurrence source does not match its bucket")
            if cls._local_hour(occurrence.occurred_at) != bucket_start:
                raise ValueError("usage occurrence time does not match its bucket")

    def _initial_lifecycle_events(
        self, observation: LifecycleObservation
    ) -> list[NewOperationalEvent]:
        events: list[NewOperationalEvent] = []
        if observation.live_since is not None:
            events.append(
                self._lifecycle_event(
                    observation,
                    event_type="agent_launched",
                    occurred_at=observation.live_since,
                )
            )
        if observation.last_updated_at is not None:
            events.append(
                self._lifecycle_event(
                    observation,
                    event_type="deployment_updated",
                    occurred_at=observation.last_updated_at,
                )
            )
        return events

    def _lifecycle_event(
        self,
        observation: LifecycleObservation,
        *,
        event_type: str,
        occurred_at: datetime,
    ) -> NewOperationalEvent:
        launched = event_type == "agent_launched"
        return NewOperationalEvent(
                agent_id=observation.agent_id,
                agent_visibility=observation.agent_visibility,
                event_type=event_type,
                event_family="lifecycle",
                severity="info",
                title=(
                    f"{observation.agent_name} entered production"
                    if launched
                    else f"{observation.agent_name} deployment was updated"
                ),
                summary=(
                    "The durable production start date was recorded."
                    if launched
                    else "A later durable deployment update was detected."
                ),
                source_kind=observation.source_kind,
                occurred_at=occurred_at,
                facts={"occurred_at": occurred_at.isoformat()},
                target_kind="agent",
                target_id=observation.agent_id,
                target_path=f"/agents/{observation.agent_id}",
                fingerprint=(
                    f"lifecycle:{observation.agent_id}:{event_type}:"
                    f"{occurred_at.isoformat()}"
                ),
            )

    @staticmethod
    def _milestones_through(total: int) -> tuple[int, ...]:
        fixed = [milestone for milestone in (100, 250, 500, 1000) if total >= milestone]
        return tuple(fixed + list(range(2000, total + 1, 1000)))

    @classmethod
    def _highest_milestone(cls, total: int) -> int:
        milestones = cls._milestones_through(total)
        return milestones[-1] if milestones else 0

    @staticmethod
    def _local_hour(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(_LOCAL_ZONE).replace(
            minute=0, second=0, microsecond=0
        )

    @staticmethod
    def _stored_datetime(state: RuleState, field: str) -> datetime | None:
        value = state.value.get(field)
        return datetime.fromisoformat(value) if value is not None else None

    @staticmethod
    def _execution_title(agent_name: str, signal_type: str) -> str:
        labels = {
            "tool_error": "tool errors",
            "fallback": "fallback responses",
            "empty_answer": "empty answers",
            "incomplete": "incomplete executions",
        }
        return f"{agent_name} produced {labels[signal_type]}"

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
    def _no_success_since(
        observation: SyncObservation, current: RuleState | None
    ) -> datetime | None:
        if (
            observation.status == "succeeded"
            or observation.last_success_at is not None
        ):
            return None
        if current is not None:
            stored = current.value.get("no_success_since")
            if stored is not None:
                return datetime.fromisoformat(stored)
        return observation.observed_at

    @staticmethod
    def _sync_summary(status: str, stale: bool) -> str:
        if status == "failed" and stale:
            return "The latest synchronization failed and its data is stale."
        if status == "failed":
            return "The latest synchronization failed."
        return "The last successful synchronization is more than 36 hours old."
