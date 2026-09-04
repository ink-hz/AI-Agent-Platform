from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
MIGRATION = (
    ROOT / "backend" / "control_migrations" / "071_hr_task_result_projection.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_result_projection_ledger_is_leased_and_app_only() -> None:
    sql = _sql()

    assert "create table platform_hr.hr_task_result_projections" in sql
    assert "unique (task_record_id)" in sql
    assert "state text not null" in sql
    assert "lease_expires_at timestamptz" in sql
    assert "attempt_count integer not null" in sql
    assert "for update of record skip locked limit 1" in sql
    assert "execution_model_version text" in sql
    assert "create function platform_hr.create_position_task_record_v71" in sql
    assert "get diagnostics claimed_count=row_count" in sql
    assert "if claimed_count=0 then return" in sql
    assert "where (platform_hr.hr_task_result_projections.state='pending'" in sql
    assert "platform_hr.hr_task_result_projections.lease_expires_at<=now()" in sql
    for function in (
        "claim_hr_task_result_projection_v71",
        "complete_hr_task_result_projection_v71",
        "fail_hr_task_result_projection_v71",
        "release_hr_task_result_projection_v71",
    ):
        assert f"create function platform_hr.{function}" in sql
        assert f"revoke all on function platform_hr.{function}" in sql
    assert "security definer set search_path=pg_catalog,platform_hr" in sql
    assert "platform_control_app','platform_control_app_preview" in sql
    assert (
        "grant execute on function platform_hr.claim_hr_task_result_projection_v71"
        in sql
    )
    assert "grant select on platform_hr.hr_task_result_projections" not in sql
    assert "grant insert on platform_hr.hr_task_result_projections" not in sql
    assert "grant update on platform_hr.hr_task_result_projections" not in sql


def test_claim_requires_an_exact_completed_hr_task_and_assistant_message() -> None:
    sql = _sql()

    for fragment in (
        "request.owner_internal_user_id=record.owner_internal_user_id",
        "request.position_id=record.position_id",
        "request.client_request_id=record.client_request_id",
        "binding.owner_internal_user_id=record.owner_internal_user_id",
        "binding.position_id=record.position_id",
        "turn.turn_id=record.turn_id",
        "turn.conversation_id=record.conversation_id",
        "turn.client_request_id=record.client_request_id",
        "turn.status='completed'",
        "execution.status='completed'",
        "message.message_id=turn.assistant_message_id",
        "message.role='assistant'",
        "message.delivery_status='completed'",
        "conversation.direct_agent_id='hr-bot'",
        "execution.agent_id='hr-bot'",
        "record.execution_model_version",
        "count(distinct execution.job_id)",
        "count(distinct message.message_id)",
    ):
        assert fragment in sql
    assert "request.task_kind=record.task_kind" in sql
    assert (
        "request.expected_context_version_id is not distinct from record.context_version_id"
        in sql
    )
    assert "request.material_attachment_ids=record.material_attachment_ids" in sql
    assert "request.candidate_id is not distinct from record.candidate_id" in sql
    assert (
        "request.position_candidate_id is not distinct from record.position_candidate_id"
        in sql
    )
