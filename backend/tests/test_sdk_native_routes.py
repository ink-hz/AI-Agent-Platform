from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.native_routes import build_sdk_native_router
from app.sdk_workbench.native_service import NativeSdkService
from app.sdk_workbench.zentao import ZenTaoRequestRejected, ZenTaoUnavailable


ACTOR = UUID("11111111-1111-4111-8111-111111111111")


class PersonalClient:
    def __init__(self):
        self.calls = []
        self.stories = [
            {"id": 69, "product": 1, "title": "Q4 SDK 需求", "status": "reviewing", "pri": 1,
             "sourceNote": "2026年Q4上线版本｜SDK｜Excel第99行", "openedBy": "sdk_import",
             "assignedTo": "sdk_owner", "openedDate": "2026-10-20 10:00:00", "version": 2},
            {"id": 2, "product": 1, "title": "部署检查", "status": "active", "pri": 2,
             "sourceNote": "", "openedBy": "sdk_import", "assignedTo": "", "version": 1},
        ]

    async def account(self, actor):
        return "sdk_owner"

    async def request(self, actor, method, path, payload=None, params=None, *, api_version=2):
        self.calls.append((actor.internal_user_id, method, path, params, api_version))
        if path == "products/1/stories":
            return {"status": "success", "stories": self.stories, "pager": {"recTotal": 2, "pageTotal": 1}}
        if path == "stories/69":
            return {"status": "success", "story": {
                **self.stories[0], "spec": "<p>客户场景</p>", "verify": "<p>验收标准</p>",
                "tasks": [{"id": 4, "name": "实现"}], "reviewer": ["sdk_reviewer"]},
                "actions": [{"id": 7, "action": "reviewed", "actor": "sdk_reviewer", "date": "2026-10-21 09:00:00", "comment": "通过"}]}
        if path == "stories/70":
            return {"status": "success", "story": {"id": 70, "product": 5, "title": "其他产品", "deleted": "0"}}
        if path == "my/tasks":
            return {"status": "success", "tasks": [{"id": 4, "name": "实现", "status": "doing", "assignedTo": "sdk_owner", "story": 69,
                "execution": 9,
                "openedDate": "2026-10-21 08:00:00", "estStarted": "2026-10-22", "deadline": "2026-10-29"}],
                "pager": {"recTotal": 1, "pageTotal": 1}}
        if path in {"tasks/4", "tasks/7"}:
            task_id = int(path.split("/")[1])
            return {"status": "success", "task": {
                "id": task_id, "name": "实现", "status": "doing", "assignedTo": "sdk_owner",
                "story": 69 if task_id == 4 else 70,
                "execution": 9 if task_id == 4 else 8,
                "desc": "实现接口", "openedDate": "2026-10-21 08:00:00",
                "estStarted": "2026-10-22", "deadline": "2026-10-29",
            }, "actions": [{"id": 2, "actor": "sdk_owner", "action": "started",
                            "date": "2026-10-22 10:00:00", "comment": "开始"}]}
        if path in {"executions/9", "executions/8"}:
            execution_id = int(path.split("/")[1])
            return {"id": execution_id, "name": "SDK 项目" if execution_id == 9 else "其他项目",
                    "products": [{"id": 1 if execution_id == 9 else 5}]}
        raise AssertionError(path)


class SourceRepository:
    def source_for(self, story_id):
        return self.sources_for([story_id]).get(story_id)

    def sources_for(self, story_ids):
        if 69 in story_ids:
            return {69: {"proposer_name": None, "responsible_raw": "行云", "planned_start": "2026-10-22",
                         "planned_end": "2026-10-29", "source_version": "v2.11", "source_risk": "", "source_status": "",
                         "source_note": "", "module": "SDK"}}
        return {}

    def names_for(self, accounts):
        return {"sdk_import": "导入账号", "sdk_owner": "行云", "sdk_reviewer": "苍渊"}


def make_app(client=None, *, actor=ACTOR):
    personal = client or PersonalClient()
    app = FastAPI()
    app.state.sdk_native_service = NativeSdkService(personal, SourceRepository(), product_id=1)

    @app.middleware("http")
    async def identity(request, call_next):
        if actor is not None:
            request.state.auth_context = AuthContext(actor, Role.MEMBER, uuid4(), False)
        return await call_next(request)

    app.include_router(build_sdk_native_router())
    return app, personal


@pytest.mark.asyncio
async def test_q4_view_reads_zentao_and_keeps_business_proposer_distinct_from_importer():
    app, personal = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        listed = await browser.get("/api/v1/sdk/native/requirements?view=q4")
        detail = await browser.get("/api/v1/sdk/native/requirements/69")

    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json()["total"] == 1
    assert [item["id"] for item in listed.json()["items"]] == [69]
    story = detail.json()
    assert story["business_proposer"] is None
    assert story["opened_by"] == "sdk_import"
    assert story["opened_by_name"] == "导入账号"
    assert story["assigned_to_name"] == "行云"
    assert story["source"]["responsible_raw"] == "行云"
    assert story["source"]["planned_start"] == "2026-10-22"
    assert story["actions"][0]["actor_name"] == "苍渊"
    assert all(call[2] in {"products/1/stories", "stories/69"} for call in personal.calls)


