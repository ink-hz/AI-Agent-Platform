from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import psycopg
import pytest

from test_control_plane_migration import control_database


def _requested_event(
    connection,
    *,
    event_type: str,
    actor: UUID,
    target: str,
    operation_id: UUID,
    reason_code: str,
    details: dict[str, object],
    occurred_at: datetime | None = None,
) -> UUID:
    event_id = uuid4()
    connection.execute(
        "insert into platform_control.audit_events ("
        "audit_event_id, actor_internal_user_id, event_type, target_type, "
        "target_internal_id, request_id, result, reason_code, "
        "sanitized_before_after, occurred_at) values "
        "(%s, %s, %s, %s, %s, %s, 'requested', %s, %s::jsonb, "
        "coalesce(%s, now()))",
        (
            event_id,
            actor,
            event_type,
            "agent_observation_scope"
            if event_type.startswith("observation_scope_")
            else "internal_user",
            target,
            operation_id,
            reason_code,
            json.dumps({"operation_id": str(operation_id), **details}),
            occurred_at,
        ),
    )
    return event_id


def _active_owner(connection, label: str) -> UUID:
    row = connection.execute(
        "select internal_user_id from platform_control.internal_users "
        "where role = 'platform_owner' and status = 'active'"
    ).fetchone()
    if row is not None:
        return row[0]
    owner = uuid4()
    connection.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id, display_name, status, role) "
        "values (%s, %s, 'active', 'platform_owner')",
        (owner, label),
    )
    return owner


@pytest.mark.postgres
def test_role_and_scope_operations_are_causal_idempotent_and_stale_replay_safe(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    target = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        owner = _active_owner(connection, "Ledger Owner")
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status, role) values "
            "(%s, 'Ledger Target', 'active', 'member')",
            (target,),
        )
        assign_operation = uuid4()
        assign_audit = _requested_event(
            connection,
            event_type="viewer_role_assignment_requested",
            actor=owner,
            target=str(target),
            operation_id=assign_operation,
            reason_code="access_approved",
            details={
                "previous_role": "member",
                "new_role": "management_viewer",
                "expected_row_version": 0,
                "result": "requested",
            },
        )

    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as connection:
        assigned = connection.execute(
            "select platform_control.assign_management_viewer(%s,%s,%s,%s,%s)",
            (assign_operation, owner, target, 0, assign_audit),
        ).fetchone()[0]
    assert assigned == {
        "operation_id": str(assign_operation),
        "previous_role": "member",
        "new_role": "management_viewer",
        "row_version": 1,
        "session_revocation_count": 0,
        "previous_scopes": [],
        "new_scopes": [],
    }

    with psycopg.connect(environment["admin"]) as connection:
        grant_operation = uuid4()
        grant_audit = _requested_event(
            connection,
            event_type="observation_scope_assignment_requested",
            actor=owner,
            target=f"{target}:fae",
            operation_id=grant_operation,
            reason_code="scope_approved",
            details={
                "agent_id": "fae",
                "expected_user_row_version": 1,
                "expected_scope_row_version": 0,
                "result": "requested",
            },
        )
    with psycopg.connect(app_url) as connection:
        granted = connection.execute(
            "select platform_control.grant_observation_scope("
            "%s,%s,%s,%s,%s,%s,%s)",
            (grant_operation, owner, target, "fae", 1, 0, grant_audit),
        ).fetchone()[0]
    assert granted["before_scope"] is False
    assert granted["after_scope"] is True
    assert granted["row_version"] == 1

    with psycopg.connect(environment["admin"]) as connection:
        revoke_scope_operation = uuid4()
        revoke_scope_audit = _requested_event(
            connection,
            event_type="observation_scope_revocation_requested",
            actor=owner,
            target=f"{target}:fae",
            operation_id=revoke_scope_operation,
            reason_code="scope_revoked",
            details={
                "agent_id": "fae",
                "expected_user_row_version": 1,
                "expected_scope_row_version": 1,
                "result": "requested",
            },
        )
    with psycopg.connect(app_url) as connection:
        connection.execute(
            "select platform_control.revoke_observation_scope("
            "%s,%s,%s,%s,%s,%s,%s)",
            (
                revoke_scope_operation,
                owner,
                target,
                "fae",
                1,
                1,
                revoke_scope_audit,
            ),
        )

    with psycopg.connect(environment["admin"]) as connection:
        revoke_role_operation = uuid4()
        revoke_role_audit = _requested_event(
            connection,
            event_type="viewer_role_revocation_requested",
            actor=owner,
            target=str(target),
            operation_id=revoke_role_operation,
            reason_code="access_revoked",
            details={
                "previous_role": "management_viewer",
                "new_role": "member",
                "expected_row_version": 1,
                "result": "requested",
            },
        )
    with psycopg.connect(app_url) as connection:
        revoked = connection.execute(
            "select platform_control.revoke_management_viewer(%s,%s,%s,%s,%s)",
            (revoke_role_operation, owner, target, 1, revoke_role_audit),
        ).fetchone()[0]
        old_role_result = connection.execute(
            "select platform_control.assign_management_viewer(%s,%s,%s,%s,%s)",
            (assign_operation, owner, target, 0, assign_audit),
        ).fetchone()[0]
        old_scope_result = connection.execute(
            "select platform_control.grant_observation_scope("
            "%s,%s,%s,%s,%s,%s,%s)",
            (grant_operation, owner, target, "fae", 1, 0, grant_audit),
        ).fetchone()[0]
    assert revoked["new_role"] == "member"
    assert old_role_result == assigned
    assert old_scope_result == granted
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select role::text, row_version from platform_control.internal_users "
            "where internal_user_id = %s",
            (target,),
        ).fetchone() == ("member", 2)
        assert connection.execute(
            "select count(*) from platform_control.observation_grants where "
            "viewer_internal_user_id = %s and revoked_at is null",
            (target,),
        ).fetchone() == (0,)
        assert connection.execute(
            "select count(*) from platform_control.management_mutations",
        ).fetchone() == (4,)


