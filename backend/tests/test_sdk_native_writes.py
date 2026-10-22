from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.native_service import NativeSdkService, _edit_token
from app.sdk_workbench.native_routes import build_sdk_native_router
from app.sdk_workbench.zentao import ZenTaoRequestRejected, ZenTaoUnavailable


ACTOR = AuthContext(UUID("11111111-1111-4111-8111-111111111111"), Role.MEMBER,
                    UUID("22222222-2222-4222-8222-222222222222"), False)
REQUEST = UUID("33333333-3333-4333-8333-333333333333")


class MemoryRepository:
    def __init__(self):
        self.intents = {}
        self.supplements = {}

    def member_account(self, account):
        return account in {"sdk_owner", "sdk_reviewer"}

    def members(self):
        return [{"account": "sdk_owner", "name": "行云"},
                {"account": "sdk_reviewer", "name": "苍渊"}]

    def reserve_create(self, actor, request_id, digest, marker, *, requested_assignee,
                       proposer, device):
        key = actor, request_id
        if key in self.intents:
            old = self.intents[key]
            if old["digest"] != digest:
                raise HTTPException(409, "sdk_request_id_conflict")
            return old, False
        if any(value["actor"] == actor and value["state"] == "pending"
               for value in self.intents.values()):
            raise HTTPException(409, "sdk_create_pending_reconciliation")
        row = {"actor": actor, "request_id": request_id, "digest": digest,
               "marker": marker, "state": "pending", "story_id": None,
               "requested_assignee": requested_assignee, "proposer_name": proposer,
               "device_platform": device,
               "rejection_status": None, "rejection_detail": None}
        self.intents[key] = row
        return row, True

    def pending_create(self, actor):
        return next((value for value in self.intents.values()
                     if value["actor"] == actor and value["state"] == "pending"), None)

    def reject_create(self, actor, request_id, status, detail):
        self.intents[actor, request_id].update(state="rejected", rejection_status=status,
                                                rejection_detail=detail)

    def complete_create(self, actor, request_id, story_id, proposer, device):
        row = self.intents[actor, request_id]
        row.update(state="created", story_id=story_id)
        self.supplements[story_id] = {"proposer_name": proposer, "device_platform": device}

    def save_supplement(self, story_id, proposer, device):
        self.supplements[story_id] = {"proposer_name": proposer, "device_platform": device}

    def source_for(self, story_id):
        return self.supplements.get(story_id)

    def sources_for(self, story_ids):
        return {story_id: self.supplements[story_id] for story_id in story_ids
                if story_id in self.supplements}

    def names_for(self, accounts):
        return {"sdk_owner": "行云", "sdk_reviewer": "苍渊", "employee": "员工"}


class FakePersonalClient:
    def __init__(self):
        self.calls = []
        self.stories = {}
        self.fail_create = False
        self.reject_create = False
        self.fail_after_create = False
        self.fail_assign = False
        self.actions = {}

    async def account(self, actor):
        assert actor.internal_user_id == ACTOR.internal_user_id
        return "employee"

    async def request(self, actor, method, path, payload=None, params=None, *, api_version=2):
        self.calls.append((method, path, payload, api_version))
        if method == "POST" and path == "stories":
            if self.reject_create:
                raise ZenTaoRequestRejected("permission denied")
            if self.fail_create:
                raise ZenTaoUnavailable("timeout")
            story_id = 70 + len(self.stories)
            self.stories[story_id] = {
                "id": story_id, "product": 1, "title": payload["title"],
                "spec": payload["spec"], "verify": payload["verify"],
                "sourceNote": payload["sourceNote"], "keywords": payload["keywords"],
                "status": "reviewing" if payload["reviewer"] else payload["status"],
                "reviewer": payload["reviewer"], "version": 1,
                "openedBy": "employee", "assignedTo": "", "pri": payload["pri"],
            }
            if self.fail_after_create:
                raise ZenTaoUnavailable("response timeout")
            return {"id": story_id}
        if method == "GET" and path.startswith("stories/"):
            story_id = int(path.split("/")[1])
            return {"status": "success", "story": self.stories[story_id],
                    "actions": self.actions.get(story_id, [])}
        if method == "GET" and path == "products/1/stories":
            return {"status": "success", "stories": list(self.stories.values()),
                    "pager": {"pageTotal": 1, "recTotal": len(self.stories)}}
        if method == "POST" and path.endswith("/assign"):
            story_id = int(path.split("/")[1])
            if self.fail_assign:
                raise ZenTaoUnavailable("assignment response timeout")
            self.stories[story_id]["assignedTo"] = payload["assignedTo"]
            return {"id": story_id}
        if method == "POST" and path.endswith("/review"):
            story_id = int(path.split("/")[1])
            self.stories[story_id]["status"] = "active" if payload["result"] == "pass" else "closed"
            self.actions.setdefault(story_id, []).append({"id": 1, "actor": "employee",
                "action": "reviewed", "date": "2026-10-22 12:00:00", "comment": payload["comment"]})
            return {"id": story_id}
        if method == "PUT" and path.startswith("stories/"):
            story_id = int(path.split("/")[1])
            self.stories[story_id].update(title=payload["title"], spec=payload["spec"],
                                          verify=payload["verify"], pri=payload["pri"])
            self.stories[story_id]["version"] += 1
            return {"status": "success"}
        raise AssertionError((method, path))


