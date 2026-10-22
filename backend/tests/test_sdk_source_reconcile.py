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


def test_bom_only_csv_is_reconcile_empty_error():
    candidate, _, workbook = fixture()
    payload = encoded(candidate)
    csv_bytes = b"\xef\xbb\xbf"
    before = (payload, csv_bytes, workbook)
    with pytest.raises(ReconcileError) as caught:
        reconcile(payload, csv_bytes, workbook)
    assert (caught.value.code, caught.value.path) == ("sdk_plan_csv_empty", "csv")
    assert (payload, csv_bytes, workbook) == before


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


def test_missing_raw_merge_range_is_reconcile_error():
    c, data, book = fixture()
    del c["records"][0]["raw_cells"][0]["merge_range"]
    with pytest.raises(ReconcileError) as caught:
        run(c, data, book)
    assert caught.value.code == "candidate_cells"


def test_oversized_merge_coordinate_is_reconcile_error():
    c, data, book = fixture()
    anchor = "A" + "9" * 5000
    region = anchor + ":" + anchor
    c["merge_ranges"] = [region]
    c["records"][0]["raw_cells"][0].update(
        merge_range=region, merge_anchor_cell=anchor)
    with pytest.raises(ReconcileError) as caught:
        run(c, data, book)
    assert caught.value.code == "candidate_merge"


def test_surrogate_sheet_name_is_candidate_json_error():
    c, data, book = fixture()
    c["source"]["sheet_name"] = "\ud800"
    for row in c["records"]:
        row["source_key"]["sheet_name"] = "\ud800"
        row["category_source_key"]["sheet_name"] = "\ud800"
    for category in c["categories"]:
        category["source_key"]["sheet_name"] = "\ud800"
    with pytest.raises(ReconcileError) as caught:
        reconcile(json.dumps(c, ensure_ascii=True).encode(), data, book)
    assert (caught.value.code, caught.value.path) == ("candidate_json", "$")


@pytest.mark.parametrize("group,field", [
    ("raw_cells", field) for field in (
        "cell", "column", "column_name", "header_cell", "value", "xml_value",
        "xml_type", "style_id", "number_format_id", "custom_number_format",
        "formula", "formula_attributes", "raw_state", "merge_range",
        "merge_anchor_cell", "hyperlinks")
] + [
    ("interpreted_cells", field) for field in (
        "cell", "column", "column_name", "value", "value_origin", "value_state",
        "source_cell", "merge_range")
])
def test_cell_schema_requires_fields_even_when_nullable(group, field):
    c, data, book = fixture()
    del c["records"][0][group][0][field]
    with pytest.raises(ReconcileError) as caught:
        run(c, data, book)
    assert caught.value.code == "candidate_cells"


@pytest.mark.parametrize("group,field,value", [
    ("raw_cells", "formula_attributes", []),
    ("raw_cells", "hyperlinks", None),
    ("raw_cells", "xml_value", 1),
    ("raw_cells", "merge_range", []),
    ("raw_cells", "merge_anchor_cell", {}),
    ("interpreted_cells", "value", []),
    ("interpreted_cells", "merge_range", {}),
    ("interpreted_cells", "source_cell", None),
])
def test_cell_schema_rejects_wrong_types(group, field, value):
    c, data, book = fixture()
    c["records"][0][group][0][field] = value
    with pytest.raises(ReconcileError) as caught:
        run(c, data, book)
    assert caught.value.code == "candidate_cells"


@pytest.mark.parametrize("row", [0, -1, 1048577, True, "2", "9" * 5000],
                         ids=["zero", "negative", "past_excel", "bool", "string", "long_string"])
def test_source_row_is_a_bounded_excel_integer(row):
    c, data, book = fixture()
    c["records"][0]["source_key"].update(row_start=row, row_end=row)
    with pytest.raises(ReconcileError) as caught:
        run(c, data, book)
    assert caught.value.code == "source_key"


@pytest.mark.parametrize("reference", ["A0", "A1048577", "A02", "A-1", "K2"])
def test_declared_merge_coordinates_must_be_valid_even_when_unused(reference):
    c, data, book = fixture()
    c["merge_ranges"] = [reference + ":" + reference]
    with pytest.raises(ReconcileError) as caught:
        run(c, data, book)
    assert caught.value.code == "candidate_merge"


@pytest.mark.parametrize("prefix", ["candidate", "export"])
@pytest.mark.parametrize("member", [
    br'"extra": ["\ud800"]', br'"extra": {"\udfff": "value"}',
    b'"extra": [1e999]', b'"extra": [-1e999]',
    b'"extra": NaN', b'"extra": Infinity', b'"extra": -Infinity',
], ids=["high_surrogate", "low_surrogate_key", "overflow", "negative_overflow",
        "nan", "infinity", "negative_infinity"])