@pytest.mark.postgres
def test_delayed_scope_revoke_cannot_cross_revoke_regrant_incarnations(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    target = uuid4()
    agent_id = "fae"
    with psycopg.connect(environment["admin"]) as connection:
        owner = _active_owner(connection, "Incarnation Owner")
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status, role, row_version) values "
            "(%s, 'Incarnation Target', 'active', 'management_viewer', 1)",
            (target,),
        )
        grant_a = uuid4()
        grant_a_audit = _requested_event(
            connection,
            event_type="observation_scope_assignment_requested",
            actor=owner,
            target=f"{target}:{agent_id}",
            operation_id=grant_a,
            reason_code="scope_approved",
            details={"agent_id": agent_id, "expected_user_row_version": 1,
                     "expected_scope_row_version": 0, "result": "requested"},
        )
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as connection:
        connection.execute(
            "select platform_control.grant_observation_scope(%s,%s,%s,%s,%s,%s,%s)",
            (grant_a, owner, target, agent_id, 1, 0, grant_a_audit),
        )
    with psycopg.connect(environment["admin"]) as connection:
        delayed = uuid4()
        delayed_audit = _requested_event(
            connection,
            event_type="observation_scope_revocation_requested",
            actor=owner,
            target=f"{target}:{agent_id}",
            operation_id=delayed,
            reason_code="scope_revoked",
            details={"agent_id": agent_id, "expected_user_row_version": 1,
                     "expected_scope_row_version": 1, "result": "requested"},
        )
        revoke_a = uuid4()
        revoke_a_audit = _requested_event(
            connection,
            event_type="observation_scope_revocation_requested",
            actor=owner,
            target=f"{target}:{agent_id}",
            operation_id=revoke_a,
            reason_code="scope_revoked",
            details={"agent_id": agent_id, "expected_user_row_version": 1,
                     "expected_scope_row_version": 1, "result": "requested"},
        )
    with psycopg.connect(app_url) as connection:
        revoked = connection.execute(
            "select platform_control.revoke_observation_scope(%s,%s,%s,%s,%s,%s,%s)",
            (revoke_a, owner, target, agent_id, 1, 1, revoke_a_audit),
        ).fetchone()[0]
    assert revoked["row_version"] == 2
    with psycopg.connect(environment["admin"]) as connection:
        grant_b = uuid4()
        grant_b_audit = _requested_event(
            connection,
            event_type="observation_scope_assignment_requested",
            actor=owner,
            target=f"{target}:{agent_id}",
            operation_id=grant_b,
            reason_code="scope_approved",
            details={"agent_id": agent_id, "expected_user_row_version": 1,
                     "expected_scope_row_version": 2, "result": "requested"},
        )
    with psycopg.connect(app_url) as connection:
        granted = connection.execute(
            "select platform_control.grant_observation_scope(%s,%s,%s,%s,%s,%s,%s)",
            (grant_b, owner, target, agent_id, 1, 2, grant_b_audit),
        ).fetchone()[0]
    with psycopg.connect(app_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.revoke_observation_scope(%s,%s,%s,%s,%s,%s,%s)",
                (delayed, owner, target, agent_id, 1, 1, delayed_audit),
            )
    assert granted["row_version"] == 3
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select row_version from platform_control.observation_grants "
            "where viewer_internal_user_id=%s and agent_id=%s and revoked_at is null",
            (target, agent_id),
        ).fetchone() == (3,)


