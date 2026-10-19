from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.routes import build_sdk_router
from app.sdk_workbench.service import SdkService
from app.sdk_workbench.zentao import ZenTaoAuthenticationError, ZenTaoRequestRejected, ZenTaoUnavailable


ACTOR = UUID("11111111-1111-4111-8111-111111111111")


class Repository:
    def __init__(self):
        self.roles = {ACTOR: "contributor"}
        self.intents = {}
        self.reviews = []
        self.tasks = []

    def role_for(self, actor):
        return self.roles.get(actor)

    def reserve(self, actor, request_id, digest, kind, story_id=None, payload=None):
        key = (actor, request_id)
        previous = self.intents.get(key)
        if previous is None:
            self.intents[key] = {"digest": digest, "state": "pending", "result": None, "kind": kind, "story_id": story_id, "request_payload": payload or {}}
            return self.intents[key].copy(), True
        if previous["state"] == "rejected" and previous["digest"] == digest:
            previous["state"] = "pending"
            return previous.copy(), True
        return previous.copy(), False

    def complete(self, actor, request_id, result):
        self.intents[(actor, request_id)].update(state="succeeded", result=result)

    def reconcile(self, actor, request_id):
        self.intents[(actor, request_id)]["state"] = "reconcile_required"

    def reject(self, actor, request_id, reason):
        self.intents[(actor, request_id)]["state"] = "rejected"

    def list_reconciliation(self):
        return [{"actor_id": str(actor), "request_id": str(request_id), **intent} for (actor, request_id), intent in self.intents.items() if intent["state"] == "reconcile_required"]

    def get_intent(self, actor, request_id):
        return self.intents.get((actor, request_id))

    def bind_reconciliation(self, admin, admin_request_id, actor, request_id, external_id):
        intent = self.intents[(actor, request_id)]
        intent.update(state="succeeded", result={"id": external_id})
        if intent["kind"] == "create_task":
            self.add_task_link(actor, intent["story_id"], external_id, request_id)
        return {"id": external_id}

    def add_review(self, actor, story_id, story_version, decision, reason, request_id):
        self.reviews.append({"actor_id": str(actor), "story_id": story_id, "story_version": story_version, "decision": decision, "reason": reason, "request_id": str(request_id)})
        self.complete(actor, request_id, self.reviews[-1])
        return self.reviews[-1]

    def list_reviews(self, story_id, page=1):
        return [row for row in self.reviews if row["story_id"] == story_id]

    def latest_review(self, story_id):
        reviews = self.list_reviews(story_id)
        return reviews[-1] if reviews else None

    def add_task_link(self, actor, story_id, task_id, request_id):
        self.tasks.append({"story_id": story_id, "task_id": task_id})

    def complete_task(self, actor, story_id, task_id, request_id):
        self.add_task_link(actor, story_id, task_id, request_id)
        self.complete(actor, request_id, {"id": task_id})

    def list_tasks(self, story_id):
        return [row for row in self.tasks if row["story_id"] == story_id]

    def members(self):
        return [{"internal_user_id": str(user), "role": role} for user, role in self.roles.items()]

    def grant_member(self, actor, target, role, request_id):
        self.roles[target] = role
        return {"internal_user_id": str(target), "role": role}


class ZenTao:
    def __init__(self):
        self.created = []
        self.fail = False
        self.version = "1"

    async def list_stories(self, product_id, page):
        return {"status": "success", "stories": [{"id": "12", "title": "USB 稳定性"}], "page": page, "total": 1}

    async def get_story(self, story_id):
        created = next((entry for entry in reversed(self.created) if entry[0] == "story"), None)
        return {"id": str(story_id), "product": "7", "version": self.version,
                "title": created[1] if created else "USB 稳定性", "spec": created[2] if created else "场景：客户现场\n设备/平台：Linux",
                "verify": created[3] if created else "连续运行", "status": "active"}

    async def create_story(self, product_id, title, spec, verify):
        self.created.append(("story", title, spec, verify))
        if self.fail:
            raise ZenTaoUnavailable("private detail")
        return 12

    async def create_task(self, execution_id, story_id, name, desc):
        self.created.append(("task", execution_id, story_id, name, desc))
        return 31

    async def get_task(self, task_id):
        created = next((entry for entry in reversed(self.created) if entry[0] == "task"), None)
        return {"id": str(task_id), "story": "12", "execution": "9", "name": created[3] if created else "修复断流", "desc": created[4] if created else "检查枚举"}


