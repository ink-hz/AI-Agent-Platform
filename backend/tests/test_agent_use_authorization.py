from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.authorization import (
    AgentUseAuthorization,
    AgentUseAuthorizationUnavailable,
)
from app.agent_brain.models import CALLABLE_AGENT_IDS
from app.control_plane.models import AuthContext, Role
from test_agent_brain_migration import _insert_grant, _seed_active_directory
from test_control_plane_migration import control_database


APP_DSN = (
    "postgresql://platform_control_app:secret@127.0.0.1/"
    "agent_platform_control"
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, owner, response):
        self.owner = owner
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.owner.queries.append((sql, params))
        actor, agent_ids = params
        rows = (
            [
                {"agent_id": agent_id, "allowed": agent_id in self.response}
                for agent_id in agent_ids
            ]
            if isinstance(self.response, set)
            else self.response
        )
        return _Rows(rows)


class _Decisions:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.calls = 0
        self.queries = []

    def connect(self, *_args, **_kwargs):
        decision = self.decisions[self.calls]
        self.calls += 1
        return _Connection(self, decision)


def _auth(role: Role = Role.MEMBER) -> AuthContext:
    return AuthContext(uuid4(), role, uuid4(), False)


def test_permitted_agents_evaluates_every_callable_card_through_v29_scope() -> None:
    decisions = _Decisions({"hr-bot", "marketing-gtm-bot"})
    auth = _auth()
    authorization = AgentUseAuthorization(APP_DSN, connect=decisions.connect)

    cards = authorization.permitted_agents(auth)

    assert tuple(card.agent_id for card in cards) == (
        "hr-bot",
        "marketing-gtm-bot",
    )
    assert decisions.calls == 1
    sql, params = decisions.queries[0]
    assert "platform_control.has_agent_use_scope_v29" in sql
    assert params == (auth.internal_user_id, list(CALLABLE_AGENT_IDS))


@pytest.mark.postgres
def test_postgres_authorization_passes_all_callable_ids_as_text_array(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        user_id, _root, _child, _generation = _seed_active_directory(connection)
        actor_id = uuid4()
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) "
            "values (%s,'Grant Actor','active')",
            (actor_id,),
        )
        for agent_id in CALLABLE_AGENT_IDS:
            _insert_grant(
                connection,
                agent_id=agent_id,
                target_kind="user",
                actor_id=actor_id,
                user_id=user_id,
            )

    authorization = AgentUseAuthorization(
        environment["urls"]["platform_control_app"]
    )
    auth = AuthContext(user_id, Role.MEMBER, uuid4(), False)

    assert tuple(
        card.agent_id for card in authorization.permitted_agents(auth)
    ) == CALLABLE_AGENT_IDS


@pytest.mark.postgres
def test_fresh_single_agent_decision_includes_directory_generation(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        user_id, _root, _child, generation_id = _seed_active_directory(connection)
        actor_id = uuid4()
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Decision Actor','active')",
            (actor_id,),
        )
        _insert_grant(
            connection,
            agent_id="hr-bot",
            target_kind="user",
            actor_id=actor_id,
            user_id=user_id,
        )

    decision = AgentUseAuthorization(
        environment["urls"]["platform_control_app"]
    ).decide_for_user_id(user_id, "hr-bot")

    assert decision.allowed is True
    assert decision.directory_generation_id == generation_id


def test_management_role_does_not_bypass_agent_use_decision() -> None:
    decisions = _Decisions(set())
    authorization = AgentUseAuthorization(APP_DSN, connect=decisions.connect)

    assert authorization.permitted_agents(_auth(Role.PLATFORM_OWNER)) == ()


def test_final_user_decisions_are_not_cached() -> None:
    decisions = _Decisions({"hr-bot"}, {"fae-bot"})
    auth = _auth()
    authorization = AgentUseAuthorization(APP_DSN, connect=decisions.connect)

    first = authorization.permitted_agents(auth)
    second = authorization.permitted_agents(auth)

    assert tuple(card.agent_id for card in first) == ("hr-bot",)
    assert tuple(card.agent_id for card in second) == ("fae-bot",)
    assert decisions.calls == 2


def test_database_failure_fails_closed() -> None:
    def unavailable(*_args, **_kwargs):
        raise psycopg.OperationalError("database unavailable")

    authorization = AgentUseAuthorization(APP_DSN, connect=unavailable)

    assert authorization.permitted_agents(_auth()) == ()


def test_orchestration_authorization_distinguishes_database_failure_from_no_grants() -> None:
    def unavailable(*_args, **_kwargs):
        raise psycopg.OperationalError("database unavailable")

    authorization = AgentUseAuthorization(APP_DSN, connect=unavailable)

    with pytest.raises(AgentUseAuthorizationUnavailable):
        authorization.permitted_agents_for_user_id(uuid4())


def _valid_rows() -> list[dict[str, object]]:
    return [
        {"agent_id": agent_id, "allowed": True}
        for agent_id in CALLABLE_AGENT_IDS
    ]


@pytest.mark.parametrize(
    "rows",
    [
        _valid_rows()[:-1],
        [*_valid_rows(), {"agent_id": "extra-bot", "allowed": True}],
        [_valid_rows()[1], _valid_rows()[0], *_valid_rows()[2:]],
        [*_valid_rows()[:-1], _valid_rows()[0]],
        [*_valid_rows()[:-1], {"agent_id": "unknown-bot", "allowed": True}],
        [{**_valid_rows()[0], "allowed": 1}, *_valid_rows()[1:]],
    ],
    ids=[
        "missing-id",
        "extra-id",
        "reordered-ids",
        "duplicated-id",
        "unknown-id",
        "non-boolean-decision",
    ],
)
def test_malformed_database_decision_rows_fail_closed(rows) -> None:
    decisions = _Decisions(rows)
    authorization = AgentUseAuthorization(APP_DSN, connect=decisions.connect)

    assert authorization.permitted_agents(_auth()) == ()