def test_json_boundary_rejects_invalid_unicode_and_nonfinite_numbers(prefix, member):
    c, data, book = fixture()
    payload = encoded(c if prefix == "candidate" else existing(c, data))
    payload = payload[:-1] + b"," + member + b"}"
    with pytest.raises(ReconcileError) as caught:
        reconcile(payload if prefix == "candidate" else encoded(c), data, book,
                  payload if prefix == "export" else None)
    assert (caught.value.code, caught.value.path) == (prefix + "_json", "$")


def test_valid_unicode_nullable_cells_and_unknown_fields_are_preserved():
    c, data, book = fixture()
    c["extra"] = {"说明😀": [None, 1.5, "原文\r\n"]}
    c["records"][0]["extra"] = "备注😀\r\n"
    raw = c["records"][0]["raw_cells"][3]
    raw.update(value=None, raw_state="absent_cell")
    c["records"][0]["interpreted_cells"][3]["value"] = None
    e = existing(c, data)
    e["extra"] = copy.deepcopy(c["extra"])
    report = reconcile(json.dumps(c, ensure_ascii=True).encode(), data, book,
                       json.dumps(e, ensure_ascii=True).encode())
    assert report["source_records"] == c["records"]
    assert report["existing_objects"]["metadata"]["extra"] == e["extra"]
    assert len(report["alias_proposals"]) == 1
    assert report["existing_objects"]["collection_state"] == "observed"


def test_excel_row_and_merge_upper_boundary_are_accepted():
    c, data, book = fixture()
    row = 1048576
    record = c["records"][0]
    record["source_key"].update(row_start=row, row_end=row)
    record.update(source_range=f"A{row}:J{row}", category_anchor_cell=f"A{row}")
    for raw, view in zip(record["raw_cells"], record["interpreted_cells"]):
        raw["cell"] = view["cell"] = view["source_cell"] = f"{raw['column']}{row}"
    region = f"A{row}:A{row}"
    c["merge_ranges"] = [region]
    record["raw_cells"][0].update(merge_range=region, merge_anchor_cell=f"A{row}")
    record["interpreted_cells"][0]["merge_range"] = region
    c["categories"][0].update(cell=f"A{row}", merge_range=region)
    report = run(c, data, book)
    assert report["source_records"] == c["records"]
    assert len(report["alias_proposals"]) == 1


def cli_inputs(tmp_path, candidate=None, export=None):
    c, data, book = fixture()
    paths = [tmp_path / n for n in ("candidate.json", "plan.csv", "source.xlsx")]
    for path, payload in zip(paths, (encoded(c if candidate is None else candidate), data, book)):
        path.write_bytes(payload)
    argv = [item for flag, path in zip(("--candidates", "--csv", "--workbook"), paths)
            for item in (flag, str(path))]
    if export is not None:
        path = tmp_path / "export.json"
        path.write_bytes(encoded(export))
        paths.append(path)
        argv += ["--existing-export", str(path)]
    output = tmp_path / "report.json"
    return paths, output, argv + ["--output", str(output)]


def input_hashes(paths):
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]


def test_module_cli_bom_only_csv_returns_fixed_empty_error(tmp_path):
    import subprocess
    inputs, output, argv = cli_inputs(tmp_path)
    inputs[1].write_bytes(b"\xef\xbb\xbf")
    before = input_hashes(inputs)
    result = subprocess.run(
        [sys.executable, "-m", "app.sdk_workbench.source_reconcile_cli", *argv],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == '{"error": "sdk_plan_csv_empty", "path": "csv"}\n'
    assert "Traceback" not in result.stderr
    assert not output.exists()
    assert input_hashes(inputs) == before


def test_cli_output_is_explicit_exclusive_and_cannot_replace_input(tmp_path, capsys):
    from app.sdk_workbench.source_reconcile_cli import main
    inputs, output, argv = cli_inputs(tmp_path)
    before = input_hashes(inputs)
    assert main(argv) == 0
    original = output.read_bytes()
    assert json.loads(original)["existing_objects"]["collection_state"] == "not_collected"
    assert capsys.readouterr() == ("", "")
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "output_exists", "path": "output"}
    assert output.read_bytes() == original
    for target in inputs:
        assert main(argv[:-1] + [str(target)]) == 2
        assert json.loads(capsys.readouterr().err) == {"error": "output_is_input", "path": "output"}
    hardlink = tmp_path / "alias.json"
    hardlink.hardlink_to(inputs[0])
    assert main(argv[:-1] + [str(hardlink)]) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "output_is_input", "path": "output"}
    assert input_hashes(inputs) == before