def app_for(repo, zentao, *, role=Role.MEMBER, stale=False, actor=ACTOR):
    app = FastAPI()
    app.state.sdk_service = SdkService(repo, zentao, product_id=7, execution_id=9)

    @app.middleware("http")
    async def identity(request, call_next):
        if actor is not None:
            request.state.auth_context = AuthContext(actor, role, uuid4(), stale)
        return await call_next(request)

    app.include_router(build_sdk_router())
    return app


@pytest.mark.asyncio
async def test_member_can_list_and_create_story_with_server_derived_actor():
    repo, zentao = Repository(), ZenTao()
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        listed = await client.get("/api/v1/sdk/requirements")
        created = await client.post("/api/v1/sdk/requirements", json={
            "request_id": str(uuid4()), "title": "USB 稳定性", "scenario": "客户现场",
            "device_platform": "Gemini 2 / Linux", "acceptance": "连续运行",
        })
    assert listed.status_code == 200
    assert listed.json()["items"][0]["title"] == "USB 稳定性"
    assert created.status_code == 201
    assert created.json() == {"id": 12}
    assert zentao.created == [("story", "USB 稳定性", "场景：客户现场\n设备/平台：Gemini 2 / Linux", "连续运行")]
    assert created.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_unauthorized_stale_and_spoofed_actor_are_rejected():
    repo, zentao = Repository(), ZenTao()
    body = {"request_id": str(uuid4()), "title": "a", "scenario": "b", "device_platform": "c", "acceptance": "d"}
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao, actor=None)), base_url="http://test") as client:
        assert (await client.get("/api/v1/sdk/requirements")).status_code == 401
    repo.roles.clear()
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        assert (await client.get("/api/v1/sdk/requirements")).status_code == 403
    repo.roles[ACTOR] = "contributor"
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao, stale=True)), base_url="http://test") as client:
        assert (await client.post("/api/v1/sdk/requirements", json=body)).status_code == 503
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        assert (await client.post("/api/v1/sdk/requirements", json={**body, "actor_id": str(uuid4())})).status_code == 422
    assert zentao.created == []


@pytest.mark.asyncio
async def test_request_id_replay_and_ambiguous_upstream_are_safe():
    repo, zentao = Repository(), ZenTao()
    body = {"request_id": str(uuid4()), "title": "USB", "scenario": "客户", "device_platform": "Linux", "acceptance": "通过"}
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        first = await client.post("/api/v1/sdk/requirements", json=body)
        again = await client.post("/api/v1/sdk/requirements", json=body)
        collision = await client.post("/api/v1/sdk/requirements", json={**body, "title": "changed"})
        zentao.fail = True
        uncertain_body = {**body, "request_id": str(uuid4())}
        uncertain = await client.post("/api/v1/sdk/requirements", json=uncertain_body)
        retry = await client.post("/api/v1/sdk/requirements", json=uncertain_body)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json() == {"id": 12}
    assert collision.status_code == 409
    assert uncertain.status_code == 503
    assert uncertain.json()["detail"] == "sdk_reconcile_required"
    assert retry.status_code == 409
    assert len(zentao.created) == 2


