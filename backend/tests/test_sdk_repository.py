from __future__ import annotations

import uuid

import psycopg
import pytest

from app.sdk_workbench.repository import SdkRepository
from test_control_plane_migration import control_database


@pytest.mark.postgres
def test_sdk_members_intents_and_reviews_persist_through_app_role(control_database):
    environment = control_database["environments"]["production"]
    actor, target, request_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'SDK Owner','active'),(%s,'SDK Contributor','active')",
            (actor, target),
        )

    repository = SdkRepository(environment["urls"]["platform_control_app"])
    assert repository.role_for(target) is None
    assert repository.grant_member(actor, target, "contributor", uuid.uuid4()) == {
        "internal_user_id": str(target), "role": "contributor"
    }
    assert repository.role_for(target) == "contributor"
    assert repository.members()[0]["internal_user_id"] == str(target)

    intent, fresh = repository.reserve(target, request_id, "a" * 64, "create_requirement")
    assert fresh and intent["state"] == "pending"
    repository.complete(target, request_id, {"id": 71})
    replay, fresh = repository.reserve(target, request_id, "a" * 64, "create_requirement")
    assert not fresh and replay["result"] == {"id": 71}

    review_request = uuid.uuid4()
    repository.reserve(target, review_request, "c" * 64, "review_requirement", 71)
    review = repository.add_review(target, 71, "1", "approved", "验收标准明确", review_request)
    assert repository.list_reviews(71) == [review]
    assert repository.get_intent(target, review_request)["state"] == "succeeded"
    assert repository.get_intent(target, review_request)["result"] == review
    repository.add_task_link(target, 71, 93, uuid.uuid4())
    assert repository.list_tasks(71) == [{"story_id": 71, "task_id": 93}]

    uncertain_id = uuid.uuid4()
    repository.reserve(target, uncertain_id, "b" * 64, "create_task", 71)
    repository.reconcile(target, uncertain_id)
    assert repository.get_intent(target, uncertain_id)["state"] == "reconcile_required"
    assert repository.list_reconciliation()[0]["story_id"] == 71
    assert repository.bind_reconciliation(actor, uuid.uuid4(), target, uncertain_id, 94) == {"id": 94}
    assert repository.get_intent(target, uncertain_id)["result"] == {"id": 94}
    assert repository.list_tasks(71)[-1] == {"story_id": 71, "task_id": 94}

    completed_task_id = uuid.uuid4()
    repository.reserve(target, completed_task_id, "e" * 64, "create_task", 71)
    repository.complete_task(target, 71, 95, completed_task_id)
    assert repository.get_intent(target, completed_task_id)["result"] == {"id": 95}
    assert repository.list_tasks(71)[-1] == {"story_id": 71, "task_id": 95}

    stale_id = uuid.uuid4()
    repository.reserve(target, stale_id, "d" * 64, "create_requirement")
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update sdk.write_intents set created_at=now()-interval '2 minutes' where actor_id=%s and request_id=%s",
            (target, stale_id),
        )
    assert any(item["request_id"] == str(stale_id) for item in repository.list_reconciliation())
    assert repository.bind_reconciliation(actor, uuid.uuid4(), target, stale_id, 72) == {"id": 72}
