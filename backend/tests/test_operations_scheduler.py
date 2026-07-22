from datetime import datetime, timedelta, timezone

import pytest

from app.fleet.models import (
    DataSourceStatus,
    FleetAgent,
    FleetOverview,
    FleetSummary,
)
from app.operations.models import (
    EventFilters,
    ExecutionObservation,
    RunHealth,
    UsageOccurrence,
)
from app.operations.repository import OperationsRepository
from app.operations.rules import OperationsRuleEngine
from app.operations.scheduler import OperationsScheduler


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)


def overview() -> FleetOverview:
    return FleetOverview(
        summary=FleetSummary(
            total_agents=1,
            running_agents=1,
            active_agents=1,
            degraded_agents=0,
            offline_agents=0,
            checking_agents=0,
            total_conversations=237,
            conversations_last_7d=3,
            conversations_previous_7d=0,
            change_percent=None,
        ),
        trend=[],
        agents=[
            FleetAgent(
                id="ai-fae-agent",
                name="FAE",
                domain="Support",
                description="Support agent",
                glyph="AI",
                accent="blue",
                visibility="business",
                state="active",
                live_since="2026-01-01T00:00:00+00:00",
                live_since_basis="release_artifact",
                last_updated_at="2026-07-01T00:00:00+00:00",
                last_updated_basis="repository_history",
                current_runtime_seconds=60,
                total_conversations=237,
                conversations_last_7d=3,
                last_activity_at=NOW.isoformat(),
                recent_summary=None,
            )
        ],
        runtime_source=DataSourceStatus(healthy=True, checked_at=NOW.isoformat()),
        usage_source=DataSourceStatus(healthy=False, checked_at=NOW.isoformat()),
    )


class FleetStub:
    def __init__(self, totals: tuple[int, ...] = (237,)) -> None:
        self.calls = 0
        self.totals = totals

    async def overview(self, _now):
        self.calls += 1
        result = overview()
        total = self.totals[min(self.calls - 1, len(self.totals) - 1)]
        result.agents[0] = result.agents[0].model_copy(
            update={"total_conversations": total}
        )
        return result


class RuleEngineStub:
    def __init__(self, *, fail_usage: bool = False, fail_execution: bool = False):
        self.calls: dict[str, tuple] = {}
        self.fail_usage = fail_usage
        self.fail_execution = fail_execution

    def evaluate_runtime(self, observations, now):
        self.calls["runtime"] = (observations, now)

    def evaluate_data_access(self, observations, now):
        self.calls["data_access"] = (observations, now)

    def evaluate_lifecycle(self, observations, now, *, initializing):
        self.calls["lifecycle"] = (observations, now, initializing)

    def evaluate_usage(self, observations, now, *, initializing):
        self.calls["usage"] = (observations, now, initializing)
        if self.fail_usage:
            raise RuntimeError("usage transaction failed")

    def evaluate_execution(self, observations, now):
        self.calls["execution"] = (observations, now)
        if self.fail_execution:
            raise RuntimeError("execution transaction failed")


class SourceStub:
    def __init__(self) -> None:
        self.usage_ranges: list[tuple[datetime, datetime]] = []
        self.execution_ranges: list[tuple[datetime, datetime]] = []

    def fetch_usage(self, after, through):
        self.usage_ranges.append((after, through))
        return (
            UsageOccurrence(
                turn_key="fae:turn-1",
                agent_id="ai-fae-agent",
                source_kind="fae",
                occurred_at=NOW - timedelta(hours=23, minutes=15),
            ),
            UsageOccurrence(
                turn_key="fae:turn-2",
                agent_id="ai-fae-agent",
                source_kind="fae",
                occurred_at=NOW - timedelta(hours=23, minutes=5),
            ),
        )

    def fetch_execution(self, after, through):
        self.execution_ranges.append((after, through))
        return (
            ExecutionObservation(
                turn_key="fae:turn-3",
                session_key="fae:session-1",
                agent_id="ai-fae-agent",
                agent_name="FAE",
                agent_visibility="business",
                source_kind="fae",
                signal_type="fallback",
                occurred_at=through,
            ),
        )