@pytest.mark.asyncio
async def test_reviewer_can_review_and_contributor_can_create_linked_task():
    repo, zentao = Repository(), ZenTao()
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        denied = await client.post("/api/v1/sdk/requirements/12/reviews", json={"request_id": str(uuid4()), "decision": "approved", "reason": "范围明确"})
        task = await client.post("/api/v1/sdk/requirements/12/tasks", json={"request_id": str(uuid4()), "name": "修复断流", "description": "检查枚举"})
    assert denied.status_code == 403
    assert task.status_code == 409
    repo.roles[ACTOR] = "reviewer"
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        review = await client.post("/api/v1/sdk/requirements/12/reviews", json={"request_id": str(uuid4()), "decision": "approved", "reason": "范围明确"})
        detail = await client.get("/api/v1/sdk/requirements/12")
        history = await client.get("/api/v1/sdk/requirements/12/reviews?page=1")
    assert review.status_code == 201
    assert detail.json()["reviews"][0]["actor_id"] == str(ACTOR)
    assert history.status_code == 200 and history.json()["items"][0]["reason"] == "范围明确"
    repo.roles[ACTOR] = "contributor"
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        task = await client.post("/api/v1/sdk/requirements/12/tasks", json={"request_id": str(uuid4()), "name": "修复断流", "description": "检查枚举"})
        detail = await client.get("/api/v1/sdk/requirements/12")
    assert task.status_code == 201 and task.json() == {"id": 31}
    assert zentao.created[-1] == ("task", 9, 12, "修复断流", "检查枚举")
    assert detail.json()["tasks"] == [{"story_id": 12, "task_id": 31}]


@pytest.mark.asyncio
async def test_story_outside_configured_product_cannot_be_used():
    repo, zentao = Repository(), ZenTao()
    async def foreign_story(story_id):
        return {"id": str(story_id), "product": "99", "title": "Foreign"}
    zentao.get_story = foreign_story
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        detail = await client.get("/api/v1/sdk/requirements/12")
        history = await client.get("/api/v1/sdk/requirements/12/reviews")
        task = await client.post("/api/v1/sdk/requirements/12/tasks", json={"request_id": str(uuid4()), "name": "偷看", "description": "x"})
    assert detail.status_code == history.status_code == task.status_code == 404
    assert zentao.created == []


@pytest.mark.asyncio
async def test_changed_story_version_needs_new_review_before_task():
    repo, zentao = Repository(), ZenTao()
    repo.roles[ACTOR] = "reviewer"
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        approved = await client.post("/api/v1/sdk/requirements/12/reviews", json={"request_id": str(uuid4()), "decision": "approved", "reason": "版本一通过"})
        zentao.version = "2"
        repo.roles[ACTOR] = "contributor"
        blocked = await client.post("/api/v1/sdk/requirements/12/tasks", json={"request_id": str(uuid4()), "name": "开发", "description": "x"})
    assert approved.status_code == 201
    assert blocked.status_code == 409
    assert zentao.created == []


@pytest.mark.asyncio
async def test_task_request_replay_survives_later_story_version_change():
    repo, zentao = Repository(), ZenTao()
    repo.reviews.append({"story_id": 12, "story_version": "1", "decision": "approved"})
    body = {"request_id": str(uuid4()), "name": "开发", "description": "x"}
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        first = await client.post("/api/v1/sdk/requirements/12/tasks", json=body)
        zentao.version = "2"
        replay = await client.post("/api/v1/sdk/requirements/12/tasks", json=body)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json() == {"id": 31}
    assert len(zentao.created) == 1


@pytest.mark.asyncio
async def test_only_platform_owner_can_manage_sdk_members():
    repo, zentao = Repository(), ZenTao()
    target = uuid4()
    body = {"request_id": str(uuid4()), "internal_user_id": str(target), "role": "reviewer"}
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        denied = await client.post("/api/v1/sdk/members", json=body)
    assert denied.status_code == 403
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao, role=Role.PLATFORM_OWNER)), base_url="http://test") as client:
        granted = await client.post("/api/v1/sdk/members", json=body)
        listed = await client.get("/api/v1/sdk/members")
    assert granted.status_code == 201
    assert granted.json() == {"internal_user_id": str(target), "role": "reviewer"}
    assert len(listed.json()["items"]) == 2


