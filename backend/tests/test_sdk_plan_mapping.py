from __future__ import annotations

import csv
import html
from io import StringIO

import pytest
from app.sdk_workbench.annual_reconcile import (
    _imported_title,
    _linked_story_ids,
    _stories,
)
from app.sdk_workbench.plan_csv import parse_plan_csv
from app.sdk_workbench.plan_mapping import PlanMappingError, map_plan, match_responsible


def _plan(rows: list[list[str]]):
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["平台", "任务", "责任人", "版本", "开始时间", "结束时间", "优先级", "风险点", "完成情况", "备注"])
    writer.writerows(rows)
    return parse_plan_csv(stream.getvalue().encode())


def test_maps_every_source_field_without_inventing_proposer_or_dates():
    parsed = _plan([["SDK", "IMU 时间戳", "周公", "版本1", "2026/10/8", "2026/10/31", "高", "固件配合", "开发中", "已调试"]])
    mapped = map_plan(parsed)[0]
    assert mapped.title == "IMU 时间戳"
    assert mapped.module == "SDK"
    assert mapped.responsible_raw == "周公"
    assert mapped.proposer is None
    assert mapped.version == "版本1"
    assert mapped.planned_start.isoformat() == "2026-10-08"
    assert mapped.planned_end.isoformat() == "2026-10-31"
    assert mapped.priority == 1
    assert (mapped.risk, mapped.source_status, mapped.note) == ("固件配合", "开发中", "已调试")
    assert match_responsible(mapped.responsible_raw, {"周公": ["account"]}) == ("matched", "account")


@pytest.mark.parametrize("raw,people,state", [
    ("夜枫/托勒密", {"夜枫": ["a"], "托勒密": ["b"]}, "multiple"),
    ("戴维？", {"戴维": ["a"]}, "uncertain"),
    ("小树", {"朝小树": ["a"]}, "not_found"),
    ("周公", {"周公": ["a", "b"]}, "duplicate_name"),
])
def test_responsible_matching_never_guesses(raw, people, state):
    assert match_responsible(raw, people) == (state, None)


def test_invalid_date_fails_before_any_write():
    parsed = _plan([["SDK", "任务", "周公", "", "2026/2/30", "", "", "", "", ""]])
    with pytest.raises(PlanMappingError, match="start_date_invalid"):
        map_plan(parsed)


def test_existing_zentao_import_flattens_multiline_titles():
    assert _imported_title("A\r\nB\r\nC") == "A B C"
    assert _imported_title(html.unescape("RK3576&amp; 地平线")) == "RK3576& 地平线"


@pytest.mark.asyncio
async def test_reconcile_uses_source_link_after_q4_body_removes_old_marker():
    class Client:
        async def get_story(self, story_id):
            assert story_id == 68
            return {"id": 68, "title": "根据客户需要更新版本",
                    "spec": "<p>根据客户需要更新版本</p>",
                    "sourceNote": "2026年Q4上线版本｜Android Wrapper｜Excel第92行"}

        async def list_stories(self, product_id, page):
            raise AssertionError("linked stories must not scan markers")

    found = await _stories(Client(), 1, "41df018a", linked_ids={92: 68})
    assert found[92]["id"] == 68


def test_source_link_query_joins_batch_and_rejects_duplicate_record_numbers():
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, query, params):
            assert "join sdk.plan_rows row" in query
            assert "join sdk.plan_imports batch" in query
            assert params == ("source-sha",)
            return Result(self.rows)

    rows = [{"record_number": 92, "story_id": 68}, {"record_number": 93, "story_id": 69}]
    assert _linked_story_ids(Connection(rows), "source-sha") == {92: 68, 93: 69}
    with pytest.raises(RuntimeError, match="sdk_source_story_conflict"):
        _linked_story_ids(Connection(rows + [rows[0]]), "source-sha")