@pytest.mark.asyncio
async def test_failed_rule_group_does_not_block_other_groups(tmp_path):
    calls: list[str] = []

    async def failed(_now):
        calls.append("runtime")
        raise RuntimeError("runtime failed")

    async def succeeded(_now):
        calls.append("usage")
        return {"cursor": "complete"}

    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        group_runners={"runtime": failed, "usage": succeeded},
        intervals={"runtime": 0, "usage": 0},
    )

    await scheduler.run_due(NOW)

    assert calls == ["runtime", "usage"]
    assert repository.latest_run("runtime").status == "failed"
    assert repository.latest_run("runtime").error_summary == "RuntimeError: runtime failed"
    assert repository.latest_run("usage").status == "succeeded"
    assert repository.latest_run("usage").cursor == {"cursor": "complete"}


@pytest.mark.asyncio
async def test_not_due_group_is_skipped(tmp_path):
    calls: list[datetime] = []

    async def runner(now):
        calls.append(now)
        return {"through": now.isoformat()}

    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        group_runners={"usage": runner},
        intervals={"usage": 300},
    )

    await scheduler.run_due(NOW)
    await scheduler.run_due(NOW + timedelta(seconds=299))
    await scheduler.run_due(NOW + timedelta(seconds=300))

    assert calls == [NOW, NOW + timedelta(seconds=300)]


@pytest.mark.asyncio
async def test_fleet_groups_reuse_one_overview_per_scheduler_pass(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    fleet = FleetStub()
    engine = RuleEngineStub()
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=fleet,
        rule_engine=engine,
        group_runners=None,
        intervals={"runtime": 0, "data_access": 0, "lifecycle": 0},
    )

    await scheduler.run_due(NOW)

    assert fleet.calls == 1
    runtime = engine.calls["runtime"][0][0]
    assert (runtime.agent_id, runtime.agent_name, runtime.source_kind) == (
        "ai-fae-agent",
        "FAE",
        "fae",
    )
    data_access = engine.calls["data_access"][0][0]
    assert data_access.source_name == "flywheel"
    assert data_access.available is False
    lifecycle = engine.calls["lifecycle"][0][0]
    assert lifecycle.live_since == datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_startup_usage_baseline_is_exact_preceding_day_and_keeps_occurrences(
    tmp_path,
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    fleet = FleetStub()
    engine = RuleEngineStub()
    source = SourceStub()
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=fleet,
        operations_source=source,
        rule_engine=engine,
        intervals={"usage": 300},
    )

    await scheduler.startup(NOW)

    assert source.usage_ranges == [(NOW - timedelta(hours=24), NOW)]
    observations, _, initializing = engine.calls["usage"]
    assert initializing is True
    assert len(observations) == 1
    assert [item.turn_key for item in observations[0].occurrences] == [
        "fae:turn-1",
        "fae:turn-2",
    ]
    assert observations[0].conversations == 2
    assert repository.latest_run("usage").cursor == {"through": NOW.isoformat()}


@pytest.mark.asyncio
async def test_restart_continues_from_cursor_without_reinitializing_usage(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    previous = NOW - timedelta(minutes=10)
    repository.record_run(
        RunHealth(
            run_name="usage",
            status="succeeded",
            started_at=previous,
            finished_at=previous,
            cursor={"through": previous.isoformat()},
        )
    )
    engine = RuleEngineStub()
    source = SourceStub()
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub(),
        operations_source=source,
        rule_engine=engine,
        intervals={"usage": 300},
    )

    await scheduler.startup(NOW)

    assert source.usage_ranges == [(previous - timedelta(hours=1), NOW)]
    assert engine.calls["usage"][2] is False


class ReplaySource:
    def __init__(self, occurrence: UsageOccurrence | None = None):
        self.occurrence = occurrence
        self.usage_ranges: list[tuple[datetime, datetime]] = []

    def fetch_usage(self, after, through):
        self.usage_ranges.append((after, through))
        return (self.occurrence,) if self.occurrence is not None else ()

    def fetch_execution(self, _after, _through):
        return ()