@pytest.mark.asyncio
async def test_owner_reconciles_ambiguous_story_without_duplicate_creation():
    repo, zentao = Repository(), ZenTao()
    request_id = uuid4()
    body = {"request_id": str(request_id), "title": "USB", "scenario": "客户", "device_platform": "Linux", "acceptance": "通过"}
    zentao.fail = True
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        uncertain = await client.post("/api/v1/sdk/requirements", json=body)
    assert uncertain.status_code == 503
    zentao.fail = False
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao, role=Role.PLATFORM_OWNER)), base_url="http://test") as client:
        pending = await client.get("/api/v1/sdk/reconciliation")
        bound = await client.post(f"/api/v1/sdk/reconciliation/{ACTOR}/{request_id}/bind", json={"request_id": str(uuid4()), "external_id": 12})
        replay = await client.post("/api/v1/sdk/requirements", json=body)
    assert pending.status_code == 200 and len(pending.json()["items"]) == 1
    assert bound.status_code == 200 and bound.json() == {"id": 12}
    assert replay.status_code == 201 and replay.json() == {"id": 12}
    assert len(zentao.created) == 1


@pytest.mark.asyncio
async def test_owner_cannot_bind_unrelated_story_after_uncertain_write():
    repo, zentao = Repository(), ZenTao()
    request_id = uuid4()
    zentao.fail = True
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        await client.post("/api/v1/sdk/requirements", json={"request_id": str(request_id), "title": "USB", "scenario": "客户", "device_platform": "Linux", "acceptance": "通过"})
    zentao.fail = False
    async def unrelated(_story_id):
        return {"id": "12", "product": "7", "version": "1", "title": "无关需求", "spec": "其他", "verify": "其他"}
    zentao.get_story = unrelated
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao, role=Role.PLATFORM_OWNER)), base_url="http://test") as client:
        rejected = await client.post(f"/api/v1/sdk/reconciliation/{ACTOR}/{request_id}/bind", json={"request_id": str(uuid4()), "external_id": 12})
    assert rejected.status_code == 409 and rejected.json()["detail"] == "sdk_external_mismatch"


@pytest.mark.asyncio
async def test_definite_upstream_auth_failure_can_retry_without_reconciliation():
    repo, zentao = Repository(), ZenTao()
    original = zentao.create_story
    async def unauthorized(*args):
        raise ZenTaoAuthenticationError("private")
    zentao.create_story = unauthorized
    body = {"request_id": str(uuid4()), "title": "USB", "scenario": "客户", "device_platform": "Linux", "acceptance": "通过"}
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        denied = await client.post("/api/v1/sdk/requirements", json=body)
        assert denied.status_code == 503 and denied.json()["detail"] == "sdk_zentao_auth_required"
        assert (await client.get("/api/v1/sdk/reconciliation")).status_code == 403
        zentao.create_story = original
        retried = await client.post("/api/v1/sdk/requirements", json=body)
    assert retried.status_code == 201
    assert len(zentao.created) == 1


@pytest.mark.asyncio
async def test_definite_permission_denial_is_retryable_after_access_is_fixed():
    repo, zentao = Repository(), ZenTao()
    original = zentao.create_story
    async def forbidden(*args):
        raise ZenTaoRequestRejected("private")
    zentao.create_story = forbidden
    body = {"request_id": str(uuid4()), "title": "USB", "scenario": "客户", "device_platform": "Linux", "acceptance": "通过"}
    async with AsyncClient(transport=ASGITransport(app=app_for(repo, zentao)), base_url="http://test") as client:
        denied = await client.post("/api/v1/sdk/requirements", json=body)
        assert denied.status_code == 502 and denied.json()["detail"] == "sdk_zentao_rejected"
        zentao.create_story = original
        retried = await client.post("/api/v1/sdk/requirements", json=body)
    assert retried.status_code == 201
