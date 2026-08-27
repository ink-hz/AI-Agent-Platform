from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "048_voc_agent_catalog_authorization.sql"
)


def test_voc_authorization_migration_extends_the_canonical_allowlist() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "create or replace function platform_control.has_agent_use_scope_v29" in sql
    assert "'voc'" in sql
    assert "Canonical nine-Agent authorization allowlist" in sql
