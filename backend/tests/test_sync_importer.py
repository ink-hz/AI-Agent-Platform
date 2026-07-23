from datetime import datetime, timezone

import pytest

from app.sync_remote.export import ExportBundle
from app.sync_remote.importer import (
    ImportValidationError,
    normalize_row,
    normalize_trace_span,
    validate_bundle,
)
from app.sync_remote.identity_matcher import match_directory_entries


SYNCED_AT = datetime(2026, 7, 21, 3, 20, tzinfo=timezone.utc)


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
