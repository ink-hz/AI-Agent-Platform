import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.fleet.models import (
    DataSourceStatus,
    FleetAgent,
    FleetOverview,
    FleetSummary,
)
from app.observability.models import SyncStatus
from app.operations import models as operation_models
from app.operations.models import (
    EventFilters,
    ExecutionObservation,
    RunHealth,
    UsageOccurrence,
)
from app.operations.repository import OperationsRepository
from app.operations.rules import OperationsRuleEngine
from app.operations.scheduler import (
    CommittedGroupRun,
    OperationsScheduler,
    operations_poll_loop,
)


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


class FixedFleetStub:
    def __init__(self, value: FleetOverview) -> None:
        self.value = value
        self.calls = 0

    async def overview(self, _now):
        self.calls += 1
        return self.value


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

    def evaluate_usage(
        self, observations, now, *, initializing, successful_run=None
    ):
        self.calls["usage"] = (observations, now, initializing)
        if self.fail_usage:
            raise RuntimeError("usage transaction failed")
        return None

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
                source_kind="metabot",
                occurred_at=NOW - timedelta(hours=23, minutes=15),
            ),
            UsageOccurrence(
                turn_key="fae:turn-2",
                agent_id="ai-fae-agent",
                source_kind="metabot",
                occurred_at=NOW - timedelta(hours=23, minutes=5),
            ),
        )

    fetch_local_usage = fetch_usage

    def fetch_local_usage_batch(self, after, through):
        return operation_models.UsageBatch(
            occurrences=self.fetch_usage(after, through),
            cumulative_totals={"ai-fae-agent": 237},
        )

    def fetch_remote_usage(self, _source_kind, **_filters):
        return ()

    def fetch_remote_usage_batch(self, _source_kind, **_filters):
        return operation_models.UsageBatch(
            occurrences=(), cumulative_totals={}
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
                source_kind="metabot",
                signal_type="fallback",
                occurred_at=through,
            ),
        )

    fetch_local_execution = fetch_execution

    def fetch_remote_execution(self, _source_kind, **_filters):
        return ()


def seed_active_usage(repository, observed_at):
    OperationsRuleEngine(repository).evaluate_usage(
        [
            operation_models.UsageObservation(
                agent_id="ai-fae-agent",
                agent_name="FAE",
                agent_visibility="business",
                source_kind="metabot",
                bucket_start=observed_at,
                conversations=1,
                cumulative_conversations=99,
                occurrences=(
                    UsageOccurrence(
                        turn_key="metabot:seed-99",
                        agent_id="ai-fae-agent",
                        source_kind="metabot",
                        occurred_at=observed_at,
                    ),
                ),
            )
        ],
        observed_at,
        initializing=True,
    )
    event = repository.list_events(EventFilters(), 100, 0).items[0]
    assert event.status == "active"
    return event


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

    assert sorted(calls) == ["runtime", "usage"]
    assert repository.latest_run("runtime").status == "failed"
    assert repository.latest_run("runtime").error_summary == "RuntimeError: runtime failed"
    assert repository.latest_run("usage").status == "succeeded"
    assert repository.latest_run("usage").cursor == {"cursor": "complete"}


@pytest.mark.asyncio
async def test_slow_runtime_does_not_delay_usage_and_each_group_runs_once(tmp_path):
    runtime_started = asyncio.Event()
    release_runtime = asyncio.Event()
    usage_finished = asyncio.Event()
    calls = {"runtime": 0, "usage": 0}

    async def runtime(_now):
        calls["runtime"] += 1
        runtime_started.set()
        await release_runtime.wait()

    async def usage(_now):
        calls["usage"] += 1
        usage_finished.set()

    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        group_runners={"runtime": runtime, "usage": usage},
        intervals={"runtime": 60, "usage": 60},
    )

    run = asyncio.create_task(scheduler.run_due(NOW))
    await asyncio.wait_for(runtime_started.wait(), timeout=1)
    try:
        await asyncio.wait_for(usage_finished.wait(), timeout=0.1)
        assert not run.done()
    finally:
        release_runtime.set()
        await asyncio.gather(run, return_exceptions=True)

    assert calls == {"runtime": 1, "usage": 1}
    assert repository.latest_run("runtime").status == "succeeded"
    assert repository.latest_run("usage").status == "succeeded"


