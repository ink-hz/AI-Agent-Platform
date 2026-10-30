from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.review.backfill import backfill_negative_feedback
from app.review.models import BackfillReport

from .export import ExportBundle
from .identity_matcher import normalize_display_name


class ImportValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedRow:
    target_schema: str
    target_table: str
    values: dict[str, Any]
    conflict_columns: tuple[str, ...]
    preserve_on_null_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncResult:
    run_id: str
    source_kind: str
    status: str
    source_counts: dict[str, int]
    applied_counts: dict[str, int]
    validation: dict[str, int]


@dataclass(frozen=True)
class CoordinatedSyncResult:
    source_sync: SyncResult
    review_backfill: BackfillReport


class ReviewBackfillError(RuntimeError):
    def __init__(self, source_sync: SyncResult) -> None:
        self.source_sync = source_sync
        self.reason = "review_backfill_failed"
        super().__init__(self.reason)


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
        "question_at", "answer_at",
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


DAQ_COLUMNS = {**FAE_COLUMNS, "chat_sessions": FAE_COLUMNS["chat_sessions"] + (
    "owner_subject_id", "owner_subject_type",
)}

VOC_COLUMNS = {
    "chat_sessions": FAE_COLUMNS["chat_sessions"] + (
        "owner_subject_id", "owner_subject_type", "primary_sender_name", "primary_sender_department",
    ),
    "chat_turns": FAE_COLUMNS["chat_turns"],
}


