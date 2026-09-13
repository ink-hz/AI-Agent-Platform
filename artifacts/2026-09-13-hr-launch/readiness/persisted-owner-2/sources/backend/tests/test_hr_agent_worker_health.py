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

CHILD = r"""
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
"""


def checker():
    assert importlib.util.find_spec("app.hr_agent.worker_health") is not None, (
        "actual worker healthcheck is missing"
    )
    from app.hr_agent.worker_health import check_worker

    return check_worker


@pytest.mark.postgres
@pytest.mark.parametrize("fault", ["stale", "dead", "nonce", "profile", "database"])
def test_live_process_binding_and_dependency_failures_are_checked(tmp_path, fault):
    check = checker()
    env = _environment(tmp_path / "runtime", attachments=False)
    env["PLATFORM_RELEASE_SHA"] = "a" * 40
    status = tmp_path / "worker.json"
    block = tmp_path / "blocked"
    with hr_agent_database(cutover_phase="legacy") as db:
        spec = tmp_path / "child.json"
        spec.write_text(
            json.dumps(
                {"env": env, "dsn": db.dsn, "status": str(status), "block": str(block)}
            )
        )
        process = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(spec)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
        )
        try:
            assert process.stdout.readline().strip() == "ready"
            result = check(status)
            assert result["ready"] and result["worker"]["pid"] == process.pid
            assert not result["new_admission_enabled"]
            if fault == "stale":
                block.touch()
                time.sleep(0.55)
            elif fault == "dead":
                process.kill()
                process.wait(timeout=3)
            elif fault == "nonce":
                value = json.loads(status.read_text())
                value["nonce"] = "wrong-instance"
                status.write_text(json.dumps(value))
            elif fault == "profile":
                Path(env["PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE"]).write_text(
                    "private invalid configuration"
                )
            else:
                with db.admin_connection() as c:
                    c.execute(
                        "delete from platform_control.schema_migrations where version=104"
                    )
            result = check(status)
            assert result["ready"] is False
            assert "private" not in json.dumps(result)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)


def test_missing_worker_state_cannot_pass_by_checking_environment(tmp_path):
    result = checker()(tmp_path / "missing.json")
    assert result["ready"] is False


@pytest.mark.postgres
def test_production_worker_lease_heartbeat_keeps_long_local_model_call_healthy(
    tmp_path,
):
    from uuid import uuid4

    from app.hr_agent.config import load_hr_agent_settings
    from app.hr_agent.resources import build_runtime_services
    from app.hr_agent.worker_health import DEFAULT_STATUS
    from test_hr_agent_context import publication
    from test_hr_agent_worker_process import (
        Provider,
        grant_test_owner,
        process_config,
        wait_until,
        wire,
    )

    check = checker()
    with hr_agent_database() as db, Provider([(5, wire())]) as provider:
        config, _ = process_config(tmp_path, db, provider.endpoint)
        publication(tmp_path / "knowledge")
        env = json.loads(config.read_text())
        env.update(
            PLATFORM_CONTROL_DATABASE_URL_FILE=str(tmp_path / "dsn"),
            PLATFORM_CONVERSATION_ATTACHMENT_ENABLED="0",
            PLATFORM_RELEASE_SHA="a" * 40,
        )
        settings = load_hr_agent_settings(env)
        repo, _ = build_runtime_services(settings, db.connection)
        owner = grant_test_owner(db)
        repo.submit(
            owner,
            {
                "thread_id": None,
                "text": "Public local model fixture",
                "objects": [],
                "references": [],
                "budget_profile": "test",
            },
            uuid4(),
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "app.hr_agent.worker"],
            env={**os.environ, **env, "PYTHONPATH": str(Path(__file__).parents[1])},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_until(lambda: bool(provider.requests) or process.poll() is not None)
            assert process.poll() is None
            time.sleep(
                3.2
            )  # longer than lease/health age; real renewals must keep it alive
            result = check(DEFAULT_STATUS)
            assert result["ready"] and result["worker"]["pid"] == process.pid
            assert repo.claim("another-worker", 3) is None
            cli = subprocess.run(
                [sys.executable, "-m", "app.hr_agent.worker", "healthcheck"],
                env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
                capture_output=True,
                timeout=8,
                check=False,
            )
            assert cli.returncode == 0
            assert json.loads(cli.stdout)["ready"]
            with db.admin_connection() as c:
                c.execute(
                    "delete from platform_control.schema_migrations where version=104"
                )
            failed = subprocess.run(
                [sys.executable, "-m", "app.hr_agent.worker", "healthcheck"],
                env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
                capture_output=True,
                timeout=8,
                check=False,
            )
            assert failed.returncode == 1
            assert "database_unavailable" in json.loads(failed.stdout)["blockers"]
            process.kill()
            process.wait(timeout=5)
            assert not check(DEFAULT_STATUS)["ready"]
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            DEFAULT_STATUS.unlink(missing_ok=True)


def test_worker_healthcheck_does_not_create_model_or_claim_work(tmp_path):
    import yaml

    document = yaml.safe_load(
        (Path(__file__).parents[2] / "deploy/cloud/compose.yaml").read_text()
    )
    assert document["services"]["platform-hr-agent-worker"]["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "app.hr_agent.worker",
        "healthcheck",
    ]