@pytest.mark.parametrize("kind", ["input", "dangling", "loop"])
def test_cli_never_replaces_existing_symlink(tmp_path, capsys, kind):
    from app.sdk_workbench.source_reconcile_cli import main
    inputs, output, argv = cli_inputs(tmp_path)
    before = input_hashes(inputs)
    target = inputs[0] if kind == "input" else tmp_path / "never-created"
    if kind == "loop":
        target = output
    output.symlink_to(target)
    assert main(argv) == 2
    error = json.loads(capsys.readouterr().err)
    assert error == {"error": "output_is_input" if kind == "input" else "output_exists", "path": "output"}
    assert output.is_symlink()
    assert input_hashes(inputs) == before
    if kind == "dangling":
        assert not target.exists()


@pytest.mark.parametrize("change,expected", [
    (lambda c: c.update(schema_version="private-original-content"), {"error": "candidate_schema", "path": "schema_version"}),
    (lambda c: c["records"][0].update(source_date_interpretations=[dict(cell="E2", raw_value="")]),
     {"error": "candidate_dates", "path": "E2"}),
])
def test_cli_invalid_input_leaves_no_report(tmp_path, capsys, change, expected):
    from app.sdk_workbench.source_reconcile_cli import main
    c, _, _ = fixture()
    change(c)
    inputs, output, argv = cli_inputs(tmp_path, candidate=c)
    before = input_hashes(inputs)
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == expected
    assert not output.exists()
    assert input_hashes(inputs) == before


@pytest.mark.parametrize("payload,expected", [
    (b"private-original-content", {"error": "candidate_json", "path": "$"}),
    (b"{}", {"error": "candidate_schema", "path": "schema_version"}),
])
def test_cli_bad_json_is_sanitized(tmp_path, capsys, payload, expected):
    from app.sdk_workbench.source_reconcile_cli import main
    inputs, output, argv = cli_inputs(tmp_path)
    inputs[0].write_bytes(payload)
    before = input_hashes(inputs)
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().err) == expected
    assert not output.exists()
    assert input_hashes(inputs) == before


@pytest.mark.parametrize("arguments", [[], ["--unknown", "private-argument"],
    ["--candidates"], ["--candidates", "private-argument"],
    ["--candidates", "a", "--csv", "b", "--workbook", "c", "--output", "d", "--unknown=private-argument"],
    ["--candidates", "a", "--csv", "b", "--workbook", "c", "--out", "private-argument"]])
def test_cli_argument_errors_return_fixed_json_without_values(capsys, arguments):
    from app.sdk_workbench.source_reconcile_cli import main
    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "invalid_arguments", "path": "arguments"}


def test_cli_help_returns_zero_without_reading_files(capsys):
    from app.sdk_workbench.source_reconcile_cli import main
    assert main(["--help"]) == 0
    captured = capsys.readouterr()
    assert "--existing-export" in captured.out
    assert captured.err == ""


def test_cli_preserves_csv_error_code_without_original_content(tmp_path, capsys):
    from app.sdk_workbench.source_reconcile_cli import main
    inputs, output, argv = cli_inputs(tmp_path)
    inputs[1].write_bytes(b"private-original-content")
    before = input_hashes(inputs)
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "sdk_plan_csv_header", "path": "csv"}
    assert not output.exists()
    assert input_hashes(inputs) == before


@pytest.mark.parametrize("kind", ["missing_input", "input_directory", "missing_parent", "nul_input", "nul_output"])
def test_cli_file_failures_are_sanitized(tmp_path, capsys, kind):
    from app.sdk_workbench.source_reconcile_cli import main
    inputs, output, argv = cli_inputs(tmp_path)
    before = input_hashes(inputs)
    if kind == "missing_input":
        argv[1] = str(tmp_path / "private-missing-input")
    elif kind == "input_directory":
        argv[1] = str(tmp_path)
    elif kind == "missing_parent":
        argv[-1] = str(tmp_path / "private-missing-parent" / "report.json")
    elif kind == "nul_input":
        argv[1] = "private\x00input"
    else:
        argv[-1] = "private\x00output"
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "file_io", "path": "explicit_files"}
    assert not output.exists()
    assert input_hashes(inputs) == before


def test_cli_optional_export_readonly_and_output_alias_protection(tmp_path, capsys):
    from app.sdk_workbench.source_reconcile_cli import main
    c, data, _ = fixture()
    inputs, output, argv = cli_inputs(tmp_path, export=existing(c, data))
    before = input_hashes(inputs)
    assert main(argv) == 0
    report = json.loads(output.read_bytes())
    assert report["existing_objects"]["collection_state"] == "observed"
    assert report["production_import_eligible"] is False
    assert capsys.readouterr() == ("", "")
    assert main(argv[:-1] + [str(inputs[-1])]) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "output_is_input", "path": "output"}
    assert input_hashes(inputs) == before


