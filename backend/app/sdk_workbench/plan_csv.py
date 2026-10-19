"""Bounded parsing of SDK annual plan CSV uploads."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
from io import StringIO
from typing import Literal


HEADERS = ("平台", "任务", "责任人", "版本", "开始时间", "结束时间", "优先级", "风险点", "完成情况", "备注")
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000


class PlanCsvError(ValueError):
    """A deterministic upload validation error safe to return to clients."""


@dataclass(frozen=True)
class PlanRow:
    record_number: int
    physical_line: int
    kind: Literal["task", "heading"]
    module: str
    cells: tuple[str, ...]


@dataclass(frozen=True)
class ParsedPlan:
    sha256: str
    rows: tuple[PlanRow, ...]
    task_count: int
    module_count: int


def parse_plan_csv(payload: bytes) -> ParsedPlan:
    if not payload:
        raise PlanCsvError("sdk_plan_csv_empty")
    if len(payload) > MAX_BYTES:
        raise PlanCsvError("sdk_plan_csv_too_large")
    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise PlanCsvError("sdk_plan_csv_encoding") from None
    if "\x00" in decoded:
        raise PlanCsvError("sdk_plan_csv_encoding")
    reader = csv.reader(StringIO(decoded, newline=""), strict=True)
    try:
        header = next(reader)
        if tuple(header[:10]) != HEADERS or len(header) < 10:
            raise PlanCsvError("sdk_plan_csv_header")
        if any(value.strip() for value in header[10:]):
            raise PlanCsvError("sdk_plan_csv_trailing_column")
        previous_line = reader.line_num
        module = ""
        rows: list[PlanRow] = []
        task_count = 0
        modules: set[str] = set()
        for record_number, raw in enumerate(reader, start=2):
            physical_line = previous_line + 1
            previous_line = reader.line_num
            if len(raw) > 10 and any(value.strip() for value in raw[10:]):
                raise PlanCsvError("sdk_plan_csv_trailing_column")
            cells = tuple((raw + [""] * 10)[:10])
            if not any(value.strip() for value in cells):
                continue
            if cells == HEADERS:
                raise PlanCsvError("sdk_plan_csv_repeated_header")
            if cells[0].strip():
                module = cells[0]
                modules.add(module)
            if not module:
                raise PlanCsvError("sdk_plan_csv_module_required")
            kind: Literal["task", "heading"] = "task" if cells[1].strip() else "heading"
            task_count += kind == "task"
            rows.append(PlanRow(record_number, physical_line, kind, module, cells))
            if len(rows) > MAX_ROWS:
                raise PlanCsvError("sdk_plan_csv_too_many_rows")
    except csv.Error:
        raise PlanCsvError("sdk_plan_csv_malformed") from None
    if not task_count:
        raise PlanCsvError("sdk_plan_csv_no_tasks")
    return ParsedPlan(hashlib.sha256(payload).hexdigest(), tuple(rows), task_count, len(modules))
