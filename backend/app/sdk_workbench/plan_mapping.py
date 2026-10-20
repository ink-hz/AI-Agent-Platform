"""Semantic, source preserving interpretation of SDK annual plan rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Sequence

from .plan_csv import ParsedPlan


class PlanMappingError(ValueError):
    pass


_PRIORITY = {"高": 1, "中": 2, "低": 3}


@dataclass(frozen=True)
class MappedRequirement:
    record_number: int
    physical_line: int
    module: str
    title: str
    proposer: None
    responsible_raw: str
    version: str
    planned_start: date | None
    planned_end: date | None
    priority: int | None
    risk: str
    source_status: str
    note: str


def _date(raw: str, field: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y/%m/%d").date()
    except ValueError:
        raise PlanMappingError(f"sdk_plan_{field}_invalid") from None


def map_plan(parsed: ParsedPlan) -> tuple[MappedRequirement, ...]:
    result = []
    for row in parsed.rows:
        if row.kind != "task":
            continue
        _, title, responsible, version, start, end, priority, risk, status, note = (
            cell.strip() for cell in row.cells
        )
        if priority and priority not in _PRIORITY:
            raise PlanMappingError("sdk_plan_priority_invalid")
        mapped = MappedRequirement(
            row.record_number, row.physical_line, row.module, title, None,
            responsible, version, _date(start, "start_date"), _date(end, "end_date"),
            _PRIORITY.get(priority), risk, status, note,
        )
        if mapped.planned_start and mapped.planned_end and mapped.planned_start > mapped.planned_end:
            raise PlanMappingError("sdk_plan_date_order_invalid")
        result.append(mapped)
    return tuple(result)


def match_responsible(raw: str, people: Mapping[str, Sequence[str]]) -> tuple[str, str | None]:
    name = raw.strip()
    if not name:
        return "missing", None
    if "/" in name or "、" in name or "，" in name:
        return "multiple", None
    if "?" in name or "？" in name:
        return "uncertain", None
    matches = people.get(name, ())
    if not matches:
        return "not_found", None
    if len(matches) != 1:
        return "duplicate_name", None
    return "matched", matches[0]
