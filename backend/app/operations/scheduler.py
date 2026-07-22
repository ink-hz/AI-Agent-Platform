from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.fleet.models import FleetOverview
from app.fleet.service import FleetReadService
from app.observability.models import SyncStatus
from app.observability.service import ObservabilityService

from .models import (
    LifecycleObservation,
    RunHealth,
    UsageBatch,
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


@dataclass(frozen=True)
class CommittedGroupRun:
    cursor: dict


GroupRunner = Callable[
    [datetime], Awaitable[dict | CommittedGroupRun | None]
]
_LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
_REPLAY_OVERLAP = timedelta(hours=1)
_REQUIRED_SYNC_SOURCES = frozenset({"fae", "admin"})
_USABLE_RUNTIME_STATES = frozenset({"active", "online", "degraded", "offline"})
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
        self._overview_task: asyncio.Task[FleetOverview] | None = None
        self._sync_status_task: asyncio.Task[tuple[SyncStatus, ...]] | None = None
        self._pass_lock = asyncio.Lock()
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
        async with self._pass_lock:
            self._overview_task = None
            self._sync_status_task = None
            tasks = [
                asyncio.create_task(
                    self._run_group(name, runner, now, force=force),
                    name=f"operations:{name}",
                )
                for name, runner in self._group_runners.items()
            ]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                self._overview_task = None
                self._sync_status_task = None

    async def _run_group(
        self,
        name: str,
        runner: GroupRunner,
        now: datetime,
        *,
        force: bool,
    ) -> None:
        try:
            latest = await asyncio.to_thread(self._repository.latest_run, name)
            if not force and not self._is_due(name, latest, now):
                return
            previous_cursor = latest.cursor if latest is not None else {}
            if latest is not None and (
                latest.status == "succeeded" or previous_cursor
            ):
                self._initialized_groups.add(name)
            try:
                outcome = await runner(now)
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
                if isinstance(outcome, CommittedGroupRun):
                    if name == "usage":
                        self._initialized_groups.add(name)
                        return
                    outcome = outcome.cursor
                run = RunHealth(
                    run_name=name,
                    status="succeeded",
                    started_at=now,
                    finished_at=now,
                    cursor=outcome or {},
                )
                self._initialized_groups.add(name)
            await asyncio.to_thread(self._repository.record_run, run)
        except Exception:
            # Scheduler bookkeeping failure must not prevent another group.
            return

    def _is_due(self, name: str, latest: RunHealth | None, now: datetime) -> bool:
        if latest is None:
            return True
        return now >= latest.started_at + timedelta(seconds=self._intervals[name])

    @staticmethod
    def _sanitized_error(error: Exception) -> str:
        message = " ".join(str(error).split()) or "evaluation failed"
        return f"{type(error).__name__}: {message[:240]}"

    async def _fleet_overview(self, now: datetime):
        if self._overview_task is None:
            if self._fleet_service is None:
                raise RuntimeError("fleet service unavailable")
            self._overview_task = asyncio.create_task(
                self._fleet_service.overview(now)
            )
        return await self._overview_task

    async def run_runtime(self, now: datetime) -> dict:
        overview = await self._fleet_overview(now)
        if (
            not overview.runtime_source.healthy
            or overview.runtime_source.stale
            or overview.runtime_source.checked_at is None
        ):
            raise RuntimeError("required runtime source unavailable")
        observed_ids = {agent.id for agent in overview.agents}
        expected_ids = set(overview.expected_agent_ids)
        if (
            not overview.agents
            or (expected_ids and not expected_ids.issubset(observed_ids))
            or any(
                agent.state not in _USABLE_RUNTIME_STATES
                for agent in overview.agents
            )
        ):
            raise RuntimeError("required runtime evidence incomplete")
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
        statuses = await self._sync_statuses()
        source_kinds = [status.source_kind for status in statuses]
        if (
            len(source_kinds) != len(_REQUIRED_SYNC_SOURCES)
            or set(source_kinds) != _REQUIRED_SYNC_SOURCES
        ):
            raise RuntimeError("required sync source coverage incomplete")
        observations: list[SyncObservation] = []
        for status in statuses:
            last_success_at = status.last_success_at
            if last_success_at is None and status.status == "succeeded":
                last_success_at = status.completed_at
            if last_success_at is None:
                raise RuntimeError("required sync success history unavailable")
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

    async def run_usage(self, now: datetime) -> dict | CommittedGroupRun:
        if self._source is None:
            raise RuntimeError("operations source unavailable")
        cursor = await self._latest_cursor("usage")
        after = self._local_after(cursor, now - timedelta(hours=24))
        local_batch = await asyncio.to_thread(
            self._source.fetch_local_usage_batch, after, now
        )
        initializing = "usage" not in self._initialized_groups
        remote_batches, remote_generations = await self._remote_usage(
            cursor, now, initializing=initializing
        )
        batches = [("metabot", local_batch), *remote_batches]
        occurrences, cumulative_totals = self._aligned_usage_inputs(batches)
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
            total_key = (agent_id, source_kind)
            if total_key not in cumulative_totals:
                raise ValueError(
                    "aligned cumulative usage total unavailable: "
                    f"{source_kind}/{agent_id}"
                )
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
                    cumulative_conversations=cumulative_totals[total_key],
                    occurrences=ordered,
                )
            )
        represented = {
            (observation.agent_id, observation.source_kind)
            for observation in observations
        }
        for (agent_id, source_kind), cumulative_total in sorted(
            cumulative_totals.items()
        ):
            if (agent_id, source_kind) in represented:
                continue
            agent = agents.get(agent_id)
            if agent is None:
                raise ValueError(f"fleet agent unavailable: {agent_id}")
            observations.append(
                UsageObservation(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    agent_visibility=agent.visibility,
                    source_kind=source_kind,
                    bucket_start=self._local_hour(now),
                    conversations=0,
                    cumulative_conversations=cumulative_total,
                    occurrences=(),
                )
            )
        candidate_cursor = {
            "local_through": now.isoformat(),
            "remote_generations": remote_generations,
        }
        successful_run = RunHealth(
            run_name="usage",
            status="succeeded",
            started_at=now,
            finished_at=now,
            cursor=candidate_cursor,
        )
        committed_run = await asyncio.to_thread(
            self._rule_engine.evaluate_usage,
            observations,
            now,
            initializing=initializing,
            successful_run=successful_run,
        )
        return (
            CommittedGroupRun(cursor=committed_run.cursor)
            if committed_run is not None
            else candidate_cursor
        )

    async def run_execution(self, now: datetime) -> dict:
        if self._source is None:
            raise RuntimeError("operations source unavailable")
        cursor = await self._latest_cursor("execution")
        initializing = "execution" not in self._initialized_groups
        default_after = (
            now
            if initializing
            else now - timedelta(seconds=self._intervals["execution"])
        )
        after = self._local_after(cursor, default_after)
        observations = list(
            await asyncio.to_thread(
                self._source.fetch_local_execution, after, now
            )
        )
        remote_observations, remote_generations = await self._remote_execution(
            cursor, now, initializing=initializing
        )
        observations.extend(remote_observations)
        await asyncio.to_thread(
            self._rule_engine.evaluate_execution, observations, now
        )
        return {
            "local_through": now.isoformat(),
            "remote_generations": remote_generations,
        }

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

    async def _latest_cursor(self, name: str) -> dict:
        latest = await asyncio.to_thread(self._repository.latest_run, name)
        return latest.cursor if latest is not None else {}

    @staticmethod
    def _local_after(cursor: dict, default: datetime) -> datetime:
        value = cursor.get("local_through") or cursor.get("through")
        return (
            datetime.fromisoformat(value) - _REPLAY_OVERLAP
            if value
            else default
        )

    async def _sync_statuses(self) -> tuple[SyncStatus, ...]:
        if self._sync_status_task is None:
            if self._observability_service is None:
                async def unavailable() -> tuple[SyncStatus, ...]:
                    return ()

                self._sync_status_task = asyncio.create_task(unavailable())
            else:
                async def load() -> tuple[SyncStatus, ...]:
                    return tuple(await self._observability_service.sync_status())

                self._sync_status_task = asyncio.create_task(
                    load()
                )
        return await self._sync_status_task

    async def _remote_usage(
        self,
        cursor: dict,
        now: datetime,
        *,
        initializing: bool,
    ) -> tuple[list[tuple[str, UsageBatch]], dict[str, str]]:
        generations = dict(cursor.get("remote_generations", {}))
        batches: list[tuple[str, UsageBatch]] = []
        for status in await self._sync_statuses():
            if status.status != "succeeded" or status.completed_at is None:
                continue
            generation = status.completed_at.isoformat()
            if generations.get(status.source_kind) == generation:
                continue
            filters = (
                {
                    "created_after": now - timedelta(hours=24),
                    "created_through": now,
                }
                if initializing
                else {}
            )
            batch = await asyncio.to_thread(
                self._source.fetch_remote_usage_batch,
                status.source_kind,
                **filters,
            )
            batches.append((status.source_kind, batch))
            generations[status.source_kind] = generation
        return batches, generations

    @staticmethod
    def _aligned_usage_inputs(
        batches: list[tuple[str, UsageBatch]],
    ) -> tuple[list[UsageOccurrence], dict[tuple[str, str], int]]:
        occurrences: list[UsageOccurrence] = []
        cumulative_totals: dict[tuple[str, str], int] = {}
        for batch_source, batch in batches:
            for occurrence in batch.occurrences:
                if occurrence.source_kind != batch_source:
                    raise ValueError(
                        "usage occurrence source does not match its batch: "
                        f"{batch_source}/{occurrence.source_kind}"
                    )
            occurrences.extend(batch.occurrences)
            for agent_id, total in batch.cumulative_totals.items():
                key = (agent_id, batch_source)
                existing = cumulative_totals.get(key)
                if existing is not None and existing != total:
                    raise ValueError(
                        "conflicting aligned cumulative usage totals: "
                        f"{batch_source}/{agent_id}"
                    )
                cumulative_totals[key] = total
        return occurrences, cumulative_totals

    async def _remote_execution(
        self,
        cursor: dict,
        now: datetime,
        *,
        initializing: bool,
    ) -> tuple[list[ExecutionObservation], dict[str, str]]:
        generations = dict(cursor.get("remote_generations", {}))
        observations: list[ExecutionObservation] = []
        for status in await self._sync_statuses():
            if status.status != "succeeded" or status.completed_at is None:
                continue
            generation = status.completed_at.isoformat()
            if generations.get(status.source_kind) == generation:
                continue
            filters = (
                {
                    "created_after": now - timedelta(hours=24),
                    "created_through": now,
                }
                if initializing
                else {}
            )
            observations.extend(
                await asyncio.to_thread(
                    self._source.fetch_remote_execution,
                    status.source_kind,
                    **filters,
                )
            )
            generations[status.source_kind] = generation
        return observations, generations

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
    await scheduler.startup()
    while True:
        await asyncio.sleep(10)
        await scheduler.run_due(datetime.now(timezone.utc))
