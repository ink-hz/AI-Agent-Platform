"""The Q4 source must survive a native ZenTao backlog migration unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.sdk_q4_operationalize import (
    OperationalConflict,
    build_source_map,
    check_story_after,
    check_story_before,
    module_name,
    plan_payload,
)
from tools.sdk_q4_repair import parse_q4_workbook, validate_manifest

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = next((path for path in (ROOT.parent, ROOT.parents[2])
                  if (path / "AI-SDK-Platform").is_dir()), ROOT.parent)
WORKBOOK = WORKSPACE / "AI-SDK-Platform" / "OrbbecSDK  v2 2026年任务计划.xlsx"
MANIFEST = json.loads((ROOT / "backend/tools/sdk_q4_manifest.json").read_text())


def _real_items():
    if not WORKBOOK.is_file():
        pytest.skip("Q4 source workbook is held in the sibling SDK workspace")
    payload = WORKBOOK.read_bytes()
    rows = parse_q4_workbook(payload)
    return rows, build_source_map(rows, validate_manifest(payload, rows, MANIFEST))


def test_real_q4_source_maps_exact_existing_ids_without_test_stories():
    _, mapped = _real_items()

    assert len(mapped) == 67
    assert {item.story_id for item in mapped} == set(range(3, 70))
    assert len({item.module_name for item in mapped}) == 16
    assert sum(len(item.row.links) for item in mapped) == 8
    assert mapped[0].excel_row == 2
    assert mapped[0].story_id == 3
    assert mapped[-1].excel_row == 95
    assert mapped[-1].story_id == 69


def test_source_module_name_keeps_project_discriminator_but_not_version_schedule():
    assert module_name("OrbbecSDK 功能及文档优化\n1. 目标: 12月底\n版本1: 10/28") == "OrbbecSDK 功能及文档优化"
    assert module_name("MB2605\n\n(双目RGB，平台计算Depth)") == "MB2605 (双目RGB，平台计算Depth)"


def test_plan_payload_states_baseline_not_release_commitment():
    payload = plan_payload()
    assert payload["title"] == "2026年Q4上线版本"
    assert payload["begin"] == "2026-10-01"
    assert payload["end"] == "2026-12-31"
    assert "需求范围" in payload["desc"]
    assert "不代表" in payload["desc"]


def _story(item):
    return {
        "id": item.story_id, "product": 1, "deleted": False,
        "title": item.row.title, "sourceNote": f"2026年Q4上线版本｜{item.module_name}｜Excel第{item.excel_row}行",
        "module": 0, "plan": "0", "status": "reviewing", "version": 2,
        "reviewer": ["cangyuan"], "assignedTo": {"account": "sdk_owner", "realname": item.row.owner},
        "assignedDate": "2026-10-20T12:00:00Z", "spec": "source spec",
        "verify": "source verify", "pri": 1, "stage": "wait",
    }


def test_preflight_rejects_manual_title_and_assignment_changes():
    _, items = _real_items()
    item = items[0]
    good = _story(item)
    check_story_before(item, good)

    with pytest.raises(OperationalConflict, match="title"):
        check_story_before(item, {**good, "title": "人工改过标题"})
    with pytest.raises(OperationalConflict, match="reviewer"):
        check_story_before(item, {**good, "reviewer": ["other"]})
    with pytest.raises(OperationalConflict, match="source"):
        check_story_before(item, {**good, "sourceNote": ""})
    with pytest.raises(OperationalConflict, match="owner"):
        check_story_before(item, {**good, "assignedTo": {"account": "other", "realname": "他人"}})


def test_readback_rejects_side_effects_on_owner_and_body():
    _, items = _real_items()
    item = items[0]
    before = _story(item)
    after = {**before, "module": 101, "plan": "7", "stage": "planned", "status": "draft", "reviewer": []}
    check_story_after(before, after, module_id=101, plan_id=7, draft=True)

    with pytest.raises(OperationalConflict, match="assignedTo"):
        check_story_after(before, {**after, "assignedTo": None}, module_id=101, plan_id=7, draft=True)
    with pytest.raises(OperationalConflict, match="spec"):
        check_story_after(before, {**after, "spec": "changed"}, module_id=101, plan_id=7, draft=True)