@pytest.mark.asyncio
async def test_concurrent_scheduler_passes_do_not_overlap_or_duplicate_a_group(
    tmp_path,
):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def runtime(_now):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        group_runners={"runtime": runtime},
        intervals={"runtime": 60},
    )

    first = asyncio.create_task(scheduler.run_due(NOW))
    await asyncio.wait_for(started.wait(), timeout=1)
    second = asyncio.create_task(scheduler.run_due(NOW))
    try:
        await asyncio.sleep(0)
        assert calls == 1
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)

    assert calls == 1


@pytest.mark.asyncio
async def test_scheduler_cancellation_cleans_up_all_group_tasks(tmp_path):
    started = {name: asyncio.Event() for name in ("runtime", "usage")}
    cleaned = {name: asyncio.Event() for name in started}

    def runner(name):
        async def wait_forever(_now):
            started[name].set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned[name].set()

        return wait_forever

    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        group_runners={name: runner(name) for name in started},
        intervals={name: 0 for name in started},
    )

    task = asyncio.create_task(scheduler.run_due(NOW))
    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())),
            timeout=0.1,
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert all(event.is_set() for event in cleaned.values())


@pytest.mark.asyncio
async def test_poll_loop_completes_baseline_before_periodic_evaluation():
    baseline_started = asyncio.Event()
    release_baseline = asyncio.Event()

    class SchedulerStub:
        def __init__(self):
            self.run_due_calls = 0

        async def startup(self):
            baseline_started.set()
            await release_baseline.wait()

        async def run_due(self, _now):
            self.run_due_calls += 1

    scheduler = SchedulerStub()
    task = asyncio.create_task(operations_poll_loop(scheduler))
    try:
        await asyncio.wait_for(baseline_started.wait(), timeout=0.1)
        await asyncio.sleep(0)
        assert scheduler.run_due_calls == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_non_usage_committed_outcome_keeps_normal_run_bookkeeping(tmp_path):
    async def runtime(_now):
        return CommittedGroupRun(cursor={"cursor": "runtime"})

    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        group_runners={"runtime": runtime},
        intervals={"runtime": 0},
    )

    await scheduler.run_due(NOW)

    latest = repository.latest_run("runtime")
    assert latest.status == "succeeded"
    assert latest.cursor == {"cursor": "runtime"}


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
@pytest.mark.parametrize(
    "fleet_overview",
    [
        overview().model_copy(
            update={
                "runtime_source": DataSourceStatus(
                    healthy=False,
                    checked_at=NOW.isoformat(),
                    error="source unavailable",
                )
            }
        ),
        overview().model_copy(
            update={
                "runtime_source": DataSourceStatus(
                    healthy=True,
                    stale=True,
                    checked_at=NOW.isoformat(),
                )
            }
        ),
        overview().model_copy(
            update={
                "runtime_source": DataSourceStatus(
                    healthy=True,
                    checked_at=None,
                )
            }
        ),
        overview().model_copy(update={"agents": []}),
        overview().model_copy(
            update={
                "agents": [
                    overview().agents[0].model_copy(update={"state": "checking"}),
                    overview().agents[0].model_copy(
                        update={"id": "unknown-bot", "state": "unknown"}
                    ),
                ]
            }
        ),
    ],
    ids=(
        "unhealthy-source",
        "stale-source",
        "unchecked-source",
        "empty-fleet",
        "no-usable-state",
    ),
)
async def test_runtime_group_fails_when_required_evidence_is_incomplete(
    tmp_path, fleet_overview
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FixedFleetStub(fleet_overview),
        intervals={"runtime": 0},
    )

    await scheduler.run_due(NOW)

    latest = repository.latest_run("runtime")
    assert latest.status == "failed"
    assert latest.cursor == {}
    assert latest.error_summary in {
        "RuntimeError: required runtime source unavailable",
        "RuntimeError: required runtime evidence incomplete",
    }


@pytest.mark.asyncio
async def test_complete_runtime_evidence_records_success(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FixedFleetStub(overview()),
        intervals={"runtime": 0},
    )

    await scheduler.run_due(NOW)

    assert repository.latest_run("runtime").status == "succeeded"
    assert repository.get_rule_state("runtime:ai-fae-agent") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("incomplete_state", ["checking", "unknown"])