@pytest.mark.postgres
def test_inactive_owner_requires_replace_and_old_replace_replay_cannot_restore(
    control_database,
) -> None:
    environment = control_database["environments"]["preview"]
    old_owner, second_owner, third_owner = uuid4(), uuid4(), uuid4()
    generation_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id, status, completed_at, content_sha256) values "
            "(%s, 'complete', now(), %s)",
            (generation_id, "a" * 64),
        )
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status, role) values "
            "(%s, 'Departed Owner', 'inactive', 'platform_owner'), "
            "(%s, 'Replacement Two', 'active', 'member'), "
            "(%s, 'Replacement Three', 'active', 'member')",
            (old_owner, second_owner, third_owner),
        )
        for index, target in enumerate((second_owner, third_owner), start=1):
            connection.execute(
                "insert into platform_control.directory_members "
                "(generation_id, member_key, internal_user_id, subject_kind, "
                "lookup_hmac, lookup_key_version, encrypted_provider_id, "
                "encryption_key_version, display_name, status) values "
                "(%s, %s, %s, 'employee', %s, 1, %s, 1, 'Replacement', 'active')",
                (
                    generation_id,
                    uuid4(),
                    target,
                    bytes([index]) * 32,
                    b"ciphertext" + bytes([index]),
                ),
            )
        bind_operation = uuid4()
        bind_audit = _requested_event(
            connection,
            event_type="owner_binding_requested",
            actor=second_owner,
            target=str(second_owner),
            operation_id=bind_operation,
            reason_code="initial_owner_binding",
            details={
                "directory_generation_id": str(generation_id),
                "directory_generation_digest": "a" * 64,
                "protected_target_lookup_hash": "01" * 32,
                "protected_target_lookup_version": 1,
                "expected_owner_row_version": 0,
                "expected_target_row_version": 0,
                "result": "requested",
            },
        )
        replace_one = uuid4()
        replace_one_audit = _requested_event(
            connection,
            event_type="owner_replacement_requested",
            actor=second_owner,
            target=str(second_owner),
            operation_id=replace_one,
            reason_code="owner_departure",
            details={
                "directory_generation_id": str(generation_id),
                "directory_generation_digest": "a" * 64,
                "protected_target_lookup_hash": "01" * 32,
                "protected_target_lookup_version": 1,
                "previous_owner_internal_user_id": str(old_owner),
                "expected_owner_row_version": 0,
                "expected_target_row_version": 0,
                "result": "requested",
            },
        )

    migrator = environment["urls"]["platform_control_migrator_preview"]
    with psycopg.connect(migrator) as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match="owner already bound"):
            connection.execute(
                "select platform_control.change_platform_owner_v2("
                "%s,'bind',%s,%s,null,0,0,%s)",
                (bind_operation, second_owner, generation_id, bind_audit),
            )
        connection.rollback()
        first_result = connection.execute(
            "select platform_control.change_platform_owner_v2("
            "%s,'replace',%s,%s,%s,0,0,%s)",
            (
                replace_one,
                second_owner,
                generation_id,
                old_owner,
                replace_one_audit,
            ),
        ).fetchone()[0]

    with psycopg.connect(environment["admin"]) as connection:
        replace_two = uuid4()
        replace_two_audit = _requested_event(
            connection,
            event_type="owner_replacement_requested",
            actor=third_owner,
            target=str(third_owner),
            operation_id=replace_two,
            reason_code="owner_departure",
            details={
                "directory_generation_id": str(generation_id),
                "directory_generation_digest": "a" * 64,
                "protected_target_lookup_hash": "02" * 32,
                "protected_target_lookup_version": 1,
                "previous_owner_internal_user_id": str(second_owner),
                "expected_owner_row_version": 1,
                "expected_target_row_version": 0,
                "result": "requested",
            },
        )
    with psycopg.connect(migrator) as connection:
        connection.execute(
            "select platform_control.change_platform_owner_v2("
            "%s,'replace',%s,%s,%s,1,0,%s)",
            (
                replace_two,
                third_owner,
                generation_id,
                second_owner,
                replace_two_audit,
            ),
        )
        replay = connection.execute(
            "select platform_control.change_platform_owner_v2("
            "%s,'replace',%s,%s,%s,0,0,%s)",
            (
                replace_one,
                second_owner,
                generation_id,
                old_owner,
                replace_one_audit,
            ),
        ).fetchone()[0]
    assert replay == first_result
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select internal_user_id from platform_control.internal_users "
            "where role = 'platform_owner'",
        ).fetchall() == [(third_owner,)]


