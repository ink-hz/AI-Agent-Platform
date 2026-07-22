from datetime import datetime, timedelta, timezone

from app.operations.models import EventFilters
from app.operations.repository import OperationsRepository
from app.operations.rules import (
    DataAccessObservation,
    OperationsRuleEngine,
    RuntimeObservation,
    SyncObservation,
    ExecutionObservation,
    LifecycleObservation,
    UsageObservation,
)


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)


def make_engine(tmp_path):
    repo = OperationsRepository(str(tmp_path / "operations.db"))
    repo.migrate()
    return OperationsRuleEngine(repo), repo


def runtime(agent_id: str, visibility: str, state: str, observed_at: datetime):
    return RuntimeObservation(
        agent_id=agent_id,
        agent_name=agent_id,
        agent_visibility=visibility,
        source_kind="metabot",
        state=state,
        observed_at=observed_at,
    )


def usage(agent_id: str, cumulative: int, bucket_start: datetime):
    return UsageObservation(
        agent_id=agent_id,
        agent_name=agent_id,
        agent_visibility="business",
        source_kind="fae" if agent_id == "ai-fae-agent" else "metabot",
        bucket_start=bucket_start,
        conversations=1,
        cumulative_conversations=cumulative,
    )


def lifecycle(
    agent_id: str,
    *,
    live_since: datetime | None,
    last_updated_at: datetime | None,
    observed_at: datetime,
):
    return LifecycleObservation(
        agent_id=agent_id,
        agent_name=agent_id,
        agent_visibility="business",
        source_kind="catalog",
        live_since=live_since,
        last_updated_at=last_updated_at,
        observed_at=observed_at,
    )


def execution(
    agent_id: str, signal_type: str, turn_key: str, occurred_at: datetime
):
    session_key = "fae:session-2" if turn_key.endswith("2") else "fae:session-1"
    return ExecutionObservation(
        turn_key=turn_key,
        session_key=session_key,
        agent_id=agent_id,
        agent_name=agent_id,
        agent_visibility="business",
        source_kind="fae",
        signal_type=signal_type,
        occurred_at=occurred_at,
    )


def event_types(repo: OperationsRepository) -> list[str]:
    return [item.event_type for item in repo.list_events(EventFilters(), 100, 0).items]


def test_runtime_requires_two_observations_to_open_and_clear(tmp_path):
    engine, repo = make_engine(tmp_path)
    offline = runtime("hr-bot", "business", "offline", NOW)
    online = runtime("hr-bot", "business", "online", NOW + timedelta(seconds=10))

    engine.evaluate_runtime([offline], NOW)
    assert repo.list_active_attention("business") == ()
    engine.evaluate_runtime([offline], NOW + timedelta(seconds=10))
    assert event_types(repo) == ["runtime_offline"]
    engine.evaluate_runtime([online], NOW + timedelta(seconds=20))
    assert event_types(repo) == ["runtime_offline"]
    engine.evaluate_runtime([online], NOW + timedelta(seconds=30))
    assert event_types(repo) == ["runtime_recovered", "runtime_offline"]


def test_runtime_degraded_is_attention_and_active_is_healthy(tmp_path):
    engine, repo = make_engine(tmp_path)
    degraded = runtime("sales-bot", "business", "degraded", NOW)
    active = runtime("sales-bot", "business", "active", NOW + timedelta(seconds=20))

    engine.evaluate_runtime([degraded], NOW)
    engine.evaluate_runtime([degraded], NOW + timedelta(seconds=10))

    attention = repo.list_active_attention("business")
    assert len(attention) == 1
    assert attention[0].event_type == "runtime_degraded"
    assert attention[0].severity == "attention"

    engine.evaluate_runtime([active], NOW + timedelta(seconds=20))
    engine.evaluate_runtime([active], NOW + timedelta(seconds=30))
    assert event_types(repo) == ["runtime_recovered", "runtime_degraded"]


def test_runtime_candidate_must_be_consecutive_and_unknown_is_ignored(tmp_path):
    engine, repo = make_engine(tmp_path)

    engine.evaluate_runtime([runtime("hr-bot", "business", "offline", NOW)], NOW)
    engine.evaluate_runtime(
        [runtime("hr-bot", "business", "degraded", NOW + timedelta(seconds=10))],
        NOW + timedelta(seconds=10),
    )
    engine.evaluate_runtime(
        [runtime("hr-bot", "business", "unknown", NOW + timedelta(seconds=20))],
        NOW + timedelta(seconds=20),
    )
    engine.evaluate_runtime(
        [runtime("hr-bot", "business", "degraded", NOW + timedelta(seconds=30))],
        NOW + timedelta(seconds=30),
    )

    assert event_types(repo) == ["runtime_degraded"]


