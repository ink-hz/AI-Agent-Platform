from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "043_agent_catalog_authorization.sql"
)


def test_catalog_authorization_migration_replaces_legacy_fae_allowlist() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for agent_id in (
        "hr-bot",
        "marketing-prospecting-bot",
        "marketing-inbound-bot",
        "marketing-voice-bot",
        "marketing-intelligence-bot",
        "marketing-gtm-bot",
        "ai-admin-agent",
        "ai-fae-agent",
    ):
        assert f"'{agent_id}'" in sql
    assert "'fae-bot'" not in sql
    assert "create or replace function platform_control.has_agent_use_scope_v29" in sql
