from datetime import datetime, timedelta, timezone

from app.operations.models import (
    EventFilters,
    NewOperationalEvent,
    RunHealth,
    UsageObservation,
    UsageOccurrence,
)
from app.operations.repository import OperationsRepository
from app.operations.rules import OperationsRuleEngine
from app.operations.service import OperationsService


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)
INTERVALS = {
    "runtime": 10,
    "sync": 60,
    "data_access": 60,
    "usage": 300,
    "execution": 300,
    "lifecycle": 600,
}


def event(
    *,
    agent_id: str = "hr-bot",
    agent_name: str = "HR Bot",
    visibility: str = "business",
    event_type: str = "deployment_updated",
    family: str = "lifecycle",
    severity: str = "info",
    occurred_at: datetime = NOW,
    conversations: int | None = None,
    fingerprint: str | None = None,
) -> NewOperationalEvent:
    facts = {} if conversations is None else {"conversations": conversations}
    return NewOperationalEvent(
        agent_id=agent_id,
        agent_visibility=visibility,
        event_type=event_type,
        event_family=family,
        severity=severity,
        title=(
            f"{agent_name} received new conversations"
            if event_type == "new_conversations"
            else f"{agent_name} changed"
        ),
        summary="A factual operational change was recorded.",
        source_kind="metabot",
        occurred_at=occurred_at,
        facts=facts,
        target_kind="agent",
        target_id=agent_id,
        target_path=f"/agents/{agent_id}",
        fingerprint=fingerprint or f"{event_type}:{agent_id}:{occurred_at.isoformat()}",
    )


def repository(tmp_path) -> OperationsRepository:
    tmp_path.mkdir(parents=True, exist_ok=True)
    result = OperationsRepository(str(tmp_path / "operations.db"))
    result.migrate()
    return result


def record_group_runs(
    repo: OperationsRepository,
    *,
    at: datetime = NOW,
    failed_group: str | None = None,
) -> None:
    for group in INTERVALS:
        status = "failed" if group == failed_group else "succeeded"
        repo.record_run(
            RunHealth(
                run_name=group,
                status=status,
                started_at=at,
                finished_at=at,
                error_summary="source unavailable" if status == "failed" else None,
            )
        )


def populated_service(
    tmp_path,
    *,
    business_changes: int,
    system_changes: int,
) -> OperationsService:
    repo = repository(tmp_path)
    for index in range(business_changes):
        repo.record_historical(
            event(
                occurred_at=NOW - timedelta(minutes=index),
                fingerprint=f"business:{index}",
            )
        )
    for index in range(system_changes):
        repo.record_historical(
            event(
                agent_id="test-bot",
                agent_name="Test Bot",
                visibility="system",
                occurred_at=NOW - timedelta(minutes=index, seconds=30),
                fingerprint=f"system:{index}",
            )
        )
    record_group_runs(repo)
    return OperationsService(repo, intervals=INTERVALS)


def service_with_run_health(tmp_path, *, failed_group: str) -> OperationsService:
    repo = repository(tmp_path)
    record_group_runs(repo, failed_group=failed_group)
    return OperationsService(repo, intervals=INTERVALS)


def test_brief_contains_business_attention_and_five_changes(tmp_path):
    service = populated_service(tmp_path, business_changes=7, system_changes=2)
    service._repository.upsert_active(
        event(
            event_type="runtime_offline",
            family="runtime",
            severity="critical",
            occurred_at=NOW - timedelta(minutes=10),
            fingerprint="runtime:hr-bot:unavailable",
        )
    )
    service._repository.upsert_active(
        event(
            agent_id="test-bot",
            agent_name="Test Bot",
            visibility="system",
            event_type="runtime_degraded",
            family="runtime",
            severity="attention",
            occurred_at=NOW - timedelta(minutes=5),
            fingerprint="runtime:test-bot:unavailable",
        )
    )

    brief = service.brief(NOW)

    assert all(item.agent_visibility == "business" for item in brief.attention)
    assert len(brief.changes) == 5
    assert brief.period_start == NOW - timedelta(hours=24)


