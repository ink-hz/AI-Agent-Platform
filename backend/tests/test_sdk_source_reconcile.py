from __future__ import annotations
import copy
import csv
import hashlib
import importlib
import json
from io import StringIO
from pathlib import Path
import socket
import sys
import pytest
from app.sdk_workbench.plan_csv import HEADERS
from app.sdk_workbench.source_reconcile import ReconcileError, reconcile


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def fixture(rows=None):
    rows = rows or [["SDK\n目标", "任务", "甲", "", "", "", "", "", "", ""]]
    workbook = b"immutable workbook bytes"
    digest = hashlib.sha256(workbook).hexdigest()
    records = []
    categories = []
    for n, values in enumerate(rows, 2):
        key = dict(file_sha256=digest, sheet_name="Q4", row_start=n, row_end=n)
        raw = [dict(cell=f"{col}{n}", column=col, column_name=name,
                    header_cell=f"{col}1", value=value,
                    xml_value=value if value else None,
                    xml_type="s" if value else None, style_id=None,
                    number_format_id=None, custom_number_format=None,
                    formula=None, formula_attributes=None,
                    raw_state="value" if value else "empty_string",
                    merge_range=None, merge_anchor_cell=None, hyperlinks=[])
               for col, name, value in zip("ABCDEFGHIJ", HEADERS, values)]
        interpreted = [dict(cell=c["cell"], column=c["column"],
                            column_name=c["column_name"], value=c["value"],
                            value_origin="source_cell" if c["value"] else "source_blank",
                            value_state="present" if c["value"] else "source_blank",
                            source_cell=c["cell"], merge_range=None) for c in raw]
        records.append(dict(source_key=key, source_range=f"A{n}:J{n}",
                            record_type="task_candidate" if values[1] else "category_only",
                            raw_cells=raw, interpreted_cells=interpreted,
                            category_source_key=key, category_anchor_cell=f"A{n}",
                            source_date_interpretations=[], business_work_id=None,
                            business_review_state="not_reviewed",
                            production_import_eligible=False))
        categories.append(dict(source_key=key, cell=f"A{n}", value=values[0], merge_range=None))
    counts = dict(records=len(records), task_candidate=sum(bool(r[1]) for r in rows),
                  category_only=sum(not bool(r[1]) for r in rows), categories=len(categories),
                  task_original_status={})
    for row in rows:
        if row[1]:
            status = row[8] if row[8] else "source_blank"
            counts["task_original_status"][status] = counts["task_original_status"].get(status, 0) + 1
    candidate = dict(schema_version="0.1.0", protocol_version="A-wave-01-scope-v0.1",
                     candidate_status="preparation_not_business_approved",
                     source=dict(file_sha256=digest, sheet_name="Q4", bytes=len(workbook)),
                     source_key_fields=["file_sha256", "sheet_name", "row_start", "row_end"],
                     control_counts=counts, categories=categories, merge_ranges=[], records=records)
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerows(rows)
    return candidate, output.getvalue().encode(), workbook


def run(candidate, csv_bytes, workbook, export=None):
    return reconcile(encoded(candidate), csv_bytes, workbook, None if export is None else encoded(export))


def test_unique_comparison_preserves_inputs_and_preparation():
    candidate, csv_bytes, workbook = fixture()
    before = copy.deepcopy(candidate)
    report = run(candidate, csv_bytes, workbook)
    assert report == run(candidate, csv_bytes, workbook)
    assert candidate == before
    assert len(report["alias_proposals"]) == 1
    assert report["source_records"] == candidate["records"]
    assert report["csv_records"][0]["cells"] == list(candidate["records"][0]["raw_cells"][i]["value"] for i in range(10))
    assert report["existing_objects"]["collection_state"] == "not_collected"
    assert report["production_import_eligible"] is False
    assert report["alias_proposals"][0]["review_state"] == "not_reviewed"
    assert "new_story_count" not in report


def test_duplicate_full_fields_queue_both_sides_without_choosing_first():
    row = ["SDK", "同名", "甲", "", "", "", "", "", "", ""]
    c, data, book = fixture([row, row])
    report = run(c, data, book)
    assert report["alias_proposals"] == []
    assert [x["code"] for x in report["review_queue"]].count("ambiguous_csv_alias") == 2
    assert all(len(x["source_keys"]) == 2 for x in report["review_queue"] if x["code"] == "ambiguous_csv_alias")