async def test_runtime_group_requires_every_returned_agent_to_be_usable(
    tmp_path, incomplete_state
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    prior_cursor = {"observed_at": (NOW - timedelta(minutes=1)).isoformat()}
    repository.record_run(
        RunHealth(
            run_name="runtime",
            status="succeeded",
            started_at=NOW - timedelta(minutes=1),
            finished_at=NOW - timedelta(minutes=1),
            cursor=prior_cursor,
        )
    )
    fleet_overview = overview().model_copy(
        update={
            "agents": [
                overview().agents[0],
                overview().agents[0].model_copy(
                    update={"id": "second-agent", "state": incomplete_state}
                ),
            ]
        }
    )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FixedFleetStub(fleet_overview),
        intervals={"runtime": 0},
    )

    await scheduler.run_due(NOW)

    latest = repository.latest_run("runtime")
    assert latest.status == "failed"
    assert latest.cursor == prior_cursor
    assert latest.error_summary == (
        "RuntimeError: required runtime evidence incomplete"
    )
    assert repository.get_rule_state("runtime:ai-fae-agent") is None
    assert repository.get_rule_state("runtime:second-agent") is None


@pytest.mark.asyncio
async def test_runtime_group_fails_when_expected_catalog_agent_is_missing(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    fleet_overview = overview().model_copy(
        update={
            "expected_agent_ids": ["ai-fae-agent", "ai-admin-agent"],
        }
    )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FixedFleetStub(fleet_overview),
        intervals={"runtime": 0},
    )

    await scheduler.run_due(NOW)

    latest = repository.latest_run("runtime")
    assert latest.status == "failed"
    assert latest.error_summary == (
        "RuntimeError: required runtime evidence incomplete"
    )
    assert repository.get_rule_state("runtime:ai-fae-agent") is None


@pytest.mark.asyncio
async def test_runtime_group_accepts_all_usable_states_including_degraded_offline(
    tmp_path,
):
    states = ("active", "online", "degraded", "offline")
    agents = [
        overview().agents[0].model_copy(
            update={"id": f"agent-{index}", "state": state}
        )
        for index, state in enumerate(states)
    ]
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FixedFleetStub(
            overview().model_copy(
                update={
                    "agents": agents,
                    "expected_agent_ids": [agent.id for agent in agents],
                }
            )
        ),
        intervals={"runtime": 0},
    )

    await scheduler.run_due(NOW)

    assert repository.latest_run("runtime").status == "succeeded"
    assert all(
        repository.get_rule_state(f"runtime:{agent.id}") is not None
        for agent in agents
    )