ADMIN_COLUMNS: dict[str, tuple[str, ...]] = {
    "admin_chat_sessions": FAE_COLUMNS["chat_sessions"],
    "admin_chat_turns": (
        "id", "session_id", "external_session_id", "turn_index", "trace_id",
        "channel", "question", "answer", "sources", "source_groups", "stages",
        "done", "fallback_used", "fallback_reason", "outcome", "duration_ms",
        "created_at", "question_at", "answer_at",
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
    "admin_directory_members": (
        "staff_id", "display_name", "departments", "active",
        "source_updated_at", "source_synced_at",
    ),
}


PRESERVE_ON_NULL_COLUMNS: dict[tuple[str, str], tuple[str, ...]] = {
    ("fae", "chat_turns"): ("question_at", "answer_at"),
    ("daq", "chat_turns"): ("question_at", "answer_at"),
    ("admin", "admin_chat_turns"): ("question_at", "answer_at"),
}

_SUBJECT_EVIDENCE_COLUMNS = {
    "internal_user_id", "verification_method", "verified_at"
}


def normalize_session_subject_link(
    row: dict[str, Any], synced_at: datetime
) -> NormalizedRow | None:
    """Accept only an explicit subject minted from a verified Platform Session."""

    try:
        native_session_id = str(row.get("id") or "").strip()
        internal_user_id = UUID(str(row.get("internal_user_id") or ""))
        verification_method = row.get("verification_method")
        verified_at = row.get("verified_at")
        verified_time = (
            datetime.fromisoformat(verified_at)
            if isinstance(verified_at, str) else verified_at
        )
        if (
            not native_session_id
            or verification_method != "platform_session"
            or not isinstance(verified_time, datetime)
            or verified_time.tzinfo is None
            or row.get("user_id") != str(internal_user_id)
            or row.get("external_user_id") != f"platform:{internal_user_id}"
        ):
            return None
    except (TypeError, ValueError):
        return None
    return NormalizedRow(
        "platform_identity",
        "session_subject_links",
        {
            "source_kind": "admin",
            "native_session_id": native_session_id,
            "internal_user_id": str(internal_user_id),
            "verification_method": verification_method,
            "verified_at": verified_at,
            "source_synced_at": synced_at,
        },
        ("source_kind", "native_session_id"),
    )


def _target_table(source_kind: str, source_table: str) -> tuple[str, str]:
    if source_kind == "voc":
        if source_table not in VOC_COLUMNS:
            raise ImportValidationError("unknown_source_table")
        return "platform_source_voc", source_table
    if source_kind in {"fae", "daq"}:
        if source_table not in FAE_COLUMNS:
            raise ImportValidationError("unknown_source_table")
        return f"platform_source_{source_kind}", source_table
    if source_kind == "admin" and source_table == "admin_directory_members":
        return "platform_identity", "dingtalk_directory_members"
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
    if source_kind == "admin" and source_table == "admin_directory_members":
        staff_id = str(row.get("staff_id") or "").strip()
        display_name = normalize_display_name(row.get("display_name"))
        if not staff_id or not display_name:
            raise ImportValidationError("missing_required_key")
        departments = [
            normalized
            for value in row.get("departments") or ()
            if (normalized := normalize_display_name(value))
        ]
        return NormalizedRow(
            schema,
            table,
            {
                "staff_id": staff_id,
                "display_name": display_name,
                "normalized_name": display_name,
                "departments": list(dict.fromkeys(departments)),
                "active": bool(row.get("active", True)),
                "source_updated_at": row.get("source_updated_at"),
                "source_synced_at": synced_at,
            },
            ("staff_id",),
        )
    columns = VOC_COLUMNS[source_table] if source_kind == "voc" else (
        (DAQ_COLUMNS if source_kind == "daq" else FAE_COLUMNS)[source_table]
        if source_kind in {"fae", "daq"}
        else ADMIN_COLUMNS[source_table]
    )
    if not row.get("id"):
        raise ImportValidationError("missing_required_key")
    values = {column: row.get(column) for column in columns}
    metadata = row.get("metadata")
    details = dict(metadata) if isinstance(metadata, dict) else {}
    known = set(columns) | {"metadata"}
    if source_kind == "admin" and source_table == "admin_chat_sessions":
        known |= _SUBJECT_EVIDENCE_COLUMNS
    details.update({key: value for key, value in row.items() if key not in known})
    if source_kind == "voc" and source_table == "chat_sessions":
        details.pop("verified_subject", None)
        if row.get("owner_subject_id") is not None:
            try:
                owner = str(UUID(str(row["owner_subject_id"])))
            except (ValueError, TypeError, AttributeError):
                raise ImportValidationError("source_identity_unverified") from None
            if row.get("owner_subject_type") != "enterprise_member" or row.get("user_id") != owner:
                raise ImportValidationError("source_identity_unverified")
            # The replica's verified_subject contract is FAE-scoped. Keep VOC's
            # canonical owner in its private mirror, not in that wire claim.
    if source_kind in {"fae", "daq"} and source_table == "chat_sessions":
        # Metadata claims never establish ownership. Only server owner columns
        # plus their persisted authentication mode can mint this projection.
        details.pop("verified_subject", None)
        verified_subject = (_daq_verified_subject(row) if source_kind == "daq"
                            else _fae_verified_subject(row))
        if verified_subject is not None:
            details["verified_subject"] = verified_subject
    values["details"] = details
    values["source_synced_at"] = synced_at
    return NormalizedRow(
        schema,
        table,
        values,
        ("id",),
        PRESERVE_ON_NULL_COLUMNS.get((source_kind, source_table), ()),
    )


def _fae_verified_subject(row: dict[str, Any]) -> dict[str, str] | None:
    owner_id = row.get("owner_subject_id")
    owner_type = row.get("owner_subject_type")
    if owner_id is None and owner_type is None:
        return None
    try:
        if not isinstance(owner_id, (str, UUID)):
            raise ValueError
        subject_id = str(UUID(str(owner_id)))
        metadata = row.get("metadata")
        mode = (
            metadata.get("authentication_mode") if isinstance(metadata, dict) else None
        )
        if owner_type == "partner_operator":
            if mode != "platform_partner" or row.get("user_id") is not None:
                raise ValueError
        elif owner_type == "enterprise_member":
            if (
                mode != "platform_enterprise"
                or str(UUID(str(row.get("user_id")))) != subject_id
            ):
                raise ValueError
        else:
            raise ValueError
        # FAE's persisted internal identity is user_id. If an export also
        # carries an explicit internal_user_id, it must agree with that column.
        if row.get("internal_user_id") is not None and (
            owner_type != "enterprise_member"
            or str(UUID(str(row["internal_user_id"]))) != subject_id
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise ImportValidationError("fae_session_owner_invalid") from None
    return {"subject_id": subject_id, "subject_type": owner_type}


def _daq_verified_subject(row: dict[str, Any]) -> dict[str, str] | None:
    """DAQ accepts enterprise login only; unconfirmed history stays unknown."""
    if row.get("owner_subject_type") != "enterprise_member":
        return None
    try:
        return _fae_verified_subject(row)
    except ImportValidationError:
        return None


def normalize_trace_span(row: dict, synced_at: datetime, *, source_kind: str = "fae") -> NormalizedRow:
    if source_kind not in {"fae", "daq"}:
        raise ImportValidationError("unknown_source_kind")
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
        "details": {"is_root": row.get("type") == "root" or bool(row.get("is_root"))},
        "source_synced_at": synced_at,
    }
    return NormalizedRow(
        f"platform_source_{source_kind}",
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
        _target_table(bundle.source_kind, table)
        for row in rows:
            if bundle.source_kind == "voc" and (
                not str(row.get("external_session_id", "")).startswith("voc:")
                or (table == "chat_turns" and (row.get("metadata") or {}).get("agent_id") != "voc")
            ):
                raise ImportValidationError("source_identity_unverified")
            if bundle.source_kind == "daq" and (
                (table == "chat_sessions" and not str(row.get("external_session_id", "")).startswith("daq:"))
                or (table == "chat_turns" and (row.get("metadata") or {}).get("agent_id") != "ai-daq-fae-agent")
            ):
                raise ImportValidationError("source_identity_unverified")
            identity_key = "staff_id" if table == "admin_directory_members" else "id"
            if not row.get(identity_key):
                raise ImportValidationError("missing_required_key")
            for key in required.get(table, ()):
                if bundle.source_kind == "voc" and key == "trace_id":
                    continue
                if not row.get(key):
                    raise ImportValidationError("missing_required_key")

    session_table = "chat_sessions" if bundle.source_kind in {"fae", "daq", "voc"} else "admin_chat_sessions"
    turn_table = "chat_turns" if bundle.source_kind in {"fae", "daq", "voc"} else "admin_chat_turns"
    session_ids = {str(row["id"]) for row in bundle.tables.get(session_table, ()) if row.get("id")}
    turns = bundle.tables.get(turn_table, ())
    orphan_turn_sessions = sum(
        1 for row in turns if row.get("session_id") and str(row["session_id"]) not in session_ids
    )
    if bundle.source_kind == "daq":
        trace_ids = {row["trace_id"] for row in turns}
        for span in bundle.trace_spans:
            if span.get("trace_id") not in trace_ids or (
                (span.get("type") == "root" or not span.get("parent_span_id"))
                and (span.get("metadata") or {}).get("agent_id") != "ai-daq-fae-agent"
            ):
                raise ImportValidationError("source_trace_unverified")
    root_trace_ids = {
        str(row["trace_id"])
        for row in bundle.trace_spans
        if row.get("trace_id") and (row.get("type") == "root" or not row.get("parent_span_id"))
    }
    turns_without_root = (
        sum(1 for row in turns if str(row.get("trace_id")) not in root_trace_ids)
        if bundle.source_kind in {"fae", "daq"}
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
            (
                sql.SQL("{} = COALESCE(EXCLUDED.{}, {}.{})").format(
                    sql.Identifier(column),
                    sql.Identifier(column),
                    sql.Identifier(row.target_table),
                    sql.Identifier(column),
                )
                if column in row.preserve_on_null_columns
                else sql.SQL("{} = EXCLUDED.{}").format(
                    sql.Identifier(column),
                    sql.Identifier(column),
                )
            )
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
                    linked_subjects = 0
                    unresolved_subjects = 0
                    if bundle.source_kind == "admin":
                        cursor.execute(
                            "delete from platform_identity.session_subject_links "
                            "where source_kind='admin'"
                        )
                    for table, rows in bundle.tables.items():
                        for source_row in rows:
                            _upsert(
                                cursor,
                                normalize_row(bundle.source_kind, table, source_row, synced_at),
                            )
                            if bundle.source_kind == "admin" and table == "admin_chat_sessions":
                                subject_link = normalize_session_subject_link(
                                    source_row, synced_at
                                )
                                if subject_link is None:
                                    unresolved_subjects += 1
                                else:
                                    _upsert(cursor, subject_link)
                                    linked_subjects += 1
                        applied[table] = len(rows)
                    if bundle.source_kind == "admin":
                        validation["admin_sessions_subject_linked"] = linked_subjects
                        validation["admin_sessions_subject_unresolved"] = unresolved_subjects
                    if bundle.source_kind == "admin" and "admin_directory_members" in bundle.tables:
                        cursor.execute("select platform_identity.refresh_dingtalk_matches()")
                    if bundle.source_kind in {"fae", "daq"}:
                        for span in bundle.trace_spans:
                            _upsert(cursor, normalize_trace_span(span, synced_at, source_kind=bundle.source_kind))
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
        run_id=str(run_id),
        source_kind=bundle.source_kind,
        status="succeeded",
        source_counts=bundle.source_counts,
        applied_counts=applied,
        validation=validation,
    )


def import_bundle_with_review(
    database_url: str,
    bundle: ExportBundle,
    *,
    review_repository,
    actor: str,
    now: datetime | None = None,
) -> CoordinatedSyncResult:
    """Commit the source mirror first, then independently backfill review state."""
    source_sync = import_bundle(database_url, bundle, now=now)
    try:
        review_backfill = backfill_negative_feedback(
            review_repository,
            actor=actor,
        )
    except Exception as error:
        raise ReviewBackfillError(source_sync) from error
    return CoordinatedSyncResult(
        source_sync=source_sync,
        review_backfill=review_backfill,
    )
