import json
import subprocess

import pytest

from app.sync_remote.config import SyncSource, TableExport
from app.sync_remote.export import (
    ExportError,
    build_remote_program,
    export_source,
    parse_export,
)


FAE = SyncSource(
    kind="fae",
    ssh_host="root@example.test",
    ssh_key_path="/tmp/key",
    remote_python="python",
    remote_container="ai-fae-backend",
    tables=(TableExport("chat_sessions", "chat_sessions"),),
    trace_jsonl_path="/app/data/logs/traces.jsonl",
)


def test_fae_remote_program_contains_only_read_operations() -> None:
    program = build_remote_program(FAE).lower()

    assert "select * from" in program
    assert "trace_jsonl_path" in program
    for statement in ("insert ", "update ", "delete ", "alter ", "drop "):
        assert statement not in program


def test_remote_export_does_not_idle_in_a_transaction_while_streaming() -> None:
    program = build_remote_program(FAE)

    assert "autocommit=True" in program


def test_export_parses_table_rows_and_trace_spans() -> None:
    output = "\n".join(
        (
            json.dumps(
                {
                    "record_type": "table_row",
                    "table": "chat_sessions",
                    "row": {"id": "session-1"},
                }
            ),
            json.dumps(
                {
                    "record_type": "trace_span",
                    "row": {"trace_id": "trace-1", "span_id": "span-1"},
                }
            ),
        )
    )

    bundle = parse_export("fae", output)

    assert bundle.tables["chat_sessions"] == ({"id": "session-1"},)
    assert bundle.trace_spans == ({"trace_id": "trace-1", "span_id": "span-1"},)
    assert bundle.malformed_lines == 0


def test_export_counts_malformed_lines_without_returning_content() -> None:
    bundle = parse_export("fae", "not-json\n")

    assert bundle.malformed_lines == 1
    assert bundle.tables == {}


def test_export_error_is_sanitized() -> None:
    def failing_runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout="secret row", stderr="password=secret")

    with pytest.raises(ExportError, match="remote_export_failed") as error:
        export_source(FAE, runner=failing_runner)

    assert "secret" not in str(error.value)


def test_export_reports_zero_rows_for_expected_tables() -> None:
    def empty_runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    bundle = export_source(FAE, runner=empty_runner)

    assert bundle.tables == {"chat_sessions": ()}
    assert bundle.source_counts["chat_sessions"] == 0
