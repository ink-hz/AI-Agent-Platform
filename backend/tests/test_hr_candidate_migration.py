from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "068_hr_candidate_intelligence.sql"
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_candidate_schema_has_owner_composite_references_and_no_ats_fields() -> None:
    sql = _sql()

    for table in (
        "candidate_drafts",
        "candidates",
        "candidate_documents",
        "position_candidates",
        "candidate_analysis_versions",
        "candidate_analysis_documents",
        "candidate_analysis_feedback",
        "human_feedback",
    ):
        assert f"create table platform_hr.{table}" in sql
    assert "foreign key (position_id,owner_internal_user_id) references platform_hr.positions" in sql
    assert "foreign key (candidate_id,owner_internal_user_id) references platform_hr.candidates" in sql
    assert "foreign key (attachment_id,owner_internal_user_id) references platform_attachments.attachments" in sql
    for forbidden in (
        "offer_status",
        "pipeline_stage",
        "interview_schedule",
        "onboarding",
        "automatic_rejection",
        "beisen",
        "boss_zhipin",
        "liepin",
    ):
        assert forbidden not in sql


def test_candidate_schema_has_bounded_states_and_exact_version_links() -> None:
    sql = _sql()

    assert "state in ('pending','processing','ready','failed','confirmed','dismissed')" in sql
    assert "status in ('active','archived')" in sql
    assert "analysis_kind in ('resume_extract','match','candidate_interview_plan','comparison')" in sql
    assert "references platform_hr.position_context_versions(context_version_id,owner_internal_user_id)" in sql
    assert "unique (position_candidate_id,version_number)" in sql
    assert "unique (analysis_version_id,document_id)" in sql
    assert "unique (analysis_version_id,feedback_id)" in sql


def test_candidate_mutations_are_audited_idempotent_and_tables_are_private() -> None:
    sql = _sql()

    for function in (
        "create_candidate_draft_v68",
        "start_candidate_draft_v68",
        "complete_candidate_draft_v68",
        "fail_candidate_draft_v68",
        "retry_candidate_draft_v68",
        "confirm_candidate_draft_v68",
        "dismiss_candidate_draft_v68",
        "create_candidate_analysis_v68",
        "append_human_feedback_v68",
    ):
        assert f"create function platform_hr.{function}" in sql
        assert f"revoke all on function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert sql.count("security definer") >= 9
    assert "unique (owner_internal_user_id,client_request_id)" in sql
    assert "revoke all on all tables in schema platform_hr from public" in sql
    assert "grant select on all tables in schema platform_hr to %i" in sql
    assert "grant insert" not in sql


def test_analysis_and_feedback_are_append_only_and_attachment_bytes_are_not_copied() -> None:
    sql = _sql()

    assert "create trigger candidate_analysis_versions_immutable_v68" in sql
    assert "create trigger human_feedback_immutable_v68" in sql
    assert "create function platform_hr.reject_candidate_history_mutation_v68" in sql
    for forbidden_column in ("storage_key", "storage_path", "object_key", "file_bytes"):
        assert forbidden_column not in sql