def test_cli_output_race_preserves_existing_file(tmp_path, monkeypatch, capsys):
    from app.sdk_workbench import source_reconcile_cli as cli
    inputs, output, argv = cli_inputs(tmp_path)
    before = input_hashes(inputs)
    original = Path.open
    def raced_open(path, mode="r", *args, **kwargs):
        if path == output and mode == "x":
            output.write_bytes(b"other writer")
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", raced_open)
    assert cli.main(argv) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "output_exists", "path": "output"}
    assert output.read_bytes() == b"other writer"
    assert input_hashes(inputs) == before


def test_cli_write_failure_is_sanitized_and_keeps_failure_evidence(tmp_path, monkeypatch, capsys):
    from app.sdk_workbench import source_reconcile_cli as cli
    inputs, output, argv = cli_inputs(tmp_path)
    before = input_hashes(inputs)
    original = Path.open
    class BrokenWriter:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def write(self, value):
            raise OSError("private filesystem details")
    def broken_open(path, mode="r", *args, **kwargs):
        if path == output and mode == "x":
            output.write_bytes(b"partial failure evidence")
            return BrokenWriter()
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", broken_open)
    assert cli.main(argv) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "file_io", "path": "explicit_files"}
    assert output.read_bytes() == b"partial failure evidence"
    assert input_hashes(inputs) == before


def test_cli_does_not_hide_unexpected_core_errors(tmp_path, monkeypatch):
    from app.sdk_workbench import source_reconcile_cli as cli
    _, output, argv = cli_inputs(tmp_path)
    def broken_core(*args):
        raise ValueError("core defect")
    monkeypatch.setattr(cli, "reconcile", broken_core)
    with pytest.raises(ValueError, match="core defect"):
        cli.main(argv)
    assert not output.exists()


def test_cli_offline_boundary_and_module_entrypoint(tmp_path, monkeypatch, capsys):
    import ast
    import subprocess
    from app.sdk_workbench import source_reconcile_cli as cli
    tree = ast.parse(Path(cli.__file__).read_text())
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imports <= {"__future__", "pathlib", "source_reconcile"}
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert names <= {"argparse", "json", "os", "sys"}
    def forbidden(*args, **kwargs):
        raise AssertionError("network called")
    monkeypatch.setattr(socket, "socket", forbidden)
    _, output, argv = cli_inputs(tmp_path)
    assert cli.main(argv) == 0
    assert capsys.readouterr() == ("", "")
    second = tmp_path / "subprocess.json"
    result = subprocess.run([sys.executable, "-m", "app.sdk_workbench.source_reconcile_cli", *argv[:-1], str(second)],
                            capture_output=True, text=True, check=False)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert output.read_bytes() == second.read_bytes()
    failed = subprocess.run([sys.executable, "-m", "app.sdk_workbench.source_reconcile_cli", "--private=secret"],
                            capture_output=True, text=True, check=False)
    assert (failed.returncode, failed.stdout) == (2, "")
    assert json.loads(failed.stderr) == {"error": "invalid_arguments", "path": "arguments"}


def test_real_candidate_csv_end_to_end_if_explicit_paths(tmp_path):
    import os
    names = ("SDK_R0A_CANDIDATES", "SDK_R0A_CSV", "SDK_R0A_WORKBOOK")
    paths = [os.environ.get(name) for name in names]
    if not all(paths):
        pytest.skip("real source paths not explicitly supplied")
    from app.sdk_workbench.source_reconcile_cli import main
    inputs = [Path(p) for p in paths]
    before = input_hashes(inputs)
    assert before == ["fd21cbe900ef08b647a8bc45f7499adc8ae7f9cf02f88c7085cd898c107939b8",
                      "41df018a029f1d27417bec3008bbe7aab3becd785cc38b312c563ce18ee8370d",
                      "402030f9a76fb966b23fc6c9ad843d5f9abd85bcdce1d685835440ae6c51b7d0"]
    argv = ["--candidates", paths[0], "--csv", paths[1], "--workbook", paths[2]]
    reports = []
    for name in ("real-report.json", "again.json"):
        output = tmp_path / name
        assert main(argv + ["--output", str(output)]) == 0
        reports.append(output.read_bytes())
        assert input_hashes(inputs) == before
    assert reports[0] == reports[1]
    report = json.loads(reports[0])
    assert report["control_counts"]["records"] == 69
    assert report["control_counts"]["task_candidate"] == 67
    assert report["control_counts"]["category_only"] == 2
    assert report["control_counts"]["categories"] == 17
    assert len(report["alias_proposals"]) == 69
    assert report["review_queue"] == []
    assert report["existing_objects"]["collection_state"] == "not_collected"
    assert report["production_import_eligible"] is False