@pytest.mark.asyncio
async def test_sync_group_requires_exact_remote_source_coverage_and_keeps_cursor(
    tmp_path,
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    prior_cursor = {"observed_at": (NOW - timedelta(minutes=1)).isoformat()}
    repository.record_run(
        RunHealth(
            run_name="sync",
            status="succeeded",
            started_at=NOW - timedelta(minutes=1),
            finished_at=NOW - timedelta(minutes=1),
            cursor=prior_cursor,
        )
    )
    scheduler = OperationsScheduler(
        repository=repository,
        observability_service=SyncSequence(
            [(sync_status("fae", "succeeded", NOW),)]
        ),
        intervals={"sync": 0},
    )

    await scheduler.run_due(NOW)

    latest = repository.latest_run("sync")
    assert latest.status == "failed"
    assert latest.cursor == prior_cursor
    assert latest.error_summary == (
        "RuntimeError: required sync source coverage incomplete"
    )
    assert repository.get_rule_state("sync:fae") is None


@pytest.mark.asyncio
async def test_complete_failed_sync_observations_are_successfully_evaluated(
    tmp_path,
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    last_success = NOW - timedelta(hours=35)
    scheduler = OperationsScheduler(
        repository=repository,
        observability_service=SyncSequence(
            [
                (
                    sync_status(
                        "fae", "failed", NOW, last_success_at=last_success
                    ),
                    sync_status(
                        "admin", "failed", NOW, last_success_at=last_success
                    ),
                )
            ]
        ),
        intervals={"sync": 0},
    )

    await scheduler.run_due(NOW)

    assert repository.latest_run("sync").status == "succeeded"
    attention = repository.list_active_attention("business")
    assert {item.agent_id for item in attention} == {
        "ai-fae-agent",
        "ai-admin-agent",
    }
    assert all(item.facts["stale"] is False for item in attention)
    assert {
        source: repository.get_rule_state(f"sync:{source}").value[
            "last_success_at"
        ]
        for source in ("fae", "admin")
    } == {"fae": last_success.isoformat(), "admin": last_success.isoformat()}


@pytest.mark.asyncio
async def test_complete_running_sync_with_stale_history_is_evaluated(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    last_success = NOW - timedelta(hours=36, microseconds=1)
    scheduler = OperationsScheduler(
        repository=repository,
        observability_service=SyncSequence(
            [
                (
                    sync_status(
                        "fae", "running", None, last_success_at=last_success
                    ),
                    sync_status("admin", "succeeded", NOW),
                )
            ]
        ),
        intervals={"sync": 0},
    )

    await scheduler.run_due(NOW)

    assert repository.latest_run("sync").status == "succeeded"
    attention = repository.list_active_attention("business")
    assert len(attention) == 1
    assert attention[0].agent_id == "ai-fae-agent"
    assert attention[0].facts["status"] == "running"
    assert attention[0].facts["stale"] is True


@pytest.mark.asyncio
async def test_complete_fresh_sync_coverage_records_success_without_attention(
    tmp_path,
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        observability_service=SyncSequence(
            [
                (
                    sync_status("fae", "succeeded", NOW),
                    sync_status("admin", "succeeded", NOW),
                )
            ]
        ),
        intervals={"sync": 0},
    )

    await scheduler.run_due(NOW)

    assert repository.latest_run("sync").status == "succeeded"
    assert repository.list_active_attention("business") == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "failed"])
async def test_old_sync_view_non_success_without_history_fails_closed(
    tmp_path, status
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    prior_cursor = {"observed_at": (NOW - timedelta(minutes=1)).isoformat()}
    repository.record_run(
        RunHealth(
            run_name="sync",
            status="succeeded",
            started_at=NOW - timedelta(minutes=1),
            finished_at=NOW - timedelta(minutes=1),
            cursor=prior_cursor,
        )
    )
    scheduler = OperationsScheduler(
        repository=repository,
        observability_service=SyncSequence(
            [
                (
                    SyncStatus(
                        source_kind="fae",
                        status=status,
                        started_at=NOW - timedelta(minutes=1),
                        completed_at=NOW if status == "failed" else None,
                        freshness="stale",
                    ),
                    sync_status("admin", "succeeded", NOW),
                )
            ]
        ),
        intervals={"sync": 0},
    )

    await scheduler.run_due(NOW)

    latest = repository.latest_run("sync")
    assert latest.status == "failed"
    assert latest.cursor == prior_cursor
    assert latest.error_summary == (
        "RuntimeError: required sync success history unavailable"
    )
    assert repository.get_rule_state("sync:fae") is None
    assert repository.get_rule_state("sync:admin") is None
    assert repository.list_active_attention("business") == ()


@pytest.mark.asyncio
async def test_old_sync_view_latest_success_uses_completed_at_fallback(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    statuses = tuple(
        SyncStatus(
            source_kind=source,
            status="succeeded",
            started_at=NOW - timedelta(minutes=1),
            completed_at=NOW,
            freshness="fresh",
        )
        for source in ("fae", "admin")
    )
    scheduler = OperationsScheduler(
        repository=repository,
        observability_service=SyncSequence([statuses]),
        intervals={"sync": 0},
    )

    await scheduler.run_due(NOW)

    assert repository.latest_run("sync").status == "succeeded"
    assert {
        source: repository.get_rule_state(f"sync:{source}").value[
            "last_success_at"
        ]
        for source in ("fae", "admin")
    } == {"fae": NOW.isoformat(), "admin": NOW.isoformat()}


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
    assert repository.latest_run("usage").cursor == {
        "local_through": NOW.isoformat(),
        "remote_generations": {},
    }


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

    fetch_local_usage = fetch_usage

    def fetch_local_usage_batch(self, after, through):
        return operation_models.UsageBatch(
            occurrences=self.fetch_usage(after, through),
            cumulative_totals={"ai-fae-agent": 237},
        )

    def fetch_remote_usage(self, _source_kind, **_filters):
        return ()

    def fetch_remote_usage_batch(self, _source_kind, **_filters):
        return operation_models.UsageBatch(
            occurrences=(), cumulative_totals={}
        )

    def fetch_execution(self, _after, _through):
        return ()

    fetch_local_execution = fetch_execution

    def fetch_remote_execution(self, _source_kind, **_filters):
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
            source_kind="metabot",
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
                source_kind="metabot",
                occurred_at=through,
            ),
        )

    fetch_local_usage = fetch_usage

    def fetch_local_usage_batch(self, after, through):
        occurrences = self.fetch_usage(after, through)
        total = 237 if self.calls == 1 else 238
        return operation_models.UsageBatch(
            occurrences=occurrences,
            cumulative_totals={"ai-fae-agent": total},
        )

    def fetch_remote_usage(self, _source_kind, **_filters):
        return ()

    def fetch_remote_usage_batch(self, _source_kind, **_filters):
        return operation_models.UsageBatch(
            occurrences=(), cumulative_totals={}
        )

    def fetch_execution(self, _after, _through):
        return ()

    fetch_local_execution = fetch_execution

    def fetch_remote_execution(self, _source_kind, **_filters):
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

    fetch_local_usage = fetch_usage

    def fetch_remote_usage(self, _source_kind, **_filters):
        return ()

    def fetch_execution(self, after, through):
        self.execution_ranges.append((after, through))
        return (self.observation,)

    fetch_local_execution = fetch_execution

    def fetch_remote_execution(self, _source_kind, **_filters):
        return ()


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
            source_kind="metabot",
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


@pytest.mark.asyncio
async def test_usage_success_cursor_insert_failure_rolls_back_full_batch(tmp_path):
    database_path = tmp_path / "operations.db"
    repository = OperationsRepository(str(database_path))
    repository.migrate()
    seeded_event = seed_active_usage(repository, NOW - timedelta(hours=2))
    previous = NOW - timedelta(minutes=10)
    previous_cursor = {
        "local_through": previous.isoformat(),
        "remote_generations": {},
    }
    repository.record_run(
        RunHealth(
            run_name="usage",
            status="succeeded",
            started_at=previous,
            finished_at=previous,
            cursor=previous_cursor,
        )
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            create trigger reject_usage_success_run
            before insert on operational_runs
            when new.run_name='usage' and new.status='succeeded'
            begin
              select raise(abort, 'forced usage success cursor failure');
            end
            """
        )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub(),
        operations_source=SourceStub(),
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.run_due(NOW)

    assert repository.usage_occurrence_count() == 1
    events = repository.list_events(EventFilters(), 100, 0).items
    assert len(events) == 1
    assert events[0].event_id == seeded_event.event_id
    assert events[0].status == "active"
    assert repository.get_rule_state("usage:ai-fae-agent").value == {
        "cumulative_conversations": 99
    }
    assert repository.get_rule_state("milestone:ai-fae-agent").value == {
        "reached": 0
    }
    latest = repository.latest_run("usage")
    assert latest.status == "failed"
    assert latest.cursor == previous_cursor
    assert "forced usage success cursor failure" in latest.error_summary


@pytest.mark.asyncio
async def test_usage_success_cursor_and_full_batch_commit_together(tmp_path):
    database_path = tmp_path / "operations.db"
    repository = OperationsRepository(str(database_path))
    repository.migrate()
    seeded_event = seed_active_usage(repository, NOW - timedelta(hours=2))
    previous = NOW - timedelta(minutes=10)
    repository.record_run(
        RunHealth(
            run_name="usage",
            status="succeeded",
            started_at=previous,
            finished_at=previous,
            cursor={
                "local_through": previous.isoformat(),
                "remote_generations": {},
            },
        )
    )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub(),
        operations_source=SourceStub(),
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.run_due(NOW)

    latest = repository.latest_run("usage")
    assert latest.status == "succeeded"
    assert latest.cursor == {
        "local_through": NOW.isoformat(),
        "remote_generations": {},
    }
    assert repository.usage_occurrence_count() == 3
    assert repository.get_rule_state("usage:ai-fae-agent").value == {
        "cumulative_conversations": 237
    }
    assert repository.get_rule_state("milestone:ai-fae-agent").value == {
        "reached": 100
    }
    events = repository.list_events(EventFilters(), 100, 0).items
    expired_seed = next(
        event for event in events if event.event_id == seeded_event.event_id
    )
    assert expired_seed.status == "historical"
    assert any(event.event_type == "new_conversations" for event in events)
    assert any(event.event_type == "conversation_milestone" for event in events)
    with sqlite3.connect(database_path) as connection:
        succeeded_runs = connection.execute(
            """
            select count(*) from operational_runs
            where run_name='usage' and status='succeeded'
            """
        ).fetchone()[0]
    assert succeeded_runs == 2


def sync_status(
    source_kind: str,
    status: str,
    completed_at: datetime | None,
    *,
    last_success_at: datetime | None = None,
) -> SyncStatus:
    return SyncStatus(
        source_kind=source_kind,
        status=status,
        started_at=(completed_at or NOW) - timedelta(minutes=1),
        completed_at=completed_at,
        last_success_at=(
            completed_at
            if last_success_at is None and status == "succeeded"
            else last_success_at
        ),
        freshness="fresh",
    )


class SyncSequence:
    def __init__(self, generations):
        self.generations = list(generations)
        self.calls = 0

    async def sync_status(self):
        value = self.generations[min(self.calls, len(self.generations) - 1)]
        self.calls += 1
        return value


class SplitSource:
    def __init__(self, *, usage_batches=(), execution_batches=()):
        self.usage_batches = list(usage_batches)
        self.execution_batches = list(execution_batches)
        self.local_usage_ranges: list[tuple[datetime, datetime]] = []
        self.local_execution_ranges: list[tuple[datetime, datetime]] = []
        self.remote_usage_scans: list[tuple[str, datetime | None, datetime | None]] = []
        self.remote_execution_scans: list[
            tuple[str, datetime | None, datetime | None]
        ] = []

    def fetch_local_usage(self, after, through):
        self.local_usage_ranges.append((after, through))
        return ()

    def fetch_local_usage_batch(self, after, through):
        return operation_models.UsageBatch(
            occurrences=self.fetch_local_usage(after, through),
            cumulative_totals={},
        )

    def fetch_remote_usage(
        self, source_kind, *, created_after=None, created_through=None
    ):
        self.remote_usage_scans.append(
            (source_kind, created_after, created_through)
        )
        return self.usage_batches.pop(0) if self.usage_batches else ()

    def fetch_remote_usage_batch(
        self, source_kind, *, created_after=None, created_through=None
    ):
        value = self.fetch_remote_usage(
            source_kind,
            created_after=created_after,
            created_through=created_through,
        )
        if isinstance(value, Exception):
            raise value
        if isinstance(value, operation_models.UsageBatch):
            return value
        totals = {item.agent_id: 237 for item in value}
        return operation_models.UsageBatch(
            occurrences=value,
            cumulative_totals=totals,
        )

    def fetch_local_execution(self, after, through):
        self.local_execution_ranges.append((after, through))
        return ()

    def fetch_remote_execution(
        self, source_kind, *, created_after=None, created_through=None
    ):
        self.remote_execution_scans.append(
            (source_kind, created_after, created_through)
        )
        return self.execution_batches.pop(0) if self.execution_batches else ()


@pytest.mark.asyncio
async def test_remote_usage_initialization_scans_only_true_preceding_day(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    generation = NOW - timedelta(minutes=1)
    source = SplitSource(usage_batches=[()])
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub(),
        observability_service=SyncSequence(
            [(sync_status("fae", "succeeded", generation),)]
        ),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.startup(NOW)

    assert source.remote_usage_scans == [
        ("fae", NOW - timedelta(hours=24), NOW)
    ]
    assert repository.latest_run("usage").cursor["remote_generations"] == {
        "fae": generation.isoformat()
    }


@pytest.mark.asyncio
async def test_remote_usage_scans_each_successful_generation_once(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    first_generation = NOW - timedelta(minutes=1)
    next_generation = NOW + timedelta(minutes=9)
    late_time = NOW - timedelta(days=40, minutes=20)
    baseline = UsageOccurrence(
        turn_key="fae:baseline-turn",
        agent_id="ai-fae-agent",
        source_kind="fae",
        occurred_at=NOW - timedelta(minutes=20),
    )
    late = UsageOccurrence(
        turn_key="fae:late-generation-turn",
        agent_id="ai-fae-agent",
        source_kind="fae",
        occurred_at=late_time,
    )
    source = SplitSource(usage_batches=[(baseline,), (baseline, late)])
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub(),
        observability_service=SyncSequence(
            [
                (sync_status("fae", "succeeded", first_generation),),
                (sync_status("fae", "succeeded", first_generation),),
                (sync_status("fae", "succeeded", next_generation),),
                (sync_status("fae", "succeeded", next_generation),),
            ]
        ),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.startup(NOW)
    await scheduler.run_due(NOW + timedelta(minutes=5))
    await scheduler.run_due(NOW + timedelta(minutes=10))
    await scheduler.run_due(NOW + timedelta(minutes=15))

    assert source.remote_usage_scans == [
        ("fae", NOW - timedelta(hours=24), NOW),
        ("fae", None, None),
    ]
    assert repository.usage_occurrence_count() == 2
    usage_events = [
        event
        for event in repository.list_events(EventFilters(), 100, 0).items
        if event.event_type == "new_conversations"
    ]
    assert len(usage_events) == 2
    late_event = next(
        event for event in usage_events if event.occurred_at < NOW - timedelta(days=1)
    )
    assert late_event.occurred_at == late_time.replace(
        minute=0, second=0, microsecond=0
    )


@pytest.mark.asyncio
async def test_failed_remote_sync_is_not_marked_processed_and_recovery_scans(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    first_generation = NOW - timedelta(minutes=1)
    recovery_generation = NOW + timedelta(minutes=9)
    source = SplitSource(usage_batches=[(), ()])
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub(),
        observability_service=SyncSequence(
            [
                (sync_status("fae", "succeeded", first_generation),),
                (sync_status("fae", "failed", NOW + timedelta(minutes=4)),),
                (sync_status("fae", "succeeded", recovery_generation),),
            ]
        ),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.startup(NOW)
    await scheduler.run_due(NOW + timedelta(minutes=5))
    failed_cursor = repository.latest_run("usage").cursor
    await scheduler.run_due(NOW + timedelta(minutes=10))

    assert failed_cursor["remote_generations"] == {
        "fae": first_generation.isoformat()
    }
    assert source.remote_usage_scans == [
        ("fae", NOW - timedelta(hours=24), NOW),
        ("fae", None, None),
    ]
    assert repository.latest_run("usage").cursor["remote_generations"] == {
        "fae": recovery_generation.isoformat()
    }


@pytest.mark.asyncio
async def test_remote_execution_scans_new_generation_once_and_keeps_true_hour(
    tmp_path,
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    first_generation = NOW - timedelta(minutes=1)
    next_generation = NOW + timedelta(minutes=9)
    late_time = NOW - timedelta(days=40, minutes=20)
    baseline = ExecutionObservation(
        turn_key="fae:baseline-turn",
        session_key="fae:baseline-session",
        agent_id="ai-fae-agent",
        agent_name="FAE",
        agent_visibility="business",
        source_kind="fae",
        signal_type="fallback",
        occurred_at=NOW - timedelta(minutes=20),
    )
    late = ExecutionObservation(
        turn_key="fae:late-generation-turn",
        session_key="fae:late-session",
        agent_id="ai-fae-agent",
        agent_name="FAE",
        agent_visibility="business",
        source_kind="fae",
        signal_type="fallback",
        occurred_at=late_time,
    )
    source = SplitSource(execution_batches=[(baseline,), (baseline, late)])
    scheduler = OperationsScheduler(
        repository=repository,
        observability_service=SyncSequence(
            [
                (sync_status("fae", "succeeded", first_generation),),
                (sync_status("fae", "succeeded", next_generation),),
                (sync_status("fae", "succeeded", next_generation),),
            ]
        ),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"execution": 0},
    )

    await scheduler.startup(NOW)
    await scheduler.run_due(NOW + timedelta(minutes=10))
    await scheduler.run_due(NOW + timedelta(minutes=15))

    assert source.remote_execution_scans == [
        ("fae", NOW - timedelta(hours=24), NOW),
        ("fae", None, None),
    ]
    events = repository.list_events(EventFilters(), 100, 0).items
    assert len(events) == 2
    late_event = next(
        event for event in events if event.occurred_at < NOW - timedelta(days=1)
    )
    assert late_event.facts["turn_keys"] == ["fae:late-generation-turn"]
    assert late_event.occurred_at == late_time.replace(
        minute=0, second=0, microsecond=0
    )


class AlignedUsageSource:
    def __init__(self, *, local_batches=(), remote_batches=()):
        self.local_batches = list(local_batches)
        self.remote_batches = list(remote_batches)

    def fetch_local_usage_batch(self, _after, _through):
        if not self.local_batches:
            return operation_models.UsageBatch(
                occurrences=(), cumulative_totals={}
            )
        value = self.local_batches.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_remote_usage_batch(self, _source_kind, **_filters):
        value = self.remote_batches.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_local_execution(self, _after, _through):
        return ()

    def fetch_remote_execution(self, _source_kind, **_filters):
        return ()


@pytest.mark.asyncio
async def test_remote_milestone_uses_aligned_batch_total_not_stale_fleet_cache(
    tmp_path,
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    first_generation = NOW - timedelta(minutes=1)
    next_generation = NOW + timedelta(minutes=4)
    turn = UsageOccurrence(
        turn_key="fae:turn-100",
        agent_id="ai-fae-agent",
        source_kind="fae",
        occurred_at=NOW + timedelta(minutes=5),
    )
    source = AlignedUsageSource(
        remote_batches=[
            operation_models.UsageBatch(
                occurrences=(),
                cumulative_totals={"ai-fae-agent": 99},
            ),
            operation_models.UsageBatch(
                occurrences=(turn,),
                cumulative_totals={"ai-fae-agent": 100},
            ),
        ]
    )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub((99, 99)),
        observability_service=SyncSequence(
            [
                (sync_status("fae", "succeeded", first_generation),),
                (sync_status("fae", "succeeded", next_generation),),
            ]
        ),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.startup(NOW)
    await scheduler.run_due(NOW + timedelta(minutes=5))

    event_types = [
        event.event_type
        for event in repository.list_events(EventFilters(), 100, 0).items
    ]
    assert "conversation_milestone" in event_types
    assert repository.get_rule_state("usage:ai-fae-agent").value == {
        "cumulative_conversations": 100
    }
    assert repository.latest_run("usage").cursor["remote_generations"] == {
        "fae": next_generation.isoformat()
    }


@pytest.mark.asyncio
async def test_remote_snapshot_dates_milestone_at_current_crossing_turn(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    first_generation = NOW - timedelta(minutes=1)
    next_generation = NOW + timedelta(minutes=4)
    old_time = NOW - timedelta(days=40)
    old_turns = tuple(
        UsageOccurrence(
            turn_key=f"fae:historical-{index}",
            agent_id="ai-fae-agent",
            source_kind="fae",
            occurred_at=old_time + timedelta(seconds=index),
        )
        for index in range(99)
    )
    crossing_turn = UsageOccurrence(
        turn_key="fae:turn-100",
        agent_id="ai-fae-agent",
        source_kind="fae",
        occurred_at=NOW + timedelta(minutes=5),
    )
    source = AlignedUsageSource(
        remote_batches=[
            operation_models.UsageBatch(
                occurrences=(),
                cumulative_totals={"ai-fae-agent": 99},
            ),
            operation_models.UsageBatch(
                occurrences=(*old_turns, crossing_turn),
                cumulative_totals={"ai-fae-agent": 100},
            ),
        ]
    )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub((99, 99)),
        observability_service=SyncSequence(
            [
                (sync_status("fae", "succeeded", first_generation),),
                (sync_status("fae", "succeeded", next_generation),),
            ]
        ),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.startup(NOW)
    await scheduler.run_due(crossing_turn.occurred_at)

    milestone = next(
        event
        for event in repository.list_events(EventFilters(), 200, 0).items
        if event.event_type == "conversation_milestone"
    )
    assert milestone.occurred_at == crossing_turn.occurred_at
    historical_usage = next(
        event
        for event in repository.list_events(EventFilters(), 200, 0).items
        if event.event_type == "new_conversations"
        and event.occurred_at < NOW - timedelta(days=1)
    )
    assert historical_usage.facts["cumulative_conversations"] == 99


@pytest.mark.asyncio
async def test_remote_batch_total_failure_retains_generation_cursor(tmp_path):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    first_generation = NOW - timedelta(minutes=1)
    next_generation = NOW + timedelta(minutes=4)
    source = AlignedUsageSource(
        remote_batches=[
            operation_models.UsageBatch(
                occurrences=(),
                cumulative_totals={"ai-fae-agent": 99},
            ),
            RuntimeError("aligned total query failed"),
        ]
    )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub((99, 99)),
        observability_service=SyncSequence(
            [
                (sync_status("fae", "succeeded", first_generation),),
                (sync_status("fae", "succeeded", next_generation),),
            ]
        ),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.startup(NOW)
    await scheduler.run_due(NOW + timedelta(minutes=5))

    latest = repository.latest_run("usage")
    assert latest.status == "failed"
    assert latest.cursor["remote_generations"] == {
        "fae": first_generation.isoformat()
    }


@pytest.mark.asyncio
async def test_local_milestone_uses_aligned_batch_total_not_stale_fleet_cache(
    tmp_path,
):
    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    turn = UsageOccurrence(
        turn_key="metabot:turn-100",
        agent_id="ai-fae-agent",
        source_kind="metabot",
        occurred_at=NOW + timedelta(minutes=5),
    )
    source = AlignedUsageSource(
        local_batches=[
            operation_models.UsageBatch(
                occurrences=(),
                cumulative_totals={"ai-fae-agent": 99},
            ),
            operation_models.UsageBatch(
                occurrences=(turn,),
                cumulative_totals={"ai-fae-agent": 100},
            ),
        ]
    )
    scheduler = OperationsScheduler(
        repository=repository,
        fleet_service=FleetStub((99, 99)),
        operations_source=source,
        rule_engine=OperationsRuleEngine(repository),
        intervals={"usage": 0},
    )

    await scheduler.startup(NOW)
    await scheduler.run_due(NOW + timedelta(minutes=5))

    event_types = [
        event.event_type
        for event in repository.list_events(EventFilters(), 100, 0).items
    ]
    assert "conversation_milestone" in event_types
    assert repository.get_rule_state("usage:ai-fae-agent").value == {
        "cumulative_conversations": 100
    }