@pytest.mark.postgres
def test_old_referenced_audit_purges_without_changing_authorization_state(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    viewer = uuid4()
    operation_id = uuid4()
    old_at = datetime.now(timezone.utc) - timedelta(days=366)
    with psycopg.connect(environment["admin"]) as connection:
        owner = _active_owner(connection, "Retention Owner")
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status, role) values "
            "(%s, 'Retention Viewer', 'active', 'member')",
            (viewer,),
        )
        event_id = _requested_event(
            connection,
            event_type="viewer_role_assignment_requested",
            actor=owner,
            target=str(viewer),
            operation_id=operation_id,
            reason_code="access_approved",
            details={
                "previous_role": "member",
                "new_role": "management_viewer",
                "expected_row_version": 0,
                "result": "requested",
            },
            occurred_at=old_at,
        )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as connection:
        connection.execute(
            "select platform_control.assign_management_viewer(%s,%s,%s,0,%s)",
            (operation_id, owner, viewer, event_id),
        )
    with psycopg.connect(environment["urls"]["platform_control_maintenance"]) as connection:
        purged = connection.execute(
            "select * from platform_control.purge_expired_control_state()"
        ).fetchone()[0]
    assert purged >= 1
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select role::text, role_audit_event_id from "
            "platform_control.internal_users where internal_user_id = %s",
            (viewer,),
        ).fetchone() == ("management_viewer", None)
        assert connection.execute(
            "select requested_audit_event_id, requested_audit_id_copy from "
            "platform_control.management_mutations where operation_id = %s",
            (operation_id,),
        ).fetchone() == (None, event_id)


@pytest.mark.postgres
def test_mutation_function_rejects_causal_parameters_not_matching_audit_payload(
    control_database,
) -> None:
    environment = control_database["environments"]["preview"]
    target = uuid4()
    operation_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        owner = _active_owner(connection, "Exact Audit Owner")
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status) "
            "values (%s, 'Exact Audit Target', 'active')",
            (target,),
        )
        event_id = _requested_event(
            connection,
            event_type="viewer_role_assignment_requested",
            actor=owner,
            target=str(target),
            operation_id=operation_id,
            reason_code="access_approved",
            details={
                "previous_role": "member",
                "new_role": "management_viewer",
                "expected_row_version": 99,
                "result": "requested",
            },
        )
    with psycopg.connect(
        environment["urls"]["platform_control_app_preview"]
    ) as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match="audit payload"):
            connection.execute(
                "select platform_control.assign_management_viewer(%s,%s,%s,0,%s)",
                (operation_id, owner, target, event_id),
            )