def form(*, reviewer="sdk_reviewer", assigned_to=None):
    return {"request_id": REQUEST, "title": "新需求", "scenario": "客户现场采集",
            "device_platform": "Gemini 330 / Linux", "acceptance": "连续采集一小时",
            "priority": 2, "business_proposer": "客户A", "reviewer": reviewer,
            "assigned_to": assigned_to}


@pytest.mark.asyncio
async def test_create_requires_explicit_reviewer_before_reserving_or_writing():
    client, repo = FakePersonalClient(), MemoryRepository()
    service = NativeSdkService(client, repo, product_id=1)
    with pytest.raises(HTTPException) as error:
        await service.create_requirement(ACTOR, form(reviewer=None))
    assert (error.value.status_code, error.value.detail) == (422, "sdk_reviewer_required")
    assert client.calls == []
    assert repo.intents == {}


@pytest.mark.asyncio
async def test_create_with_reviewer_uses_personal_session_and_is_idempotent():
    client, repo = FakePersonalClient(), MemoryRepository()
    service = NativeSdkService(client, repo, product_id=1)
    first = await service.create_requirement(ACTOR, form())
    second = await service.create_requirement(ACTOR, form())
    assert first["status"] == second["status"] == "created"
    assert first["requirement"]["status"] == "reviewing"
    assert first["requirement"]["business_proposer"] == "客户A"
    assert first["requirement"]["device_platform"] == "Gemini 330 / Linux"
    assert first["requirement"]["source"]["device_platform"] == "Gemini 330 / Linux"
    assert [call[1] for call in client.calls].count("stories") == 1
    posted = next(call for call in client.calls if call[1] == "stories")
    assert posted[3] == 1
    assert posted[2]["status"] == "active"
    assert posted[2]["reviewer"] == ["sdk_reviewer"]
    assert "needNotReview" not in posted[2]


@pytest.mark.asyncio
async def test_create_with_reviewer_stays_in_review_and_assigns_bound_member():
    client, repo = FakePersonalClient(), MemoryRepository()
    service = NativeSdkService(client, repo, product_id=1)
    result = await service.create_requirement(ACTOR, form(reviewer="sdk_reviewer", assigned_to="sdk_owner"))
    assert result["requirement"]["status"] == "reviewing"
    assert result["requirement"]["assigned_to"] == "sdk_owner"
    posted = next(call for call in client.calls if call[1] == "stories")
    assert posted[2]["reviewer"] == ["sdk_reviewer"]
    assert posted[2]["status"] == "active"


@pytest.mark.asyncio
async def test_selected_unbound_people_are_prepared_before_create_and_repeated_request_is_safe():
    class DirectoryRepository(MemoryRepository):
        def __init__(self):
            super().__init__()
            self.prepared = []

        def ensure_member(self, account, sso_service):
            assert sso_service is not None
            self.prepared.append(account)
            return account

    client, repo = FakePersonalClient(), DirectoryRepository()
    service = NativeSdkService(client, repo, product_id=1, sso_service=object())
    first = await service.create_requirement(ACTOR, form(reviewer="sdk_future", assigned_to="sdk_future"))
    again = await service.create_requirement(ACTOR, form(reviewer="sdk_future", assigned_to="sdk_future"))
    assert first["requirement"]["assigned_to"] == again["requirement"]["assigned_to"] == "sdk_future"
    assert repo.prepared == ["sdk_future"]
    assert [call[1] for call in client.calls].count("stories") == 1


