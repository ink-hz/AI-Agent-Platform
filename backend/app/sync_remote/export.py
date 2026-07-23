from __future__ import annotations

import base64
import json
import subprocess
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from .config import SyncSource


Runner = Callable[..., subprocess.CompletedProcess[str]]


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportBundle:
    source_kind: str
    tables: dict[str, tuple[dict, ...]]
    trace_spans: tuple[dict, ...]
    malformed_lines: int

    @property
    def source_counts(self) -> dict[str, int]:
        counts = {table: len(rows) for table, rows in self.tables.items()}
        if self.source_kind == "fae":
            counts["trace_spans"] = len(self.trace_spans)
            counts["malformed_trace_lines"] = self.malformed_lines
        return counts


def _connection_program(kind: str) -> str:
    if kind == "fae":
        return """
from urllib.parse import quote
database_url = (
    "postgresql://" + quote(os.environ["POSTGRES_USER"], safe="") + ":" +
    quote(os.environ["POSTGRES_PASSWORD"], safe="") +
    "@ai-fae-postgres:5432/" + quote(os.environ["POSTGRES_DB"], safe="")
)
"""
    return """
pid = subprocess.check_output(
    ["systemctl", "show", "-p", "MainPID", "--value", "ai-admin-agent"],
    text=True,
).strip()
entries = (
    item.split(b"=", 1)
    for item in open("/proc/" + pid + "/environ", "rb").read().split(b"\\0")
    if b"=" in item
)
environment = dict(entries)
database_url = environment[b"DATABASE_URL"].decode()
"""


def build_remote_program(source: SyncSource) -> str:
    table_specs = [(table.remote_name, table.order_by) for table in source.tables]
    trace_path = source.trace_jsonl_path
    return f"""
import json
import os
import subprocess
import psycopg
from psycopg.rows import dict_row

{_connection_program(source.kind)}

tables = {table_specs!r}
trace_jsonl_path = {trace_path!r}
with psycopg.connect(database_url, row_factory=dict_row, autocommit=True) as connection:
    with connection.cursor() as cursor:
        for table, order_by in tables:
            cursor.execute("select * from " + table + " order by " + order_by)
            for row in cursor.fetchall():
                print(json.dumps(
                    {{"record_type": "table_row", "table": table, "row": row}},
                    ensure_ascii=False,
                    default=str,
                ))

if trace_jsonl_path:
    try:
        with open(trace_jsonl_path, encoding="utf-8") as trace_file:
            for line in trace_file:
                try:
                    span = json.loads(line)
                except (TypeError, ValueError):
                    print(json.dumps({{"record_type": "malformed_trace_line"}}))
                    continue
                print(json.dumps(
                    {{"record_type": "trace_span", "row": span}},
                    ensure_ascii=False,
                    default=str,
                ))
    except FileNotFoundError:
        print(json.dumps({{"record_type": "trace_file_missing"}}))
""".strip()


def build_ssh_command(source: SyncSource) -> list[str]:
    encoded = base64.b64encode(build_remote_program(source).encode()).decode()
    python_command = (
        f"{source.remote_python} -c "
        f"'import base64;exec(base64.b64decode(\"{encoded}\"))'"
    )
    remote_command = (
        f"docker exec -i {source.remote_container} {python_command}"
        if source.remote_container
        else python_command
    )
    return [
        "ssh",
        "-i",
        source.ssh_key_path,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        source.ssh_host,
        remote_command,
    ]


def parse_export(source_kind: str, output: str) -> ExportBundle:
    tables: defaultdict[str, list[dict]] = defaultdict(list)
    spans: list[dict] = []
    malformed = 0
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            malformed += 1
            continue
        if not isinstance(record, dict):
            malformed += 1
            continue
        record_type = record.get("record_type")
        row = record.get("row")
        if record_type == "table_row" and isinstance(record.get("table"), str) and isinstance(row, dict):
            tables[record["table"]].append(row)
        elif record_type == "trace_span" and isinstance(row, dict):
            spans.append(row)
        elif record_type in {"malformed_trace_line", "trace_file_missing"}:
            malformed += 1
        else:
            malformed += 1
    return ExportBundle(
        source_kind=source_kind,
        tables={key: tuple(value) for key, value in tables.items()},
        trace_spans=tuple(spans),
        malformed_lines=malformed,
    )


def export_source(
    source: SyncSource,
    *,
    runner: Runner = subprocess.run,
    timeout_seconds: int = 600,
) -> ExportBundle:
    result = runner(
        build_ssh_command(source),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        raise ExportError("remote_export_failed")
    parsed = parse_export(source.kind, result.stdout)
    return ExportBundle(
        source_kind=parsed.source_kind,
        tables={
            table.remote_name: parsed.tables.get(table.remote_name, ())
            for table in source.tables
        },
        trace_spans=parsed.trace_spans,
        malformed_lines=parsed.malformed_lines,
    )
