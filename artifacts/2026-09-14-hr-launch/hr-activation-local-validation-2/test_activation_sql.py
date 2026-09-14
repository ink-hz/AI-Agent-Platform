from pathlib import Path
import json
import subprocess

from hr_agent_support import hr_agent_database
from app.control_plane.migrate import migrate_control_database

SQL = Path(__file__).resolve().parent.parent / "hr-activation"


def test_exact_psql_activation_transactions_and_readback():
    with hr_agent_database(migrate_hr=False) as database:
        migrations = Path("backend/control_migrations").resolve()
        for family in ("hr_web", "hr_agent"):
            migrate_control_database(database.migrator_dsn, migrations / family,
                                     owner_role="platform_control_owner")
        with database.admin_connection() as connection:
            connection.execute("REVOKE platform_control_owner FROM platform_control_migrator")
        _run_exact_sql(database.admin_dsn)


def _run_exact_sql(dsn):
    outputs = {}
    for name in ("initialize", "counts-under-lock", "draining-legacy", "cloud", "readback"):
        raw = (SQL / (name + ".sql")).read_bytes()
        result = subprocess.run(["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", dsn],
                                input=raw, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr.decode()
        outputs[name] = [json.loads(line) for line in result.stdout.decode().splitlines()
                         if line.startswith("{")]
    assert outputs["initialize"][0]["result"]["phase"] == "legacy"
    assert outputs["counts-under-lock"][0]["counts"] == {"legacy_nonterminal": 0, "cloud_nonterminal": 0}
    assert outputs["draining-legacy"][0]["result"]["phase"] == "draining_legacy"
    assert outputs["cloud"][0]["result"]["phase"] == "cloud"
    final = outputs["readback"][0]
    assert final["cutover"]["phase"] == "cloud"
    assert final["cutover"]["epoch"] == 3
    assert [x["target_phase"] for x in final["operations"]] == ["legacy", "draining_legacy", "cloud"]
    assert final["root107"] == "d0405bdb767d2cc74efae79b566fab9380432d277c57d59589569a5e31687170"
    assert final["erasure_expired_running"] == final["erasure_exhausted"] == 0
    assert final["migrator_sessions"] == 0
