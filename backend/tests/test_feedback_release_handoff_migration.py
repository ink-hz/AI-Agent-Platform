from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/010_feedback_release_handoffs.sql"


def normalized(value: str) -> str:
    return " ".join(value.lower().split())


def test_release_handoff_migration_defines_canonical_identity_and_ledger():
    sql = normalized(MIGRATION.read_text(encoding="utf-8"))

    assert "add column if not exists canonical_key text" in sql
    assert "feedback_issues_canonical_key_uq" in sql
    assert "(agent_id, canonical_key)" in sql
    assert "where canonical_key is not null" in sql
    assert "create table if not exists platform_review.feedback_release_handoffs" in sql
    for required in (
        "idempotency_key text primary key",
        "batch_id text not null unique",
        "payload_sha256 text not null",
        "release_name text not null",
        "deployment_sha text not null",
        "import_status text not null",
        "failure_reason text not null",
        "result jsonb not null",
        "created_at timestamptz not null",
        "updated_at timestamptz not null",
    ):
        assert required in sql
    for status in ("processing", "blocked", "imported", "terminal_failed"):
        assert status in sql


def test_release_handoff_audit_is_append_only_and_import_identity_is_immutable():
    sql = normalized(MIGRATION.read_text(encoding="utf-8"))

    assert "platform_review.feedback_release_handoff_events" in sql
    assert "before update or delete" in sql
    assert "prevent_issue_event_mutation" in sql
    assert "prevent_imported_handoff_identity_mutation" in sql
    assert "old.import_status = 'imported'" in sql
    for field in (
        "idempotency_key",
        "batch_id",
        "agent_id",
        "payload_sha256",
        "release_name",
        "deployment_sha",
    ):
        assert f"new.{field} is distinct from old.{field}" in sql


def test_release_handoff_permissions_keep_writer_non_destructive_and_analyst_read_only():
    sql = normalized(MIGRATION.read_text(encoding="utf-8"))

    assert (
        "grant select, insert, update on "
        "platform_review.feedback_release_handoffs to platform_review_writer"
    ) in sql
    assert (
        "grant select, insert on platform_review.feedback_release_handoff_events "
        "to platform_review_writer"
    ) in sql
    assert (
        "revoke delete on platform_review.feedback_release_handoffs "
        "from platform_review_writer"
    ) in sql
    assert (
        "revoke update, delete on platform_review.feedback_release_handoff_events "
        "from platform_review_writer"
    ) in sql
    assert (
        "grant select on platform_review.feedback_release_handoffs, "
        "platform_review.feedback_release_handoff_events to flywheel_analyst"
    ) in sql
