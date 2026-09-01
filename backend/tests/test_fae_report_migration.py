from pathlib import Path

MIGRATION = Path(__file__).parents[1] / "migrations" / "012_fae_analysis_reports.sql"
CLOUD_GRANT_MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "013_fae_report_cloud_projection.sql"
)


def normalized(value: str) -> str:
    return " ".join(value.lower().split())


def test_report_migration_defines_immutable_versions_and_evidence():
    sql = normalized(MIGRATION.read_text(encoding="utf-8"))

    assert "create schema if not exists platform_fae_reports" in sql
    for table in (
        "reports",
        "report_evidence",
        "finding_issue_links",
        "report_audit_events",
    ):
        assert f"create table if not exists platform_fae_reports.{table}" in sql
    assert "unique(report_id, report_version)" in sql
    assert "payload jsonb not null" in sql
    assert "payload_digest char(64) not null" in sql
    assert "prevent_immutable_report_mutation" in sql
    assert "before update or delete" in sql


def test_report_migration_keeps_import_writer_non_destructive_and_analyst_read_only():
    sql = normalized(MIGRATION.read_text(encoding="utf-8"))

    assert "grant select, insert on platform_fae_reports.reports" in sql
    assert "revoke update, delete on platform_fae_reports.reports" in sql
    assert (
        "grant select on platform_read.sessions, platform_read.turns, "
        "platform_read.feedback to platform_review_writer"
    ) in sql
    assert (
        "grant select on platform_review.feedback_issues to platform_review_writer"
        in sql
    )
    assert (
        "grant select on all tables in schema platform_fae_reports to flywheel_analyst"
        in sql
    )


def test_cloud_projection_grant_is_explicit_and_read_only():
    sql = normalized(CLOUD_GRANT_MIGRATION.read_text(encoding="utf-8"))

    assert "grant usage on schema platform_fae_reports to flywheel_analyst" in sql
    assert "grant select on platform_fae_reports.reports" in sql
    assert "platform_fae_reports.report_evidence" in sql
    assert "platform_fae_reports.finding_issue_links" in sql
    assert "insert" not in sql
    assert "update" not in sql
    assert "delete" not in sql
