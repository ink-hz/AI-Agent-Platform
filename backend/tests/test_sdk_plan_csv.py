from __future__ import annotations

import csv
from io import StringIO

import pytest

from app.sdk_workbench.plan_csv import PlanCsvError, parse_plan_csv


HEADERS = ["平台", "任务", "责任人", "版本", "开始时间", "结束时间", "优先级", "风险点", "完成情况", "备注"]


def _csv(rows: list[list[str]], *, bom: bool = False) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS + ["", ""])
    for row in rows:
        writer.writerow(row + ["", ""])
    return (("\ufeff" if bom else "") + output.getvalue()).encode("utf-8")


def test_plan_csv_preserves_source_text_positions_and_module_across_blank_rows() -> None:
    payload = _csv([
        ["SDK\n2026目标", "同名任务", "周公", "版本1", "", "", "高", "", "开发中", "第一行\n第二行"],
        ["", "", "", "", "", "", "", "", "", ""],
        ["", "同名任务", "夜枫", "", "", "", "", "", "", ""],
        ["Viewer", "", "", "", "", "", "", "", "", ""],
        ["", "多相机", "夜枫", "", "", "", "高", "", "", ""],
    ], bom=True)

    parsed = parse_plan_csv(payload)

    assert parsed.task_count == 3
    assert parsed.module_count == 2
    assert [(r.record_number, r.physical_line, r.kind, r.module) for r in parsed.rows] == [
        (2, 2, "task", "SDK\n2026目标"),
        (4, 6, "task", "SDK\n2026目标"),
        (5, 7, "heading", "Viewer"),
        (6, 8, "task", "Viewer"),
    ]
    assert parsed.rows[0].cells[9] == "第一行\n第二行"
    assert parsed.rows[0].cells[1] == parsed.rows[1].cells[1]


@pytest.mark.parametrize("payload,code", [
    (b"", "sdk_plan_csv_empty"),
    (b"\xff", "sdk_plan_csv_encoding"),
    ("错误,表头\n".encode(), "sdk_plan_csv_header"),
    (_csv([["", "orphan", "", "", "", "", "", "", "", ""]]), "sdk_plan_csv_module_required"),
    (_csv([["SDK", "", "", "", "", "", "", "", "", ""]]), "sdk_plan_csv_no_tasks"),
    (_csv([HEADERS, ["SDK", "任务", "", "", "", "", "", "", "", ""]]), "sdk_plan_csv_repeated_header"),
])
def test_plan_csv_rejects_invalid_input(payload: bytes, code: str) -> None:
    with pytest.raises(PlanCsvError) as error:
        parse_plan_csv(payload)
    assert str(error.value) == code


def test_plan_csv_rejects_trailing_nonempty_column_and_file_limit() -> None:
    with pytest.raises(PlanCsvError, match="sdk_plan_csv_trailing_column"):
        parse_plan_csv(_csv([["SDK", "任务", "", "", "", "", "", "", "", ""]]).replace(b",,\r\n", b",extra,\r\n"))
    with pytest.raises(PlanCsvError, match="sdk_plan_csv_too_large"):
        parse_plan_csv(b"x" * (2 * 1024 * 1024 + 1))


def test_plan_csv_handles_full_year_shape_without_deduplicating_titles() -> None:
    rows = []
    for module in range(17):
        for task in range(4 if module < 16 else 3):
            rows.append([f"模块{module}" if task == 0 else "", "同名任务", "负责人", "", "", "", "", "", "", ""])
    parsed = parse_plan_csv(_csv(rows))
    assert parsed.task_count == 67
    assert parsed.module_count == 17
    assert len(parsed.rows) == 67
