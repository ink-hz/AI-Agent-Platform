from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.fleet.models import FleetOverview
from app.fleet.service import FleetReadService
from app.observability.service import ObservabilityService

from .models import (
    LifecycleObservation,
    RunHealth,
    UsageObservation,
    UsageOccurrence,
)
from .repository import OperationsRepository
from .rules import (
    DataAccessObservation,
    OperationsRuleEngine,
    RuntimeObservation,
    SyncObservation,
)
from .source import PsycopgOperationsSource


GroupRunner = Callable[[datetime], Awaitable[dict | None]]
_LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
_REPLAY_OVERLAP = timedelta(hours=1)
_DEFAULT_INTERVALS = {
    "runtime": 10.0,
    "sync": 60.0,
    "data_access": 60.0,
    "usage": 300.0,
    "execution": 300.0,
    "lifecycle": 600.0,
}


class OperationsScheduler:
    def __init__(
        self,
        *,
        repository: OperationsRepository,
        fleet_service: FleetReadService | None = None,
        observability_service: ObservabilityService | None = None,
        operations_source: PsycopgOperationsSource | None = None,
        rule_engine: OperationsRuleEngine | None = None,
        group_runners: Mapping[str, GroupRunner] | None = None,
        intervals: Mapping[str, float] | None = None,
    ) -> None:
        self._repository = repository
        self._fleet_service = fleet_service
        self._observability_service = observability_service
        self._source = operations_source
        self._rule_engine = rule_engine or OperationsRuleEngine(repository)
        self._overview: FleetOverview | None = None
        self._initialized_groups: set[str] = set()

        default_runners: dict[str, GroupRunner] = {
            "runtime": self.run_runtime,
            "sync": self.run_sync,
            "data_access": self.run_data_access,
            "usage": self.run_usage,
            "execution": self.run_execution,
            "lifecycle": self.run_lifecycle,
        }
        if group_runners is not None:
            self._group_runners = dict(group_runners)
        elif intervals is not None:
            self._group_runners = {
                name: default_runners[name] for name in intervals
            }
        else:
            self._group_runners = default_runners
        configured = dict(_DEFAULT_INTERVALS if intervals is None else intervals)
        self._intervals = {
            name: float(configured.get(name, 0)) for name in self._group_runners
        }

    async def startup(self, now: datetime | None = None) -> None:
        await self._run_groups(
            now or datetime.now(timezone.utc),
            force=True,
        )

    async def run_due(self, now: datetime) -> None:
        await self._run_groups(now, force=False)

    async def _run_groups(self, now: datetime, *, force: bool) -> None:
        self._overview = None
        try:
            for name, runner in self._group_runners.items():
                try:
                    latest = await asyncio.to_thread(
                        self._repository.latest_run, name
                    )
                    if not force and not self._is_due(name, latest, now):
                        continue
                    previous_cursor = latest.cursor if latest is not None else {}
                    if latest is not None and (
                        latest.status == "succeeded" or previous_cursor
                    ):
                        self._initialized_groups.add(name)
                    try:
                        cursor = await runner(now)
                    except Exception as error:
                        run = RunHealth(
                            run_name=name,
                            status="failed",
                            started_at=now,
                            finished_at=now,
                            cursor=previous_cursor,
                            error_summary=self._sanitized_error(error),
                        )
                    else:
                        run = RunHealth(
                            run_name=name,
                            status="succeeded",
                            started_at=now,
                            finished_at=now,
                            cursor=cursor or {},
                        )
                        self._initialized_groups.add(name)
                    await asyncio.to_thread(self._repository.record_run, run)
                except Exception:
                    # Scheduler bookkeeping failure must not prevent another group.
                    continue
        finally:
            self._overview = None

    def _is_due(self, name: str, latest: RunHealth | None, now: datetime) -> bool:
        if latest is None:
            return True
        return now >= latest.started_at + timedelta(seconds=self._intervals[name])

    @staticmethod
    def _sanitized_error(error: Exception) -> str:
        message = " ".join(str(error).split()) or "evaluation failed"
        return f"{type(error).__name__}: {message[:240]}"

    async def _fleet_overview(self, now: datetime):
        if self._overview is None:
            if self._fleet_service is None:
                raise RuntimeError("fleet service unavailable")
            self._overview = await self._fleet_service.overview(now)
        return self._overview

    async def run_runtime(self, now: datetime) -> dict:
        overview = await self._fleet_overview(now)
        observations = [
            RuntimeObservation(
                agent_id=agent.id,
                agent_name=agent.name,
                agent_visibility=agent.visibility,
                source_kind=self._source_kind(agent.id),
                state=agent.state,
                observed_at=now,
            )
            for agent in overview.agents
        ]
        await asyncio.to_thread(
            self._rule_engine.evaluate_runtime, observations, now
        )
        return {"observed_at": now.isoformat()}

    async def run_sync(self, now: datetime) -> dict:
        if self._observability_service is None:
            raise RuntimeError("observability service unavailable")
        statuses = await self._observability_service.sync_status()
        observations: list[SyncObservation] = []
        for status in statuses:
            last_success_at = (
                status.completed_at if status.status == "succeeded" else None
            )
            if last_success_at is None:
                state = await asyncio.to_thread(
                    self._repository.get_rule_state,
                    f"sync:{status.source_kind}",
                )
                if state is not None and state.value.get("last_success_at"):
                    last_success_at = datetime.fromisoformat(
                        state.value["last_success_at"]
                    )
            observations.append(
                SyncObservation(
                    source_kind=status.source_kind,
                    status=status.status,
                    completed_at=status.completed_at,
                    observed_at=now,
                    last_success_at=last_success_at,
                )
            )
        await asyncio.to_thread(
            self._rule_engine.evaluate_sync, observations, now
        )
        return {"observed_at": now.isoformat()}

    async def run_data_access(self, now: datetime) -> dict:
        overview = await self._fleet_overview(now)
        observations = [
            DataAccessObservation(
                source_name="flywheel",
                available=overview.usage_source.healthy,
                observed_at=now,
            )
        ]
        await asyncio.to_thread(
            self._rule_engine.evaluate_data_access, observations, now
        )
        return {"observed_at": now.isoformat()}

    async def run_usage(self, now: datetime) -> dict:
        if self._source is None:
            raise RuntimeError("operations source unavailable")
        after = await self._cursor_after(
            "usage",
            now - timedelta(hours=24),
        )
        occurrences = await asyncio.to_thread(
            self._source.fetch_usage, after, now
        )
        overview = await self._fleet_overview(now)
        agents = {agent.id: agent for agent in overview.agents}
        grouped: dict[
            tuple[str, str, datetime], list[UsageOccurrence]
        ] = {}
        for occurrence in occurrences:
            key = (
                occurrence.agent_id,
                occurrence.source_kind,
                self._local_hour(occurrence.occurred_at),
            )
            grouped.setdefault(key, []).append(occurrence)

        observations: list[UsageObservation] = []
        for (agent_id, source_kind, bucket_start), items in sorted(
            grouped.items(), key=lambda item: item[0]
        ):
            agent = agents.get(agent_id)
            if agent is None:
                raise ValueError(f"fleet agent unavailable: {agent_id}")
            ordered = tuple(
                sorted(items, key=lambda item: (item.occurred_at, item.turn_key))
            )
            observations.append(
                UsageObservation(
                    agent_id=agent_id,
                    agent_name=agent.name,
                    agent_visibility=agent.visibility,
                    source_kind=source_kind,
                    bucket_start=bucket_start,
                    conversations=len(ordered),
                    cumulative_conversations=max(
                        agent.total_conversations or 0,
                        len(ordered),
                    ),
                    occurrences=ordered,
                )
            )
        if "usage" not in self._initialized_groups:
            represented_agents = {
                observation.agent_id for observation in observations
            }
            for agent in overview.agents:
                if (
                    agent.total_conversations is None
                    or agent.id in represented_agents
                ):
                    continue
                observations.append(
                    UsageObservation(
                        agent_id=agent.id,
                        agent_name=agent.name,
                        agent_visibility=agent.visibility,
                        source_kind=self._source_kind(agent.id),
                        bucket_start=self._local_hour(now),
                        conversations=0,
                        cumulative_conversations=agent.total_conversations,
                        occurrences=(),
                    )
                )
        await asyncio.to_thread(
            self._rule_engine.evaluate_usage,
            observations,
            now,
            initializing="usage" not in self._initialized_groups,
        )
        return {"through": now.isoformat()}

    async def run_execution(self, now: datetime) -> dict:
        if self._source is None:
            raise RuntimeError("operations source unavailable")
        default_after = (
            now
            if "execution" not in self._initialized_groups
            else now - timedelta(seconds=self._intervals["execution"])
        )
        after = await self._cursor_after("execution", default_after)
        observations = await asyncio.to_thread(
            self._source.fetch_execution, after, now
        )
        await asyncio.to_thread(
            self._rule_engine.evaluate_execution, list(observations), now
        )
        return {"through": now.isoformat()}

    async def run_lifecycle(self, now: datetime) -> dict:
        overview = await self._fleet_overview(now)
        observations = [
            LifecycleObservation(
                agent_id=agent.id,
                agent_name=agent.name,
                agent_visibility=agent.visibility,
                source_kind="catalog",
                live_since=self._parse_datetime(agent.live_since),
                last_updated_at=self._parse_datetime(agent.last_updated_at),
                observed_at=now,
            )
            for agent in overview.agents
        ]
        await asyncio.to_thread(
            self._rule_engine.evaluate_lifecycle,
            observations,
            now,
            initializing="lifecycle" not in self._initialized_groups,
        )
        return {"observed_at": now.isoformat()}

    async def _cursor_after(self, name: str, default: datetime) -> datetime:
        latest = await asyncio.to_thread(self._repository.latest_run, name)
        if latest is None:
            return default
        value = latest.cursor.get("through")
        return (
            datetime.fromisoformat(value) - _REPLAY_OVERLAP
            if value
            else default
        )

    @staticmethod
    def _source_kind(agent_id: str) -> str:
        if agent_id == "ai-fae-agent":
            return "fae"
        if agent_id == "ai-admin-agent":
            return "admin"
        return "metabot"

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed
        )

    @staticmethod
    def _local_hour(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(_LOCAL_ZONE).replace(
            minute=0, second=0, microsecond=0
        )


async def operations_poll_loop(scheduler: OperationsScheduler) -> None:
    while True:
        await scheduler.run_due(datetime.now(timezone.utc))
        await asyncio.sleep(10)
