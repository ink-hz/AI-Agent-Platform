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
    policy_help = build_parser()._subparsers._group_actions[0].choices[
        "sync-identity-policy"
    ].format_help()
    assert "--keyring-file" in policy_help


@pytest.mark.parametrize(
    ("time_health", "wal_health"),
    [("unknown", "healthy"), ("breached", "healthy"), ("healthy", "unknown"), ("healthy", "breached")],
)
def test_purge_refuses_unknown_or_breached_health(
    time_health: str, wal_health: str
) -> None:
    repository = MaintenanceRepository(
        "postgresql://platform_control_maintenance@/agent_platform_control"
    )
    with pytest.raises(MaintenanceHealthError, match="health is not confirmed"):
        repository.purge_expired(
            time_health=time_health,
            wal_health=wal_health,
        )


@pytest.mark.parametrize("versions", [(), (0,), (2, 1), (1, 1), (1, 2, 3, 4)])
def test_identity_policy_rejects_invalid_transition_versions(versions) -> None:
    repository = MaintenanceRepository(
        "postgresql://platform_control_maintenance@/agent_platform_control"
    )
    with pytest.raises(ValueError, match="transition versions invalid"):
        repository.sync_identity_key_policy(versions)


@pytest.mark.postgres
def test_maintenance_syncs_identity_policy_to_exact_keyring_versions(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    repository = MaintenanceRepository(
        environment["urls"]["platform_control_maintenance"]
    )

    assert repository.sync_identity_key_policy((1, 2)) == (1, 2)

    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select lookup_transition_versions from "
            "platform_control.provider_identity_key_policies "
            "where provider='dingtalk'"
        ).fetchone()
    assert row == ([1, 2],)


@pytest.mark.postgres
def test_maintenance_purges_only_already_expired_data_at_exact_365_days(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    actor = uuid4()
    request_ids = [uuid4(), uuid4()]
    audit_ids = [uuid4(), uuid4()]
    access_ids = [uuid4(), uuid4()]
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
            "(bucket_key, bucket_key_version, bucket_kind, window_started_at, updated_at) "
            "values (%s, 1, 'login', now() - interval '2 days', now() - interval '2 days')",
            (b"expired",),
        )
        for event_id, occurred_at in (
            (access_ids[0], now - timedelta(days=91)),
            (access_ids[1], now - timedelta(days=89)),
        ):
            connection.execute(
                "insert into platform_control.user_access_events "
                "(access_event_id,internal_user_id,session_id,event_kind,login_kind,occurred_at) "
                "values (%s,%s,%s,'login_succeeded','qr',%s)",
                (event_id, actor, uuid4(), occurred_at),
            )

    result = MaintenanceRepository(
        environment["urls"]["platform_control_maintenance"]
    ).purge_expired(time_health="healthy", wal_health="healthy")

    assert result["audit_events"] == 1
    assert result["login_attempts"] == 1
    assert result["rate_buckets"] == 1
    assert result["access_events"] == 1
    with psycopg.connect(environment["admin"]) as connection:
        remaining = connection.execute(
            "select audit_event_id from platform_control.audit_events "
            "where audit_event_id = any(%s) order by occurred_at",
            (audit_ids,),
        ).fetchall()
        assert remaining == [(audit_ids[1],)]
        remaining_access = connection.execute(
            "select access_event_id from platform_control.user_access_events "
            "where access_event_id = any(%s) order by occurred_at",
            (access_ids,),
        ).fetchall()
        assert remaining_access == [(access_ids[1],)]


@pytest.mark.postgres
def test_real_maintenance_path_bounds_rate_cleanup_and_leaves_locked_rows(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                "insert into platform_control.auth_rate_buckets "
                "(bucket_key,bucket_key_version,bucket_kind,window_started_at,updated_at) "
                "values (%s,23,'authenticated_read',now()-interval '2 days',"
                "now()-interval '2 days')",
                [((index + 90_000).to_bytes(32, "big"),) for index in range(1_501)],
            )
    repository = MaintenanceRepository(
        environment["urls"]["platform_control_maintenance"]
    )
    with psycopg.connect(environment["admin"]) as locker:
        locker.execute(
            "select 1 from platform_control.auth_rate_buckets where bucket_key=%s "
            "for update",
            ((90_000).to_bytes(32, "big"),),
        )
        result = repository.purge_expired(
            time_health="healthy", wal_health="healthy"
        )

    assert result["rate_buckets"] == 1_000
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.auth_rate_buckets "
            "where bucket_key_version=23"
        ).fetchone() == (501,)
        source = connection.execute(
            "select pg_get_functiondef("
            "'platform_control.purge_expired_control_state()'::regprocedure)"
        ).fetchone()[0].lower()
    assert "delete from platform_control.auth_rate_buckets" not in source


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
