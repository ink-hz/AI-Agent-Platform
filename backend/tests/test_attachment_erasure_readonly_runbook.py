from pathlib import Path

import psycopg
from test_control_plane_migration import (
    control_database as control_database,  # noqa: PLC0414 - pytest fixture
)
from test_conversation_attachment_migration import _insert_attachment, _seed_task


def test_readonly_census_does_not_depend_on_queue_and_checks_reviewed_migrations(
    control_database,
):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        admin.execute(
            "UPDATE platform_attachments.attachments SET state='deleted',deleted_at=now() WHERE attachment_id=%s",
            (attachment_id,),
        )
        assert (
            admin.execute(
                "SELECT count(*) FROM platform_attachments.erasure_jobs"
            ).fetchone()[0]
            == 0
        )
    script = (
        Path(__file__).parents[2]
        / "docs/runbooks/2026-09-11-platform-erasure-readonly.sql"
    ).read_text()
    script = "\n".join(
        line for line in script.splitlines() if not line.startswith("\\")
    )
    result_sets = []
    with psycopg.connect(environment["admin"], autocommit=True) as conn:
        cursor = conn.execute(script)
        while True:
            if cursor.description:
                keys = tuple(column.name for column in cursor.description)
                result_sets.append((keys, cursor.fetchall()))
            if not cursor.nextset():
                break
        assert conn.execute(
            "SELECT state,deleted_at IS NOT NULL FROM platform_attachments.attachments WHERE attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("deleted", True)
    census = [
        rows
        for keys, rows in result_sets
        if keys == ("state", "has_deleted_at", "attachments", "declared_bytes")
    ]
    assert census == [[("deleted", True, 1, 128)]]
    receipts = [
        rows
        for keys, rows in result_sets
        if keys == ("version", "sha256", "applied_at", "checksum_matches_reviewed_file")
    ]
    assert len(receipts) == 1
    assert any(row[0] == 64 and row[3] is True for row in receipts[0])
