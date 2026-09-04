from __future__ import annotations

from pathlib import Path

from app.agent_brain.models import CALLABLE_AGENT_IDS
from app.execution_relay.metabot_client import _APPROVED_AGENT_IDS

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "070_hr_candidate_intelligence.sql"
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_candidate_schema_has_owner_composite_references_and_no_ats_fields() -> None:
    sql = _sql()

    for table in (
        "candidate_draft_batches",
        "candidate_drafts",
        "candidates",
        "candidate_documents",
        "position_candidates",
        "candidate_analysis_versions",
        "candidate_analysis_documents",
        "candidate_analysis_feedback",
        "human_feedback",
        "candidate_confirmation_events",
        "candidate_draft_mutation_events",
        "candidate_draft_processing_attempts",
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
        assert f"{forbidden} text" not in sql


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
        "register_candidate_draft_batch_v70",
        "create_candidate_draft_v70",
        "retry_candidate_draft_v70",
        "confirm_candidate_draft_v70",
        "dismiss_candidate_draft_v70",
        "create_candidate_analysis_v70",
        "append_human_feedback_v70",
    ):
        assert f"create function platform_hr.{function}" in sql
        assert f"revoke all on function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert sql.count("security definer") >= 9
    assert "unique (owner_internal_user_id,client_request_id)" in sql
    assert "revoke all on all tables in schema platform_hr from public" in sql
    assert "grant select on all tables in schema platform_hr to %i" in sql
    assert "grant select on all tables in schema platform_hr to %i,%i" not in sql
    assert "grant insert" not in sql
    for function in (
        "start_candidate_draft_v70",
        "complete_candidate_draft_v70",
        "fail_candidate_draft_v70",
    ):
        assert f"grant execute on function platform_hr.{function}" not in sql


def test_analysis_and_feedback_are_append_only_and_attachment_bytes_are_not_copied() -> None:
    sql = _sql()

    assert "create trigger candidate_analysis_versions_immutable_v70" in sql
    assert "create trigger human_feedback_immutable_v70" in sql
    assert "create function platform_hr.reject_candidate_history_mutation_v70" in sql
    for forbidden_column in ("storage_key", "storage_path", "object_key", "file_bytes"):
        assert f"{forbidden_column} text" not in sql


def test_idempotent_replays_are_bound_to_the_complete_mutation_payload() -> None:
    sql = _sql()

    assert "candidate batch idempotency mismatch" in sql
    assert "candidate confirmation idempotency mismatch" in sql
    assert "candidate analysis idempotency mismatch" in sql
    assert "candidate feedback idempotency mismatch" in sql
    for comparison in (
        "selected.result<>selected_result",
        "selected.evidence<>selected_evidence",
        "selected.unknowns<>selected_unknowns",
        "selected.conflicts<>selected_conflicts",
        "selected.verification_questions<>selected_verification_questions",
        "selected.correction is distinct from",
        "selected.reason<>btrim(selected_reason)",
    ):
        assert comparison in sql


def test_identity_suggestions_are_server_derived_owner_scoped_and_replay_safe() -> None:
    sql = _sql()

    assert "cardinality(selected_identity_candidates)<>0" in sql
    assert "candidate.owner_internal_user_id=selected_owner_internal_user_id" in sql
    assert "document.status='active'" in sql
    assert "document.content_sha256=selected_content_sha256" in sql
    assert "limit 100" in sql
    assert "identity_candidates=derived_identity_candidates" in sql


def test_analysis_feedback_is_an_exact_context_pinned_task_snapshot() -> None:
    sql = _sql()

    assert "cardinality(selected_feedback_ids)>100" in sql
    assert "analysis.context_version_id=selected_context_version_id" in sql
    assert "feedback.analysis_version_id=analysis.analysis_version_id" in sql


def test_context_foreign_keys_are_guarded_for_the_isolated_subsystem_branch() -> None:
    sql = _sql()

    assert "to_regclass('platform_hr.position_context_versions') is not null" in sql
    assert "add constraint position_candidates_context_owner_fk_v70" in sql
    assert "add constraint candidate_analysis_context_owner_fk_v70" in sql


def test_confirmation_uses_real_v68_state_column_and_rebases_existing_relation() -> None:
    sql = _sql()

    assert "and position_id=selected_draft.position_id and state='confirmed'" in sql
    assert "and position_id=selected_draft.position_id and status='confirmed'" not in sql
    assert "context_version_id=excluded.context_version_id" in sql
    assert "row_version=position_candidates.row_version+1" in sql


def test_candidate_mutations_serialize_and_persist_full_replay_payloads() -> None:
    sql = _sql()

    assert "create table platform_hr.candidate_draft_mutation_events" in sql
    assert "payload_sha256 bytea not null" in sql
    assert "result_snapshot jsonb not null" in sql
    assert sql.count("perform pg_advisory_xact_lock(hashtextextended(") >= 10
    for kind in ("start", "complete", "fail", "retry", "dismiss"):
        assert f"'{kind}'" in sql


