"""Release wiring for the already verified opt-in HR WEB execution contract."""

from pathlib import Path

import yaml
import psycopg
from test_control_plane_migration import control_database as control_database  # noqa: F401
from app.control_plane.migrate import load_numbered_migrations, verify_or_apply

ROOT = Path(__file__).parents[2]


def test_hr_web_has_explicit_independent_worker_and_preserves_api_settings():
    services = yaml.safe_load((ROOT / "deploy/cloud/compose.yaml").read_text())["services"]
    api = services["platform-api"]
    assert api["environment"].get("PLATFORM_HR_WEB_WORKER_ENABLED") == "${PLATFORM_HR_WEB_WORKER_ENABLED:-0}"
    assert "platform-hr-web-worker" in services
    worker = services["platform-hr-web-worker"]
    assert worker["profiles"] == ["hr-web"]
    assert worker["command"][:2] == ["/bin/sh", "-ec"]
    assert worker["command"][2].endswith("exec python -m app.agent_brain.direct_worker\n")
    assert worker["command"][2].split("exec ")[0] == api["command"][2].split("exec ")[0]
    assert worker["environment"] == api["environment"]
    assert worker["volumes"] == api["volumes"]
    assert worker["read_only"] is True
    assert worker["user"] == "10001:10001"
    assert "ports" not in worker
    assert worker["networks"] == {"platform-internal": {}, "platform-edge": {"gw_priority": 1}}


def test_authorized_numbered_migrations_match_verified_pending_sql():
    migrations = ROOT / "backend/control_migrations"
    names = ["hr_turn_attempts", "hr_v5_readiness", "hr_direct_dispatch", "hr_web_result_recovery", "hr_turn_input_context"]
    for number, name in enumerate(names, 89):
        numbered = migrations / "hr_web" / f"{number:03}_{name}.sql"
        assert numbered.exists(), f"missing production migration {number}"
        meaningful = lambda source: "\n".join(line for line in source.splitlines() if not line.startswith("--"))
        assert meaningful(numbered.read_text()) == meaningful((migrations / "pending" / f"{name}.sql").read_text())


def test_promoted_opt_in_chain_applies_and_replays_on_actual_base(control_database):
    environment = control_database["environments"]["production"]
    migrations = list(load_numbered_migrations(ROOT / "backend/control_migrations/hr_web"))
    with psycopg.connect(environment["admin"]) as connection:
        for _ in range(2):
            with connection.cursor() as cursor:
                cursor.execute("set local role platform_control_owner")
                for migration in migrations:
                    verify_or_apply(cursor, migration.version, migration.sha256, migration.sql)
        assert connection.execute("select max(version) from platform_control.schema_migrations").fetchone()[0] == 93
        assert connection.execute("select to_regclass('platform_control.turn_attempts'), to_regclass('platform_control.result_artifact_intents')").fetchone() == ("platform_control.turn_attempts", "platform_control.result_artifact_intents")
