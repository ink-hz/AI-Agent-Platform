"""Offline source alias proposals; no application or external I/O."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import hashlib
import json
import re
from .plan_csv import HEADERS, PlanCsvError, parse_plan_csv

RULE = "r0a-compare-1"
FIELDS = ["file_sha256", "sheet_name", "row_start", "row_end"]
COLS = "ABCDEFGHIJ"


class ReconcileError(ValueError):
    def __init__(self, code: str, path: str):
        super().__init__(code)
        self.code, self.path = code, path


def require(condition, code, path):
    if not condition:
        raise ReconcileError(code, path)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode(payload, prefix):
    try:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result
        value = json.loads(payload.decode("utf-8-sig"), object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError("constant")))
    except (UnicodeError, ValueError, RecursionError):
        raise ReconcileError(prefix + "_json", "$") from None
    require(isinstance(value, dict), prefix + "_schema", "$")
    return value


def source_key(value, source, path):
    require(isinstance(value, dict) and set(value) == set(FIELDS), "source_key", path)
    require(value["file_sha256"] == source["file_sha256"] and value["sheet_name"] == source["sheet_name"], "source_key", path)
    require(all(type(value[k]) is int and value[k] > 0 for k in FIELDS[2:]) and value["row_start"] == value["row_end"], "source_key", path)
    return tuple(value[k] for k in FIELDS)


def coord(value):
    found = re.fullmatch(r"([A-J])([1-9][0-9]*)", value or "") if isinstance(value, (str, type(None))) else None
    require(found is not None, "candidate_merge", "cell_reference")
    return COLS.index(found[1]), int(found[2])


def merge_contains(region, cell, anchor):
    require(isinstance(region, str) and region.count(":") == 1, "candidate_merge", "merge_range")
    first, last = region.split(":")
    a, b, c = coord(first), coord(last), coord(cell)
    return anchor == first and a[0] <= c[0] <= b[0] and a[1] <= c[1] <= b[1]


def validate_candidate(payload, workbook):
    c = decode(payload, "candidate")
    require(c.get("schema_version") == "0.1.0" and c.get("protocol_version") == "A-wave-01-scope-v0.1" and c.get("source_key_fields") == FIELDS, "candidate_schema", "schema_version")
    require(c.get("candidate_status") == "preparation_not_business_approved", "candidate_preparation", "candidate_status")
    source = c.get("source")
    require(isinstance(source, dict) and source.get("file_sha256") == digest(workbook) and type(source.get("bytes")) is int and source["bytes"] == len(workbook) and isinstance(source.get("sheet_name"), str) and bool(source["sheet_name"]), "source_digest", "source")
    records, categories, merges = c.get("records"), c.get("categories"), c.get("merge_ranges")
    require(isinstance(records, list) and 0 < len(records) <= 1000 and isinstance(categories, list) and isinstance(merges, list) and all(isinstance(m, str) for m in merges), "candidate_schema", "records")
    seen, cells = set(), {}
    for n, r in enumerate(records):
        path = f"records[{n}]"
        require(isinstance(r, dict), "candidate_schema", path)
        key = source_key(r.get("source_key"), source, path + ".source_key")
        require(key not in seen, "duplicate_source_key", path + ".source_key")
        seen.add(key)
        row = key[2]
        require(r.get("source_range") == f"A{row}:J{row}", "candidate_cells", path + ".source_range")
        raw, interpreted = r.get("raw_cells"), r.get("interpreted_cells")
        require(isinstance(raw, list) and len(raw) == 10 and isinstance(interpreted, list) and len(interpreted) == 10, "candidate_cells", path)
        require(r.get("business_work_id") is None and r.get("business_review_state") == "not_reviewed" and r.get("production_import_eligible") is False, "candidate_preparation", path)
        for col, name, cell, view in zip(COLS, HEADERS, raw, interpreted):
            require(isinstance(cell, dict) and isinstance(view, dict), "candidate_cells", path)
            require(cell.get("cell") == f"{col}{row}" and cell.get("column") == col and cell.get("column_name") == name and cell.get("header_cell") == f"{col}1", "candidate_cells", path)
            require(view.get("cell") == cell["cell"] and view.get("column") == col and view.get("column_name") == name, "candidate_cells", path)
            require(all(k in cell and (cell[k] is None or isinstance(cell[k], str)) for k in ("value", "xml_value", "xml_type", "style_id", "number_format_id", "custom_number_format", "formula")), "candidate_cells", path)
            require(cell.get("formula_attributes") is None or isinstance(cell["formula_attributes"], dict), "candidate_cells", path)
            require(isinstance(cell.get("hyperlinks"), list), "candidate_cells", path)
            state, value = cell.get("raw_state"), cell["value"]
            require((state == "value" and isinstance(value, str) and bool(value)) or (state == "empty_string" and value == "") or (state in ("empty_cell_element", "absent_cell") and value is None), "candidate_cells", path)
            region, anchor = cell.get("merge_range"), cell.get("merge_anchor_cell")
            require((region is None and anchor is None) or (region in merges and merge_contains(region, cell["cell"], anchor)), "candidate_merge", path)
            cells[cell["cell"]] = cell
        task = bool(raw[1]["value"] and raw[1]["value"].strip())
        require(r.get("record_type") == ("task_candidate" if task else "category_only"), "candidate_counts", path + ".record_type")
    for n, r in enumerate(records):
        for raw, view in zip(r["raw_cells"], r["interpreted_cells"]):
            path = f"records[{n}].interpreted_cells.{raw['column']}"
            origin = view.get("value_origin")
            if origin == "merge_anchor":
                anchor = raw.get("merge_anchor_cell")
                require(raw.get("merge_range") in merges and anchor in cells and view.get("source_cell") == anchor and view.get("merge_range") == raw["merge_range"] and view.get("value") == cells[anchor]["value"] and bool(view["value"]) and view.get("value_state") == "present", "candidate_merge", path)
            else:
                expected = "source_cell" if raw["value"] not in (None, "") else "source_blank"
                require(origin == expected and view.get("source_cell") == raw["cell"] and view.get("value") == raw["value"] and view.get("value_state") == ("present" if expected == "source_cell" else "source_blank"), "candidate_cells", path)
            require(view.get("merge_range") == raw["merge_range"], "candidate_merge", path)
        dates = r.get("source_date_interpretations")
        require(isinstance(dates, list) and all(isinstance(x, dict) and isinstance(x.get("cell"), str) for x in dates), "candidate_dates", f"records[{n}].source_date_interpretations")
        by_cell = {x["cell"]: x for x in dates}
        nonempty = {x["cell"] for x in r["raw_cells"][4:6] if x["value"] not in (None, "")}
        require(len(by_cell) == len(dates) and set(by_cell) <= {x["cell"] for x in r["raw_cells"][4:6]} and nonempty <= set(by_cell), "candidate_dates", f"records[{n}].source_date_interpretations")
        for name, item in by_cell.items():
            raw = cells[name]
            require("raw_value" in item and item["raw_value"] == raw["value"] and "interpreted_iso" in item and (item["interpreted_iso"] is None or isinstance(item["interpreted_iso"], str)), "candidate_dates", name)
            if raw["value"] not in (None, ""):
                value = raw["value"]
                require(value.isdecimal(), "candidate_dates", name)
                try:
                    serial = int(value)
                except ValueError:
                    raise ReconcileError("candidate_dates", name) from None
                require(serial != 60 and 1 <= serial <= 2958465 and item.get("date_system") == "1900" and item.get("precision") == "day" and item.get("timezone") is None and raw["number_format_id"] == "14" and raw["formula"] is None, "candidate_dates", name)
                iso = (date(1899, 12, 31) + timedelta(days=serial if serial < 60 else serial - 1)).isoformat()
                require(item.get("interpreted_iso") == iso, "candidate_dates", name)
            else:
                require(item.get("interpreted_iso") is None, "candidate_dates", name)
    anchors = {}
    for n, cat in enumerate(categories):
        require(isinstance(cat, dict), "candidate_cells", f"categories[{n}]")
        key = source_key(cat.get("source_key"), source, f"categories[{n}].source_key")
        name = cat.get("cell")
        require(name == f"A{key[2]}" and name in cells and name not in anchors and cat.get("value") == cells[name]["value"] and bool(cat.get("value")) and cat.get("merge_range") == cells[name]["merge_range"], "candidate_cells", f"categories[{n}]")
        anchors[name] = cat
    for r in records:
        name = r.get("category_anchor_cell")
        require(isinstance(name, str) and name in anchors and r.get("category_source_key") == anchors[name]["source_key"] and r["interpreted_cells"][0]["source_cell"] == name, "candidate_merge", "category_anchor_cell")
    count = Counter(r["record_type"] for r in records)
    status = Counter((r["raw_cells"][8]["value"] or "source_blank") for r in records if r["record_type"] == "task_candidate")
    actual = dict(records=len(records), task_candidate=count["task_candidate"], category_only=count["category_only"], categories=len(categories), task_original_status=dict(status))
    declared = c.get("control_counts")
    require(isinstance(declared, dict) and all(type(declared.get(k)) is int for k in ("records", "task_candidate", "category_only", "categories")) and isinstance(declared.get("task_original_status"), dict) and all(type(v) is int for v in declared["task_original_status"].values()) and declared == actual, "candidate_counts", "control_counts")
    c["records"] = sorted(records, key=lambda r: tuple(r["source_key"][k] for k in FIELDS))
    return c


def text(value):
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def candidate_signature(row):
    values = [text(c["value"]) for c in row["raw_cells"]]
    dates = {x["cell"]: x["interpreted_iso"] for x in row["source_date_interpretations"]}
    for index in (4, 5):
        values[index] = dates.get(row["raw_cells"][index]["cell"]) or values[index]
    return (row["record_type"], *values, text(row["interpreted_cells"][0]["value"]))


def csv_signature(row):
    values = list(map(text, row.cells))
    for index in (4, 5):
        value = values[index]
        if re.fullmatch(r"[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}", value):
            try:
                values[index] = date(*map(int, value.split("/"))).isoformat()
            except ValueError:
                pass
    return ("task_candidate" if row.kind == "task" else "category_only", *values, text(row.module))


def validate_export(payload):
    e = decode(payload, "export")
    require(e.get("schema_version") == "r0a-existing-1", "export_schema", "schema_version")
    require(all(isinstance(e.get(k), str) and bool(e[k]) for k in ("instance_id", "zentao_version", "exported_at")) and e.get("evidence_state") in ("current", "historical") and type(e.get("complete")) is bool, "export_schema", "metadata")
    try:
        stamp = datetime.fromisoformat(e["exported_at"])
        require(stamp.utcoffset() is not None, "export_schema", "exported_at")
    except ValueError:
        raise ReconcileError("export_schema", "exported_at") from None
    scope = e.get("scope")
    require(isinstance(scope, dict) and isinstance(scope.get("csv_sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", scope["csv_sha256"]) is not None and scope.get("native_object_type") == "story" and isinstance(scope.get("native_ids"), list) and all(isinstance(v, str) and bool(v) for v in scope["native_ids"]), "export_schema", "scope")
    require(isinstance(e.get("rows"), list) and len(e["rows"]) <= 1000, "export_schema", "rows")
    for n, row in enumerate(e["rows"]):
        require(isinstance(row, dict) and type(row.get("logical_record_number")) is int and row["logical_record_number"] >= 2 and all(isinstance(row.get(k), str) and bool(row[k]) for k in ("plan_import_id", "plan_row_id", "native_id", "native_version", "native_state")), "export_schema", f"rows[{n}]")
        require(not {"source_key", "collection_state", "review_state"}.intersection(row), "export_schema", f"rows[{n}]")
    return e


def reconcile(candidate_payload: bytes, csv_payload: bytes, workbook_payload: bytes,
              export_payload: bytes | None = None) -> dict:
    c = validate_candidate(candidate_payload, workbook_payload)
    try:
        parsed = parse_plan_csv(csv_payload)
    except PlanCsvError as error:
        raise ReconcileError(str(error), "csv") from None
    sources, old = defaultdict(list), defaultdict(list)
    for r in c["records"]:
        sources[candidate_signature(r)].append(r)
    for r in parsed.rows:
        old[csv_signature(r)].append(r)
    proposals, queue, by_record = [], [], {}
    for row in parsed.rows:
        signature = csv_signature(row)
        possible = sources.get(signature, [])
        ref = dict(csv_file_sha256=parsed.sha256, logical_record_number=row.record_number)
        if len(possible) == 1 and len(old[signature]) == 1:
            endpoint = dict(source_key=possible[0]["source_key"], csv_ref=ref, rule_version=RULE)
            proposal = dict(**endpoint, proposal_id=digest(canonical(endpoint).encode()), review_state="not_reviewed", relationship_type="csv_alias_candidate")
            proposals.append(proposal)
            by_record[row.record_number] = proposal
        else:
            queue.append(dict(code="ambiguous_csv_alias" if possible else "unmatched_csv", csv_ref=ref, source_keys=[r["source_key"] for r in possible]))
    for r in c["records"]:
        if not old.get(candidate_signature(r)):
            queue.append(dict(code="unmatched_source", source_key=r["source_key"]))
    existing = dict(collection_state="not_collected", metadata=None, links=[], uncovered_csv_records=[r.record_number for r in parsed.rows if r.kind == "task"], uncovered_state="unknown")
    if export_payload is not None:
        e = validate_export(export_payload)
        conflicts = []
        if e["scope"]["csv_sha256"] != parsed.sha256:
            conflicts.append(dict(code="export_scope_mismatch"))
        if len(set(e["scope"]["native_ids"])) != len(e["scope"]["native_ids"]) or set(e["scope"]["native_ids"]) != {r["native_id"] for r in e["rows"]}:
            conflicts.append(dict(code="export_native_scope_mismatch"))
        record_counts = Counter(r["logical_record_number"] for r in e["rows"])
        row_counts = Counter((r["plan_import_id"], r["plan_row_id"]) for r in e["rows"])
        native_counts = Counter(r["native_id"] for r in e["rows"])
        task_numbers = {r.record_number for r in parsed.rows if r.kind == "task"}
        for row in e["rows"]:
            n = row["logical_record_number"]
            if record_counts[n] > 1 or row_counts[(row["plan_import_id"], row["plan_row_id"])] > 1 or native_counts[row["native_id"]] > 1:
                conflicts.append(dict(code="legacy_one_to_one_conflict", exported_row=row))
            if n not in task_numbers or n not in by_record:
                conflicts.append(dict(code="export_alias_unresolved", exported_row=row))
        state = "observed" if e["complete"] and e["evidence_state"] == "current" and not conflicts else "unknown"
        existing = dict(collection_state=state, metadata={k: v for k, v in e.items() if k != "rows"}, links=[], uncovered_csv_records=sorted(task_numbers - set(record_counts)), uncovered_state="unknown")
        for row in e["rows"]:
            proposal = by_record.get(row["logical_record_number"])
            existing["links"].append(dict(**row, source_key=proposal["source_key"] if proposal and not conflicts else None, collection_state=state, review_state="not_reviewed"))
        queue.extend(conflicts)
    return dict(schema_version="r0a-report-1", rule_version=RULE,
                candidate_status=c["candidate_status"], production_import_eligible=False,
                input_sha256=dict(candidate=digest(candidate_payload), csv=parsed.sha256, workbook=digest(workbook_payload), existing_export=digest(export_payload) if export_payload is not None else None),
                source_validation="digest_and_structure_only_not_xlsx_reextraction",
                control_counts=c["control_counts"], source_records=c["records"],
                csv_counts=dict(records=len(parsed.rows), task=parsed.task_count, modules=parsed.module_count),
                csv_records=[dict(csv_file_sha256=parsed.sha256, logical_record_number=r.record_number, physical_start_line=r.physical_line, kind=r.kind, module=r.module, cells=list(r.cells), named_cells=dict(zip(HEADERS, r.cells))) for r in parsed.rows],
                alias_proposals=proposals, existing_objects=existing, review_queue=queue,
                compatibility_issues=[dict(code="legacy_one_to_one_not_general_relation_store", evidence=["backend/control_migrations/117_sdk_plan_intake.sql", "backend/control_migrations/121_sdk_plan_semantics.sql"], action="review_only_no_schema_or_object_changes")])
