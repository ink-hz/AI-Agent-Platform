from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from base64 import b32encode
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException

from app.sdk_workbench.repository import SdkRepository
from app.sdk_workbench.sso_repository import SdkSsoRepository
from test_control_plane_migration import control_database


def _activate_directory_member(connection, user_id):
    generation, member_key = uuid4(), uuid4()
    connection.execute(
        "insert into platform_control.directory_generations(generation_id,status,member_count,department_count,content_sha256,completed_at) "
        "values (%s,'complete',1,0,%s,now())", (generation, "d" * 64),
    )
    connection.execute(
        "insert into platform_control.directory_members(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
        "lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status) "
        "values (%s,%s,%s,'employee',%s,1,%s,1,'SDK Member','active')",
        (generation, member_key, user_id, b"d" * 32, b"d" * 29),
    )
    connection.execute(
        "update platform_control.directory_state set active_generation_id=%s,last_complete_at=now() where singleton",
        (generation,),
    )
    return generation


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
        generation = _activate_directory_member(connection, member)
    SdkRepository(env["urls"]["platform_control_app"]).grant_member(owner, member, "contributor", uuid4())
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    assert sso.bind(owner, member, "cangyuan") == {"internal_user_id": str(member), "account": "cangyuan"}
    digest = sha256(b"first-ticket").digest()
    assert sso.issue(member, session_id, digest)
    assert sso.bind(owner, member, "another-user")["account"] == "another-user"
    assert sso.consume(digest) == ("another-user", "home")
    assert sso.consume(digest) is None

    second = sha256(b"second-ticket").digest()
    assert sso.issue(member, session_id, second)
    with psycopg.connect(env["admin"]) as connection:
        connection.execute("update platform_control.web_sessions set revoked_at=now() where session_id=%s", (session_id,))
    assert sso.consume(second) is None
    assert not sso.issue(member, session_id, sha256(b"third-ticket").digest())
    with psycopg.connect(env["admin"]) as connection:
        connection.execute("update platform_control.web_sessions set revoked_at=null where session_id=%s", (session_id,))
        connection.execute("update platform_control.directory_generations set completed_at=now()-interval '25 hours' where generation_id=%s", (generation,))
    assert not sso.issue(member, session_id, sha256(b"stale-manual-ticket").digest())


@pytest.mark.postgres
def test_zentao_apilogin_timestamps_are_unique_and_increasing(control_database):
    env = control_database["environments"]["production"]
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    first = sso.next_login_time()
    second = sso.next_login_time()
    assert len(str(first)) == 10
    assert second > first


@pytest.mark.postgres
def test_prepared_directory_account_is_adopted_only_by_verified_member(control_database):
    env = control_database["environments"]["production"]
    owner, member = uuid4(), uuid4()
    with psycopg.connect(env["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'Owner','active'),(%s,'SDK Member','active')", (owner, member),
        )
        generation = _activate_directory_member(connection, member)
        member_key = connection.execute(
            "select member_key from platform_control.directory_members where generation_id=%s", (generation,)
        ).fetchone()[0]
        account = SdkSsoRepository.account_for(member_key)
        marker = "sdk:" + uuid4().hex
        connection.execute(
            "insert into sdk.directory_accounts(member_key,account,marker,display_name,state) "
            "values (%s,%s,%s,'SDK Member','ready')", (member_key, account, marker),
        )
    SdkRepository(env["urls"]["platform_control_app"]).grant_member(owner, member, "contributor", uuid4())
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    with sso.provision_slot(member) as (connection, identity):
        assert identity["prepared"] == {"account": account, "marker": marker}
        sso.adopt_directory_account(connection, member, account, marker)
        sso.complete_provision(connection, member, account)
    assert sso.identity(member)["account"] == account
    with psycopg.connect(env["admin"]) as connection:
        connection.execute("update platform_control.directory_members set status='inactive' where generation_id=%s", (generation,))
    with pytest.raises(HTTPException) as error:
        with sso.provision_slot(member):
            pass
    assert error.value.status_code == 403


@pytest.mark.postgres
def test_zentao_login_slot_holds_order_across_network_call_and_commits_failed_attempt(control_database):
    env = control_database["environments"]["production"]
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def first_login():
        with pytest.raises(RuntimeError, match="network failed"):
            with sso.login_slot() as timestamp:
                first_entered.set()
                assert release_first.wait(5)
                raise RuntimeError("network failed")
        return timestamp

    def second_login():
        with sso.login_slot() as timestamp:
            second_entered.set()
            return timestamp

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_login)
        assert first_entered.wait(5)
        second = pool.submit(second_login)
        try:
            assert not second_entered.wait(0.3)
        finally:
            release_first.set()
        first_timestamp = first.result(timeout=5)
        second_timestamp = second.result(timeout=5)
    assert second_timestamp > first_timestamp


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


