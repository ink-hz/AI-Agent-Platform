"""Aggregate classification for cutover; fixture payloads never leave the DB."""

import json
from uuid import uuid4

import pytest
from tests.hr_agent_support import hr_agent_database
from tests.test_hr_agent_inventory import _seed_aggregates
from tools.hr_agent.inventory import run_inventory


@pytest.fixture
def database():
    with hr_agent_database() as database:
        yield database


def test_hr_jobs_classified_by_kind_live_references_and_pending_stop(database):
    sentinel = _seed_aggregates(database)
    with database.admin_connection() as connection:
        connection.execute("set session_replication_role=replica")
        job, run, turn = connection.execute(
            "select j.job_id,j.run_id,a.turn_id from platform_control.execution_jobs j "
            "join platform_control.direct_command_bindings b using(job_id) "
            "join platform_control.turn_attempts a using(attempt_id) where j.agent_id='hr-bot'"
        ).fetchone()
        connection.execute(
            "update platform_control.execution_jobs set job_kind='worker_direct_v5',status='interrupted',"
            "lease_worker_id='inventory-hr-worker',stop_requested_status='interrupted',"
            "stop_acknowledged_at=null,terminal_at=now() where job_id=%s",
            (job,),
        )
        mission = uuid4()
        connection.execute(
            "insert into platform_control.mission_runs(run_id,mission_id,task_id,phase,agent_id,status,"
            "input_ciphertext,encryption_key_version,terminal_at) values(%s,%s,%s,'direct','hr-bot','interrupted',%s,1,now())",
            (run, mission, uuid4(), b"x" * 29),
        )
        connection.execute(
            "update platform_control.conversation_turns set mission_id=%s where turn_id=%s",
            (mission, turn),
        )
        connection.execute(
            "insert into platform_control.execution_jobs(job_id,run_id,agent_id,job_kind,status,"
            "payload_ciphertext,encryption_key_version,lease_worker_id,terminal_at) "
            "values(%s,%s,'hr-bot','metabot_local','interrupted',%s,1,'inventory-hr-worker',now())",
            (uuid4(), uuid4(), sentinel.encode()),
        )
    report = run_inventory(database.connection)
    item = next(
        item for item in report["assets"] if item["name"] == "old_hr_job_references"
    )
    assert item["status"] == "ok"
    assert item["total"] == 3  # one count per HR job, including overlapping references
    groups = {group["state"]["job_kind"]: group for group in item["groups"]}
    assert groups["legacy_brain"]["state"]["status"] == "queued"
    assert groups["legacy_brain"]["state"]["has_turn_reference"] is False
    linked = groups["worker_direct_v5"]["state"]
    assert linked["has_direct_binding"] is True
    assert linked["has_mission_run"] is True
    assert linked["has_turn_reference"] is True
    assert linked["has_nonterminal_turn"] is True
    assert linked["has_nonterminal_attempt"] is True
    assert linked["pending_stop"] is True
    assert linked["terminal_recorded"] is True
    local = groups["metabot_local"]["state"]
    assert local["has_direct_binding"] is False
    assert local["has_mission_run"] is False
    assert local["has_nonterminal_turn"] is False
    assert local["pending_stop"] is False
    rendered = json.dumps(item)
    assert sentinel not in rendered
    assert str(job) not in rendered and str(run) not in rendered


def test_reference_classification_missing_dependency_is_not_zero(database):
    with database.admin_connection() as connection:
        connection.execute(
            "alter table platform_control.mission_runs rename to mission_runs_saved"
        )
    report = run_inventory(database.connection)
    item = next(
        item for item in report["assets"] if item["name"] == "old_hr_job_references"
    )
    assert item["status"] == "missing_table"
    assert "total" not in item
