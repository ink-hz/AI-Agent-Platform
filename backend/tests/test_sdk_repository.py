from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi import HTTPException

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

    intent, fresh = repository.reserve(target, request_id, "a" * 64, "create_requirement", payload={"title": "需求"})
    assert fresh and intent["state"] == "pending"
    assert repository.get_intent(target, request_id)["request_payload"] == {"title": "需求"}
    repository.complete(target, request_id, {"id": 71})
    replay, fresh = repository.reserve(target, request_id, "a" * 64, "create_requirement")
    assert not fresh and replay["result"] == {"id": 71}

    rejected_id = uuid.uuid4()
    repository.reserve(target, rejected_id, "9" * 64, "create_requirement")
    repository.reject(target, rejected_id, "sdk_zentao_auth_required")
    assert repository.get_intent(target, rejected_id)["state"] == "rejected"
    retried, fresh = repository.reserve(target, rejected_id, "9" * 64, "create_requirement")
    assert fresh and retried["state"] == "pending"
    repository.reject(target, rejected_id, "sdk_zentao_rejected")
    with psycopg.connect(environment["admin"]) as connection:
        rejection_reasons = connection.execute(
            "select reason from sdk.write_rejection_events where actor_id=%s and request_id=%s order by id",
            (target, rejected_id),
        ).fetchall()
    assert rejection_reasons == [("sdk_zentao_auth_required",), ("sdk_zentao_rejected",)]
    repository.reserve(target, rejected_id, "9" * 64, "create_requirement")
    repository.complete(target, rejected_id, {"id": 73})
    assert repository.get_intent(target, rejected_id)["result"] == {"id": 73}
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from sdk.write_rejection_events where actor_id=%s and request_id=%s",
            (target, rejected_id),
        ).fetchone()[0] == 2

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
    duplicate_story_id = uuid.uuid4()
    repository.reserve(target, duplicate_story_id, "f" * 64, "create_requirement")
    repository.reconcile(target, duplicate_story_id)
    with pytest.raises(HTTPException) as duplicate:
        repository.bind_reconciliation(actor, uuid.uuid4(), target, duplicate_story_id, 72)
    assert duplicate.value.status_code == 409

    with psycopg.connect(environment["admin"]) as connection:
        for index in range(501):
            connection.execute(
                "insert into sdk.review_events(actor_id,request_id,story_id,story_version,decision,reason,created_at) "
                "values (%s,%s,71,'1','rejected',%s,now()+%s * interval '1 second')",
                (target, uuid.uuid4(), f"review-{index}", index),
            )
    assert repository.list_reviews(71)[-1]["reason"] == "review-500"
    assert repository.latest_review(71)["reason"] == "review-500"
    assert repository.list_reviews(71, 11)[-1]["reason"] == "review-0"