@pytest.mark.asyncio
async def test_unknown_create_result_never_resubmits_same_request():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.fail_create = True
    service = NativeSdkService(client, repo, product_id=1)
    first = await service.create_requirement(ACTOR, form())
    second = await service.create_requirement(ACTOR, form())
    assert first == second == {"status": "reconciliation_required", "request_id": str(REQUEST)}
    assert [call[1] for call in client.calls].count("stories") == 1


@pytest.mark.asyncio
async def test_timeout_after_zentao_commit_is_reconciled_from_unique_marker():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.fail_after_create = True
    service = NativeSdkService(client, repo, product_id=1)
    pending = await service.create_requirement(ACTOR, form())
    recovered = await service.create_requirement(ACTOR, form())
    assert pending["status"] == "reconciliation_required"
    assert recovered["status"] == "created"
    assert recovered["requirement"]["opened_by"] == "employee"
    assert [call[1] for call in client.calls].count("stories") == 1


@pytest.mark.asyncio
async def test_create_remains_pending_until_requested_assignment_is_visible():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.fail_assign = True
    service = NativeSdkService(client, repo, product_id=1)
    pending = await service.create_requirement(ACTOR, form(assigned_to="sdk_owner"))
    repeated = await service.create_requirement(ACTOR, form(assigned_to="sdk_owner"))
    assert pending["status"] == repeated["status"] == "reconciliation_required"
    assert [call[1] for call in client.calls].count("stories") == 1
    assert [call[1] for call in client.calls].count("stories/70/assign") == 1
    client.stories[70]["assignedTo"] = "sdk_owner"
    resolved = await service.create_requirement(ACTOR, form(assigned_to="sdk_owner"))
    assert resolved["status"] == "created"
    assert resolved["requirement"]["assigned_to"] == "sdk_owner"


@pytest.mark.asyncio
async def test_pending_create_blocks_new_request_id_and_can_be_recovered_without_original_form():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.fail_after_create = True
    service = NativeSdkService(client, repo, product_id=1)
    pending = await service.create_requirement(ACTOR, form())
    assert pending["status"] == "reconciliation_required"
    with pytest.raises(HTTPException) as duplicate:
        await service.create_requirement(ACTOR, {**form(), "request_id": UUID("44444444-4444-4444-8444-444444444444")})
    assert (duplicate.value.status_code, duplicate.value.detail) == (409, "sdk_create_pending_reconciliation")
    recovered = await service.pending_create(ACTOR)
    assert recovered["status"] == "created"
    assert recovered["story_id"] == 70
    assert [call[1] for call in client.calls].count("stories") == 1


@pytest.mark.asyncio
async def test_pending_intent_blocks_new_request_before_other_target_is_provisioned():
    class TrackingRepository(MemoryRepository):
        def __init__(self):
            super().__init__()
            self.provisioned = []

        def ensure_member(self, account, sso_service):
            self.provisioned.append(account)
            return account

    client, repo = FakePersonalClient(), TrackingRepository()
    client.fail_create = True
    service = NativeSdkService(client, repo, product_id=1, sso_service=object())
    assert (await service.create_requirement(ACTOR, form()))["status"] == "reconciliation_required"
    with pytest.raises(HTTPException) as blocked:
        await service.create_requirement(ACTOR, {**form(assigned_to="sdk_new_person"),
            "request_id": UUID("44444444-4444-4444-8444-444444444444")})
    assert blocked.value.detail == "sdk_create_pending_reconciliation"
    assert repo.provisioned == ["sdk_reviewer"]


