import re
from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/005_feedback_fix_closure.sql"


def normalized(value: str) -> str:
    return " ".join(value.lower().split())


def dml_targets(value: str) -> set[str]:
    return {
        match.group(1).lower()
        for match in re.finditer(
            r"\b(?:insert\s+into|update|delete\s+from)\s+([a-z0-9_.]+)",
            value,
            flags=re.IGNORECASE,
        )
    }


def test_feedback_closure_migration_has_required_boundaries():
    sql = MIGRATION.read_text(encoding="utf-8")
    compact = normalized(sql)
    for name in [
        "platform_review.feedback_issues",
        "platform_review.feedback_issue_links",
        "platform_review.feedback_fix_evidence",
        "platform_review.feedback_replay_runs",
        "platform_review.feedback_issue_events",
        "platform_review.issue_progress_inputs",
    ]:
        assert name in sql
    assert "unique (issue_link_id, idempotency_key)" in compact
    assert "where active and link_role = 'primary'" in compact
    assert "grant select, insert, update" in compact
    assert not any(
        target.startswith("platform_source_fae")
        or target.startswith("platform_source_admin")
        for target in dml_targets(sql)
    )


def test_replay_raw_evidence_becomes_immutable_after_completion():
    sql = normalized(MIGRATION.read_text(encoding="utf-8"))

    assert "prevent_completed_replay_mutation" in sql
    assert "old.execution_status <> 'running'" in sql
    for field in (
        "question",
        "context_snapshot",
        "attachment_manifest",
        "answer",
        "sources",
        "done",
        "trace_id",
        "actual_git_sha",
        "actual_model",
    ):
        assert f"new.{field} is distinct from old.{field}" in sql


def test_review_permissions_are_isolated_and_events_are_append_only():
    sql = normalized(MIGRATION.read_text(encoding="utf-8"))

    assert "create role platform_review_writer login" in sql
    assert "revoke all on schema platform_review from platform_sync_writer" in sql
    assert "revoke all on schema platform_source_fae from platform_review_writer" in sql
    assert "revoke all on schema platform_source_admin from platform_review_writer" in sql
    assert "grant usage on schema platform_read to platform_review_writer" in sql
    assert (
        "grant select on platform_read.feedback, platform_read.turns "
        "to platform_review_writer"
    ) in sql
    assert (
        "revoke update, delete on platform_review.feedback_issue_events "
        "from platform_review_writer"
    ) in sql


def test_progress_view_requires_runtime_and_semantic_evidence():
    sql = normalized(MIGRATION.read_text(encoding="utf-8"))

    for required in (
        "qualified_replay_link_count",
        "semantic_passed_link_count",
        "actual_git_sha",
        "actual_model",
        "configured_model",
        "fallback_used",
        "truncation_rounds",
        "trace_id",
    ):
        assert required in sql
