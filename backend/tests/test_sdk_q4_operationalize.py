"""The Q4 source must survive a native ZenTao backlog migration unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.sdk_q4_operationalize import (
    NativeZen,
    OperationalConflict,
    SourceItem,
    build_source_map,
    check_story_after,
    check_story_before,
    module_name,
    operate,
    plan_payload,
    source_gap_register,
)
from tools.sdk_q4_repair import (
    Q4Row,
    parse_q4_workbook,
    render_story,
    validate_manifest,
)

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
    rendered = render_story(item.row)
    return {
        "id": item.story_id, "product": 1, "deleted": False,
        "title": item.row.title, "sourceNote": rendered["sourceNote"],
        "module": 0, "plan": "0", "status": "reviewing", "version": 2,
        "reviewer": ["cangyuan"], "assignedTo": {"account": "sdk_owner", "realname": item.row.owner},
        "assignedDate": "2026-10-20T12:00:00Z", "spec": rendered["spec"],
        "verify": rendered["verify"], "source": rendered["source"],
        "pri": 2, "stage": "wait", "openedBy": {"account": "sdk-api"},
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
    with pytest.raises(OperationalConflict, match="source"):
        check_story_before(item, {**good, "sourceNote": good["sourceNote"].replace(
            good["sourceNote"].split("｜")[1], "伪造栏目")})
    with pytest.raises(OperationalConflict, match="source_field"):
        check_story_before(item, {**good, "spec": "人工改过正文"})
    with pytest.raises(OperationalConflict, match="owner"):
        check_story_before(item, {**good, "assignedTo": {"account": "other", "realname": "他人"}})


def test_readback_rejects_side_effects_on_owner_and_body():
    _, items = _real_items()
    item = items[0]
    before = _story(item)
    after = {**before, "module": 101, "plan": "7", "status": "draft", "reviewer": []}
    check_story_after(before, after, module_id=101, plan_id=7, draft=True)

    with pytest.raises(OperationalConflict, match="assignedTo"):
        check_story_after(before, {**after, "assignedTo": None}, module_id=101, plan_id=7, draft=True)
    with pytest.raises(OperationalConflict, match="spec"):
        check_story_after(before, {**after, "spec": "changed"}, module_id=101, plan_id=7, draft=True)
    with pytest.raises(OperationalConflict, match="stage"):
        check_story_after(before, {**after, "stage": "released"}, module_id=101, plan_id=7, draft=True)
    with pytest.raises(OperationalConflict, match="category"):
        check_story_after(before, {**after, "category": "bug"}, module_id=101, plan_id=7, draft=True)


def _fixture_item() -> SourceItem:
    row = Q4Row(2, "SDK 核心", "时间戳需求", "周公", "版本1", "2026-10-08",
                "2026-10-31", "高", "固件配合", "开发中", "待测", ())
    return SourceItem(row, 3, "SDK 核心")


def test_self_contained_source_guards_and_gap_register():
    item = _fixture_item()
    story = _story(item)
    story["pri"] = 1
    check_story_before(item, story)
    with pytest.raises(OperationalConflict, match="source_priority"):
        check_story_before(item, {**story, "pri": 3})
    with pytest.raises(OperationalConflict, match="source_creator"):
        check_story_before(item, {**story, "openedBy": {"account": "other"}})
    register = source_gap_register((item,), {3: story})
    assert register["rows"][0]["source"]["cells"]["H"] == "固件配合"
    assert register["rows"][0]["zentao"]["assignee_realname"] == "周公"
    assert "choose_reviewer" in register["rows"][0]["business_decisions_needed"]


def test_plan_membership_rejects_duplicate_or_extra_stories():
    zen = object.__new__(NativeZen)
    zen.api = lambda *_args: {"stories": [{"id": 3}, {"id": 4}]}
    assert zen.plan_stories(1) == {3, 4}
    zen.api = lambda *_args: {"stories": [{"id": 3}, {"id": 3}]}
    with pytest.raises(OperationalConflict, match="duplicate"):
        zen.plan_stories(1)


def test_verify_requires_exact_plan_membership(tmp_path):
    item = _fixture_item()
    story = {**_story(item), "pri": 1, "module": 5, "plan": "1",
             "status": "draft", "reviewer": [], "stage": "planned"}

    class FakeZen:
        def story(self, story_id):
            if story_id == 3:
                return dict(story)
            return {"id": story_id, "plan": "0"}

        def plan(self, *, create):
            return 1

        def modules(self, expected_names):
            return {"SDK 核心": 5}

        def plan_stories(self, plan_id):
            return {3, 70}

    with pytest.raises(OperationalConflict, match="plan_members_mismatch"):
        operate((item,), FakeZen(), tmp_path, phase="verify", apply=False, review_mode="pending")


def test_completed_link_and_draft_phases_write_nothing_on_resume(tmp_path):
    item = _fixture_item()
    story = {**_story(item), "pri": 1, "module": 5, "plan": "1",
             "status": "draft", "reviewer": [], "stage": "planned"}

    class FakeZen:
        def story(self, story_id):
            return dict(story) if story_id == 3 else {"id": story_id, "plan": "0"}

        def plan(self, *, create):
            return 1

        def modules(self, expected_names):
            return {"SDK 核心": 5}

        def plan_stories(self, plan_id):
            return {3}

        def link_plan(self, *_args):
            raise AssertionError("must not relink")

        def batch_edit(self, *_args, **_kwargs):
            raise AssertionError("must not edit")

        def recall(self, *_args):
            raise AssertionError("must not recall")

    zen = FakeZen()
    link = operate((item,), zen, tmp_path, phase="link", apply=True, review_mode="pending")
    draft = operate((item,), zen, tmp_path, phase="draft", apply=True, review_mode="pending")
    assert (link["written"], draft["written"]) == (0, 0)
    register = json.loads((tmp_path / "source-gap-register.json").read_text())
    assert register["source_rows"] == 1


def test_plan_conflict_and_nested_module_are_rejected():
    zen = object.__new__(NativeZen)
    zen.plans = lambda: [{"id": 1, "title": "2026年Q4上线版本", "desc": "other"}]
    with pytest.raises(OperationalConflict, match="plan_conflict"):
        zen.plan(create=True)
    zen.api = lambda *_args, **_kwargs: {"modules": [{
        "id": 9, "root": 1, "type": "story", "name": "SDK 核心",
        "parent": 2, "deleted": False,
    }]}
    with pytest.raises(OperationalConflict, match="parent_conflict"):
        zen.modules({"SDK 核心"})