def test_runtime_reclassifies_degraded_as_offline_without_duplicate_or_recovery(
    tmp_path,
):
    engine, repo = make_engine(tmp_path)
    degraded = runtime("hr-bot", "business", "degraded", NOW)
    offline = runtime("hr-bot", "business", "offline", NOW + timedelta(seconds=20))

    engine.evaluate_runtime([degraded], NOW)
    engine.evaluate_runtime([degraded], NOW + timedelta(seconds=10))
    original = repo.list_active_attention("business")[0]
    engine.evaluate_runtime([offline], NOW + timedelta(seconds=20))
    engine.evaluate_runtime([offline], NOW + timedelta(seconds=30))

    active = repo.list_active_attention("business")
    assert len(active) == 1
    assert active[0].event_id == original.event_id
    assert active[0].event_type == "runtime_offline"
    assert active[0].severity == "critical"
    assert active[0].title == "hr-bot is offline"
    assert active[0].summary == "Two consecutive runtime observations reported offline."
    assert active[0].facts == {"state": "offline", "observations": 2}
    assert event_types(repo) == ["runtime_offline"]


def test_runtime_reclassifies_offline_as_degraded_without_duplicate_or_recovery(
    tmp_path,
):
    engine, repo = make_engine(tmp_path)
    offline = runtime("hr-bot", "business", "offline", NOW)
    degraded = runtime(
        "hr-bot", "business", "degraded", NOW + timedelta(seconds=20)
    )

    engine.evaluate_runtime([offline], NOW)
    engine.evaluate_runtime([offline], NOW + timedelta(seconds=10))
    original = repo.list_active_attention("business")[0]
    engine.evaluate_runtime([degraded], NOW + timedelta(seconds=20))
    engine.evaluate_runtime([degraded], NOW + timedelta(seconds=30))

    active = repo.list_active_attention("business")
    assert len(active) == 1
    assert active[0].event_id == original.event_id
    assert active[0].event_type == "runtime_degraded"
    assert active[0].severity == "attention"
    assert active[0].title == "hr-bot is degraded"
    assert active[0].summary == "Two consecutive runtime observations reported degraded."
    assert active[0].facts == {"state": "degraded", "observations": 2}
    assert event_types(repo) == ["runtime_degraded"]


def test_sync_failure_and_staleness_share_one_active_event(tmp_path):
    engine, repo = make_engine(tmp_path)
    failed = SyncObservation(
        source_kind="fae",
        status="failed",
        completed_at=NOW,
        observed_at=NOW,
        last_success_at=NOW - timedelta(hours=35),
    )
    stale = failed.model_copy(
        update={
            "observed_at": NOW + timedelta(hours=2),
            "last_success_at": NOW - timedelta(hours=37),
        }
    )

    engine.evaluate_sync([failed], NOW)
    engine.evaluate_sync([stale], NOW + timedelta(hours=2))

    active = repo.list_active_attention("business")
    assert len(active) == 1
    assert active[0].event_type == "remote_sync_unavailable"
    assert active[0].facts["stale"] is True


def test_sync_staleness_opens_and_success_resolves_mapped_agent(tmp_path):
    engine, repo = make_engine(tmp_path)
    stale = SyncObservation(
        source_kind="admin",
        status="running",
        completed_at=None,
        observed_at=NOW,
        last_success_at=NOW - timedelta(hours=37),
    )

    engine.evaluate_sync([stale], NOW)

    active = repo.list_active_attention("business")
    assert len(active) == 1
    assert active[0].agent_id == "ai-admin-agent"
    assert active[0].fingerprint == "sync:admin:unavailable"

    recovered_at = NOW + timedelta(minutes=1)
    engine.evaluate_sync(
        [
            stale.model_copy(
                update={"status": "succeeded", "completed_at": recovered_at}
            )
        ],
        recovered_at,
    )
    assert event_types(repo) == ["sync_recovered", "remote_sync_unavailable"]


def test_successful_sync_without_active_condition_does_not_emit_event(tmp_path):
    engine, repo = make_engine(tmp_path)

    engine.evaluate_sync(
        [
            SyncObservation(
                source_kind="fae",
                status="succeeded",
                completed_at=NOW,
                observed_at=NOW,
                last_success_at=NOW,
            )
        ],
        NOW,
    )

    assert event_types(repo) == []