def test_brief_filters_attention_and_execution_before_five_change_limit(tmp_path):
    repo = repository(tmp_path)
    approved = [
        ("usage-1", "usage", NOW - timedelta(minutes=10)),
        ("lifecycle-1", "lifecycle", NOW - timedelta(minutes=11)),
        ("recovery-1", "recovery", NOW - timedelta(minutes=12)),
        ("usage-2", "usage", NOW - timedelta(minutes=13)),
        ("lifecycle-2", "lifecycle", NOW - timedelta(minutes=14)),
        ("usage-older", "usage", NOW - timedelta(minutes=15)),
    ]
    for event_type, family, occurred_at in approved:
        repo.record_historical(
            event(
                event_type=event_type,
                family=family,
                occurred_at=occurred_at,
                fingerprint=event_type,
            )
        )
    repo.upsert_active(
        event(
            event_type="runtime_offline",
            family="runtime",
            severity="critical",
            occurred_at=NOW - timedelta(minutes=1),
            fingerprint="runtime:hr-bot:unavailable",
        )
    )
    repo.record_historical(
        event(
            event_type="fallback",
            family="execution",
            severity="attention",
            occurred_at=NOW - timedelta(minutes=2),
            fingerprint="execution:hr-bot:fallback",
        )
    )
    record_group_runs(repo)

    brief = OperationsService(repo, intervals=INTERVALS).brief(NOW)

    assert [item.event_type for item in brief.changes] == [
        "usage-1",
        "lifecycle-1",
        "recovery-1",
        "usage-2",
        "lifecycle-2",
    ]
    assert [item.event_type for item in brief.attention] == ["runtime_offline"]
    assert not {
        item.event_id for item in brief.attention
    }.intersection(item.event_id for item in brief.changes)


def test_attention_orders_critical_then_attention_and_newest_occurrence(tmp_path):
    repo = repository(tmp_path)
    repo.upsert_active(
        event(
            agent_id="older-critical",
            severity="critical",
            occurred_at=NOW - timedelta(minutes=5),
            fingerprint="critical:older",
        )
    )
    repo.upsert_active(
        event(
            agent_id="newer-attention",
            severity="attention",
            occurred_at=NOW - timedelta(minutes=1),
            fingerprint="attention:newer",
        )
    )
    repo.upsert_active(
        event(
            agent_id="newer-critical",
            severity="critical",
            occurred_at=NOW - timedelta(minutes=2),
            fingerprint="critical:newer",
        )
    )
    record_group_runs(repo)

    brief = OperationsService(repo, intervals=INTERVALS).brief(NOW)

    assert [item.agent_id for item in brief.attention] == [
        "newer-critical",
        "older-critical",
        "newer-attention",
    ]
    assert brief.can_claim_healthy is False


def test_partial_or_stale_brief_cannot_claim_healthy(tmp_path):
    service = service_with_run_health(tmp_path, failed_group="execution")
    brief = service.brief(NOW)
    assert brief.freshness.status == "partial"
    assert brief.freshness.failed_groups == ["execution"]
    assert brief.can_claim_healthy is False


def test_incomplete_required_source_runs_cannot_claim_healthy(tmp_path):
    repo = repository(tmp_path)
    record_group_runs(repo, at=NOW - timedelta(minutes=1))
    for group, error in (
        ("runtime", "RuntimeError: required runtime evidence unavailable"),
        ("sync", "RuntimeError: required sync source coverage incomplete"),
    ):
        repo.record_run(
            RunHealth(
                run_name=group,
                status="failed",
                started_at=NOW,
                finished_at=NOW,
                error_summary=error,
            )
        )

    brief = OperationsService(repo, intervals=INTERVALS).brief(NOW)

    assert brief.freshness.status == "partial"
    assert brief.freshness.failed_groups == ["runtime", "sync"]
    assert brief.can_claim_healthy is False


def test_newer_failure_keeps_complete_successful_baseline_and_is_partial(tmp_path):
    repo = repository(tmp_path)
    baseline_times = {}
    for index, group in enumerate(INTERVALS):
        at = NOW - timedelta(minutes=index + 1)
        baseline_times[group] = at
        repo.record_run(
            RunHealth(
                run_name=group,
                status="succeeded",
                started_at=at,
                finished_at=at,
            )
        )
    repo.record_run(
        RunHealth(
            run_name="lifecycle",
            status="failed",
            started_at=NOW,
            finished_at=NOW,
            error_summary="lifecycle unavailable",
        )
    )

    freshness = OperationsService(repo, intervals=INTERVALS).brief(NOW).freshness

    assert freshness.status == "partial"
    assert freshness.failed_groups == ["lifecycle"]
    assert freshness.evaluated_at == min(baseline_times.values())


def test_all_groups_success_then_failure_is_partial_not_unavailable(tmp_path):
    repo = repository(tmp_path)
    for group in INTERVALS:
        repo.record_run(
            RunHealth(
                run_name=group,
                status="succeeded",
                started_at=NOW - timedelta(minutes=1),
                finished_at=NOW - timedelta(minutes=1),
            )
        )
        repo.record_run(
            RunHealth(
                run_name=group,
                status="failed",
                started_at=NOW,
                finished_at=NOW,
                error_summary=f"{group} unavailable",
            )
        )

    freshness = OperationsService(repo, intervals=INTERVALS).brief(NOW).freshness

    assert freshness.status == "partial"
    assert freshness.failed_groups == list(INTERVALS)
    assert freshness.evaluated_at == NOW - timedelta(minutes=1)


