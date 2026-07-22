import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.operations.models import EventFilters, NewOperationalEvent, RuleState, RunHealth
from app.operations.repository import MIGRATION_VERSION_1, OperationsRepository


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)


def migrated_repository(tmp_path) -> OperationsRepository:
    repo = OperationsRepository(str(tmp_path / "operations.db"))
    repo.migrate()
    return repo


def runtime_event(**updates) -> NewOperationalEvent:
    values = {
        "agent_id": "ai-fae-agent",
        "agent_visibility": "business",
        "event_type": "runtime_offline",
        "event_family": "runtime",
        "severity": "critical",
        "title": "AI FAE Agent is offline",
        "summary": "Two consecutive runtime observations reported offline.",
        "source_kind": "fae",
        "occurred_at": NOW,
        "facts": {"state": "offline", "observations": 2},
        "target_kind": "agent",
        "target_id": "ai-fae-agent",
        "target_path": "/agents/ai-fae-agent",
        "fingerprint": "runtime:ai-fae-agent:unavailable",
    }
    values.update(updates)
    return NewOperationalEvent(**values)


def test_migrate_creates_versioned_operations_schema(tmp_path):
    repo = OperationsRepository(str(tmp_path / "operations.db"))
    repo.migrate()

    assert repo.schema_version() == 2
    with sqlite3.connect(tmp_path / "operations.db") as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "pragma table_info(operational_usage_occurrences)"
            ).fetchall()
        }
    assert columns == {
        "occurrence_key",
        "agent_id",
        "bucket_start",
        "occurred_at",
        "processed_at",
    }


def test_schema_version_returns_zero_before_migration(tmp_path):
    repo = OperationsRepository(str(tmp_path / "operations.db"))

    assert repo.schema_version() == 0


def test_migrate_upgrades_v1_without_losing_existing_state(tmp_path):
    database_path = tmp_path / "operations.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(MIGRATION_VERSION_1)
        connection.execute(
            "insert into operations_schema_version(version, applied_at) values (1, ?)",
            (NOW.isoformat(),),
        )
    repo = OperationsRepository(str(database_path))
    event = repo.upsert_active(runtime_event())
    state = repo.put_rule_state(
        RuleState(rule_key="runtime:ai-fae-agent", value={"count": 2}, updated_at=NOW)
    )
    run = repo.record_run(
        RunHealth(run_name="runtime", status="succeeded", started_at=NOW)
    )

    repo.migrate()

    assert repo.schema_version() == 2
    assert repo.list_events(EventFilters(), 20, 0).items[0].event_id == event.event_id
    assert repo.get_rule_state(state.rule_key) == state
    assert repo.latest_run(run.run_name) == run
    assert repo.usage_occurrence_count() == 0


def test_schema_version_propagates_non_missing_table_operational_error(tmp_path):
    repo = OperationsRepository(str(tmp_path))

    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        repo.schema_version()


def test_upsert_active_is_idempotent_and_resolve_is_transactional(tmp_path):
    repo = migrated_repository(tmp_path)
    event = runtime_event()

    first = repo.upsert_active(event)
    second = repo.upsert_active(
        event.model_copy(update={"facts": {"state": "offline", "observations": 3}})
    )
    recovery = repo.resolve_active(
        fingerprint=event.fingerprint,
        resolved_at=NOW + timedelta(minutes=1),
        recovery_title="AI FAE Agent recovered",
        recovery_summary="Runtime returned to online.",
        recovery_facts={"state": "online"},
    )

    assert first.event_id == second.event_id
    assert second.facts == {"state": "offline", "observations": 3}
    assert repo.list_active_attention("business") == ()
    assert recovery is not None
    assert [item.event_type for item in repo.list_events(EventFilters(), 20, 0).items] == [
        "runtime_recovered",
        "runtime_offline",
    ]


