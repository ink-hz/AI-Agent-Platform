from datetime import datetime, timezone

import pytest

from app.sync_remote.export import ExportBundle
from app.sync_remote.importer import (
    ImportValidationError,
    normalize_row,
    normalize_trace_span,
    validate_bundle,
)


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
