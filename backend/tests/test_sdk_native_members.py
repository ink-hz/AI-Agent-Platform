from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import HTTPException

from app.sdk_workbench.native_repository import NativeSdkRepository
from app.sdk_workbench.sso_repository import SdkSsoRepository
from app.sdk_workbench.provision import ProvisionUnavailable


PERSON_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PERSON_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
PERSON_C = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
USER_B = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


class Result:
    def __init__(self, *, one=None, rows=()):
        self.one = one
        self.rows = list(rows)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class DirectoryConnection:
    def __init__(self, people):
        self.people = people
        self.prepared = {}
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, sql, args=()):
        self.statements.append(sql)
        if "from sdk.scoped_people() where member_key=" in sql:
            return Result(one=next((p for p in self.people if p["member_key"] == args[0]), None))
        if "from sdk.scoped_people()" in sql:
            return Result(rows=self.people)
        if "from sdk.sso_bindings binding" in sql:
            return Result(rows=[])
        if "from sdk.directory_accounts where member_key=" in sql:
            return Result(one=self.prepared.get(args[0]))
        if "insert into sdk.directory_accounts" in sql:
            self.prepared[args[0]] = {"account": args[1], "marker": args[2], "state": "reserved"}
            return Result()
        if "update sdk.directory_accounts" in sql:
            self.prepared[args[0]]["state"] = "ready"
            return Result()
        if "pg_advisory_lock" in sql or "pg_advisory_unlock" in sql:
            return Result()
        raise AssertionError(sql)


class Provisioner:
    def __init__(self):
        self.calls = []
        self.fail = False

    def create(self, account, name, marker):
        self.calls.append(("create", account, name, marker))
        if self.fail:
            raise ProvisionUnavailable("offline")

    def reconcile(self, account, name, marker):
        self.calls.append(("reconcile", account, name, marker))

    def set_access(self, account, marker, *, enabled):
        self.calls.append(("access", account, marker, enabled))


class Sso:
    def __init__(self, provisioner):
        self.provisioner = provisioner
        self.adopted = []

    def _ensure_personal_account(self, user_id):
        self.adopted.append(user_id)


def person(member_key, name, *, internal_id=None, bound=None, intent=None, state=None,
           identity_available=True):
    return {"member_key": member_key, "internal_user_id": internal_id,
            "display_name": name, "bound_account": bound,
            "intent_account": intent, "intent_state": state,
            "identity_available": identity_available}


def repo(connection):
    return NativeSdkRepository("postgresql://platform_control_app@localhost/agent_platform_control",
                               connect=lambda *a, **k: connection)


def test_member_selector_includes_bound_prepared_and_unprepared_without_creating_accounts():
    a = SdkSsoRepository.account_for(PERSON_A)
    b = SdkSsoRepository.account_for(PERSON_B)
    c = SdkSsoRepository.account_for(PERSON_C)
    connection = DirectoryConnection([
        person(PERSON_A, "同名", bound=a),
        person(PERSON_B, "同名", internal_id=USER_B),
        person(PERSON_C, "独名"),
        person(UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"), "失效", identity_available=False),
    ])
    connection.prepared[PERSON_B] = {"account": b, "marker": "sdk:" + "1" * 32, "state": "ready"}
    result = repo(connection).members()
    assert {item["account"] for item in result} == {a, b, c}
    assert len({item["label"] for item in result}) == 3
    assert next(item for item in result if item["account"] == c)["label"] == "独名"
    assert not any("insert " in sql.lower() or "update " in sql.lower() for sql in connection.statements)


def test_unbound_directory_assignee_is_named_in_requirement_detail():
    account = SdkSsoRepository.account_for(PERSON_C)
    connection = DirectoryConnection([person(PERSON_C, "独名")])
    assert repo(connection).names_for([account]) == {account: "独名"}


def test_unprepared_member_is_created_once_without_product_access_before_login():
    account = SdkSsoRepository.account_for(PERSON_C)
    connection = DirectoryConnection([person(PERSON_C, "独名")])
    sso = Sso(Provisioner())
    repository = repo(connection)
    assert repository.ensure_member(account, sso) == account
    assert repository.ensure_member(account, sso) == account
    assert [call[0] for call in sso.provisioner.calls].count("create") == 1
    assert [call[0] for call in sso.provisioner.calls].count("reconcile") == 1
    assert [call[0] for call in sso.provisioner.calls].count("access") == 0
    assert connection.prepared[PERSON_C]["state"] == "ready"


def test_prepared_member_is_reconciled_without_duplicate_creation():
    account = SdkSsoRepository.account_for(PERSON_C)
    marker = "sdk:" + "a" * 32
    connection = DirectoryConnection([person(PERSON_C, "独名")])
    connection.prepared[PERSON_C] = {"account": account, "marker": marker, "state": "ready"}
    sso = Sso(Provisioner())
    assert repo(connection).ensure_member(account, sso) == account
    assert sso.provisioner.calls == [("reconcile", account, "独名", marker)]


def test_bound_and_pending_identity_use_the_existing_sso_account():
    bound = SdkSsoRepository.account_for(PERSON_A)
    pending = SdkSsoRepository.account_for(USER_B)
    connection = DirectoryConnection([
        person(PERSON_A, "甲", bound=bound),
        person(PERSON_B, "乙", internal_id=USER_B, intent=pending, state="reconcile_required"),
    ])
    sso = Sso(Provisioner())
    repository = repo(connection)
    assert repository.ensure_member(bound, sso) == bound
    assert repository.ensure_member(pending, sso) == pending
    assert sso.adopted == [USER_B]
    assert not sso.provisioner.calls


def test_stale_or_revoked_directory_refuses_provision_even_after_selector_was_loaded():
    account = SdkSsoRepository.account_for(PERSON_C)
    connection = DirectoryConnection([person(PERSON_C, "独名")])
    repository = repo(connection)
    assert repository.members()[0]["account"] == account
    connection.people = []
    sso = Sso(Provisioner())
    with pytest.raises(HTTPException) as error:
        repository.ensure_member(account, sso)
    assert error.value.status_code == 422
    assert not sso.provisioner.calls


def test_provision_failure_does_not_select_unconfirmed_account():
    account = SdkSsoRepository.account_for(PERSON_C)
    connection = DirectoryConnection([person(PERSON_C, "独名")])
    sso = Sso(Provisioner())
    sso.provisioner.fail = True
    with pytest.raises(HTTPException) as error:
        repo(connection).ensure_member(account, sso)
    assert error.value.status_code == 503
    assert not any(call[0] == "access" for call in sso.provisioner.calls)