def test_candidate_writes_reject_erasing_attachments_and_json_is_defended_in_depth() -> None:
    sql = _sql()

    assert "create function platform_hr.candidate_attachment_usable_v70" in sql
    assert "from platform_attachments.erasure_jobs" in sql
    assert "create function platform_hr.candidate_json_safe_v70" in sql
    assert "candidate facts contain forbidden fields" in sql
    assert "normalize(btrim(item.key),nfkc)" in sql
    assert "regexp_replace" in sql
    for protected_alias in ("性别", "birthdate", "dateofbirth", "出生日期", "政治面貌"):
        assert f"'{protected_alias}'" in sql


def test_resume_processing_has_a_durable_brain_worker_claim_boundary() -> None:
    sql = _sql()

    assert "create table platform_hr.candidate_draft_processing_attempts" in sql
    assert "execution_job_id uuid references" in sql
    assert "conversation_id uuid" in sql and "turn_id uuid" in sql
    assert "assistant_message_id uuid" in sql
    for function in (
        "claim_next_candidate_draft_v70",
        "attach_candidate_draft_execution_v70",
        "recover_candidate_draft_attempt_v70",
        "recover_next_candidate_draft_attempt_v70",
        "discover_candidate_draft_execution_v70",
        "read_candidate_draft_attempt_v70",
        "complete_claimed_candidate_draft_v70",
        "fail_claimed_candidate_draft_v70",
    ):
        assert f"create function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert "for update of draft skip locked" in sql
    assert "order by claimed_at,attempt_id for update skip locked limit 1" in sql
    assert "candidate execution identity is ambiguous" in sql
    assert "lease_expires_at" in sql
    assert "create index candidate_draft_attempt_expiry_v70" in sql
    assert "(lease_expires_at) where state='processing'" in sql
    assert "position_id uuid not null" in sql
    assert "attachment_id uuid not null" in sql
    assert "draft_client_request_id uuid not null" in sql
    assert "execution.agent_id='hr-bot'" in sql
    assert "run.agent_id='hr-bot'" in sql
    assert "mission.direct_agent_id='hr-bot'" in sql
    assert "conversation.direct_agent_id='hr-bot'" in sql
    assert "hr-candidate-bot" not in sql
    assert "turn.client_request_id=selected_attempt.attempt_id" in sql
    assert "selected_draft.client_request_id,selected_worker_id" in sql
    assert "binding.kind='turn_input'" in sql
    assert "binding.attachment_id<>selected_attempt.attachment_id" in sql
    assert "candidate_attachment_usable_v70( selected_attempt.owner_internal_user_id" in sql
    assert "execution.status='completed'" in sql
    assert "turn.status='completed'" in sql
    assert "execution.status in ('completed','failed','cancelled','interrupted')" in sql
    assert "turn.status in ('completed','failed','cancelled','interrupted')" in sql
    assert "selected_attempt.owner_internal_user_id<>selected_owner_internal_user_id" in sql
    assert "selected_attempt.draft_id<>selected_draft_id" in sql
    assert "selected_attempt.worker_id<>selected_worker_id" in sql
    assert "not exists ( select 1 from platform_hr.position_conversations" in sql
    assert "grant select on all tables in schema platform_hr" not in sql.replace(
        "grant select on all tables in schema platform_hr to %i',selected_app", ""
    )


def test_candidate_parser_result_reader_is_exact_and_brain_only() -> None:
    sql = _sql()

    assert "create function platform_hr.read_candidate_draft_execution_result_v70" in sql
    assert "selected_attempt.execution_job_id" in sql
    assert "turn.assistant_message_id" in sql
    assert "message.role='assistant'" in sql
    assert "message.mission_id=mission.mission_id" in sql
    assert "message.message_id=selected_attempt.assistant_message_id" in sql
    assert (
        "turn.assistant_message_id is not distinct from "
        "selected_attempt.assistant_message_id"
        in sql
    )
    assert (
        "grant execute on function "
        "platform_hr.read_candidate_draft_execution_result_v70(uuid,text)"
        in sql
    )
    assert "to %i',selected_brain" in sql


def test_candidate_parser_submission_collision_is_app_scoped_and_terminal() -> None:
    sql = _sql()

    assert "create function platform_hr.fail_candidate_parser_submission_collision_v70" in sql
    assert "conversation.started_by_client_request_id=selected_attempt.attempt_id" in sql
    assert "'parser_request_collision'" in sql
    assert (
        "grant execute on function "
        "platform_hr.fail_candidate_parser_submission_collision_v70(uuid,uuid)"
        in sql
    )
    assert "to %i',selected_app" in sql


def test_resume_processing_uses_an_agent_supported_by_brain_and_relay() -> None:
    assert "hr-bot" in CALLABLE_AGENT_IDS
    assert "hr-bot" in _APPROVED_AGENT_IDS
    assert "hr-candidate-bot" not in CALLABLE_AGENT_IDS
    assert "hr-candidate-bot" not in _APPROVED_AGENT_IDS


def test_candidate_replaces_position_task_validation_seam_with_exact_scope() -> None:
    sql = _sql()

    assert "create or replace function platform_hr.validate_candidate_task_inputs_v69" in sql
    assert "relation.context_version_id=selected_context_version_id" in sql
    assert "document.attachment_id=requested.attachment_id" in sql
    assert "feedback.position_candidate_id=selected_position_candidate_id" in sql
    assert "analysis.context_version_id=selected_context_version_id" in sql