@pytest.mark.asyncio
async def test_failed_target_provision_rejects_intent_without_posting_story():
    class FailingRepository(MemoryRepository):
        fail = True

        def ensure_member(self, account, sso_service):
            if self.fail:
                raise HTTPException(503, "sdk_provision_reconcile_required")
            return account

    client, repo = FakePersonalClient(), FailingRepository()
    service = NativeSdkService(client, repo, product_id=1, sso_service=object())
    for _ in range(2):
        with pytest.raises(HTTPException) as failure:
            await service.create_requirement(ACTOR, form())
        assert (failure.value.status_code, failure.value.detail) == (503, "sdk_create_rejected")
    assert repo.pending_create(ACTOR.internal_user_id) is None
    assert not any(call[1] == "stories" for call in client.calls)
    repo.fail = False
    result = await service.create_requirement(ACTOR, {**form(),
        "request_id": UUID("44444444-4444-4444-8444-444444444444")})
    assert result["status"] == "created"


@pytest.mark.asyncio
async def test_definitive_zen_rejection_releases_pending_guard_for_corrected_request():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.reject_create = True
    service = NativeSdkService(client, repo, product_id=1)
    with pytest.raises(HTTPException) as rejected:
        await service.create_requirement(ACTOR, form())
    assert (rejected.value.status_code, rejected.value.detail) == (403, "sdk_create_rejected")
    assert repo.pending_create(ACTOR.internal_user_id) is None
    client.reject_create = False
    next_form = {**form(), "request_id": UUID("44444444-4444-4444-8444-444444444444")}
    created = await service.create_requirement(ACTOR, next_form)
    assert created["status"] == "created"


@pytest.mark.asyncio
async def test_stale_edit_and_out_of_product_assignment_are_rejected_before_write():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.stories[71] = {"id": 71, "product": 1, "title": "原需求", "spec": "原场景",
                          "verify": "原验收", "status": "reviewing", "version": 2,
                          "sourceNote": "", "openedBy": "employee", "assignedTo": "",
                          "pri": 2, "reviewer": ["sdk_reviewer"]}
    client.stories[72] = {**client.stories[71], "id": 72, "product": 5}
    service = NativeSdkService(client, repo, product_id=1)
    with pytest.raises(HTTPException) as conflict:
        await service.edit_requirement(ACTOR, 71, expected_version=1,
                                       expected_token=_edit_token(client.stories[71]), title="改",
                                       scenario="场景", device_platform="设备", acceptance="验收",
                                       priority=2, business_proposer="客户")
    assert (conflict.value.status_code, conflict.value.detail) == (409, "sdk_requirement_version_conflict")
    with pytest.raises(HTTPException) as wrong_product:
        await service.assign_requirement(ACTOR, 72, expected_version=2,
                                         expected_token=_edit_token(client.stories[72]), assigned_to="sdk_owner")
    assert wrong_product.value.status_code == 404
    assert all(call[0] == "GET" for call in client.calls)


@pytest.mark.asyncio
async def test_legacy_q4_story_edit_accepts_unknown_device_without_inventing_one():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.stories[71] = {"id": 71, "product": 1, "title": "原需求", "spec": "原场景",
                          "verify": "原验收", "status": "reviewing", "version": 2,
                          "sourceNote": "2026年Q4上线版本", "openedBy": "employee",
                          "assignedTo": "", "pri": 2, "reviewer": ["sdk_reviewer"]}
    service = NativeSdkService(client, repo, product_id=1)
    edited = await service.edit_requirement(ACTOR, 71, expected_version=2,
                                            expected_token=_edit_token(client.stories[71]), title="新标题",
        scenario="新场景", device_platform=None, acceptance="新验收", priority=1,
        business_proposer=None)
    assert edited["title"] == "新标题"
    assert edited["device_platform"] is None
    assert edited["source"]["device_platform"] is None


@pytest.mark.asyncio
async def test_edit_rejects_stale_story_when_zentao_version_does_not_change():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.stories[71] = {"id": 71, "product": 1, "title": "原需求", "spec": "原场景",
                          "verify": "原验收", "status": "reviewing", "version": 1,
                          "sourceNote": "", "openedBy": "employee", "assignedTo": "",
                          "pri": 2, "reviewer": ["sdk_reviewer"]}
    service = NativeSdkService(client, repo, product_id=1)
    token = _edit_token(client.stories[71])
    client.stories[71]["spec"] = "其他人已经修改"
    with pytest.raises(HTTPException) as stale:
        await service.edit_requirement(ACTOR, 71, expected_version=1, expected_token=token,
            title="我的新标题", scenario="我的场景", device_platform=None,
            acceptance="我的验收", priority=2, business_proposer=None)
    assert (stale.value.status_code, stale.value.detail) == (409, "sdk_requirement_version_conflict")
    assert not any(call[0] == "PUT" for call in client.calls)


