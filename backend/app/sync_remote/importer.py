from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from .export import ExportBundle


class ImportValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedRow:
    target_schema: str
    target_table: str
    values: dict[str, Any]
    conflict_columns: tuple[str, ...]


@dataclass(frozen=True)
class SyncResult:
    source_kind: str
    status: str
    source_counts: dict[str, int]
    applied_counts: dict[str, int]
    validation: dict[str, int]


FAE_COLUMNS: dict[str, tuple[str, ...]] = {
    "chat_sessions": (
        "id", "external_session_id", "channel", "user_id", "external_user_id",
        "conversation_title", "created_at", "last_active_at",
    ),
    "chat_turns": (
        "id", "session_id", "external_session_id", "turn_index", "trace_id",
        "channel", "question", "answer", "sources", "stages", "done",
        "planned_capabilities", "capability_coverage", "fallback_used",
        "fallback_reason", "outcome", "duration_ms", "created_at",
    ),
    "turn_feedback": (
        "id", "turn_id", "external_session_id", "trace_id", "rating",
        "reason_code", "comment", "channel", "user_id", "external_user_id",
        "created_at",
    ),
    "turn_reviews": (
        "id", "turn_id", "priority", "review_status", "failure_layer",
        "failure_reason", "expected_answer_notes", "corrected_answer", "reviewer",
        "should_add_to_eval", "should_update_knowledge", "created_at", "updated_at",
    ),
    "eval_candidates": (
        "id", "turn_id", "candidate_status", "testset_name", "case_json",
        "exported_path", "created_at", "exported_at",
    ),
    "knowledge_improvement_tasks": (
        "id", "turn_id", "task_status", "knowledge_area", "gap_summary",
        "proposed_source", "owner", "created_at", "updated_at",
    ),
    "qa_review_items": (
        "id", "source_type", "source_ref", "turn_id", "question",
        "original_answer", "reviewed_answer", "product_tags", "technical_tags",
        "review_status", "reviewer", "review_notes", "created_at", "updated_at",
    ),
}


ADMIN_COLUMNS: dict[str, tuple[str, ...]] = {
    "admin_chat_sessions": FAE_COLUMNS["chat_sessions"],
    "admin_chat_turns": (
        "id", "session_id", "external_session_id", "turn_index", "trace_id",
        "channel", "question", "answer", "sources", "source_groups", "stages",
        "done", "fallback_used", "fallback_reason", "outcome", "duration_ms",
        "created_at",
    ),
    "admin_turn_feedback": FAE_COLUMNS["turn_feedback"],
    "admin_turn_reviews": (
        "id", "turn_id", "feedback_id", "external_session_id", "trace_id",
        "reviewer", "verdict", "severity", "failure_layer", "capability_batch",
        "scope", "notes", "suggested_action", "created_at",
    ),
    "admin_eval_candidates": (
        "id", "review_id", "source_turn_id", "source_feedback_id", "case_id",
        "question", "sample_type", "capability_batch", "scope", "case_json",
        "status", "created_at",
    ),
    "admin_knowledge_improvement_tasks": (
        "id", "review_id", "source_turn_id", "source_feedback_id", "task_type",
        "failure_layer", "title", "description", "status", "priority",
        "source_refs", "created_at", "updated_at",
    ),
}


def _target_table(source_kind: str, source_table: str) -> tuple[str, str]:
    if source_kind == "fae":
        if source_table not in FAE_COLUMNS:
            raise ImportValidationError("unknown_source_table")
        return "platform_source_fae", source_table
    if source_kind == "admin" and source_table in ADMIN_COLUMNS:
        return "platform_source_admin", source_table.removeprefix("admin_")
    raise ImportValidationError("unknown_source_table")


def normalize_row(
    source_kind: str,
    source_table: str,
    row: dict,
    synced_at: datetime,
) -> NormalizedRow:
    schema, table = _target_table(source_kind, source_table)
    columns = (
        FAE_COLUMNS[source_table]
        if source_kind == "fae"
        else ADMIN_COLUMNS[source_table]
    )
    if not row.get("id"):
        raise ImportValidationError("missing_required_key")
    values = {column: row.get(column) for column in columns}
    metadata = row.get("metadata")
    details = dict(metadata) if isinstance(metadata, dict) else {}
    known = set(columns) | {"metadata"}
    details.update({key: value for key, value in row.items() if key not in known})
    values["details"] = details
    values["source_synced_at"] = synced_at
    return NormalizedRow(schema, table, values, ("id",))


