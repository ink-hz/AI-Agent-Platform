from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException

from app.sdk_workbench.repository import SdkRepository
from app.sdk_workbench.sso_repository import SdkSsoRepository
from test_control_plane_migration import control_database


@pytest.mark.postgres
def test_sso_ticket_is_single_use_bound_to_active_session_and_current_account(control_database):
    env = control_database["environments"]["production"]
    owner, member, session_id = uuid4(), uuid4(), uuid4()
    with psycopg.connect(env["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'SDK Owner','active'),(%s,'SDK Member','active')",
            (owner, member),
        )
        connection.execute(
            "insert into platform_control.web_sessions(session_id,internal_user_id,token_hash,csrf_hash," 
            "idle_expires_at,absolute_expires_at) values (%s,%s,%s,%s,now()+interval '1 hour',now()+interval '1 day')",
            (session_id, member, b"a" * 32, b"b" * 32),
        )
    SdkRepository(env["urls"]["platform_control_app"]).grant_member(owner, member, "contributor", uuid4())
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    assert sso.bind(owner, member, "cangyuan") == {"internal_user_id": str(member), "account": "cangyuan"}
    digest = sha256(b"first-ticket").digest()
    assert sso.issue(member, session_id, digest)
    assert sso.bind(owner, member, "another-user")["account"] == "another-user"
    assert sso.consume(digest) == "another-user"
    assert sso.consume(digest) is None

    second = sha256(b"second-ticket").digest()
    assert sso.issue(member, session_id, second)
    with psycopg.connect(env["admin"]) as connection:
        connection.execute("update platform_control.web_sessions set revoked_at=now() where session_id=%s", (session_id,))
    assert sso.consume(second) is None
    assert not sso.issue(member, session_id, sha256(b"third-ticket").digest())


@pytest.mark.postgres
def test_zentao_apilogin_timestamps_are_unique_and_increasing(control_database):
    env = control_database["environments"]["production"]
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    first = sso.next_login_time()
    second = sso.next_login_time()
    assert len(str(first)) == 10
    assert second > first


@pytest.mark.postgres
def test_sso_binding_rejects_reserved_and_duplicate_accounts(control_database):
    env = control_database["environments"]["production"]
    owner, first, second = uuid4(), uuid4(), uuid4()
    with psycopg.connect(env["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'SDK Owner','active'),(%s,'SDK First','active'),(%s,'SDK Second','active')",
            (owner, first, second),
        )
    members = SdkRepository(env["urls"]["platform_control_app"])
    for member in (first, second):
        members.grant_member(owner, member, "viewer", uuid4())
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    for account in ("sdk-api", "sdkadmin", "admin", "bad/account"):
        with pytest.raises(HTTPException) as error:
            sso.bind(owner, first, account)
        assert error.value.status_code == 422
    sso.bind(owner, first, "cangyuan")
    with pytest.raises(HTTPException) as error:
        sso.bind(owner, second, "cangyuan")
    assert error.value.status_code == 409
