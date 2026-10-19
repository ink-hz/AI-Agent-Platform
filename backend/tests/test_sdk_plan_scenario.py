"""Opt-in real source CSV through HTTP, enterprise middleware and PostgreSQL."""

from __future__ import annotations

from collections import Counter
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.authorization import AuthorizationService
from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.plan_repository import PlanRepository
from app.sdk_workbench.plan_service import PlanService
from app.sdk_workbench.repository import SdkRepository
from app.sdk_workbench.routes import build_sdk_router
from app.sdk_workbench.service import SdkService
from test_control_plane_migration import control_database
from test_dingtalk_auth_api import FakeAuth
from test_sdk_routes import ZenTao


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_real_2026_plan_import_to_reconciled_requirement_and_task(control_database):
    source_path = os.getenv("SDK_PLAN_SOURCE_CSV")
    if not source_path:
        pytest.skip("set SDK_PLAN_SOURCE_CSV to run the real 2026 intake scenario")
    source = Path(source_path)
    payload = source.read_bytes()
    environment = control_database["environments"]["production"]
    owner, contributor, reviewer, outsider = (uuid4() for _ in range(4))
    with psycopg.connect(environment["admin"]) as db:
        for user, name in ((owner, "owner"), (contributor, "contributor"), (reviewer, "reviewer"), (outsider, "outsider")):
            db.execute("insert into platform_control.internal_users(internal_user_id,display_name,status) values (%s,%s,'active')", (user, name))

    members = SdkRepository(environment["urls"]["platform_control_app"])
    plans = PlanRepository(environment["urls"]["platform_control_app"])
    zentao = ZenTao()
    app = FastAPI()
    app.state.sdk_plan_service = PlanService(members, plans)
    app.state.sdk_service = SdkService(members, zentao, product_id=7, execution_id=9)
    app.include_router(build_sdk_router())
    auth = FakeAuth()
    app.add_middleware(IdentitySecurityMiddleware, auth=auth, public_assets=frozenset(),
                       authorization=AuthorizationService(None), routes=tuple(app.router.routes))
    cookies = {auth.cookie_name: "valid-cookie", auth.csrf_cookie_name: auth.csrf}
    mutation_headers = {"Origin": auth.public_base_url, "X-CSRF-Token": auth.csrf}

    def identity(user, role=Role.MEMBER, stale=False):
        auth.context = AuthContext(user, role, uuid4(), stale)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=auth.public_base_url, cookies=cookies) as client:
        identity(owner, Role.PLATFORM_OWNER)
        for user, role in ((contributor, "contributor"), (reviewer, "reviewer")):
            granted = await client.post("/api/v1/sdk/members", headers=mutation_headers, json={
                "request_id": str(uuid4()), "internal_user_id": str(user), "role": role,
            })
            assert granted.status_code == 201, granted.text
        assert (await client.get("/api/v1/sdk/plan-imports")).status_code == 403

        identity(outsider)
        assert (await client.get("/api/v1/sdk/plan-imports")).status_code == 403
        identity(contributor)
        assert (await client.post("/api/v1/sdk/plan-imports?filename=source.csv", content=payload,
                                  headers={"Content-Type": "text/csv"})).status_code == 403
        assert (await client.post("/api/v1/sdk/plan-imports?filename=source.csv", content=payload,
                                  headers={"Content-Type": "text/csv", "Origin": "https://evil.example", "X-CSRF-Token": auth.csrf})).status_code == 403
        upload = await client.post("/api/v1/sdk/plan-imports?filename=source.csv", content=payload,
                                   headers={**mutation_headers, "Content-Type": "text/csv"})
        assert upload.status_code == 201, upload.text
        batch = upload.json()
        assert (batch["sha256"], batch["row_count"], batch["task_count"], batch["module_count"]) == (
            hashlib.sha256(payload).hexdigest(), 69, 67, 17,
        )
        again = await client.post("/api/v1/sdk/plan-imports?filename=renamed.csv", content=payload,
                                  headers={**mutation_headers, "Content-Type": "text/csv"})
        assert again.status_code == 201 and again.json()["id"] == batch["id"]
        assert (await client.get("/api/v1/sdk/plan-imports")).json()["total"] == 1
        first = await client.get(f"/api/v1/sdk/plan-imports/{batch['id']}/rows?page=1")
        second = await client.get(f"/api/v1/sdk/plan-imports/{batch['id']}/rows?page=2")
        rows = first.json()["items"] + second.json()["items"]
        assert first.json()["total"] == 69 and len(rows) == 69
        assert sum(row["kind"] == "task" for row in rows) == 67
        assert len({row["module"] for row in rows}) == 17
        assert any(count > 1 for count in Counter(row["cells"][1] for row in rows if row["kind"] == "task").values())
        assert all(row["sha256"] == batch["sha256"] for row in rows)
        task_row = next(row for row in rows if row["kind"] == "task" and len(row["cells"][1]) <= 200)
        source_detail = await client.get(f"/api/v1/sdk/plan-rows/{task_row['id']}")
        assert source_detail.json()["cells"] == task_row["cells"]
        identity(contributor, stale=True)
        assert (await client.post("/api/v1/sdk/plan-imports?filename=x.csv", content=payload,
                                  headers={**mutation_headers, "Content-Type": "text/csv"})).status_code == 503
        identity(contributor)
        request = {"request_id": str(uuid4()), "title": task_row["cells"][1],
                   "scenario": "SDK 客户端连续采集场景", "device_platform": "Gemini 2 / Linux",
                   "acceptance": "连续采集 60 秒、时间戳单调且无崩溃"}
        path = f"/api/v1/sdk/plan-rows/{task_row['id']}/requirements"
        assert (await client.post(path, headers=mutation_headers, json={**request, "acceptance": ""})).status_code == 422
        zentao.fail = True
        uncertain = await client.post(path, headers=mutation_headers, json=request)
        assert uncertain.status_code == 503 and uncertain.json()["detail"] == "sdk_reconcile_required"
        assert (await client.get(f"/api/v1/sdk/plan-rows/{task_row['id']}")).json()["story_id"] is None
        assert (await client.post(path, headers=mutation_headers, json=request)).status_code == 409
        identity(owner, Role.PLATFORM_OWNER)
        bound = await client.post(f"/api/v1/sdk/reconciliation/{contributor}/{request['request_id']}/bind",
                                  headers=mutation_headers, json={"request_id": str(uuid4()), "external_id": 12})
        assert bound.status_code == 200, bound.text
        identity(contributor)
        assert (await client.post(path, headers=mutation_headers, json=request)).json() == {"id": 12}
        assert (await client.get(f"/api/v1/sdk/plan-rows/{task_row['id']}")).json()["story_id"] == 12
        assert (await client.post(path, headers=mutation_headers, json={**request, "request_id": str(uuid4())})).status_code == 409
        assert len([entry for entry in zentao.created if entry[0] == "story"]) == 1
        before_review = await client.post("/api/v1/sdk/requirements/12/tasks", headers=mutation_headers,
                                          json={"request_id": str(uuid4()), "name": "开发", "description": "验证采集"})
        assert before_review.status_code == 409
        identity(reviewer)
        reviewed = await client.post("/api/v1/sdk/requirements/12/reviews", headers=mutation_headers,
                                     json={"request_id": str(uuid4()), "decision": "approved", "reason": "验收条件明确"})
        assert reviewed.status_code == 201, reviewed.text
        identity(contributor)
        zentao.fail = False
        created_task = await client.post("/api/v1/sdk/requirements/12/tasks", headers=mutation_headers,
                                         json={"request_id": str(uuid4()), "name": "开发", "description": "验证采集"})
        assert created_task.status_code == 201 and created_task.json() == {"id": 31}
    print({"sha256": batch["sha256"], "row_count": batch["row_count"], "task_count": batch["task_count"],
           "module_count": batch["module_count"], "story_id": 12, "task_id": 31, "reconciled": True})
