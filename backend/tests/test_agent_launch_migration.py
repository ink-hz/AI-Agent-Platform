from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "control_migrations"
    / "052_agent_launch_identity_binding.sql"
)


def test_agent_launch_migration_defines_single_use_codes_and_session_bindings() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table platform_control.agent_launch_codes" in sql
    assert "create table platform_control.agent_identity_bindings" in sql
    assert "code_hash bytea not null" in sql
    assert "unique (code_key_version, code_hash)" in sql
    assert "source_session_id uuid not null" in sql
    assert sql.count(
        "references platform_control.web_sessions(session_id) on delete cascade"
    ) == 2
    assert "consumed_at timestamptz" in sql
    assert "expires_at timestamptz not null" in sql
    assert "issue_agent_launch_v52" in sql
    assert "exchange_agent_launch_v52" in sql
    assert "validate_agent_identity_binding_v52" in sql
    assert "security definer" in sql


def test_agent_launch_functions_are_execute_only_for_the_app_role() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "revoke all on platform_control.agent_launch_codes from public" in sql
    assert "revoke all on platform_control.agent_identity_bindings from public" in sql
    assert "grant execute on function platform_control.issue_agent_launch_v52" in sql
    assert "grant execute on function platform_control.exchange_agent_launch_v52" in sql
    assert "grant execute on function '" in sql
    assert "platform_control.validate_agent_identity_binding_v52(uuid,text) to %i" in sql
    assert "grant insert" not in sql
    assert "grant update" not in sql