@pytest.mark.asyncio
async def test_late_usage_replay_uses_overlap_and_records_true_hour_once(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    previous = NOW - timedelta(minutes=5)
    repository.record_run(
        RunHealth(
            run_name="usage",
            status="succeeded",
            started_at=previous,
            finished_at=previous,
            cursor={"through": previous.isoformat()},
        )
    )
    late_time = NOW - timedelta(days=2, minutes=20)
    source = ReplaySource(
        UsageOccurrence(
            turn_key="fae:late-turn",
            agent_id="ai-fae-agent",
            source_kind="fae",
            occurred_at=late_time,
        )
    )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub(),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.run_due(NOW)
    await scheduler.run_due(NOW + timedelta(minutes=5))

    assert source.usage_ranges == [
        (previous - timedelta(hours=1), NOW),
        (NOW - timedelta(hours=1), NOW + timedelta(minutes=5)),
    ]
    assert repository.usage_occurrence_count() == 1
    events = repository.list_events(EventFilters(), 100, 0).items
    usage_events = [
        event for event in events if event.event_type == "new_conversations"
    ]
    assert len(usage_events) == 1
    event_hour = usage_events[0].occurred_at.astimezone(
        timezone(timedelta(hours=8))
    )
    expected_hour = late_time.astimezone(timezone(timedelta(hours=8))).replace(
        minute=0, second=0, microsecond=0
    )
    assert event_hour == expected_hour


class EmptyThenTurnSource:
    def __init__(self):
        self.calls = 0

    def fetch_usage(self, _after, through):
        self.calls += 1
        if self.calls == 1:
            return ()
        return (
            UsageOccurrence(
                turn_key="fae:turn-238",
                agent_id="ai-fae-agent",
                source_kind="fae",
                occurred_at=through,
            ),
        )

    def fetch_execution(self, _after, _through):
        return ()


@pytest.mark.asyncio
async def test_zero_occurrence_baseline_seeds_cumulative_and_milestone_state(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    source = EmptyThenTurnSource()
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub((237, 238)),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.startup(NOW)

    assert repository.get_rule_state("usage:ai-fae-agent").value == {
        "cumulative_conversations": 237
    }
    assert repository.get_rule_state("milestone:ai-fae-agent").value == {
        "reached": 100
    }
    assert repository.list_events(EventFilters(), 100, 0).items == []

    await scheduler.run_due(NOW + timedelta(minutes=5))

    event_types = [
        event.event_type
        for event in repository.list_events(EventFilters(), 100, 0).items
    ]
    assert event_types == ["new_conversations"]
    assert repository.get_rule_state("usage:ai-fae-agent").value == {
        "cumulative_conversations": 238
    }


class ExecutionReplaySource:
    def __init__(self, observation: ExecutionObservation):
        self.observation = observation
        self.execution_ranges: list[tuple[datetime, datetime]] = []

    def fetch_usage(self, _after, _through):
        return ()

    def fetch_execution(self, after, through):
        self.execution_ranges.append((after, through))
        return (self.observation,)


@pytest.mark.asyncio
async def test_late_execution_replay_uses_overlap_and_records_signal_once(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    previous = NOW - timedelta(minutes=5)
    repository.record_run(
        RunHealth(
            run_name="execution",
            status="succeeded",
            started_at=previous,
            finished_at=previous,
            cursor={"through": previous.isoformat()},
        )
    )
    late_time = NOW - timedelta(days=2, minutes=20)
    source = ExecutionReplaySource(
        ExecutionObservation(
            turn_key="fae:late-turn",
            session_key="fae:late-session",
            agent_id="ai-fae-agent",
            agent_name="FAE",
            agent_visibility="business",
            source_kind="fae",
            signal_type="fallback",
            occurred_at=late_time,
        )
    )
    scheduler = OperationsScheduler(
        repository=repository,
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"execution": 0},
    )

    await scheduler.run_due(NOW)
    await scheduler.run_due(NOW + timedelta(minutes=5))

    assert source.execution_ranges == [
        (previous - timedelta(hours=1), NOW),
        (NOW - timedelta(hours=1), NOW + timedelta(minutes=5)),
    ]
    events = repository.list_events(EventFilters(), 100, 0).items
    assert len(events) == 1
    assert events[0].event_type == "fallback"
    assert events[0].facts["turn_keys"] == ["fae:late-turn"]


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["usage", "execution"])
async def test_incremental_cursor_does_not_advance_when_rule_transaction_fails(
    tmp_path, group
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    repository.record_run(
        RunHealth(
            run_name=group,
            status="succeeded",
            started_at=NOW - timedelta(minutes=10),
            finished_at=NOW - timedelta(minutes=10),
            cursor={"through": (NOW - timedelta(minutes=10)).isoformat()},
        )
    )
    engine = RuleEngineStub(
        fail_usage=group == "usage", fail_execution=group == "execution"
    )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub(),
        operations_source=SourceStub(),
        rule_engine=engine,
        intervals={group: 0},
    )

    await scheduler.run_due(NOW)

    latest = repository.latest_run(group)
    assert latest.status == "failed"
    assert latest.cursor == {"through": (NOW - timedelta(minutes=10)).isoformat()}