@pytest.mark.postgres
def test_sso_department_scope_requires_current_active_member_and_allowed_ancestor(control_database):
    env = control_database["environments"]["production"]
    owner, employee, generation, member_key, root, child, session_id = (uuid4() for _ in range(7))
    with psycopg.connect(env["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,role,display_name,status) "
            "values (%s,'platform_owner','Owner','active'),(%s,'member','SDK Dev','active')",
            (owner, employee),
        )
        connection.execute(
            "insert into platform_control.directory_generations(generation_id,status,member_count,department_count,content_sha256,completed_at) "
            "values (%s,'complete',1,2,%s,now())", (generation, "a" * 64),
        )
        connection.execute(
            "insert into platform_control.directory_members(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status) "
            "values (%s,%s,%s,'employee',%s,1,%s,1,'SDK Dev','active')",
            (generation, member_key, employee, b"m" * 32, b"m" * 29),
        )
        for index, (department, parent) in enumerate(((root, None), (child, root)), start=1):
            connection.execute(
                "insert into platform_control.directory_departments(generation_id,department_key,parent_department_key,"
                "lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version,display_name) "
                "values (%s,%s,%s,%s,1,%s,1,%s)",
                (generation, department, parent, bytes([index]) * 32, bytes([index]) * 29, f"Dept {index}"),
            )
        connection.execute(
            "insert into platform_control.department_closure(generation_id,ancestor_department_key,descendant_department_key,depth) "
            "values (%s,%s,%s,0),(%s,%s,%s,0),(%s,%s,%s,1)",
            (generation,root,root,generation,child,child,generation,root,child),
        )
        connection.execute("insert into platform_control.member_departments(generation_id,member_key,department_key) values (%s,%s,%s)",
                           (generation, member_key, child))
        connection.execute("update platform_control.directory_state set active_generation_id=%s,last_complete_at=now() where singleton", (generation,))
        connection.execute(
            "insert into platform_control.web_sessions(session_id,internal_user_id,token_hash,csrf_hash,idle_expires_at,absolute_expires_at) "
            "values (%s,%s,%s,%s,now()+interval '1 hour',now()+interval '1 day')",
            (session_id, employee, b"s" * 32, b"c" * 32),
        )
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    assert sso.identity(employee)["eligible"] is False
    assert sso.set_allowed_department(owner, root, True) is False
    assert sso.set_allowed_department(owner, child, True) is True
    assert sso.identity(employee)["eligible"] is True
    choices = {item["id"]: item for item in sso.departments()}
    assert choices[str(root)]["path"] == "Dept 1"
    assert choices[str(root)]["people"] == 1
    assert choices[str(root)]["root"] is True
    assert choices[str(child)]["path"] == "Dept 1 / Dept 2"
    with psycopg.connect(env["admin"]) as connection:
        assert connection.execute(
            "select actor_id,department_key,enabled from sdk.department_policy_events"
        ).fetchall() == [(owner, child, True)]
    with psycopg.connect(env["admin"]) as connection:
        connection.execute(
            "insert into sdk.members(internal_user_id,role,granted_by,grant_source) values (%s,'contributor',%s,'directory')",
            (employee, employee),
        )
    sso.bind(owner, employee, "sdk_" + b32encode(employee.bytes).decode("ascii").rstrip("=").lower())
    marker = "sdk:" + uuid4().hex
    with psycopg.connect(env["admin"]) as connection:
        connection.execute(
            "insert into sdk.provision_intents(internal_user_id,account,marker,state) values (%s,%s,%s,'bound')",
            (employee, sso.account_for(employee), marker),
        )
    ticket = sha256(b"department-ticket").digest()
    assert sso.issue(employee, session_id, ticket, "tasks") is True
    with psycopg.connect(env["admin"]) as connection:
        connection.execute("update platform_control.directory_members set status='inactive' where generation_id=%s and member_key=%s", (generation, member_key))
    assert sso.identity(employee)["eligible"] is False
    assert sso.consume(ticket) is None
    assert not sso.issue(employee, session_id, sha256(b"revoked-ticket").digest(), "tasks")
    assert SdkRepository(env["urls"]["platform_control_app"]).role_for(employee) is None
    assert sso.access_changes() == [{"internal_user_id": employee, "account": sso.account_for(employee),
                                     "marker": marker, "state": "bound", "eligible": False}]
    sso.defer_access_retry(employee)
    assert sso.access_changes() == []
    with psycopg.connect(env["admin"]) as connection:
        connection.execute("update sdk.provision_intents set last_access_attempt_at=now()-interval '6 minutes' where internal_user_id=%s", (employee,))
    assert sso.access_changes()[0]["internal_user_id"] == employee
    with sso.access_slot(employee) as (connection, identity):
        assert identity["eligible"] is False
        sso.mark_access_state(connection, employee, "bound", "suspended")
    assert sso.access_changes() == []
    with psycopg.connect(env["admin"]) as connection:
        connection.execute("update platform_control.directory_members set status='active' where generation_id=%s and member_key=%s", (generation, member_key))
    assert sso.access_changes()[0]["state"] == "suspended"
    with sso.access_slot(employee) as (connection, identity):
        assert identity["eligible"] is True
        sso.mark_access_state(connection, employee, "suspended", "bound")
    assert sso.access_changes() == []
    with psycopg.connect(env["admin"]) as connection:
        connection.execute("update platform_control.directory_generations set completed_at=now()-interval '25 hours' where generation_id=%s", (generation,))
    assert sso.identity(employee)["eligible"] is False
    assert sso.access_changes() == []


@pytest.mark.postgres
def test_sso_provision_intent_is_durable_and_completion_binds_exact_account(control_database):
    env = control_database["environments"]["production"]
    owner, employee = uuid4(), uuid4()
    with psycopg.connect(env["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'Owner','active'),(%s,'SDK Person','active')", (owner, employee),
        )
        _activate_directory_member(connection, employee)
    SdkRepository(env["urls"]["platform_control_app"]).grant_member(owner, employee, "contributor", uuid4())
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    with sso.provision_slot(employee) as (connection, identity):
        assert identity["eligible"] is True
        assert identity["account"] is None
        account = sso.account_for(employee)
        assert len(account) == 30
        sso.mark_provision_attempt(connection, employee, account, "sdk:" + uuid4().hex)
    assert sso.identity(employee)["provision_state"] == "reconcile_required"
    with sso.provision_slot(employee) as (connection, identity):
        assert identity["provision_state"] == "reconcile_required"
        sso.complete_provision(connection, employee, account)
    assert sso.identity(employee)["account"] == account
    assert sso.identity(employee)["provision_state"] == "bound"


@pytest.mark.postgres
def test_parallel_first_login_returns_explicit_in_progress_without_waiting_for_network(control_database):
    env = control_database["environments"]["production"]
    owner, employee = uuid4(), uuid4()
    with psycopg.connect(env["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'Owner','active'),(%s,'SDK Parallel','active')", (owner, employee),
        )
        _activate_directory_member(connection, employee)
    SdkRepository(env["urls"]["platform_control_app"]).grant_member(owner, employee, "contributor", uuid4())
    sso = SdkSsoRepository(env["urls"]["platform_control_app"])
    entered, release = Event(), Event()

    def first():
        with sso.provision_slot(employee):
            entered.set()
            assert release.wait(5)

    def second():
        with sso.provision_slot(employee):
            return "unexpected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first)
        assert entered.wait(5)
        contender = pool.submit(second)
        try:
            with pytest.raises(HTTPException) as error:
                contender.result(timeout=1)
            assert error.value.status_code == 409
            assert error.value.detail == "sdk_provision_in_progress"
        finally:
            release.set()
        pending.result(timeout=5)
