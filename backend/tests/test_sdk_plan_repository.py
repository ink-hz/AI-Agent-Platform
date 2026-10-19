from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi import HTTPException

from app.sdk_workbench.plan_csv import ParsedPlan, PlanRow, parse_plan_csv
from app.sdk_workbench.plan_repository import PlanRepository
from app.sdk_workbench.repository import SdkRepository
from test_control_plane_migration import control_database


def sample() -> ParsedPlan:
    return parse_plan_csv(("平台,任务,责任人,版本,开始时间,结束时间,优先级,风险点,完成情况,备注\n"
                           "模块A,同名任务,甲,,,,高,,,首行\n"
                           ",同名任务,乙,,,,中,,,第二行\n"
                           "模块B,,,,,,,,,\n").encode())


@pytest.mark.postgres
def test_plan_repository_import_replay_source_and_link(control_database):
    env = control_database["environments"]["production"]
    actor = uuid.uuid4()
    with psycopg.connect(env["admin"]) as db:
        db.execute("insert into platform_control.internal_users(internal_user_id,display_name,status) values (%s,'plan','active')", (actor,))
    repo = PlanRepository(env["urls"]["platform_control_app"])
    plan = sample()
    first = repo.import_plan(actor, "2026.csv", plan)
    assert first["row_count"] == 3 and first["task_count"] == 2
    assert first["module_count"] == 2 and first["sha256"] == plan.sha256
    assert repo.import_plan(actor, "renamed.csv", plan)["id"] == first["id"]
    assert len(repo.list_batches()["items"]) == 1
    rows = repo.list_rows(first["id"], page=1, page_size=2)
    assert rows["total"] == 3 and len(rows["items"]) == 2
    assert rows["items"][0]["cells"][1] == rows["items"][1]["cells"][1] == "同名任务"
    assert rows["items"][1]["cells"][2] == "乙"
    assert repo.list_rows(first["id"], page=2, page_size=2)["items"][0]["kind"] == "heading"
    row_id = rows["items"][0]["id"]
    assert repo.row(row_id)["physical_line"] == 2
    sdk = SdkRepository(env["urls"]["platform_control_app"])
    request_id = uuid.uuid4()
    intent, fresh = sdk.reserve(actor, request_id, "a" * 64, "create_requirement", payload={"title": "同名任务"}, plan_row_id=row_id)
    assert fresh and intent["plan_row_id"] == row_id
    sdk.complete(actor, request_id, {"id": 71})
    linked = repo.row(row_id)
    assert linked["story_id"] == 71 and linked["conversion_state"] == "succeeded"
    assert linked["request_id"] == str(request_id)
    with pytest.raises(HTTPException) as duplicate:
        sdk.reserve(actor, uuid.uuid4(), "b" * 64, "create_requirement", payload={}, plan_row_id=row_id)
    assert duplicate.value.status_code == 409
    rejected_row_id = rows["items"][1]["id"]
    sdk.reserve(actor, uuid.uuid4(), "c" * 64, "create_requirement", payload={}, plan_row_id=rejected_row_id)
    rejected_request_id = repo.row(rejected_row_id)["request_id"]
    sdk.reject(actor, uuid.UUID(rejected_request_id), "sdk_zentao_rejected")
    assert repo.row(rejected_row_id)["conversion_state"] == "rejected"
    replacement, fresh = sdk.reserve(actor, uuid.uuid4(), "d" * 64, "create_requirement", payload={}, plan_row_id=rejected_row_id)
    assert fresh and replacement["state"] == "pending"
    assert repo.row(rejected_row_id)["conversion_state"] == "pending"
    changed = parse_plan_csv(("平台,任务,责任人,版本,开始时间,结束时间,优先级,风险点,完成情况,备注\n"
                             "模块A,新任务,,,,,,,,\n").encode())
    assert repo.import_plan(actor, "2026.csv", changed)["id"] != first["id"]
    long_module = "长" * 1200
    long_plan = parse_plan_csv(("平台,任务,责任人,版本,开始时间,结束时间,优先级,风险点,完成情况,备注\n"
                                f"{long_module},新任务,,,,,,,,\n").encode())
    long_batch = repo.import_plan(actor, "long.csv", long_plan)
    assert repo.list_rows(long_batch["id"])["items"][0]["module"] == long_module


@pytest.mark.postgres
def test_plan_import_is_atomic_and_app_grants_are_narrow(control_database):
    env = control_database["environments"]["preview"]
    actor = uuid.uuid4()
    with psycopg.connect(env["admin"]) as db:
        db.execute("insert into platform_control.internal_users(internal_user_id,display_name,status) values (%s,'plan','active')", (actor,))
    repo = PlanRepository(env["urls"]["platform_control_app_preview"])
    valid = sample()
    bad = ParsedPlan(valid.sha256, (valid.rows[0], PlanRow(3, 3, "task", "模块A", ("x",))), 2, 1)
    with pytest.raises(HTTPException):
        repo.import_plan(actor, "bad.csv", bad)
    assert repo.list_batches()["items"] == []
    with psycopg.connect(env["admin"]) as db:
        role = env["roles"][1]
        assert db.execute("select has_table_privilege(%s,'sdk.plan_rows','update')", (role,)).fetchone() == (False,)
        assert db.execute("select has_table_privilege(%s,'sdk.plan_rows','delete')", (role,)).fetchone() == (False,)
