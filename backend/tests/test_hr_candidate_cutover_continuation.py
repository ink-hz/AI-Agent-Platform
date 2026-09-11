"""Accepted batch continues in its lane; cutover changes use an isolated DB."""

from uuid import uuid4

import pytest

from tests.hr_agent_support import hr_agent_database
from tests.test_hr_agent_candidate_intake import (
    batch_request,
    intake,
    secured,
    uploaded,
)

_FIXTURES = (intake, secured, uploaded)

@pytest.fixture
def database():
    with hr_agent_database() as database:
        yield database


def test_accepted_candidate_continues_children_during_cloud_drain(uploaded, intake, database):
    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    with database.admin_connection() as connection:
        connection.execute(
            "select platform_control.transition_hr_execution_cutover_v102('draining_cloud',%s)",
            (uuid4(),),
        )
    assert service.advance_one("drain-worker") is True
    item = service.get_batch(owner, batch["batch_id"])["items"][0]
    assert item["state"] in {"parsing", "profiling"}
    assert item["error_code"] is None
    assert service.advance_one("restarted-drain-worker") is True
    item = service.get_item(owner, item["item_id"])
    assert item["state"] in {"parsing", "profiling"}
    assert item["error_code"] is None
