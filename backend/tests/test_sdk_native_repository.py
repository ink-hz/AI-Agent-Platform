from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.sdk_workbench.native_repository import NativeSdkRepository


ACTOR = UUID("11111111-1111-4111-8111-111111111111")
REQUEST = UUID("22222222-2222-4222-8222-222222222222")


class Result:
    def __init__(self, *, one=None, all=None):
        self.one, self.all = one, all or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all


class Connection:
    def __init__(self):
        self.intents = {}
        self.supplements = {}
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, args=()):
        self.statements.append((sql, args))
        if "insert into sdk.native_create_intents" in sql:
            key = args[:2]
            if key in self.intents:
                return Result()
            row = {"actor_id": args[0], "request_id": args[1], "payload_digest": args[2],
                   "marker": args[3], "requested_assignee": args[4],
                   "proposer_name": args[5], "device_platform": args[6],
                   "state": "pending", "story_id": None,
                   "rejection_status": None, "rejection_detail": None}
            self.intents[key] = row
            return Result(one=row)
        if "from sdk.native_create_intents" in sql:
            if "state='pending'" in sql:
                return Result(one=next((row for (actor_id, _), row in self.intents.items()
                                        if actor_id == args[0] and row["state"] == "pending"), None))
            return Result(one=self.intents.get(args[:2]))
        if "update sdk.native_create_intents" in sql:
            row = self.intents[args[1:3]]
            row.update(state="created", story_id=args[0])
            return Result(one=row)
        if "insert into sdk.native_story_supplements" in sql:
            self.supplements[args[0]] = {"story_id": args[0], "proposer_name": args[1],
                                          "device_platform": args[2]}
            return Result()
        if "from sdk.native_story_supplements" in sql:
            return Result(all=[self.supplements[i] for i in args[0] if i in self.supplements])
        if "from sdk.plan_story_sources" in sql:
            return Result(all=[])
        if "from sdk.sso_bindings" in sql:
            if "where binding.account=" in sql:
                return Result(one={"account": args[0]} if args[0] == "sdk_owner" else None)
            return Result(all=[{"account": "sdk_owner", "name": "行云"}])
        raise AssertionError(sql)


def repo(connection):
    return NativeSdkRepository("postgresql://platform_control_app@localhost/agent_platform_control",
                               connect=lambda *a, **k: connection)


def test_create_intent_is_durable_and_repeated_request_cannot_change_payload():
    connection = Connection()
    repository = repo(connection)
    first, fresh = repository.reserve_create(ACTOR, REQUEST, "a" * 64, "sdk-native:marker",
        requested_assignee=None, proposer="客户A", device="Gemini 330")
    again, repeated = repository.reserve_create(ACTOR, REQUEST, "a" * 64, "sdk-native:marker",
        requested_assignee=None, proposer="客户A", device="Gemini 330")
    assert fresh and not repeated
    assert first == again
    with pytest.raises(HTTPException) as mismatch:
        repository.reserve_create(ACTOR, REQUEST, "b" * 64, "sdk-native:marker",
            requested_assignee=None, proposer="客户A", device="Gemini 330")
    assert mismatch.value.status_code == 409
    assert repository.pending_create(ACTOR)["request_id"] == REQUEST
    repository.complete_create(ACTOR, REQUEST, 77, "客户A", "Gemini 330")
    assert connection.intents[ACTOR, REQUEST]["story_id"] == 77
    assert repository.source_for(77)["device_platform"] == "Gemini 330"


def test_member_selector_contains_only_current_bound_sdk_users():
    connection = Connection()
    repository = repo(connection)
    assert repository.member_account("sdk_owner")
    assert not repository.member_account("outsider")
    assert repository.members() == [{"account": "sdk_owner", "name": "行云"}]
    statements = "\n".join(sql for sql, _ in connection.statements)
    assert "sdk.sso_eligible" in statements


def test_native_write_migration_limits_grants_to_platform_app():
    path = Path(__file__).parents[1] / "control_migrations/122_sdk_native_requirements.sql"
    sql = path.read_text()
    assert "create table sdk.native_create_intents" in sql
    assert "primary key (actor_id,request_id)" in sql
    assert "story_id integer unique" in sql
    assert "create table sdk.native_story_supplements" in sql
    assert "where state='pending'" in sql
    assert "revoke all on sdk.native_create_intents" in sql
    assert "grant select, insert, update" in sql
