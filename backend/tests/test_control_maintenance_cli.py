from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from app.control_plane.maintenance_cli import (
    MaintenanceHealthError,
    MaintenanceRepository,
    build_parser,
)
from test_control_plane_migration import control_database


def test_purge_cli_has_fixed_command_without_cutoff_override() -> None:
    help_text = build_parser().format_help()
    purge_help = build_parser()._subparsers._group_actions[0].choices[
        "purge-expired"
    ].format_help()
    assert "purge-expired" in help_text
    assert "--cutoff" not in purge_help
    assert "--days" not in purge_help
    assert "--time-health" in purge_help
    assert "--wal-health" in purge_help


@pytest.mark.parametrize(
    ("time_health", "wal_health"),
    [("unknown", "healthy"), ("breached", "healthy"), ("healthy", "unknown"), ("healthy", "breached")],
)
def test_purge_refuses_unknown_or_breached_health(
    time_health: str, wal_health: str
) -> None:
    repository = MaintenanceRepository(
        "postgresql:///agent_platform_control"
    )
    with pytest.raises(MaintenanceHealthError, match="health is not confirmed"):
        repository.purge_expired(
            time_health=time_health,
            wal_health=wal_health,
        )


@pytest.mark.postgres
def test_maintenance_purges_only_already_expired_data_at_exact_365_days(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    actor = uuid4()
    request_ids = [uuid4(), uuid4()]
    audit_ids = [uuid4(), uuid4()]
    now = datetime.now(timezone.utc)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status) values (%s, 'Retention Actor', 'active')",
            (actor,),
        )
        for event_id, request_id, occurred_at in (
            (audit_ids[0], request_ids[0], now - timedelta(days=366)),
            (audit_ids[1], request_ids[1], now - timedelta(days=364)),
        ):
            connection.execute(
                "insert into platform_control.audit_events "
                "(audit_event_id, actor_internal_user_id, event_type, target_type, "
                "target_internal_id, request_id, result, reason_code, occurred_at) "
                "values (%s, %s, 'retention_test', 'internal_user', %s, %s, "
                "'completed', 'retention test', %s)",
                (event_id, actor, str(actor), request_id, occurred_at),
            )
        connection.execute(
            "insert into platform_control.login_attempts "
            "(login_attempt_id, attempt_kind, state_hash, expires_at) "
            "values (%s, 'qr', %s, now() - interval '1 second')",
            (uuid4(), b"expired"),
        )
        connection.execute(
            "insert into platform_control.auth_rate_buckets "
            "(bucket_key, bucket_kind, window_started_at, updated_at) "
            "values (%s, 'login', now() - interval '2 days', now() - interval '2 days')",
            (b"expired",),
        )

    result = MaintenanceRepository(
        environment["urls"]["platform_control_maintenance"]
    ).purge_expired(time_health="healthy", wal_health="healthy")

    assert result["audit_events"] == 1
    assert result["login_attempts"] == 1
    assert result["rate_buckets"] == 1
    with psycopg.connect(environment["admin"]) as connection:
        remaining = connection.execute(
            "select audit_event_id from platform_control.audit_events "
            "where audit_event_id = any(%s) order by occurred_at",
            (audit_ids,),
        ).fetchall()
        assert remaining == [(audit_ids[1],)]


@pytest.mark.postgres
def test_retention_function_rejects_cutoff_even_one_second_under_365_days(
    control_database,
) -> None:
    environment = control_database["environments"]["preview"]
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance_preview"]
    ) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.retain_audit_events("
                "clock_timestamp() - interval '365 days' + interval '1 second')"
            )
