from datetime import datetime, timezone

import pytest

from app.sync_remote.export import ExportBundle
from app.sync_remote.importer import (
    ImportValidationError,
    _upsert,
    normalize_row,
    normalize_session_subject_link,
    normalize_trace_span,
    validate_bundle,
)
from app.sync_remote.identity_matcher import match_directory_entries


SYNCED_AT = datetime(2026, 7, 21, 3, 20, tzinfo=timezone.utc)


class RecordingCursor:
    def __init__(self) -> None:
        self.statement = ""
        self.params: tuple = ()

    def execute(self, statement, params) -> None:
        self.statement = statement.as_string()
        self.params = tuple(params)


def test_admin_session_uses_only_explicit_platform_verified_subject() -> None:
    row = {
        "id": "admin-session-1",
        "internal_user_id": "00000000-0000-4000-8000-000000000123",
        "verification_method": "platform_session",
        "verified_at": "2026-08-24T09:00:00+00:00",
        "display_name": "钉钉临时名称",
        "sender_user_id": "browser-controlled",
    }

    link = normalize_session_subject_link(row, SYNCED_AT)

    assert link is not None
    assert link.target_schema == "platform_identity"
    assert link.target_table == "session_subject_links"
    assert link.values == {
        "source_kind": "admin",
        "native_session_id": "admin-session-1",
        "internal_user_id": "00000000-0000-4000-8000-000000000123",
        "verification_method": "platform_session",
        "verified_at": "2026-08-24T09:00:00+00:00",
        "source_synced_at": SYNCED_AT,
    }


@pytest.mark.parametrize(
    "row",
    [
        {"id": "legacy", "display_name": "苍渊"},
        {"id": "legacy", "sender_user_id": "on_forged"},
        {"id": "legacy", "internal_user_id": "00000000-0000-4000-8000-000000000123"},
        {
            "id": "legacy",
            "internal_user_id": "00000000-0000-4000-8000-000000000123",
            "verification_method": "name_guess",
            "verified_at": "2026-08-24T09:00:00+00:00",
        },
    ],
)
def test_admin_session_without_complete_platform_evidence_stays_unresolved(row) -> None:
    assert normalize_session_subject_link(row, SYNCED_AT) is None


def test_fae_review_preserves_corrected_answer() -> None:
    normalized = normalize_row(
        "fae",
        "turn_reviews",
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "turn_id": "00000000-0000-0000-0000-000000000002",
            "priority": "P1",
            "review_status": "reviewed",
            "failure_layer": "synthesis",
            "failure_reason": "wrong answer",
            "expected_answer_notes": "expected",
            "corrected_answer": "This is the corrected answer.",
            "reviewer": "codex",
            "should_add_to_eval": True,
            "should_update_knowledge": False,
            "created_at": "2026-07-21T00:00:00+00:00",
            "updated_at": "2026-07-21T01:00:00+00:00",
            "metadata": {"source": "production"},
        },
        SYNCED_AT,
    )

    assert normalized.values["corrected_answer"] == "This is the corrected answer."
    assert normalized.values["details"] == {"source": "production"}


def test_admin_prefix_is_removed_only_for_target_table() -> None:
    normalized = normalize_row(
        "admin",
        "admin_chat_sessions",
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "external_session_id": "ding-session",
            "channel": "dingtalk",
            "user_id": None,
            "external_user_id": "user-1",
            "conversation_title": None,
            "created_at": "2026-07-21T00:00:00+00:00",
            "last_active_at": "2026-07-21T00:01:00+00:00",
            "metadata": {},
        },
        SYNCED_AT,
    )

    assert normalized.target_schema == "platform_source_admin"
    assert normalized.target_table == "chat_sessions"
    assert normalized.values["external_session_id"] == "ding-session"


def test_turn_message_timestamps_are_compatible_with_legacy_exports() -> None:
    exact = normalize_row(
        "fae",
        "chat_turns",
        {
            "id": "00000000-0000-0000-0000-000000000010",
            "question_at": "2026-07-30T08:00:00+00:00",
            "answer_at": "2026-07-30T08:00:05+00:00",
        },
        SYNCED_AT,
    )
    legacy = normalize_row(
        "admin",
        "admin_chat_turns",
        {"id": "00000000-0000-0000-0000-000000000011"},
        SYNCED_AT,
    )

    assert exact.values["question_at"] == "2026-07-30T08:00:00+00:00"
    assert exact.values["answer_at"] == "2026-07-30T08:00:05+00:00"
    assert legacy.values["question_at"] is None
    assert legacy.values["answer_at"] is None


