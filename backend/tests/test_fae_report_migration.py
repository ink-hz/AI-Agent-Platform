from pathlib import Path

MIGRATION = Path(__file__).parents[1] / "migrations" / "012_fae_analysis_reports.sql"


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
        "grant select on all tables in schema platform_fae_reports to flywheel_analyst"
        in sql
    )