def test_sync_without_success_becomes_stale_after_persisted_interval(tmp_path):
    engine, repo = make_engine(tmp_path)
    running = SyncObservation(
        source_kind="fae",
        status="running",
        completed_at=None,
        observed_at=NOW,
        last_success_at=None,
    )

    engine.evaluate_sync([running], NOW)
    engine.evaluate_sync(
        [running.model_copy(update={"observed_at": NOW + timedelta(hours=36)})],
        NOW + timedelta(hours=36),
    )
    assert repo.list_active_attention("business") == ()
    state = repo.get_rule_state("sync:fae")
    assert state is not None
    assert state.value["no_success_since"] == NOW.isoformat()

    stale_at = NOW + timedelta(hours=36, seconds=1)
    engine.evaluate_sync(
        [running.model_copy(update={"observed_at": stale_at})], stale_at
    )

    active = repo.list_active_attention("business")
    assert len(active) == 1
    assert active[0].event_type == "remote_sync_unavailable"
    assert active[0].facts["stale"] is True


def test_system_runtime_event_is_not_business_attention(tmp_path):
    engine, repo = make_engine(tmp_path)
    observation = runtime("test-bot", "system", "offline", NOW)
    engine.evaluate_runtime([observation], NOW)
    engine.evaluate_runtime([observation], NOW + timedelta(seconds=10))

    assert repo.list_active_attention("business") == ()
    assert len(repo.list_active_attention("system")) == 1