def test_upsert_active_refreshes_classification_and_presentation(tmp_path):
    repo = migrated_repository(tmp_path)
    first = repo.upsert_active(
        runtime_event(
            event_type="runtime_degraded",
            severity="attention",
            title="AI FAE Agent is degraded",
            summary="Runtime is degraded.",
            facts={"state": "degraded"},
            target_kind=None,
            target_id=None,
            target_path=None,
        )
    )
    refreshed_at = NOW + timedelta(minutes=1)

    refreshed = repo.upsert_active(
        runtime_event(
            agent_id="replacement-agent",
            agent_visibility="system",
            event_type="runtime_offline",
            event_family="data",
            severity="critical",
            title="Replacement Agent is offline",
            summary="Runtime is offline.",
            source_kind="replacement-source",
            occurred_at=refreshed_at,
            facts={"state": "offline"},
            target_kind="agent",
            target_id="replacement-agent",
            target_path="/agents/replacement-agent",
        )
    )

    assert refreshed.event_id == first.event_id
    assert refreshed.occurred_at == first.occurred_at
    assert refreshed.first_observed_at == first.first_observed_at
    assert refreshed.last_observed_at == refreshed_at
    assert refreshed.agent_id == "replacement-agent"
    assert refreshed.agent_visibility == "system"
    assert refreshed.event_type == "runtime_offline"
    assert refreshed.event_family == "data"
    assert refreshed.severity == "critical"
    assert refreshed.title == "Replacement Agent is offline"
    assert refreshed.summary == "Runtime is offline."
    assert refreshed.source_kind == "replacement-source"
    assert refreshed.facts == {"state": "offline"}
    assert refreshed.target_kind == "agent"
    assert refreshed.target_id == "replacement-agent"
    assert refreshed.target_path == "/agents/replacement-agent"


def test_resolve_active_rolls_back_when_recovery_insert_fails(tmp_path):
    database_path = tmp_path / "operations.db"
    repo = OperationsRepository(str(database_path))
    repo.migrate()
    event = runtime_event()
    active = repo.upsert_active(event)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            create trigger reject_recovery before insert on operational_events
            when new.event_family='recovery'
            begin
              select raise(abort, 'forced recovery insert failure');
            end
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced recovery insert failure"):
        repo.resolve_active(
            fingerprint=event.fingerprint,
            resolved_at=NOW + timedelta(minutes=1),
            recovery_title="AI FAE Agent recovered",
            recovery_summary="Runtime returned to online.",
            recovery_facts={"state": "online"},
        )

    assert [item.event_id for item in repo.list_active_attention("business")] == [
        active.event_id
    ]
    assert repo.list_events(EventFilters(), 20, 0).total == 1


@pytest.mark.parametrize(
    ("event_type", "fingerprint", "recovery_type"),
    (
        ("remote_sync_unavailable", "sync:fae:unavailable", "sync_recovered"),
        (
            "business_data_unavailable",
            "data:flywheel:unavailable",
            "data_access_recovered",
        ),
    ),
)
def test_resolve_active_uses_source_specific_recovery_type(
    tmp_path, event_type, fingerprint, recovery_type
):
    repo = migrated_repository(tmp_path)
    repo.upsert_active(
        runtime_event(
            event_type=event_type,
            event_family="data",
            severity="attention",
            fingerprint=fingerprint,
        )
    )

    recovery = repo.resolve_active(
        fingerprint=fingerprint,
        resolved_at=NOW + timedelta(minutes=1),
        recovery_title="Source recovered",
        recovery_summary="Source is available.",
        recovery_facts={"available": True},
    )

    assert recovery is not None
    assert recovery.event_type == recovery_type


def test_resolve_active_returns_none_when_fingerprint_is_not_active(tmp_path):
    repo = migrated_repository(tmp_path)

    recovery = repo.resolve_active(
        fingerprint="runtime:missing:unavailable",
        resolved_at=NOW,
        recovery_title="Missing recovered",
        recovery_summary="No active event exists.",
        recovery_facts={},
    )

    assert recovery is None


def test_record_historical_reuses_event_during_replay(tmp_path):
    repo = migrated_repository(tmp_path)
    event = runtime_event(event_type="deployment_updated", event_family="lifecycle")

    first = repo.record_historical(event)
    second = repo.record_historical(event)

    assert first.event_id == second.event_id
    assert repo.list_events(EventFilters(), 20, 0).total == 1


