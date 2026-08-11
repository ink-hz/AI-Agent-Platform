from __future__ import annotations

import os
from pathlib import Path
import subprocess

import psycopg
import pytest


MIGRATIONS = Path(__file__).parents[1] / "migrations"
GRANT_MIGRATION = MIGRATIONS / "008_feedback_review_grants.sql"


def _normalized_sql(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def _apply_migration(database_url: str, migration: Path) -> None:
    result = subprocess.run(
        [
            "psql",
            database_url,
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            str(migration),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"migration failed: {migration.name}\n{result.stdout}\n{result.stderr}"
    )


def test_review_writer_grants_are_reasserted_after_view_replacements():
    sql = _normalized_sql(GRANT_MIGRATION)

    assert "grant usage on schema platform_read to platform_review_writer" in sql
    assert (
        "grant select on platform_read.feedback, platform_read.turns "
        "to platform_review_writer"
    ) in sql
    assert "platform_read.attachments" not in sql
    assert "revoke all on schema platform_source_fae from platform_review_writer" in sql
    assert "revoke all on schema platform_source_admin from platform_review_writer" in sql
    assert "revoke all on schema platform_sync from platform_review_writer" in sql


@pytest.mark.postgres
def test_full_migration_chain_preserves_least_privilege():
    database_url = os.getenv("PLATFORM_MIGRATION_TEST_DATABASE_URL", "").strip()
    flywheel_migrations_dir = Path(
        os.getenv("FLYWHEEL_MIGRATIONS_DIR", "").strip()
    )
    if not database_url or not flywheel_migrations_dir.is_dir():
        pytest.skip("isolated database and Flywheel migrations are not configured")

    migration_chain = (
        *sorted(flywheel_migrations_dir.glob("[0-9][0-9][0-9]_*.sql")),
        *sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")),
    )
    for migration in migration_chain:
        _apply_migration(database_url, migration)

    # Historical view migrations are not downgrade-safe when replayed after
    # later column additions. Re-run the additive grant repair itself to prove
    # its idempotency without rewriting unrelated migration history.
    _apply_migration(database_url, GRANT_MIGRATION)

    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            select
              has_table_privilege(
                'platform_review_writer', 'platform_read.feedback', 'select'
              ),
              has_table_privilege(
                'platform_review_writer', 'platform_read.turns', 'select'
              ),
              has_table_privilege(
                'platform_review_writer', 'platform_read.attachments', 'select'
              )
            """
        )
        assert cursor.fetchone() == (True, True, False)

        cursor.execute("set role platform_review_writer")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute("delete from platform_review.feedback_issue_events")
        connection.rollback()
        cursor.execute("set role platform_review_writer")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute("delete from platform_source_fae.chat_turns")
