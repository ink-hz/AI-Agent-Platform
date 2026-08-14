from pathlib import Path
import re


MIGRATION = Path(__file__).parents[1] / "migrations" / "008_cloud_replica.sql"


def test_cloud_replica_migration_has_dedicated_encrypted_schema_and_roles():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create schema if not exists platform_replica" in sql
    for table in (
        "generations",
        "agents",
        "sessions",
        "runtime_snapshots",
        "aggregate_snapshots",
        "management_projections",
        "import_audit",
        "retention_audit",
    ):
        assert f"create table if not exists platform_replica.{table}" in sql
    assert "display_payload bytea" in sql
    assert "payload_nonce bytea" in sql
    assert "platform_replica_import" in sql
    assert "platform_replica_read" in sql
    assert "grant select" in sql
    assert "grant insert" in sql or "grant select, insert" in sql
    assert "revoke all on schema public" in sql
    assert "replica_management_agent_time_idx" in sql


def test_cloud_replica_schema_has_no_raw_or_cleartext_sensitive_columns():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    columns = set(
        re.findall(
            r"^\s*([a-z][a-z0-9_]*)\s+(?:text|varchar|jsonb|bytea|uuid)",
            sql,
            flags=re.MULTILINE,
        )
    )

    for forbidden in (
        "raw_identity",
        "user_identity",
        "native_id",
        "question",
        "answer",
        "filename",
        "display_name",
        "credential",
        "details",
        "sources",
    ):
        assert forbidden not in columns