def test_same_title_and_row_number_are_insufficient():
    c, data, book = fixture()
    c["records"][0]["raw_cells"][2]["value"] = "乙"
    c["records"][0]["interpreted_cells"][2]["value"] = "乙"
    report = run(c, data, book)
    assert not report["alias_proposals"]
    assert {x["code"] for x in report["review_queue"]} == {"unmatched_csv", "unmatched_source"}


@pytest.mark.parametrize("change,code", [
    (lambda c: c.update(schema_version="9"), "candidate_schema"),
    (lambda c: c["source"].update(file_sha256="0" * 64), "source_digest"),
    (lambda c: c["records"][0]["source_key"].update(row_start=True), "source_key"),
    (lambda c: c["records"].append(copy.deepcopy(c["records"][0])), "duplicate_source_key"),
    (lambda c: c["records"][0]["raw_cells"][1].update(cell="B99"), "candidate_cells"),
    (lambda c: c["records"][0]["interpreted_cells"][0].update(value_origin="merge_anchor", merge_range="A2:A9", source_cell="A2"), "candidate_merge"),
    (lambda c: c["control_counts"].update(task_candidate=67), "candidate_counts"),
    (lambda c: c["records"][0].update(production_import_eligible=True), "candidate_preparation"),
])
def test_invalid_candidates_fail_closed(change, code):
    c, data, book = fixture()
    change(c)
    with pytest.raises(ReconcileError) as caught:
        run(c, data, book)
    assert caught.value.code == code


def test_newline_date_projection_retains_raw_and_positions():
    c, _, book = fixture()
    raw = c["records"][0]["raw_cells"][4]
    view = c["records"][0]["interpreted_cells"][4]
    raw.update(value="46286", xml_value="46286", xml_type="n", number_format_id="14", raw_state="value")
    view.update(value="46286", value_origin="source_cell", value_state="present")
    c["records"][0]["source_date_interpretations"] = [dict(cell="E2", raw_value="46286", date_system="1900", precision="day", timezone=None, interpreted_iso="2026-09-21")]
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(HEADERS)
    writer.writerow(["SDK\r\n目标", "任务", "甲", "", "2026/9/21", "", "", "", "", ""])
    report = run(c, stream.getvalue().encode(), book)
    assert len(report["alias_proposals"]) == 1
    assert report["source_records"][0]["raw_cells"][4]["value"] == "46286"
    assert report["csv_records"][0]["cells"][4] == "2026/9/21"
    assert report["csv_records"][0]["cells"][0] == "SDK\r\n目标"
    assert report["csv_records"][0]["physical_start_line"] == 2
    c["records"][0]["source_date_interpretations"][0]["interpreted_iso"] = "2026-09-22"
    with pytest.raises(ReconcileError, match="candidate_dates"):
        run(c, stream.getvalue().encode(), book)


def test_source_identity_uses_sheet_and_reordering_keeps_proposal_ids():
    c, data, book = fixture([["SDK", "甲任务", "甲", "", "", "", "", "", "", ""],
                              ["Viewer", "乙任务", "乙", "", "", "", "", "", "", ""]])
    first = run(c, data, book)
    changed = copy.deepcopy(c)
    changed["source"]["sheet_name"] = "OtherSheet"
    for row in changed["records"]:
        row["source_key"]["sheet_name"] = "OtherSheet"
        row["category_source_key"]["sheet_name"] = "OtherSheet"
    for category in changed["categories"]:
        category["source_key"]["sheet_name"] = "OtherSheet"
    assert first["alias_proposals"][0]["proposal_id"] != run(changed, data, book)["alias_proposals"][0]["proposal_id"]
    c["records"].reverse()
    reordered = run(c, data, book)
    assert first["alias_proposals"] == reordered["alias_proposals"]
    assert first["input_sha256"]["candidate"] != reordered["input_sha256"]["candidate"]
    assert first["source_records"] == reordered["source_records"]


def test_malformed_json_and_export_are_deterministic():
    c, data, book = fixture()
    for payload in (b"not JSON", b'{"schema_version":"0.1.0","schema_version":"0.1.0"}'):
        with pytest.raises(ReconcileError) as caught:
            reconcile(payload, data, book)
        assert caught.value.code == "candidate_json"
    with pytest.raises(ReconcileError, match="export_schema"):
        run(c, data, book, {"schema_version": "r0a-existing-1"})


