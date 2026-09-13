"""Owned child process and real PG readiness; no external model call."""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_hr_agent_preflight import _environment
from tests.hr_agent_support import hr_agent_database

CHILD = r'''
import json, sys, time
from pathlib import Path
import psycopg
from app.hr_agent.config import load_hr_agent_settings
from app.hr_agent.readiness import HrReadiness
from app.hr_agent.worker_health import WorkerHealth
value=json.loads(Path(sys.argv[1]).read_text())
probe=HrReadiness(load_hr_agent_settings(value['env']),value['env'],lambda:psycopg.connect(value['dsn']),role='worker')
with WorkerHealth(probe,Path(value['status']),max_age=0.4) as health:
    health.progress()
    print('ready',flush=True)
    while True:
        if not Path(value['block']).exists(): health.progress()
        time.sleep(.03)
'''


def checker():
    assert importlib.util.find_spec("app.hr_agent.worker_health") is not None, "actual worker healthcheck is missing"
    from app.hr_agent.worker_health import check_worker
    return check_worker


@pytest.mark.postgres
@pytest.mark.parametrize("fault", ["stale", "dead", "nonce", "profile", "database"])
def test_live_process_binding_and_dependency_failures_are_checked(tmp_path, fault):
    check = checker()
    env = _environment(tmp_path / "runtime", attachments=False)
    status = tmp_path / "worker.json"; block = tmp_path / "blocked"
    with hr_agent_database(cutover_phase="legacy") as db:
        spec = tmp_path / "child.json"
        spec.write_text(json.dumps({"env": env, "dsn": db.dsn, "status": str(status), "block": str(block)}))
        process = subprocess.Popen([sys.executable, "-c", CHILD, str(spec)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ,"PYTHONPATH":str(Path(__file__).parents[1])})
        try:
            assert process.stdout.readline().strip() == "ready"
            result = check(status)
            assert result["ready"] and result["worker"]["pid"] == process.pid
            assert not result["new_admission_enabled"]
            if fault == "stale":
                block.touch();time.sleep(.55)
            elif fault == "dead":
                process.kill();process.wait(timeout=3)
            elif fault == "nonce":
                value=json.loads(status.read_text());value["nonce"]="wrong-instance";status.write_text(json.dumps(value))
            elif fault == "profile":
                Path(env["PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE"]).write_text("private invalid configuration")
            else:
                with db.admin_connection() as c:c.execute("delete from platform_control.schema_migrations where version=104")
            result=check(status)
            assert result["ready"] is False
            assert "private" not in json.dumps(result)
        finally:
            if process.poll() is None: process.kill()
            process.communicate(timeout=3)


def test_missing_worker_state_cannot_pass_by_checking_environment(tmp_path):
    result = checker()(tmp_path / "missing.json")
    assert result["ready"] is False
