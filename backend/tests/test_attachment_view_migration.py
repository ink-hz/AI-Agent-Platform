import re
from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/007_attachment_views.sql"


def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_attachment_view_has_exact_safe_column_allowlist() -> None:
    sql = migration_sql()
    match = re.search(
        r"create\s+(?:or replace\s+)?view\s+platform_read\.attachments.*?\bas\s*"
        r"select\s+(.*?)\s+from\s+flywheel_core\.attachments",
        sql,
        re.DOTALL,
    )
    assert match is not None
    expressions = [" ".join(item.split()) for item in match.group(1).split(",")]
    columns = [
        expression.rsplit(" as ", 1)[-1].split()[-1]
        for expression in expressions
    ]
    assert columns == [
        "attachment_id",
        "turn_key",
        "direction",
        "display_name",
        "mime_type",
        "size_bytes",
        "received_or_generated_at",
        "archive_status",
        "delivery_status",
        "expires_at",
    ]


def test_attachment_view_is_restricted_to_safe_metadata() -> None:
    sql = migration_sql()
    assert "with (security_barrier = true)" in sql
    assert "where turn_id is not null" in sql
    assert "case when archive_status = 'expired' then null else name end" in sql
    assert "alter view platform_read.attachments owner to flywheel_owner" in sql
    assert "revoke all on platform_read.attachments from public, flywheel_ingest" in sql
    assert "grant select on platform_read.attachments to flywheel_analyst" in sql
    for forbidden in ("bucket", "object_key", "platform_ref", "local_ref", "sha256"):
        assert forbidden not in sql


def test_attachment_view_prefers_archived_rows_over_legacy_metadata_shadows() -> None:
    sql = migration_sql()
    assert "create or replace view platform_read.attachments" in sql
    assert "ingest_key like 'legacy:%'" in sql
    assert "canonical.ingest_key not like 'legacy:%'" in sql
    assert "canonical.platform_message_id is not distinct from attachment.platform_message_id" in sql