def test_only_turn_rows_preserve_existing_message_timestamps_on_null() -> None:
    exact_turn = normalize_row(
        "fae",
        "chat_turns",
        {
            "id": "00000000-0000-0000-0000-000000000010",
            "question_at": "2026-07-30T08:00:00+00:00",
            "answer_at": "2026-07-30T08:00:05+00:00",
        },
        SYNCED_AT,
    )
    legacy_turn = normalize_row(
        "admin",
        "admin_chat_turns",
        {"id": "00000000-0000-0000-0000-000000000011"},
        SYNCED_AT,
    )
    non_turn = normalize_row(
        "fae",
        "chat_sessions",
        {"id": "00000000-0000-0000-0000-000000000012"},
        SYNCED_AT,
    )

    assert exact_turn.preserve_on_null_columns == ("question_at", "answer_at")
    assert legacy_turn.preserve_on_null_columns == ("question_at", "answer_at")
    assert non_turn.preserve_on_null_columns == ()


def test_upsert_coalesces_preserved_columns_against_target_table() -> None:
    normalized = normalize_row(
        "fae",
        "chat_turns",
        {
            "id": "00000000-0000-0000-0000-000000000010",
            "question": "When was this asked?",
            "question_at": None,
            "answer_at": None,
        },
        SYNCED_AT,
    )
    cursor = RecordingCursor()

    _upsert(cursor, normalized)

    assert (
        '"question_at" = COALESCE(EXCLUDED."question_at", "chat_turns"."question_at")'
        in cursor.statement
    )
    assert (
        '"answer_at" = COALESCE(EXCLUDED."answer_at", "chat_turns"."answer_at")'
        in cursor.statement
    )
    assert '"question" = EXCLUDED."question"' in cursor.statement


def test_admin_directory_row_is_normalized_into_protected_identity_schema() -> None:
    normalized = normalize_row(
        "admin",
        "admin_directory_members",
        {
            "staff_id": "staff-1",
            "display_name": "  Ｌina  ",
            "departments": [" Marketing ", ""],
            "active": True,
            "source_updated_at": None,
            "source_synced_at": "2026-07-23T03:00:00+00:00",
            "mobile": "must-not-cross-boundary",
        },
        SYNCED_AT,
    )

    assert normalized.target_schema == "platform_identity"
    assert normalized.target_table == "dingtalk_directory_members"
    assert normalized.conflict_columns == ("staff_id",)
    assert normalized.values["display_name"] == "Lina"
    assert normalized.values["normalized_name"] == "Lina"
    assert normalized.values["departments"] == ["Marketing"]
    assert "mobile" not in normalized.values


def test_directory_matching_requires_one_exact_active_unicode_normalized_name() -> None:
    directory = [
        {"staff_id": "1", "display_name": "Ｌina", "departments": ["Marketing"], "active": True},
        {"staff_id": "2", "display_name": "Noah", "departments": ["Sales"], "active": False},
        {"staff_id": "3", "display_name": "Alex", "departments": ["Finance"], "active": True},
        {"staff_id": "4", "display_name": "Alex", "departments": ["Legal"], "active": True},
    ]

    matches = match_directory_entries([" Lina ", "Noah", "Alex", "Unknown"], directory)

    assert matches[" Lina "].department == "Marketing"
    assert matches[" Lina "].status == "resolved"
    assert matches["Noah"].status == "unmatched"
    assert matches["Alex"].status == "ambiguous"
    assert matches["Unknown"].status == "unmatched"


def test_trace_span_uses_trace_and_span_as_conflict_key() -> None:
    normalized = normalize_trace_span(
        {
            "type": "span",
            "trace_id": "trace-1",
            "span_id": "span-1",
            "parent_span_id": "root-1",
            "node": "llm_call",
            "started_at": "2026-07-21T00:00:00+00:00",
            "ended_at": "2026-07-21T00:00:01+00:00",
            "duration_ms": 1000,
            "input_summary": {},
            "output_summary": {},
            "metadata": {},
            "error": None,
        },
        SYNCED_AT,
    )

    assert normalized.conflict_columns == ("trace_id", "span_id")
    assert normalized.values["node"] == "llm_call"


def test_validation_rejects_turn_without_required_identity() -> None:
    bundle = ExportBundle(
        source_kind="fae",
        tables={"chat_turns": ({"question": "missing ids"},)},
        trace_spans=(),
        malformed_lines=0,
    )

    with pytest.raises(ImportValidationError, match="missing_required_key"):
        validate_bundle(bundle)


def test_validation_reports_orphan_links_without_reading_content() -> None:
    bundle = ExportBundle(
        source_kind="fae",
        tables={
            "chat_sessions": (
                {"id": "00000000-0000-0000-0000-000000000001"},
            ),
            "chat_turns": (
                {
                    "id": "00000000-0000-0000-0000-000000000002",
                    "session_id": "00000000-0000-0000-0000-000000000099",
                    "trace_id": "trace-1",
                },
            ),
        },
        trace_spans=(),
        malformed_lines=0,
    )

    validation = validate_bundle(bundle)

    assert validation["orphan_turn_sessions"] == 1
    assert validation["turns_without_root_span"] == 1