def test_record_occurrences_updates_and_finalizes_one_hourly_event(tmp_path):
    repo = migrated_repository(tmp_path)
    state = RuleState(rule_key="usage:ai-fae-agent", value={"count": 1}, updated_at=NOW)
    event = runtime_event(
        event_type="new_conversations",
        event_family="usage",
        severity="info",
        facts={"count": 1},
        fingerprint="usage:ai-fae-agent:2026-07-22T11:00:00+08:00",
    )

    first = repo.record_occurrences((event,), status="active", states=(state,))[0]
    final_state = state.model_copy(
        update={"value": {"count": 2}, "updated_at": NOW + timedelta(hours=1)}
    )
    finalized = repo.record_occurrences(
        (event.model_copy(update={"facts": {"count": 2}}),),
        status="historical",
        states=(final_state,),
    )[0]

    assert finalized.event_id == first.event_id
    assert finalized.status == "historical"
    assert finalized.facts == {"count": 2}
    assert repo.list_events(EventFilters(), 20, 0).total == 1
    assert repo.get_rule_state(state.rule_key) == final_state


def test_record_occurrences_rolls_back_events_when_rule_state_fails(tmp_path):
    database_path = tmp_path / "operations.db"
    repo = OperationsRepository(str(database_path))
    repo.migrate()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            create trigger reject_occurrence_state before insert on operational_rule_state
            begin
              select raise(abort, 'forced occurrence state failure');
            end
            """
        )
    state = RuleState(rule_key="usage:ai-fae-agent", value={"count": 1}, updated_at=NOW)

    with pytest.raises(sqlite3.IntegrityError, match="forced occurrence state failure"):
        repo.record_occurrences(
            (runtime_event(event_family="usage"),),
            status="active",
            states=(state,),
        )

    assert repo.list_events(EventFilters(), 20, 0).total == 0
    assert repo.get_rule_state(state.rule_key) is None


def test_rule_state_and_run_health_round_trip_json_and_datetimes(tmp_path):
    repo = migrated_repository(tmp_path)
    state = RuleState(rule_key="runtime:ai-fae-agent", value={"count": 2}, updated_at=NOW)
    run = RunHealth(
        run_name="runtime",
        status="succeeded",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
        cursor={"agent": "ai-fae-agent"},
    )

    repo.put_rule_state(state)
    repo.record_run(run)

    assert repo.get_rule_state(state.rule_key) == state
    assert repo.latest_run(run.run_name) == run


def test_latest_successful_run_survives_a_newer_failure(tmp_path):
    repo = migrated_repository(tmp_path)
    succeeded = RunHealth(
        run_name="runtime",
        status="succeeded",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
    )
    failed = RunHealth(
        run_name="runtime",
        status="failed",
        started_at=NOW + timedelta(seconds=10),
        finished_at=NOW + timedelta(seconds=11),
        error_summary="runtime unavailable",
    )

    repo.record_run(succeeded)
    repo.record_run(failed)

    assert repo.latest_run("runtime") == failed
    assert repo.latest_successful_run("runtime") == succeeded


def test_event_filters_attention_visibility_and_expiration(tmp_path):
    repo = migrated_repository(tmp_path)
    repo.upsert_active(runtime_event())
    repo.upsert_active(
        runtime_event(
            agent_id="test-bot",
            agent_visibility="system",
            severity="attention",
            occurred_at=NOW + timedelta(minutes=1),
            fingerprint="runtime:test-bot:unavailable",
        )
    )
    repo.record_historical(
        runtime_event(
            event_type="deployment_updated",
            event_family="lifecycle",
            severity="info",
            occurred_at=NOW + timedelta(minutes=2),
            fingerprint="lifecycle:ai-fae-agent:deployment:2026-07-22",
        )
    )

    business = repo.list_active_attention("business")
    filtered = repo.list_events(
        EventFilters(agent_id="ai-fae-agent", severity="info", date_from=NOW), 10, 0
    )
    expired = repo.expire_active_occurrences("runtime", NOW + timedelta(minutes=2))

    assert [item.agent_id for item in business] == ["ai-fae-agent"]
    assert [item.event_type for item in filtered.items] == ["deployment_updated"]
    assert expired == 2
    assert repo.list_active_attention("business") == ()
    assert repo.list_active_attention("system") == ()
