from __future__ import annotations

import hashlib
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_migration import _insert_grant, _seed_active_directory
from test_control_plane_migration import (  # noqa: F401 - fixture registration
    MIGRATIONS,
    control_database,
)

MIGRATION = MIGRATIONS / "113_daq_agent_launch.sql"


def test_daq_migration_is_additive_to_existing_identity_contract() -> None:
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())
    assert "create or replace function platform_control.has_agent_use_scope_v29" in sql
    assert "create or replace function platform_control.issue_agent_launch_v57" in sql
    assert "create or replace function platform_control.validate_agent_identity_binding_v57" in sql
    assert "'ai-daq-fae-agent'" in sql
    assert "drop constraint agent_launch_codes_agent_id_check" in sql
    assert "drop constraint agent_identity_bindings_agent_id_check" in sql
    assert "drop function" not in sql
    assert "drop table" not in sql


@pytest.mark.postgres
def test_daq_launch_requires_own_grant_and_binding_rejects_camera_id(
    control_database,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    session_id = uuid4()
    binding_id = uuid4()
    code_hash = hashlib.sha256(uuid4().bytes).digest()
    with psycopg.connect(environment["admin"]) as admin:
        user_id, *_ = _seed_active_directory(admin)
        admin.execute(
            "insert into platform_control.web_sessions "
            "(session_id,internal_user_id,token_hash,token_hash_key_version,"
            "csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) "
            "values (%s,%s,%s,1,%s,1,now()+interval '1 hour',"
            "now()+interval '2 hours')",
            (session_id, user_id, b"t" * 32, b"c" * 32),
        )
        _insert_grant(
            admin,
            agent_id="ai-fae-agent",
            target_kind="user",
            actor_id=user_id,
            user_id=user_id,
        )

    with psycopg.connect(environment["urls"]["platform_control_app"]) as app, pytest.raises(
        psycopg.errors.InsufficientPrivilege
    ):
        app.execute(
            "select platform_control.issue_agent_launch_v57("
            "%s,%s,1,%s,'enterprise_member',%s,%s,'ai-daq-fae-agent',%s,60)",
            (uuid4(), hashlib.sha256(uuid4().bytes).digest(), user_id,
             session_id, user_id, uuid4()),
        )

    with psycopg.connect(environment["urls"]["platform_control_app"]) as app, pytest.raises(
        psycopg.errors.CheckViolation
    ):
        app.execute(
            "select platform_control.issue_agent_launch_v57("
            "%s,%s,1,%s,'partner_operator',null,null,'ai-daq-fae-agent',%s,60)",
            (uuid4(), hashlib.sha256(uuid4().bytes).digest(), uuid4(), uuid4()),
        )

    with psycopg.connect(environment["admin"]) as admin:
        _insert_grant(
            admin,
            agent_id="ai-daq-fae-agent",
            target_kind="user",
            actor_id=user_id,
            user_id=user_id,
        )

    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        expires_at = app.execute(
            "select platform_control.issue_agent_launch_v57("
            "%s,%s,1,%s,'enterprise_member',%s,%s,'ai-daq-fae-agent',%s,60)",
            (uuid4(), code_hash, user_id, session_id, user_id, binding_id),
        ).fetchone()[0]
        assert expires_at is not None
        exchanged = app.execute(
            "select * from platform_control.exchange_agent_launch_v57(%s,1)",
            (code_hash,),
        ).fetchone()
        assert exchanged[3] == "ai-daq-fae-agent"
        assert app.execute(
            "select * from platform_control.validate_agent_identity_binding_v57(%s,%s)",
            (binding_id, "ai-fae-agent"),
        ).fetchone() is None
        assert app.execute(
            "select active from platform_control.validate_agent_identity_binding_v57(%s,%s)",
            (binding_id, "ai-daq-fae-agent"),
        ).fetchone() == (True,)

    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_control.agent_use_grants "
            "set revoked_at=now(),revoked_by=%s "
            "where agent_id='ai-daq-fae-agent' and target_internal_user_id=%s",
            (user_id, user_id),
        )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        assert app.execute(
            "select active from platform_control.validate_agent_identity_binding_v57(%s,%s)",
            (binding_id, "ai-daq-fae-agent"),
        ).fetchone() == (False,)