def test_freshness_boundary_is_current_then_stale_and_no_baseline_is_unavailable(
    tmp_path,
):
    current_repo = repository(tmp_path / "current")
    for group, interval in INTERVALS.items():
        at = NOW - timedelta(seconds=interval * 2)
        current_repo.record_run(
            RunHealth(
                run_name=group,
                status="succeeded",
                started_at=at,
                finished_at=at,
            )
        )
    current = OperationsService(current_repo, intervals=INTERVALS).brief(NOW)

    stale_repo = repository(tmp_path / "stale")
    for group, interval in INTERVALS.items():
        at = NOW - timedelta(seconds=interval * 2)
        if group == "runtime":
            at -= timedelta(microseconds=1)
        stale_repo.record_run(
            RunHealth(
                run_name=group,
                status="succeeded",
                started_at=at,
                finished_at=at,
            )
        )
    stale = OperationsService(stale_repo, intervals=INTERVALS).brief(NOW)

    empty_repo = repository(tmp_path / "empty")
    unavailable = OperationsService(empty_repo, intervals=INTERVALS).brief(NOW)

    assert current.freshness.status == "current"
    assert current.can_claim_healthy is True
    assert stale.freshness.status == "stale"
    assert stale.can_claim_healthy is False
    assert unavailable.freshness.status == "unavailable"
    assert unavailable.freshness.evaluated_at is None
    assert unavailable.can_claim_healthy is False


def test_usage_uses_exact_occurrences_across_non_hour_aligned_cutoff_without_replay(
    tmp_path,
):
    repo = repository(tmp_path)
    engine = OperationsRuleEngine(repo)
    period_end = NOW + timedelta(minutes=30)
    period_start = period_end - timedelta(hours=24)
    bucket_start = period_start.replace(minute=0)
    old_bucket = UsageObservation(
        agent_id="hr-bot",
        agent_name="HR Bot",
        agent_visibility="business",
        source_kind="metabot",
        bucket_start=bucket_start,
        conversations=2,
        cumulative_conversations=2,
        occurrences=(
            UsageOccurrence(
                turn_key="metabot:excluded",
                agent_id="hr-bot",
                source_kind="metabot",
                occurred_at=period_start - timedelta(microseconds=1),
            ),
            UsageOccurrence(
                turn_key="metabot:included",
                agent_id="hr-bot",
                source_kind="metabot",
                occurred_at=period_start + timedelta(minutes=5),
            ),
        ),
    )
    current_bucket = UsageObservation(
        agent_id="hr-bot",
        agent_name="HR Bot",
        agent_visibility="business",
        source_kind="metabot",
        bucket_start=period_end.replace(minute=0),
        conversations=1,
        cumulative_conversations=3,
        occurrences=(
            UsageOccurrence(
                turn_key="metabot:current",
                agent_id="hr-bot",
                source_kind="metabot",
                occurred_at=period_end - timedelta(minutes=5),
            ),
        ),
    )
    system_bucket = UsageObservation(
        agent_id="test-bot",
        agent_name="Test Bot",
        agent_visibility="system",
        source_kind="metabot",
        bucket_start=period_end.replace(minute=0),
        conversations=1,
        cumulative_conversations=1,
        occurrences=(
            UsageOccurrence(
                turn_key="metabot:system",
                agent_id="test-bot",
                source_kind="metabot",
                occurred_at=period_end - timedelta(minutes=1),
            ),
        ),
    )
    observations = [old_bucket, current_bucket, system_bucket]
    engine.evaluate_usage(observations, period_end, initializing=True)
    engine.evaluate_usage(observations, period_end, initializing=False)
    record_group_runs(repo, at=period_end)

    usage = OperationsService(repo, intervals=INTERVALS).brief(period_end).usage

    assert repo.usage_occurrence_count() == 4
    assert usage.conversations == 2
    assert usage.active_agents == 1
    assert [
        (item.agent_id, item.agent_name, item.conversations)
        for item in usage.leaders
    ] == [
        ("hr-bot", "HR Bot", 2),
    ]


def test_default_events_are_business_but_explicit_system_agent_is_allowed(tmp_path):
    service = populated_service(tmp_path, business_changes=1, system_changes=1)
    default_page = service.list_events(EventFilters(), 50, 0)
    system_page = service.list_events(EventFilters(agent_id="test-bot"), 50, 0)
    assert [item.agent_visibility for item in default_page.items] == ["business"]
    assert [item.agent_id for item in system_page.items] == ["test-bot"]


def test_default_business_filter_is_applied_before_pagination_and_total(tmp_path):
    service = populated_service(tmp_path, business_changes=3, system_changes=3)

    page = service.list_events(EventFilters(), 2, 1)

    assert page.total == 3
    assert page.limit == 2
    assert page.offset == 1
    assert len(page.items) == 2
    assert all(item.agent_visibility == "business" for item in page.items)