def existing(c, data):
    return dict(schema_version="r0a-existing-1", instance_id="fixture-instance",
                zentao_version="fixture-version", exported_at="2026-10-22T12:00:00+08:00",
                evidence_state="current", complete=True,
                scope=dict(csv_sha256=hashlib.sha256(data).hexdigest(), native_object_type="story", native_ids=["03"]),
                rows=[dict(logical_record_number=2, plan_import_id="7", plan_row_id="8", native_id="03", native_version="1", native_state="active")])


def test_optional_export_does_not_approve_and_partial_historical_conflicts_are_unknown():
    c, data, book = fixture()
    e = existing(c, data)
    report = run(c, data, book, e)
    assert report["existing_objects"]["collection_state"] == "observed"
    assert report["existing_objects"]["links"][0]["native_id"] == "03"
    assert report["existing_objects"]["links"][0]["review_state"] == "not_reviewed"
    for field, value in [("complete", False), ("evidence_state", "historical")]:
        altered = copy.deepcopy(e)
        altered[field] = value
        assert run(c, data, book, altered)["existing_objects"]["collection_state"] == "unknown"
    e["rows"].append(copy.deepcopy(e["rows"][0]))
    report = run(c, data, book, e)
    assert report["existing_objects"]["collection_state"] == "unknown"
    assert any(x["code"] == "legacy_one_to_one_conflict" for x in report["review_queue"])
    e = existing(c, data)
    e["scope"]["csv_sha256"] = "0" * 64
    assert run(c, data, book, e)["existing_objects"]["collection_state"] == "unknown"


def test_core_has_no_external_imports_and_does_not_open_network(monkeypatch):
    module = importlib.import_module("app.sdk_workbench.source_reconcile")
    tree = __import__("ast").parse(Path(module.__file__).read_text())
    imports = {n.module for n in __import__("ast").walk(tree) if isinstance(n, __import__("ast").ImportFrom)}
    assert imports <= {"__future__", "collections", "datetime", "typing", "plan_csv"}
    names = {a.name for n in __import__("ast").walk(tree) if isinstance(n, __import__("ast").Import) for a in n.names}
    assert names <= {"hashlib", "json", "re"}
    def forbidden(*args, **kwargs):
        raise AssertionError("external I/O called")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    c, data, book = fixture()
    assert run(c, data, book)["existing_objects"]["collection_state"] == "not_collected"

@pytest.mark.parametrize("missing", ["raw_value", "interpreted_iso"])
def test_missing_date_fields_are_reconcile_errors(missing):
    c, data, book = fixture()
    item = dict(cell="E2", raw_value="", interpreted_iso=None)
    del item[missing]
    c["records"][0]["source_date_interpretations"] = [item]
    with pytest.raises(ReconcileError) as caught:
        run(c, data, book)
    assert caught.value.code == "candidate_dates"
    assert caught.value.path == "E2"


def test_missing_category_value_is_reconcile_error():
    c, data, book = fixture()
    raw = c["records"][0]["raw_cells"][0]
    raw.update(value=None, xml_value=None, xml_type=None, raw_state="absent_cell")
    c["records"][0]["interpreted_cells"][0].update(
        value=None, value_origin="source_blank", value_state="source_blank")
    del c["categories"][0]["value"]
    with pytest.raises(ReconcileError, match="candidate_cells"):
        run(c, data, book)


def test_long_date_serial_is_reconcile_error():
    c, data, book = fixture()
    value = "9" * 5000
    c["records"][0]["raw_cells"][4].update(
        value=value, xml_value=value, xml_type="n", number_format_id="14", raw_state="value")
    c["records"][0]["interpreted_cells"][4].update(
        value=value, value_origin="source_cell", value_state="present")
    c["records"][0]["source_date_interpretations"] = [dict(
        cell="E2", raw_value=value, interpreted_iso="2026-09-21",
        date_system="1900", precision="day", timezone=None)]
    with pytest.raises(ReconcileError, match="candidate_dates"):
        run(c, data, book)


@pytest.mark.parametrize("field", ["source_key", "collection_state", "review_state"])
def test_export_rows_cannot_inject_report_fields(field):
    c, data, book = fixture()
    e = existing(c, data)
    e["rows"][0][field] = "approved"
    with pytest.raises(ReconcileError, match="export_schema"):
        run(c, data, book, e)
