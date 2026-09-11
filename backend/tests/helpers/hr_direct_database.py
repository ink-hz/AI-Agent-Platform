"""Current deployed HR schema for direct binding and transport regressions.

Each test owns its complete disposable PostgreSQL process. Historical pending
migration tests retain their separate fixtures; this path applies hr_web once
and never drops deployed objects to imitate the old partial schema.
"""
from uuid import uuid4

import psycopg
import pytest

from hr_agent_support import hr_agent_database
from test_agent_brain_conversation_repository import _codec, repository
from app.agent_brain.turn_attempts import TurnAttemptRepository
from app.execution_relay.readiness_v5 import record_observation
from app.execution_relay.repository import ExecutionRelayRepository
from tests.helpers.v5_readiness import observation

_FIXTURES = (repository,)


@pytest.fixture()
def control_database():
    with hr_agent_database(cutover_phase="legacy") as database:
        yield {"environments": {"production": {
            "admin": database.admin_dsn,
            "urls": {"platform_control_app": database.dsn},
        }}}


@pytest.fixture()
def conversation_database(control_database):
    environment = control_database["environments"]["production"]
    owner_id, other_id = uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values(%s,'Synthetic HR owner','active'),(%s,'Other synthetic owner','active')",
            (owner_id, other_id),
        )
    return environment, owner_id, other_id


@pytest.fixture()
def attempt_repository(conversation_database):
    environment, _, _ = conversation_database
    worker_id = "readiness-fixture-" + uuid4().hex
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status) "
            "values(%s,array['hr-bot'],'active')", (worker_id,),
        )
    # Current HR turns persist scope via v6; advertise actual compatible fixture
    # capabilities through the production observation validator.
    observed = observation()
    observed["version"] = "hr_v6_readiness_v1"
    observed["service"].update(
        contractVersion="core_chat_collaboration_v6",
        rolePackage={"teamCommit": "a" * 40, "catalogRelease": "recruiting-1", "manifestSha256": "b" * 64},
        toolCapabilities=["hr.read_context", "hr.submit_result", "hr.confirm_standard"],
    )
    record_observation(
        ExecutionRelayRepository(environment["urls"]["platform_control_app"], content_codec=_codec()),
        worker_id, observed,
    )
    return TurnAttemptRepository(environment["urls"]["platform_control_app"], _codec())


@pytest.fixture()
def direct_database(attempt_repository, conversation_database):
    return conversation_database