def test_business_data_failure_opens_one_recoverable_attention(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_data_access(
        [DataAccessObservation(source_name="flywheel", available=False, observed_at=NOW)],
        NOW,
    )
    assert event_types(repo) == ["business_data_unavailable"]
    engine.evaluate_data_access(
        [
            DataAccessObservation(
                source_name="flywheel",
                available=True,
                observed_at=NOW + timedelta(minutes=1),
            )
        ],
        NOW + timedelta(minutes=1),
    )
    assert event_types(repo) == ["data_access_recovered", "business_data_unavailable"]


def test_data_access_is_fleet_level_and_only_flywheel_links_to_sessions(tmp_path):
    engine, repo = make_engine(tmp_path)

    engine.evaluate_data_access(
        [
            DataAccessObservation(
                source_name="flywheel", available=False, observed_at=NOW
            ),
            DataAccessObservation(
                source_name="local-cache", available=False, observed_at=NOW
            ),
        ],
        NOW,
    )

    events = repo.list_events(EventFilters(), 100, 0).items
    flywheel = next(event for event in events if event.source_kind == "flywheel")
    local_cache = next(event for event in events if event.source_kind == "local-cache")
    assert flywheel.agent_id is None
    assert flywheel.target_path == "/sessions"
    assert local_cache.agent_id is None
    assert local_cache.target_path is None


def test_initial_baseline_does_not_emit_old_milestones(tmp_path):
    engine, repo = make_engine(tmp_path)

    engine.evaluate_usage([usage("ai-fae-agent", 237, NOW)], NOW, initializing=True)

    assert event_types(repo) == []
    assert repo.get_rule_state("milestone:ai-fae-agent").value["reached"] == 100


def test_usage_is_bucketed_and_milestone_is_emitted_once(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_usage([usage("hr-bot", 99, NOW)], NOW, initializing=True)
    engine.evaluate_usage(
        [usage("hr-bot", 100, NOW + timedelta(minutes=1))],
        NOW,
        initializing=False,
    )
    engine.evaluate_usage(
        [usage("hr-bot", 100, NOW + timedelta(minutes=2))],
        NOW,
        initializing=False,
    )

    assert event_types(repo).count("conversation_milestone") == 1
    hourly = [
        event
        for event in repo.list_events(EventFilters(), 100, 0).items
        if event.event_type == "new_conversations"
    ]
    assert len(hourly) == 1
    assert hourly[0].facts["conversations"] == 1
    assert hourly[0].fingerprint.endswith("2026-07-22T11:00:00+08:00")


def test_usage_records_each_new_milestone_after_initialization(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_usage([usage("hr-bot", 99, NOW)], NOW, initializing=True)

    for total in (100, 250, 500, 1000, 2000):
        engine.evaluate_usage(
            [usage("hr-bot", total, NOW)], NOW, initializing=False
        )

    milestones = [
        event.facts["milestone"]
        for event in repo.list_events(EventFilters(), 100, 0).items
        if event.event_type == "conversation_milestone"
    ]
    assert sorted(milestones) == [100, 250, 500, 1000, 2000]


def test_usage_accumulates_separate_passes_into_one_hourly_bucket(tmp_path):
    engine, repo = make_engine(tmp_path)

    engine.evaluate_usage([usage("hr-bot", 99, NOW)], NOW, initializing=True)
    engine.evaluate_usage([usage("hr-bot", 100, NOW)], NOW, initializing=False)
    engine.evaluate_usage(
        [
            usage("hr-bot", 102, NOW + timedelta(minutes=5)).model_copy(
                update={"conversations": 2}
            )
        ],
        NOW + timedelta(minutes=5),
        initializing=False,
    )

    hourly = [
        event
        for event in repo.list_events(EventFilters(), 100, 0).items
        if event.event_type == "new_conversations"
    ]
    assert len(hourly) == 1
    assert hourly[0].facts["conversations"] == 3


def test_usage_retry_adds_only_the_unseen_cumulative_delta(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_usage([usage("hr-bot", 99, NOW)], NOW, initializing=True)
    engine.evaluate_usage([usage("hr-bot", 100, NOW)], NOW, initializing=False)

    replay = usage("hr-bot", 102, NOW + timedelta(minutes=5)).model_copy(
        update={"conversations": 3}
    )
    engine.evaluate_usage(
        [replay], NOW + timedelta(minutes=5), initializing=False
    )

    hourly = next(
        event
        for event in repo.list_events(EventFilters(), 100, 0).items
        if event.event_type == "new_conversations"
    )
    assert hourly.facts["conversations"] == 3


def test_usage_tail_observation_updates_bucket_as_the_hour_closes(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_usage([usage("hr-bot", 99, NOW)], NOW, initializing=True)
    engine.evaluate_usage([usage("hr-bot", 100, NOW)], NOW, initializing=False)

    tail = usage("hr-bot", 102, NOW + timedelta(minutes=59)).model_copy(
        update={"conversations": 2}
    )
    engine.evaluate_usage(
        [tail], NOW + timedelta(hours=1), initializing=False
    )

    hourly = next(
        event
        for event in repo.list_events(EventFilters(), 100, 0).items
        if event.event_type == "new_conversations"
    )
    assert hourly.status == "historical"
    assert hourly.facts["conversations"] == 3


def test_usage_multi_bucket_retry_allocates_only_global_unseen_total(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_usage([usage("hr-bot", 98, NOW)], NOW, initializing=True)
    engine.evaluate_usage([usage("hr-bot", 99, NOW)], NOW, initializing=False)

    next_hour = NOW + timedelta(hours=1)
    replayed_first = usage("hr-bot", 101, NOW).model_copy(
        update={"conversations": 1}
    )
    new_second = usage("hr-bot", 101, next_hour).model_copy(
        update={"conversations": 2}
    )
    engine.evaluate_usage(
        [replayed_first, new_second], next_hour, initializing=False
    )

    hourly = [
        event
        for event in repo.list_events(EventFilters(), 100, 0).items
        if event.event_type == "new_conversations"
    ]
    assert len(hourly) == 2
    assert sum(event.facts["conversations"] for event in hourly) == 3
    assert next(
        event for event in hourly if event.occurred_at == NOW
    ).facts["conversations"] == 1


def test_lifecycle_initialization_imports_actual_dates_and_replays_cleanly(tmp_path):
    engine, repo = make_engine(tmp_path)
    live_since = NOW - timedelta(days=30)
    last_updated = NOW - timedelta(days=3)
    observation = lifecycle(
        "hr-bot",
        live_since=live_since,
        last_updated_at=last_updated,
        observed_at=NOW,
    )

    engine.evaluate_lifecycle([observation], NOW, initializing=True)
    engine.evaluate_lifecycle([observation], NOW, initializing=False)

    events = repo.list_events(EventFilters(), 100, 0).items
    assert {event.event_type for event in events} == {
        "agent_launched",
        "deployment_updated",
    }
    assert {event.occurred_at for event in events} == {live_since, last_updated}


def test_lifecycle_emits_only_a_later_deployment_update(tmp_path):
    engine, repo = make_engine(tmp_path)
    initial_update = NOW - timedelta(days=3)
    observation = lifecycle(
        "hr-bot",
        live_since=NOW - timedelta(days=30),
        last_updated_at=initial_update,
        observed_at=NOW,
    )
    engine.evaluate_lifecycle([observation], NOW, initializing=True)

    changed_at = NOW + timedelta(minutes=10)
    engine.evaluate_lifecycle(
        [
            observation.model_copy(
                update={
                    "last_updated_at": changed_at,
                    "observed_at": changed_at,
                }
            )
        ],
        changed_at,
        initializing=False,
    )
    engine.evaluate_lifecycle(
        [observation.model_copy(update={"observed_at": changed_at})],
        changed_at,
        initializing=False,
    )

    updates = [
        event
        for event in repo.list_events(EventFilters(), 100, 0).items
        if event.event_type == "deployment_updated"
    ]
    assert len(updates) == 2
    assert updates[0].occurred_at == changed_at


def test_lifecycle_equivalent_timestamp_offsets_do_not_emit_an_update(tmp_path):
    engine, repo = make_engine(tmp_path)
    observation = lifecycle(
        "hr-bot",
        live_since=NOW - timedelta(days=30),
        last_updated_at=NOW,
        observed_at=NOW,
    )
    engine.evaluate_lifecycle([observation], NOW, initializing=True)

    equivalent = NOW.astimezone(timezone(timedelta(hours=8)))
    engine.evaluate_lifecycle(
        [
            observation.model_copy(
                update={"last_updated_at": equivalent, "observed_at": equivalent}
            )
        ],
        equivalent,
        initializing=False,
    )

    updates = [
        event
        for event in repo.list_events(EventFilters(), 100, 0).items
        if event.event_type == "deployment_updated"
    ]
    assert len(updates) == 1


def test_execution_events_group_by_agent_signal_and_hour(tmp_path):
    engine, repo = make_engine(tmp_path)
    observations = [
        execution("ai-fae-agent", "fallback", "fae:turn-1", NOW),
        execution(
            "ai-fae-agent",
            "fallback",
            "fae:turn-2",
            NOW + timedelta(minutes=2),
        ),
    ]

    engine.evaluate_execution(observations, NOW + timedelta(minutes=2))

    events = repo.list_active_attention("business")
    assert len(events) == 1
    assert events[0].facts["count"] == 2
    assert events[0].target_path == "/sessions/fae%3Asession-2"
    assert events[0].fingerprint.endswith("2026-07-22T11:00:00+08:00")


def test_execution_latest_session_is_deterministic_when_timestamps_match(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_execution(
        [execution("ai-fae-agent", "fallback", "fae:turn-2", NOW)], NOW
    )

    engine.evaluate_execution(
        [execution("ai-fae-agent", "fallback", "fae:turn-1", NOW)], NOW
    )

    event = repo.list_active_attention("business")[0]
    assert event.facts["count"] == 2
    assert event.target_path == "/sessions/fae%3Asession-2"


def test_execution_events_expire_after_their_local_hour_without_recovery(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_execution(
        [execution("ai-fae-agent", "tool_error", "fae:turn-1", NOW)], NOW
    )

    engine.evaluate_execution([], NOW + timedelta(hours=1))

    assert repo.list_active_attention("business") == ()
    assert event_types(repo) == ["tool_error"]


def test_execution_tail_observation_updates_bucket_as_the_hour_closes(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_execution(
        [execution("ai-fae-agent", "fallback", "fae:turn-1", NOW)], NOW
    )

    tail = execution(
        "ai-fae-agent", "fallback", "fae:turn-2", NOW + timedelta(minutes=59)
    )
    engine.evaluate_execution([tail], NOW + timedelta(hours=1))

    events = repo.list_events(EventFilters(), 100, 0).items
    assert len(events) == 1
    assert events[0].status == "historical"
    assert events[0].facts["count"] == 2
    assert events[0].target_path == "/sessions/fae%3Asession-2"


def test_execution_replay_after_finalization_retains_complete_bucket(tmp_path):
    engine, repo = make_engine(tmp_path)
    first = execution("ai-fae-agent", "fallback", "fae:turn-1", NOW)
    tail = execution(
        "ai-fae-agent", "fallback", "fae:turn-2", NOW + timedelta(minutes=59)
    )
    engine.evaluate_execution([first], NOW)
    engine.evaluate_execution([tail], NOW + timedelta(hours=1))

    engine.evaluate_execution([tail], NOW + timedelta(hours=1, minutes=1))

    event = repo.list_events(EventFilters(), 100, 0).items[0]
    assert event.status == "historical"
    assert event.facts["count"] == 2
    assert event.target_path == "/sessions/fae%3Asession-2"
