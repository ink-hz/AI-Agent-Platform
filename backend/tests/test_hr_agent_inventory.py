import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from hr_agent_support import hr_agent_database


@pytest.fixture(scope="module")
def database():
    with hr_agent_database() as selected:
        yield selected


def _asset(report, name):
    return next(item for item in report["assets"] if item["name"] == name)


def _seed_aggregates(database):
    sentinel = "PRIVATE-CANDIDATE-SENTINEL"
    with database.admin_connection() as connection:
        connection.execute("set session_replication_role=replica")
        owner = uuid4()
        connection.execute(
            "insert into platform_hr.candidates(candidate_id,owner_internal_user_id,"
            "confirmation_request_id,stable_name,facts) values (%s,%s,%s,%s,'{}')",
            (uuid4(), owner, uuid4(), sentinel),
        )
        connection.execute(
            "insert into platform_hr_agent.results(owner_id,result_id,origin_work_id,"
            "current_revision,kind) values (%s,%s,%s,%s,'research')",
            (owner, result_id := uuid4(), uuid4(), uuid4()),
        )
        connection.execute(
            "insert into platform_hr_agent.result_links(owner_id,result_id,object_kind,"
            "object_id,linked_by_operation) values (%s,%s,'candidate','not-a-uuid',%s)",
            (owner, result_id, uuid4()),
        )
        connection.execute(
            "insert into platform_hr_agent.candidate_positions(owner_id,candidate_id,"
            "position_id,created_by_operation) values (%s,%s,%s,%s),(%s,%s,%s,%s)",
            (owner, uuid4(), uuid4(), uuid4(), owner, uuid4(), uuid4(), uuid4()),
        )
        for agent in ("hr-bot", "marketing-bot"):
            connection.execute(
                "insert into platform_control.execution_jobs(job_id,run_id,agent_id,"
                "payload_ciphertext,encryption_key_version,status) "
                "values (%s,%s,%s,%s,1,'queued')",
                (uuid4(), uuid4(), agent, sentinel.encode()),
            )
    return sentinel


@pytest.mark.postgres
def test_inventory_aggregates_old_new_and_classifies_hr_execution(database):
    from tools.hr_agent.inventory import run_inventory

    sentinel = _seed_aggregates(database)
    report = run_inventory(database.connection)
    rendered = json.dumps(report, ensure_ascii=False)

    assert report["transaction"] == "read_only_rolled_back"
    assert report["write_probe"] == "rejected"
    assert _asset(report, "old_candidates")["total"] >= 1
    assert _asset(report, "new_results")["total"] >= 1
    references = _asset(report, "new_candidate_position_refs")
    assert references["total"] >= 2
    assert len([group for group in references["groups"] if group["state"] == {"resolution": "missing"}]) == 1
    assert _asset(report, "new_result_link_refs")["groups"] == [
        {"state": {"kind": "unsupported", "resolution": "unsupported"}, "count": 1}
    ]
    execution = _asset(report, "old_execution_jobs")
    assert {tuple(sorted(group["state"].items())): group["count"] for group in execution["groups"]}[
        (("scope", "hr"), ("status", "queued"))
    ] >= 1
    assert {tuple(sorted(group["state"].items())): group["count"] for group in execution["groups"]}[
        (("scope", "other"), ("status", "queued"))
    ] >= 1
    assert sentinel not in rendered
    assert "payload_ciphertext" not in rendered


@pytest.mark.postgres
def test_inventory_distinguishes_missing_table_and_column(database):
    from tools.hr_agent.inventory import run_inventory

    with database.admin_connection() as connection:
        connection.execute("alter schema platform_hr rename to platform_hr_saved")
        connection.execute("alter table platform_hr_agent.results rename column kind to kind_saved")
    try:
        report = run_inventory(database.connection)
        assert _asset(report, "old_candidates")["status"] == "missing_table"
        assert _asset(report, "old_positions")["status"] == "missing_table"
        assert _asset(report, "new_results")["status"] == "missing_column"
        assert _asset(report, "old_candidates").get("total") is None
        assert _asset(report, "new_results").get("total") is None
    finally:
        with database.admin_connection() as connection:
            connection.execute("alter schema platform_hr_saved rename to platform_hr")
            connection.execute("alter table platform_hr_agent.results rename column kind_saved to kind")


@pytest.mark.postgres
def test_inventory_reports_permission_denied_without_sensitive_error(database):
    from tools.hr_agent.inventory import run_inventory

    sentinel = "SECRET-ERROR-SENTINEL"
    with database.admin_connection() as connection:
        connection.execute("revoke select on platform_hr.candidates from platform_control_app")
        connection.execute(f"comment on table platform_hr.candidates is '{sentinel}'")
    try:
        report = run_inventory(database.connection)
        assert _asset(report, "old_candidates")["status"] == "unreadable"
        assert _asset(report, "old_candidates").get("total") is None
        assert sentinel not in json.dumps(report)
    finally:
        with database.admin_connection() as connection:
            connection.execute("grant select on platform_hr.candidates to platform_control_app")


def test_cli_rejects_unsafe_dsn_file_and_output(tmp_path, capsys):
    from tools.hr_agent.inventory import main

    dsn = tmp_path / "dsn"
    dsn.write_text("PRIVATE-DSN-SENTINEL")
    dsn.chmod(0o644)
    with pytest.raises(SystemExit) as failure:
        main(["--dsn-file", str(dsn)])
    assert failure.value.code == 2
    assert "PRIVATE-DSN-SENTINEL" not in capsys.readouterr().err

    dsn.chmod(0o600)
    output = tmp_path / "report.json"
    output.symlink_to(tmp_path / "target.json")
    with pytest.raises(SystemExit) as failure:
        main(["--dsn-file", str(dsn), "--output", str(output)])
    assert failure.value.code == 2


def test_registry_never_selects_sensitive_columns():
    from tools.hr_agent.inventory import QUERY_REGISTRY

    forbidden = {
        "stable_name", "facts", "payload_ciphertext", "sealed_document",
        "manifest", "job", "object_ref_ciphertext", "original_name_ciphertext",
    }
    sql = " ".join(spec.sql.lower() for spec in QUERY_REGISTRY)
    assert not forbidden.intersection(sql.split())
    assert all(spec.allowed_states is not None for spec in QUERY_REGISTRY if spec.state_columns)
