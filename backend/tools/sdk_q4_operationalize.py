"""Operate the reviewed 2026 Q4 source as one native ZenTao product backlog.

Dry run is the default. This tool never creates, deletes, approves, or
reassigns a story. It uses the existing story IDs and native ZenTao actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import httpx

from tools.sdk_q4_repair import (
    PRIORITY,
    Q4Row,
    _normalized,
    parse_q4_workbook,
    render_story,
    validate_manifest,
)

SHEET = "2026年Q4上线版本"
WORKBOOK_SHA = "402030f9a76fb966b23fc6c9ad843d5f9abd85bcdce1d685835440ae6c51b7d0"
# Reviewed online version vector before the 2026-10-22 one-time write.
INITIAL_VERSIONS_SHA = "ee2a336ba5201c12ce8dfd55e8db3b587269a344a8968687551fde009a4fe252"
PLAN_TITLE = SHEET
_PROTECTED = ("id", "product", "title", "spec", "verify", "pri", "version",
              "sourceNote", "assignedTo", "assignedDate", "deleted", "branch",
              "roadmap", "color", "estimate", "category", "source", "keywords",
              "closedBy", "closedReason", "openedBy", "openedDate")


class OperationalConflict(RuntimeError):
    """Stop rather than overwriting live work or guessing an uncertain result."""


@dataclass(frozen=True)
class SourceItem:
    row: Q4Row
    story_id: int
    module_name: str

    @property
    def excel_row(self) -> int:
        return self.row.excel_row


def module_name(raw: str) -> str:
    if raw.startswith("OrbbecSDK 功能及文档优化"):
        return "OrbbecSDK 功能及文档优化"
    return " ".join(raw.split())


def build_source_map(rows: tuple[Q4Row, ...], identities: dict[int, dict]) -> tuple[SourceItem, ...]:
    if len(rows) != 67 or set(identities) != {row.excel_row for row in rows}:
        raise OperationalConflict("q4_source_coverage")
    items = tuple(SourceItem(row, int(identities[row.excel_row]["id"]), module_name(row.module))
                  for row in rows)
    if ({item.story_id for item in items} != set(range(3, 70))
            or len({item.module_name for item in items}) != 16
            or sum(len(item.row.links) for item in items) != 8):
        raise OperationalConflict("q4_source_shape")
    return items


def plan_payload() -> dict[str, str]:
    return {
        "title": PLAN_TITLE,
        "begin": "2026-10-01",
        "end": "2026-12-31",
        "desc": ("2026 SDK 原始工作簿的 Q4 需求范围；来源工作表：2026年Q4上线版本；"
                 f"原件 SHA-256：{WORKBOOK_SHA}。"
                 "仅用于需求梳理，不代表发布承诺、开发完成或验收通过。"),
    }


def _account(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("account") or "")
    return str(value or "")


def _same(field: str, before: dict, after: dict) -> bool:
    if field in {"assignedTo", "openedBy", "closedBy"}:
        return _account(before.get(field)) == _account(after.get(field))
    return before.get(field) == after.get(field)


def check_story_before(item: SourceItem, story: dict) -> None:
    if int(story.get("id", 0)) != item.story_id or int(story.get("product", 0)) != 1 or story.get("deleted") in (1, "1", True):
        raise OperationalConflict(f"q4_identity:{item.excel_row}")
    if " ".join(str(story.get("title") or "").split()) != " ".join(item.row.title.split()):
        raise OperationalConflict(f"q4_title:{item.excel_row}")
    expected = render_story(item.row)
    if story.get("sourceNote") != expected["sourceNote"]:
        raise OperationalConflict(f"q4_source:{item.excel_row}")
    for field in ("spec", "verify", "source"):
        if _normalized(story.get(field)) != _normalized(expected[field]):
            raise OperationalConflict(f"q4_source_field:{item.excel_row}:{field}")
    if item.row.priority and int(story.get("pri") or 0) != PRIORITY[item.row.priority]:
        raise OperationalConflict(f"q4_source_priority:{item.excel_row}")
    if _account(story.get("openedBy")) != "sdk-api":
        raise OperationalConflict(f"q4_source_creator:{item.excel_row}")
    assignee = story.get("assignedTo")
    if assignee and (not isinstance(assignee, dict)
                     or assignee.get("realname") != item.row.owner):
        raise OperationalConflict(f"q4_owner:{item.excel_row}")
    status = story.get("status")
    reviewers = story.get("reviewer") or []
    if status == "reviewing" and reviewers != ["cangyuan"]:
        raise OperationalConflict(f"q4_reviewer:{item.excel_row}")
    if status == "draft" and reviewers:
        raise OperationalConflict(f"q4_reviewer:{item.excel_row}")
    if status not in {"reviewing", "draft", "changing"}:
        raise OperationalConflict(f"q4_status:{item.excel_row}")


def check_story_after(before: dict, after: dict, *, module_id: int, plan_id: int,
                      draft: bool) -> None:
    for field in _PROTECTED:
        if not _same(field, before, after):
            raise OperationalConflict(f"q4_side_effect:{field}:{before.get('id')}")
    if after.get("stage") != before.get("stage"):
        raise OperationalConflict(f"q4_side_effect:stage:{before.get('id')}")
    if int(after.get("module") or 0) != module_id or str(plan_id) not in str(after.get("plan") or "").split(","):
        raise OperationalConflict(f"q4_link_readback:{before.get('id')}")
    if draft:
        if after.get("status") != "draft" or (after.get("reviewer") or []):
            raise OperationalConflict(f"q4_review_readback:{before.get('id')}")
    elif after.get("status") != before.get("status") or after.get("reviewer") != before.get("reviewer"):
        raise OperationalConflict(f"q4_review_side_effect:{before.get('id')}")


def load_source(workbook: bytes, manifest: dict) -> tuple[SourceItem, ...]:
    if hashlib.sha256(workbook).hexdigest() != WORKBOOK_SHA:
        raise OperationalConflict("q4_workbook_sha")
    rows = parse_q4_workbook(workbook)
    identities = validate_manifest(workbook, rows, manifest)
    return build_source_map(rows, identities)


class NativeZen:
    """Short lived, personal ZenTao session for native actions on one product."""

    def __init__(self, origin: str, account: str, entry_code: str, entry_key: str):
        if not re.fullmatch(r"https?://(?:zentao|127\.0\.0\.1)(?::\d+)?", origin):
            raise OperationalConflict("q4_private_origin_required")
        if not account or not entry_code or len(entry_key) != 32:
            raise OperationalConflict("q4_native_identity_invalid")
        self.origin = origin
        self.account = account
        self.entry_code = entry_code
        self.entry_key = entry_key
        self.client = httpx.Client(timeout=12, trust_env=False, follow_redirects=False)
        self.token = ""

    def __enter__(self) -> Self:
        stamp = int(time.time())
        digest = hashlib.md5(f"{self.entry_code}{self.entry_key}{stamp}".encode("ascii")).hexdigest()
        try:
            response = self.client.get(self.origin + "/api.php", params={
                "m": "user", "f": "apilogin", "account": self.account,
                "code": self.entry_code, "time": stamp, "token": digest,
            })
        except httpx.RequestError:
            raise OperationalConflict("q4_native_login_unavailable") from None
        if response.status_code not in {302, 303} or "zentaosid" not in self.client.cookies:
            raise OperationalConflict("q4_native_login_failed")
        self.token = "ss_" + self.client.cookies["zentaosid"]
        return self

    def __exit__(self, *_exc: object) -> None:
        self.client.close()

    def api(self, method: str, endpoint: str, *, payload: dict | None = None,
            params: dict | None = None, expected: tuple[int, ...] = (200,)) -> dict:
        response = self.client.request(method, self.origin + "/api.php/v1/" + endpoint,
                                       headers={"Token": self.token}, json=payload, params=params)
        if response.status_code not in expected:
            raise OperationalConflict(f"q4_native_api:{method}:{endpoint}:{response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise OperationalConflict(f"q4_native_api_json:{endpoint}") from None
        if not isinstance(body, dict):
            raise OperationalConflict(f"q4_native_api_shape:{endpoint}")
        return body

    def story(self, story_id: int) -> dict:
        story = self.api("GET", f"stories/{story_id}")
        if int(story.get("id", 0)) != story_id:
            raise OperationalConflict(f"q4_story_readback:{story_id}")
        return story

    def plans(self) -> list[dict]:
        body = self.api("GET", "products/1/plans", params={"limit": 100})
        plans = body.get("plans")
        if not isinstance(plans, list) or int(body.get("total", -1)) > 100:
            raise OperationalConflict("q4_plan_catalog_invalid")
        return plans

    def plan(self, *, create: bool) -> int | None:
        exact = [plan for plan in self.plans() if plan.get("title") == PLAN_TITLE and not plan.get("deleted")]
        if len(exact) > 1:
            raise OperationalConflict("q4_plan_duplicate")
        if exact:
            plan = exact[0]
            expected = plan_payload()
            if any(str(plan.get(key) or "") != expected[key] for key in expected):
                raise OperationalConflict("q4_plan_conflict")
            return int(plan["id"])
        if not create:
            return None
        created = self.api("POST", "products/1/plans", payload=plan_payload(), expected=(201,))
        plan_id = int(created.get("id") or 0)
        if plan_id < 1:
            raise OperationalConflict("q4_plan_create_uncertain")
        if not any(int(plan.get("id", 0)) == plan_id for plan in self.plans()):
            raise OperationalConflict("q4_plan_readback")
        return plan_id

    def plan_stories(self, plan_id: int) -> set[int]:
        body = self.api("GET", f"productplans/{plan_id}")
        stories = body.get("stories")
        if not isinstance(stories, list):
            raise OperationalConflict("q4_plan_members_invalid")
        try:
            ids = [int(story["id"]) for story in stories]
        except (TypeError, ValueError, KeyError):
            raise OperationalConflict("q4_plan_members_invalid") from None
        if len(ids) != len(set(ids)):
            raise OperationalConflict("q4_plan_members_duplicate")
        return set(ids)

    def modules(self, expected_names: set[str] | None = None) -> dict[str, int]:
        body = self.api("GET", "modules", params={"type": "story", "id": 1})
        tree = body.get("modules")
        if not isinstance(tree, list):
            raise OperationalConflict("q4_module_catalog_invalid")
        found: dict[str, int] = {}

        def visit(nodes: list[dict]) -> None:
            for node in nodes:
                if (not isinstance(node, dict) or node.get("type") != "story"
                        or int(node.get("root", 0)) != 1 or node.get("deleted") in (1, "1", True)):
                    raise OperationalConflict("q4_module_catalog_invalid")
                name = str(node.get("name") or "")
                if name in found:
                    raise OperationalConflict("q4_module_duplicate")
                if expected_names and name in expected_names and int(node.get("parent") or 0) != 0:
                    raise OperationalConflict(f"q4_module_parent_conflict:{name}")
                found[name] = int(node["id"])
                if node.get("children"):
                    visit(node["children"])

        visit(tree)
        return found

    def browse(self) -> str:
        response = self.client.get(self.origin + "/sdk/index.php", params={
            "m": "tree", "f": "browse", "root": 1, "viewType": "story",
        })
        if response.status_code != 200 or "manageChild" not in response.text:
            raise OperationalConflict("q4_module_browse_unavailable")
        return str(response.url)

    def create_module(self, name: str) -> int:
        referer = self.browse()
        response = self.client.post(self.origin + "/sdk/index.php", params={
            "m": "tree", "f": "manageChild", "root": 1, "viewType": "story",
        }, data={"modules[]": name, "shorts[]": "", "parentModuleID": "0", "maxOrder": "0"},
            headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"})
        try:
            body = response.json()
        except ValueError:
            raise OperationalConflict("q4_module_create_uncertain") from None
        if response.status_code != 200 or body.get("result") != "success":
            raise OperationalConflict("q4_module_create_rejected")
        module_id = self.modules({name}).get(name)
        if not module_id:
            raise OperationalConflict("q4_module_create_readback")
        return module_id

    def link_plan(self, plan_id: int, story_id: int) -> None:
        self.api("POST", f"productplans/{plan_id}/linkstories", payload={"stories": [story_id]})

    def batch_edit(self, story: dict, *, module_id: int, plan_id: int,
                   status: str | None = None) -> None:
        story_id = int(story["id"])
        if not story.get("category") or not story.get("stage") or story.get("branch") is None:
            raise OperationalConflict(f"q4_story_edit_missing_field:{story_id}")
        referer = self.client.get(self.origin + "/sdk/index.php", params={
            "m": "product", "f": "browse", "productID": 1,
        })
        if referer.status_code != 200:
            raise OperationalConflict("q4_story_browse_unavailable")
        fields = {
            "branch": "branch" + str(story["branch"]),
            "module": module_id, "roadmap": story.get("roadmap") or "",
            "plan": plan_id, "title": story["title"], "color": story.get("color") or "",
            "estimate": story.get("estimate") or "",
            "category": story["category"], "pri": story["pri"],
            "assignedTo": _account(story.get("assignedTo")),
            "source": story.get("source") or "", "sourceNote": story["sourceNote"],
            "status": status or story["status"], "stage": story["stage"],
            "closedBy": _account(story.get("closedBy")),
            "closedReason": story.get("closedReason") or "",
            "keywords": story.get("keywords") or "",
        }
        data = {f"{key}[{story_id}]": str(value) for key, value in fields.items()}
        response = self.client.post(self.origin + "/sdk/index.php", params={
            "m": "story", "f": "batchEdit", "productID": 1,
        }, data=data, headers={"Referer": str(referer.url), "X-Requested-With": "XMLHttpRequest"})
        try:
            body = response.json()
        except ValueError:
            raise OperationalConflict(f"q4_story_edit_uncertain:{story_id}") from None
        if response.status_code != 200 or body.get("result") != "success":
            raise OperationalConflict(f"q4_story_edit_rejected:{story_id}")

    def recall(self, story_id: int) -> None:
        self.api("DELETE", f"stories/{story_id}/recall")


def _check_protected_against_snapshot(snapshot: dict, current: dict) -> None:
    for field in _PROTECTED:
        if not _same(field, snapshot, current):
            raise OperationalConflict(f"q4_live_changed:{field}:{snapshot.get('id')}")


def _write_private(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def source_gap_register(items: tuple[SourceItem, ...], stories: dict[int, dict]) -> dict:
    rows = []
    for item in items:
        row = item.row
        story = stories[item.story_id]
        assignee = story.get("assignedTo") or {}
        rows.append({
            "source": {
                "sheet": SHEET, "excel_row": row.excel_row,
                "cells": {"A": row.module, "B": row.title, "C": row.owner,
                          "D": row.version, "E": row.planned_start, "F": row.planned_end,
                          "G": row.priority, "H": row.risk, "I": row.progress, "J": row.note},
                "links": [{"cell": cell, "label": label, "url": url}
                          for cell, label, url in row.links],
            },
            "zentao": {"story_id": item.story_id, "source_module": item.module_name,
                       "status_at_register": story.get("status"),
                       "plan_at_register": story.get("plan"),
                       "module_at_register": story.get("module"),
                       "assignee_account": _account(assignee) or None,
                       "assignee_realname": assignee.get("realname") if isinstance(assignee, dict) else None},
            "business_decisions_needed": [
                "confirm_requester", "define_scenario_and_device_scope", "define_acceptance_criteria",
                "choose_reviewer", "confirm_target_version_and_dates",
                *(["resolve_responsible_account"] if not assignee else []),
            ],
        })
    return {"workbook_sha256": WORKBOOK_SHA, "sheet": SHEET,
            "source_rows": len(rows), "rows": rows}


def operate(items: tuple[SourceItem, ...], zen: NativeZen, output: Path,
            *, phase: str, apply: bool, review_mode: str) -> dict:
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output, 0o700)
    snapshot_path = output / "preflight.json"
    current = {item.story_id: zen.story(item.story_id) for item in items}
    for item in items:
        check_story_before(item, current[item.story_id])
    if snapshot_path.exists():
        baseline = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if set(map(int, baseline.get("stories", {}))) != {item.story_id for item in items}:
            raise OperationalConflict("q4_snapshot_coverage")
        for story_id, story in current.items():
            _check_protected_against_snapshot(baseline["stories"][str(story_id)], story)
    else:
        vector = ",".join(f"{story_id}:{current[story_id].get('version')}"
                          for story_id in sorted(current))
        if hashlib.sha256(vector.encode()).hexdigest() != INITIAL_VERSIONS_SHA:
            raise OperationalConflict("q4_initial_version_drift")
        baseline = {"sheet": SHEET, "workbook_sha256": WORKBOOK_SHA,
                    "stories": {str(key): value for key, value in current.items()}}
        _write_private(snapshot_path, baseline)
    _write_private(output / "source-gap-register.json", source_gap_register(items, current))
    extra = {story_id: zen.story(story_id) for story_id in (1, 2)}
    plan_id = zen.plan(create=False)
    expected_modules = tuple(dict.fromkeys(item.module_name for item in items))
    modules = zen.modules(set(expected_modules))
    report = {"phase": phase, "dry_run": not apply, "source_rows": len(items),
              "plan_id": plan_id, "modules_present": len(set(expected_modules) & set(modules)),
              "stories_linked": 0, "stories_draft": 0, "written": 0}

    if phase == "structure" and apply:
        plan_id = zen.plan(create=True)
        for name in expected_modules:
            if name not in modules:
                modules[name] = zen.create_module(name)
                report["written"] += 1
        report["plan_id"] = plan_id
        report["modules_present"] = len(set(expected_modules) & set(modules))

    if phase in {"link", "draft", "verify"}:
        if not plan_id or not all(name in modules for name in expected_modules):
            raise OperationalConflict("q4_structure_incomplete")
        for story_id, story in extra.items():
            if str(plan_id) in str(story.get("plan") or "").split(","):
                raise OperationalConflict(f"q4_test_story_in_plan:{story_id}")

    if phase == "link":
        for item in items:
            before = zen.story(item.story_id)
            _check_protected_against_snapshot(baseline["stories"][str(item.story_id)], before)
            existing_plans = set(filter(None, str(before.get("plan") or "").split(","))) - {"0"}
            if existing_plans - {str(plan_id)}:
                raise OperationalConflict(f"q4_other_plan:{item.story_id}")
            if apply and str(plan_id) not in existing_plans:
                zen.link_plan(plan_id, item.story_id)
                report["written"] += 1
                after_link = zen.story(item.story_id)
                _check_protected_against_snapshot(before, after_link)
                before = after_link
            module_id = modules[item.module_name]
            if int(before.get("module") or 0) not in {0, module_id}:
                raise OperationalConflict(f"q4_other_module:{item.story_id}")
            if apply and int(before.get("module") or 0) != module_id:
                if str(plan_id) not in str(before.get("plan") or "").split(","):
                    raise OperationalConflict(f"q4_plan_missing:{item.story_id}")
                zen.batch_edit(before, module_id=module_id, plan_id=plan_id)
                report["written"] += 1
                after = zen.story(item.story_id)
                check_story_after(before, after, module_id=module_id, plan_id=plan_id, draft=False)
            after = zen.story(item.story_id)
            if str(plan_id) in str(after.get("plan") or "").split(","):
                report["stories_linked"] += 1
        if apply and zen.plan_stories(plan_id) != {item.story_id for item in items}:
            raise OperationalConflict("q4_plan_members_mismatch")

    if phase == "draft":
        if review_mode != "pending":
            raise OperationalConflict("q4_draft_requires_pending_mode")
        if zen.plan_stories(plan_id) != {item.story_id for item in items}:
            raise OperationalConflict("q4_plan_members_mismatch")
        for item in items:
            before = zen.story(item.story_id)
            _check_protected_against_snapshot(baseline["stories"][str(item.story_id)], before)
            module_id = modules[item.module_name]
            if int(before.get("module") or 0) != module_id or str(plan_id) not in str(before.get("plan") or "").split(","):
                raise OperationalConflict(f"q4_link_incomplete:{item.story_id}")
            if apply and before["status"] == "reviewing":
                zen.recall(item.story_id)
                report["written"] += 1
                recalled = zen.story(item.story_id)
                _check_protected_against_snapshot(before, recalled)
                if recalled.get("status") not in {"draft", "changing"} or recalled.get("reviewer"):
                    raise OperationalConflict(f"q4_recall_readback:{item.story_id}")
                before = recalled
            if apply and before["status"] == "changing":
                zen.batch_edit(before, module_id=module_id, plan_id=plan_id, status="draft")
                report["written"] += 1
            after = zen.story(item.story_id)
            if apply:
                check_story_after(before, after, module_id=module_id, plan_id=plan_id, draft=True)
            if after.get("status") == "draft" and not after.get("reviewer"):
                report["stories_draft"] += 1

    if phase == "verify":
        if zen.plan_stories(plan_id) != {item.story_id for item in items}:
            raise OperationalConflict("q4_plan_members_mismatch")
        for item in items:
            story = zen.story(item.story_id)
            _check_protected_against_snapshot(baseline["stories"][str(item.story_id)], story)
            if int(story.get("module") or 0) != modules[item.module_name]:
                raise OperationalConflict(f"q4_module_missing:{item.story_id}")
            if str(plan_id) not in str(story.get("plan") or "").split(","):
                raise OperationalConflict(f"q4_plan_missing:{item.story_id}")
            if review_mode == "pending" and (story.get("status") != "draft" or story.get("reviewer")):
                raise OperationalConflict(f"q4_draft_missing:{item.story_id}")
            if review_mode == "keep" and (story.get("status") != "reviewing" or story.get("reviewer") != ["cangyuan"]):
                raise OperationalConflict(f"q4_review_changed:{item.story_id}")
            report["stories_linked"] += 1
            if story.get("status") == "draft":
                report["stories_draft"] += 1

    _write_private(output / f"{phase}-report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("audit", "structure", "link", "draft", "verify"), default="audit")
    parser.add_argument("--review-mode", choices=("pending", "keep"), default="pending")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and args.phase in {"audit", "verify"}:
        parser.error("audit and verify are read-only")
    items = load_source(args.workbook.read_bytes(), json.loads(args.manifest.read_text()))
    entry_key = Path(os.environ["PLATFORM_SDK_SSO_ENTRY_KEY_FILE"]).read_text().strip()
    with NativeZen(os.environ["PLATFORM_SDK_ZENTAO_BASE_URL"],
                   os.environ.get("SDK_Q4_OPERATOR_ACCOUNT", "cangyuan"),
                   os.environ["PLATFORM_SDK_SSO_ENTRY_CODE"], entry_key) as zen:
        report = operate(items, zen, args.output_dir, phase=args.phase,
                         apply=args.apply, review_mode=args.review_mode)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
