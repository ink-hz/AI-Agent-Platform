"""DAQ-only source snapshot; never falls back to the camera database/schema."""

from __future__ import annotations

import json
from collections import Counter

from .config import SyncSource
from .export import ExportError

DAQ_TABLES = (
    "chat_sessions",
    "chat_turns",
    "turn_feedback",
    "turn_reviews",
    "eval_candidates",
    "knowledge_improvement_tasks",
    "qa_review_items",
)


def validate_daq_records(output: str) -> None:
    identity = False
    completed = {}
    counts: Counter = Counter()
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            raise ExportError("source_snapshot_incomplete") from None
        if not isinstance(record, dict):
            raise ExportError("source_snapshot_incomplete")
        kind = record.get("record_type")
        if kind == "source_identity":
            if (
                identity
                or record.get("agent_id") != "ai-daq-fae-agent"
                or record.get("schema") != "daq_fae"
            ):
                raise ExportError("source_identity_unverified")
            identity = True
        elif kind in {"table_row", "table_complete"}:
            table = record.get("table")
            if table not in DAQ_TABLES:
                raise ExportError("source_table_not_allowed")
            if kind == "table_row":
                if not isinstance(record.get("row"), dict) or table in completed:
                    raise ExportError("source_snapshot_incomplete")
                counts[table] += 1
            elif table in completed or type(record.get("count")) is not int:
                raise ExportError("source_snapshot_incomplete")
            else:
                completed[table] = record["count"]
        elif kind == "trace_span":
            if not isinstance(record.get("row"), dict):
                raise ExportError("source_snapshot_incomplete")
        elif kind not in {"malformed_trace_line", "trace_file_missing"}:
            raise ExportError("source_snapshot_incomplete")
    if not identity:
        raise ExportError("source_identity_unverified")
    if set(completed) != set(DAQ_TABLES) or any(
        counts[t] != n for t, n in completed.items()
    ):
        raise ExportError("source_snapshot_incomplete")


def build_daq_program(source: SyncSource) -> str:
    if tuple(t.remote_name for t in source.tables) != DAQ_TABLES or any(
        t.target_name != t.remote_name or t.order_by != "id" for t in source.tables
    ):
        raise ExportError("source_table_not_allowed")
    return f"""
import json
import os
from urllib.parse import parse_qsl, urlsplit
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

database_url = os.environ["DAQ_DATABASE_URL"]
if parse_qsl(urlsplit(database_url).query, keep_blank_values=True) != [("options", "-csearch_path=daq_fae")]:
    raise RuntimeError("source_schema_unverified")
trace_jsonl_path = os.environ.get("DAQ_TRACE_LOG_PATH", {source.trace_jsonl_path!r})
tables = {DAQ_TABLES!r}
trace_ids = set()
with psycopg.connect(database_url, row_factory=dict_row, autocommit=True) as connection:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        marker = cursor.execute("select current_schema() as schema, agent_id, database_name = current_database() as valid from daq_fae.daq_installation_identity where singleton").fetchone()
        if marker != {{"schema": "daq_fae", "agent_id": "ai-daq-fae-agent", "valid": True}}:
            raise RuntimeError("source_identity_unverified")
        if cursor.execute("select count(*) as count from daq_fae.chat_sessions where external_session_id not like 'daq:%'").fetchone()["count"]:
            raise RuntimeError("source_identity_unverified")
        if cursor.execute("select count(*) as count from daq_fae.chat_turns where metadata->>'agent_id' is distinct from 'ai-daq-fae-agent'").fetchone()["count"]:
            raise RuntimeError("source_identity_unverified")
        print(json.dumps({{"record_type": "source_identity", "schema": "daq_fae", "agent_id": "ai-daq-fae-agent"}}))
        for table in tables:
            cursor.execute(sql.SQL("select * from {{}}.{{}} order by id").format(sql.Identifier("daq_fae"), sql.Identifier(table)))
            count = 0
            for row in cursor:
                if table == "chat_turns":
                    trace_ids.add(row["trace_id"])
                print(json.dumps({{"record_type": "table_row", "table": table, "row": row}}, ensure_ascii=False, default=str))
                count += 1
            print(json.dumps({{"record_type": "table_complete", "table": table, "count": count}}))

try:
    with open(trace_jsonl_path, encoding="utf-8") as trace_file:
        for line in trace_file:
            try:
                span = json.loads(line)
                if not isinstance(span, dict):
                    raise ValueError()
            except (TypeError, ValueError):
                print(json.dumps({{"record_type": "malformed_trace_line"}}))
                continue
            if span.get("trace_id") in trace_ids:
                print(json.dumps({{"record_type": "trace_span", "row": span}}, ensure_ascii=False, default=str))
except FileNotFoundError:
    print(json.dumps({{"record_type": "trace_file_missing"}}))
""".strip()
