from datetime import datetime, timedelta, timezone

from app.operations.models import EventFilters
from app.operations.repository import OperationsRepository
from app.operations.rules import (
    DataAccessObservation,
    OperationsRuleEngine,
    RuntimeObservation,
    SyncObservation,
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