@pytest.mark.asyncio
async def test_review_writes_employee_action_and_reads_current_status():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.stories[71] = {"id": 71, "product": 1, "title": "待评审", "spec": "场景",
                          "verify": "验收", "status": "reviewing", "version": 1,
                          "sourceNote": "", "openedBy": "employee", "assignedTo": "",
                          "pri": 2, "reviewer": ["employee"]}
    service = NativeSdkService(client, repo, product_id=1)
    result = await service.review_requirement(ACTOR, 71, expected_version=1,
                                              expected_token=_edit_token(client.stories[71]),
                                              decision="pass", comment="可实施")
    assert result["status"] == "active"
    posted = next(call for call in client.calls if call[1] == "stories/71/review")
    assert posted[2]["result"] == "pass"
    assert posted[2]["comment"] == "可实施"
    assert posted[3] == 1
    assert result["actions"][0]["actor"] == "employee"


@pytest.mark.asyncio
async def test_native_write_routes_validate_payload_and_return_detail():
    client, repo = FakePersonalClient(), MemoryRepository()
    app = FastAPI()
    app.state.sdk_native_service = NativeSdkService(client, repo, product_id=1)

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.auth_context = ACTOR
        return await call_next(request)

    app.include_router(build_sdk_native_router())
    payload = {**form(), "request_id": str(REQUEST)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        members = await browser.get("/api/v1/sdk/native/members")
        invalid = await browser.post("/api/v1/sdk/native/requirements", json={**payload, "priority": 9})
        created = await browser.post("/api/v1/sdk/native/requirements", json=payload)
        repeated = await browser.post("/api/v1/sdk/native/requirements", json=payload)
        edited = await browser.put(f"/api/v1/sdk/native/requirements/{created.json()['story_id']}",
            json={"expected_version": 1,
                  "expected_token": created.json()["requirement"]["edit_token"],
                  "title": "新需求", "scenario": "客户现场采集",
                  "device_platform": None, "acceptance": "连续采集一小时",
                  "priority": 2, "business_proposer": "客户A"})
    assert members.json()["items"] == repo.members()
    assert invalid.status_code == 422
    assert created.status_code == repeated.status_code == 201
    assert edited.status_code == 200
    assert edited.json()["device_platform"] is None
    assert created.json()["story_id"] == repeated.json()["story_id"]
    assert created.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_pending_create_route_reports_reconciliation_without_resubmitting():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.fail_create = True
    app = FastAPI()
    app.state.sdk_native_service = NativeSdkService(client, repo, product_id=1)

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.auth_context = ACTOR
        return await call_next(request)

    app.include_router(build_sdk_native_router())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        created = await browser.post("/api/v1/sdk/native/requirements",
                                     json={**form(), "request_id": str(REQUEST)})
        pending = await browser.get("/api/v1/sdk/native/requirements/pending-create")
    assert created.status_code == pending.status_code == 202
    assert pending.json() == {"status": "reconciliation_required", "request_id": str(REQUEST)}
    assert [call[1] for call in client.calls].count("stories") == 1


@pytest.mark.asyncio
async def test_rejected_request_id_replays_same_definitive_permission_error():
    client, repo = FakePersonalClient(), MemoryRepository()
    client.reject_create = True
    app = FastAPI()
    app.state.sdk_native_service = NativeSdkService(client, repo, product_id=1)

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.auth_context = ACTOR
        return await call_next(request)

    app.include_router(build_sdk_native_router())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        first = await browser.post("/api/v1/sdk/native/requirements",
                                   json={**form(), "request_id": str(REQUEST)})
        replay = await browser.post("/api/v1/sdk/native/requirements",
                                    json={**form(), "request_id": str(REQUEST)})
    assert first.status_code == replay.status_code == 403
    assert first.json() == replay.json() == {"detail": "sdk_create_rejected"}
    assert [call[1] for call in client.calls].count("stories") == 1
