from uuid import uuid4

import psycopg

from app.agent_brain.authorization import AgentUseAuthorization
from app.agent_brain.models import CALLABLE_AGENT_IDS
from app.control_plane.models import AuthContext, Role


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
    def __init__(self, owner, allowed_ids):
        self.owner = owner
        self.allowed_ids = set(allowed_ids)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.owner.queries.append((sql, params))
        actor, agent_ids = params
        return _Rows(
            [
                {"agent_id": agent_id, "allowed": agent_id in self.allowed_ids}
                for agent_id in agent_ids
            ]
        )


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


def test_permitted_agents_evaluates_every_callable_card_through_v28_scope() -> None:
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
    assert "platform_control.has_agent_use_scope_v28" in sql
    assert params == (auth.internal_user_id, CALLABLE_AGENT_IDS)


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
