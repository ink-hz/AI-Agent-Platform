from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.plan_service import PlanService
from app.sdk_workbench.routes import build_sdk_router
from app.sdk_workbench.service import SdkService
from test_sdk_routes import ACTOR, Repository, ZenTao


CSV = ("平台,任务,责任人,版本,开始时间,结束时间,优先级,风险点,完成情况,备注\n"
       "SDK,同名任务,甲,,,,高,,,原始备注\n").encode()


class Plans:
    def __init__(self):
        self.saved = None

    def import_plan(self, actor, filename, parsed):
        self.saved = (actor, filename, parsed)
        return {"id": 1, "row_count": len(parsed.rows), "task_count": parsed.task_count, "sha256": parsed.sha256}

    def list_batches(self, *, page=1):
        return {"items": [], "page": page, "total": 0}

    def list_rows(self, import_id, *, page=1, module=None, priority=None, owner=None):
        return {"items": [], "page": page, "total": 0}

    def row(self, row_id):
        return {"id": row_id, "kind": "task", "cells": ["SDK", "同名任务", "甲", "", "", "", "高", "", "", "原始备注"], "story_id": None, "request_id": None, "conversion_state": None}


def make_app(repo, plans, *, role=Role.MEMBER, stale=False, actor=ACTOR, zentao=False):
    app = FastAPI()
    app.state.sdk_plan_service = PlanService(repo, plans)
    app.state.sdk_service = SdkService(repo, ZenTao(), product_id=7, execution_id=9) if zentao else None

    @app.middleware("http")
    async def identity(request, call_next):
        if actor is not None:
            request.state.auth_context = AuthContext(actor, role, uuid4(), stale)
        return await call_next(request)

    app.include_router(build_sdk_router())
    return app


@pytest.mark.asyncio
async def test_plan_mode_import_browse_roles_and_boundaries():
    repo, plans = Repository(), Plans()
    app = make_app(repo, plans)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me = await client.get("/api/v1/sdk/me")
        upload = await client.post("/api/v1/sdk/plan-imports?filename=2026.csv", content=CSV, headers={"Content-Type": "text/csv"})
        batches = await client.get("/api/v1/sdk/plan-imports")
        rows = await client.get("/api/v1/sdk/plan-imports/1/rows?module=SDK&priority=高&owner=甲")
        detail = await client.get("/api/v1/sdk/plan-rows/1")
        requirement = await client.get("/api/v1/sdk/requirements")
    assert me.json()["requirements_enabled"] is False
    assert upload.status_code == 201 and upload.json()["task_count"] == 1
    assert plans.saved[0] == ACTOR and plans.saved[1] == "2026.csv"
    assert batches.status_code == rows.status_code == detail.status_code == 200
    assert requirement.status_code == 503
    assert batches.headers["cache-control"] == "no-store"
    assert (await AsyncClient(transport=ASGITransport(app=make_app(repo, Plans(), actor=None)), base_url="http://test").get("/api/v1/sdk/plan-imports")).status_code == 401
    repo.roles.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/sdk/plan-imports")).status_code == 403


@pytest.mark.asyncio
async def test_plan_upload_rejects_bad_files_and_stale_identity():
    repo, plans = Repository(), Plans()
    async with AsyncClient(transport=ASGITransport(app=make_app(repo, plans)), base_url="http://test") as client:
        assert (await client.post("/api/v1/sdk/plan-imports?filename=x.txt", content=CSV, headers={"Content-Type": "text/plain"})).status_code == 415
        assert (await client.post("/api/v1/sdk/plan-imports?filename=x.csv", content=b"invalid", headers={"Content-Type": "text/csv"})).status_code == 422
        assert (await client.post("/api/v1/sdk/plan-imports?filename=x.csv", content=b"x" * (2 * 1024 * 1024 + 1), headers={"Content-Type": "text/csv"})).status_code == 413
    assert plans.saved is None
    async with AsyncClient(transport=ASGITransport(app=make_app(repo, plans, stale=True)), base_url="http://test") as client:
        assert (await client.post("/api/v1/sdk/plan-imports?filename=x.csv", content=CSV, headers={"Content-Type": "text/csv"})).status_code == 503


@pytest.mark.asyncio
async def test_plan_conversion_requires_complete_fields_and_enabled_zentao():
    repo, plans = Repository(), Plans()
    body = {"request_id": str(uuid4()), "title": "同名任务", "scenario": "客户现场", "device_platform": "Linux", "acceptance": "连续运行"}
    async with AsyncClient(transport=ASGITransport(app=make_app(repo, plans)), base_url="http://test") as client:
        assert (await client.post("/api/v1/sdk/plan-rows/1/requirements", json=body)).status_code == 503
    async with AsyncClient(transport=ASGITransport(app=make_app(repo, plans, zentao=True)), base_url="http://test") as client:
        assert (await client.post("/api/v1/sdk/plan-rows/1/requirements", json={**body, "acceptance": ""})).status_code == 422
        created = await client.post("/api/v1/sdk/plan-rows/1/requirements", json=body)
        replay = await client.post("/api/v1/sdk/plan-rows/1/requirements", json=body)
    assert created.status_code == replay.status_code == 201
    assert created.json() == replay.json() == {"id": 12}
    assert repo.intents[(ACTOR, __import__("uuid").UUID(body["request_id"]))]["request_payload"]["plan_row_id"] == 1