def normalize_trace_span(row: dict, synced_at: datetime) -> NormalizedRow:
    for key in ("trace_id", "span_id", "node", "started_at"):
        if not row.get(key):
            raise ImportValidationError("missing_required_key")
    values = {
        "trace_id": row["trace_id"],
        "span_id": row["span_id"],
        "parent_span_id": row.get("parent_span_id"),
        "record_type": row.get("type", "span"),
        "node": row["node"],
        "started_at": row["started_at"],
        "ended_at": row.get("ended_at"),
        "duration_ms": row.get("duration_ms"),
        "input_summary": row.get("input_summary") or {},
        "output_summary": row.get("output_summary") or {},
        "metadata": row.get("metadata") or {},
        "error": row.get("error"),
        "details": {"is_root": bool(row.get("is_root"))},
        "source_synced_at": synced_at,
    }
    return NormalizedRow(
        "platform_source_fae",
        "trace_spans",
        values,
        ("trace_id", "span_id"),
    )


def validate_bundle(bundle: ExportBundle) -> dict[str, int]:
    required: dict[str, tuple[str, ...]] = {
        "chat_sessions": ("id",),
        "admin_chat_sessions": ("id",),
        "chat_turns": ("id", "session_id", "trace_id"),
        "admin_chat_turns": ("id", "session_id", "trace_id"),
    }
    for table, rows in bundle.tables.items():
        for row in rows:
            if not row.get("id"):
                raise ImportValidationError("missing_required_key")
            for key in required.get(table, ()):
                if not row.get(key):
                    raise ImportValidationError("missing_required_key")

    session_table = "chat_sessions" if bundle.source_kind == "fae" else "admin_chat_sessions"
    turn_table = "chat_turns" if bundle.source_kind == "fae" else "admin_chat_turns"
    session_ids = {str(row["id"]) for row in bundle.tables.get(session_table, ()) if row.get("id")}
    turns = bundle.tables.get(turn_table, ())
    orphan_turn_sessions = sum(
        1 for row in turns if row.get("session_id") and str(row["session_id"]) not in session_ids
    )
    root_trace_ids = {
        str(row["trace_id"])
        for row in bundle.trace_spans
        if row.get("trace_id") and (row.get("type") == "root" or not row.get("parent_span_id"))
    }
    turns_without_root = (
        sum(1 for row in turns if str(row.get("trace_id")) not in root_trace_ids)
        if bundle.source_kind == "fae"
        else 0
    )
    return {
        "orphan_turn_sessions": orphan_turn_sessions,
        "turns_without_root_span": turns_without_root,
        "malformed_trace_lines": bundle.malformed_lines,
    }


def _adapt(value: Any) -> Any:
    return Jsonb(value) if isinstance(value, (dict, list)) else value


def _upsert(cursor, row: NormalizedRow) -> None:
    columns = tuple(row.values)
    updates = tuple(column for column in columns if column not in row.conflict_columns)
    statement = sql.SQL(
        "insert into {}.{} ({}) values ({}) on conflict ({}) do update set {}"
    ).format(
        sql.Identifier(row.target_schema),
        sql.Identifier(row.target_table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.SQL(", ").join(map(sql.Identifier, row.conflict_columns)),
        sql.SQL(", ").join(
            sql.SQL("{} = excluded.{}").format(sql.Identifier(column), sql.Identifier(column))
            for column in updates
        ),
    )
    cursor.execute(statement, tuple(_adapt(row.values[column]) for column in columns))


def import_bundle(
    database_url: str,
    bundle: ExportBundle,
    *,
    now: datetime | None = None,
) -> SyncResult:
    synced_at = now or datetime.now(timezone.utc)
    validation: dict[str, int] = {}
    applied: dict[str, int] = {}
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into platform_sync.runs (source_kind, status, source_counts)
                values (%s, 'running', %s) returning id
                """,
                (bundle.source_kind, Jsonb(bundle.source_counts)),
            )
            run_id = cursor.fetchone()[0]
        connection.commit()
        try:
            validation = validate_bundle(bundle)
            with connection.transaction():
                with connection.cursor() as cursor:
                    for table, rows in bundle.tables.items():
                        for source_row in rows:
                            _upsert(
                                cursor,
                                normalize_row(bundle.source_kind, table, source_row, synced_at),
                            )
                        applied[table] = len(rows)
                    if bundle.source_kind == "fae":
                        for span in bundle.trace_spans:
                            _upsert(cursor, normalize_trace_span(span, synced_at))
                        applied["trace_spans"] = len(bundle.trace_spans)
                    cursor.execute(
                        """
                        update platform_sync.runs set status='succeeded', completed_at=now(),
                          applied_counts=%s, validation=%s where id=%s
                        """,
                        (Jsonb(applied), Jsonb(validation), run_id),
                    )
        except Exception as error:
            connection.rollback()
            summary = (
                "validation_failed"
                if isinstance(error, ImportValidationError)
                else "local_import_failed"
            )
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update platform_sync.runs set status='failed', completed_at=now(),
                          validation=%s, error_summary=%s where id=%s
                        """,
                        (Jsonb(validation), summary, run_id),
                    )
            raise
    return SyncResult(
        source_kind=bundle.source_kind,
        status="succeeded",
        source_counts=bundle.source_counts,
        applied_counts=applied,
        validation=validation,
    )