@pytest.mark.asyncio
async def test_native_detail_rejects_another_product_and_requires_platform_identity():
    app, _ = make_app()
    anonymous, _ = make_app(actor=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        wrong_product = await browser.get("/api/v1/sdk/native/requirements/70")
    async with AsyncClient(transport=ASGITransport(app=anonymous), base_url="http://test") as browser:
        no_identity = await browser.get("/api/v1/sdk/native/requirements")
    assert wrong_product.status_code == 404
    assert no_identity.status_code == 401


@pytest.mark.asyncio
async def test_my_tasks_are_read_with_the_current_personal_zen_session():
    app, personal = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        response = await browser.get("/api/v1/sdk/native/my-tasks")
    assert response.status_code == 200
    assert response.json()["items"] == [{"id": 4, "name": "实现", "status": "doing", "assignee": "sdk_owner",
        "assignee_name": "行云", "story_id": 69, "opened_date": "2026-10-21 08:00:00",
        "planned_start": "2026-10-22", "deadline": "2026-10-29"}]
    assert personal.calls[0][2] == "my/tasks"


@pytest.mark.asyncio
async def test_my_tasks_excludes_other_product_before_pagination():
    class MixedProducts(PersonalClient):
        async def request(self, actor, method, path, payload=None, params=None, *, api_version=2):
            if path == "my/tasks":
                return {"status": "success", "tasks": [
                    {"id": 7, "name": "其他产品任务", "status": "doing", "assignedTo": "sdk_owner", "story": 70, "execution": 8},
                    {"id": 4, "name": "SDK 任务", "status": "doing", "assignedTo": "sdk_owner", "story": 69, "execution": 9}],
                    "pager": {"recTotal": 2, "pageTotal": 1}}
            return await super().request(actor, method, path, payload, params, api_version=api_version)

    app, _ = make_app(MixedProducts())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        response = await browser.get("/api/v1/sdk/native/my-tasks")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [task["id"] for task in response.json()["items"]] == [4]


@pytest.mark.asyncio
async def test_multi_product_execution_does_not_expose_other_or_unscoped_tasks():
    class MultiProduct(PersonalClient):
        async def request(self, actor, method, path, payload=None, params=None, *, api_version=2):
            if path == "my/tasks":
                return {"status": "success", "tasks": [
                    {"id": 7, "name": "其他产品任务", "assignedTo": "sdk_owner", "story": 70, "execution": 9},
                    {"id": 8, "name": "无需求任务", "assignedTo": "sdk_owner", "story": 0, "execution": 9},
                    {"id": 4, "name": "SDK 任务", "assignedTo": "sdk_owner", "story": 69, "execution": 9}],
                    "pager": {"recTotal": 3, "pageTotal": 1}}
            if path == "executions/9":
                return {"id": 9, "name": "混合执行", "products": [{"id": 1}, {"id": 5}]}
            if path == "tasks/8":
                return {"status": "success", "task": {"id": 8, "name": "无需求任务",
                        "assignedTo": "sdk_owner", "story": 0, "execution": 9}, "actions": []}
            return await super().request(actor, method, path, payload, params, api_version=api_version)

    app, _ = make_app(MultiProduct())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        listed = await browser.get("/api/v1/sdk/native/my-tasks")
        unscoped = await browser.get("/api/v1/sdk/native/tasks/8")
    assert listed.status_code == 200
    assert [task["id"] for task in listed.json()["items"]] == [4]
    assert listed.json()["total"] == 1
    assert unscoped.status_code == 404


@pytest.mark.asyncio
async def test_owner_filter_uses_exact_zentao_assignee():
    app, _ = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        selected = await browser.get("/api/v1/sdk/native/requirements?view=all&owner=sdk_owner")
        empty = await browser.get("/api/v1/sdk/native/requirements?view=all&owner=sdk_reviewer")
    assert [item["id"] for item in selected.json()["items"]] == [69]
    assert empty.json()["total"] == 0


@pytest.mark.asyncio
async def test_task_detail_requires_personal_assignment_and_sdk_execution_product():
    app, personal = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        detail = await browser.get("/api/v1/sdk/native/tasks/4")
        outside = await browser.get("/api/v1/sdk/native/tasks/7")
    assert detail.status_code == 200
    assert detail.json()["description"] == "实现接口"
    assert detail.json()["execution_id"] == 9
    assert detail.json()["actions"][0]["actor_name"] == "行云"
    assert outside.status_code == 404
    assert (ACTOR, "GET", "executions/9", None, 1) in personal.calls


@pytest.mark.asyncio
async def test_native_api_reports_zentao_permission_and_outage_without_internal_error():
    class Denied(PersonalClient):
        async def request(self, *args, **kwargs):
            raise ZenTaoRequestRejected("private detail")

    class Offline(PersonalClient):
        async def request(self, *args, **kwargs):
            raise ZenTaoUnavailable("private detail")

    denied, _ = make_app(Denied())
    offline, _ = make_app(Offline())
    async with AsyncClient(transport=ASGITransport(app=denied), base_url="http://test") as browser:
        no_right = await browser.get("/api/v1/sdk/native/requirements")
    async with AsyncClient(transport=ASGITransport(app=offline), base_url="http://test") as browser:
        unavailable = await browser.get("/api/v1/sdk/native/requirements")
    assert (no_right.status_code, no_right.json()) == (403, {"detail": "sdk_zentao_permission_denied"})
    assert (unavailable.status_code, unavailable.json()) == (503, {"detail": "sdk_zentao_unavailable"})
