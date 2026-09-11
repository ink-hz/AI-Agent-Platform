import json
from uuid import uuid4

import psycopg
import pytest
from hr_agent_support import hr_agent_database
from psycopg.conninfo import conninfo_to_dict, make_conninfo


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
            "current_revision,kind) values (%s,%s,%s,%s,'candidate_assessment')",
            (owner, result_id := uuid4(), uuid4(), uuid4()),
        )
        for kind in ("future-private-alpha", "future-private-beta"):
            connection.execute(
                "insert into platform_hr_agent.results(owner_id,result_id,origin_work_id,"
                "current_revision,kind) values (%s,%s,%s,%s,%s)",
                (owner, uuid4(), uuid4(), uuid4(), kind),
            )
        connection.execute(
            "insert into platform_hr_agent.result_links(owner_id,result_id,object_kind,"
            "object_id,linked_by_operation) values (%s,%s,'candidate','not-a-uuid',%s)",
            (owner, result_id, uuid4()),
        )
        owned_position, foreign_position = uuid4(), uuid4()
        v7_position = "00000000-0000-7000-8000-000000000001"
        connection.execute(
            "insert into platform_hr.positions(position_id,owner_internal_user_id,client_request_id,"
            "source_kind,title) values (%s,%s,%s,'manual','sensitive-title'),"
            "(%s,%s,%s,'manual','other-sensitive-title'),(%s,%s,%s,'manual','v7-title')",
            (owned_position, owner, uuid4(), foreign_position, uuid4(), uuid4(),
             v7_position, owner, uuid4()),
        )
        connection.execute(
            "insert into platform_hr_agent.result_links(owner_id,result_id,object_kind,object_id,"
            "linked_by_operation) values (%s,%s,'position',%s,%s),(%s,%s,'position',%s,%s),"
            "(%s,%s,'position',%s,%s),(%s,%s,'position',%s,%s)",
            (owner, result_id, str(owned_position).upper(), uuid4(),
             owner, result_id, "-" * 36, uuid4(),
             owner, result_id, str(foreign_position), uuid4(),
             owner, result_id, v7_position.upper(), uuid4()),
        )
        connection.execute(
            "insert into platform_hr_agent.candidate_positions(owner_id,candidate_id,"
            "position_id,created_by_operation) values (%s,%s,%s,%s),(%s,%s,%s,%s)",
            (owner, uuid4(), uuid4(), uuid4(), owner, uuid4(), uuid4(), uuid4()),
        )
        connection.execute(
            "insert into platform_hr.candidate_drafts(draft_id,owner_internal_user_id,"
            "position_id,attachment_id,batch_request_id,client_request_id,state) "
            "values (%s,%s,%s,%s,%s,%s,'pending')",
            (uuid4(), owner, uuid4(), uuid4(), uuid4(), uuid4()),
        )
        for agent in ("hr-bot", "hannah", "marketing-bot"):
            connection.execute(
                "insert into platform_control.execution_jobs(job_id,run_id,agent_id,"
                "payload_ciphertext,encryption_key_version,status) "
                "values (%s,%s,%s,%s,1,'queued')",
                (uuid4(), uuid4(), agent, sentinel.encode()),
            )
        bound_hr_job = uuid4()
        connection.execute(
            "insert into platform_control.execution_jobs(job_id,run_id,agent_id,payload_ciphertext,"
            "encryption_key_version,status,cancel_requested,created_at) "
            "values (%s,%s,'hr-bot',%s,1,'queued',true,now()-interval '2 days')",
            (bound_hr_job, uuid4(), sentinel.encode()),
        )
        connection.execute(
            "insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status,last_seen_at) "
            "values ('inventory-hr-worker',array['hr-bot'],'active',now()-interval '2 hours'),"
            "('inventory-other-worker',array['marketing-bot'],'active',now())",
        )
        for suffix, agent in (("hr", "hr-bot"), ("other", "marketing-bot")):
            conversation, turn = uuid4(), uuid4()
            connection.execute(
                "insert into platform_control.conversations(conversation_id,owner_internal_user_id,"
                "started_by_client_request_id,mode,direct_agent_id,title) "
                "values (%s,%s,%s,'direct_agent',%s,%s)",
                (conversation, owner, uuid4(), agent, f"{suffix}-sensitive"),
            )
            connection.execute(
                "insert into platform_control.conversation_turns(turn_id,conversation_id,user_message_id,"
                "client_request_id,status) values (%s,%s,%s,%s,'running')",
                (turn, conversation, uuid4(), uuid4()),
            )
            attempt = uuid4()
            connection.execute(
                "insert into platform_control.turn_attempts(attempt_id,turn_id,attempt_no,executor_kind,status) "
                "values (%s,%s,1,'legacy_api_v1','queued')",
                (attempt, turn),
            )
            if suffix == "other":
                connection.execute(
                    "insert into platform_control.direct_command_bindings("
                    "attempt_id,command_id,job_id,conversation_id,command_seq,command_hash) "
                    "values (%s,%s,%s,%s,1,%s)",
                    (attempt, uuid4(), bound_hr_job, conversation, "a" * 64),
                )
    return sentinel


@pytest.fixture(scope="module")
def seeded_database(database):
    return database, _seed_aggregates(database)


@pytest.mark.postgres
def test_inventory_aggregates_old_new_and_classifies_hr_execution(seeded_database):
    from tools.hr_agent.inventory import run_inventory

    database, sentinel = seeded_database
    report = run_inventory(database.connection)
    rendered = json.dumps(report, ensure_ascii=False)

    assert report["transaction"] == "read_only_rolled_back"
    assert report["transaction_read_only"] is True
    assert _asset(report, "old_candidates")["total"] >= 1
    assert _asset(report, "new_results")["total"] >= 1
    assert {group["state"]["kind"]: group["count"] for group in _asset(report, "new_results")["groups"]}[
        "candidate_assessment"
    ] >= 1
    assert len([group for group in _asset(report, "new_results")["groups"] if group["state"] == {"kind": "unknown"}]) == 1
    assert {group["state"]["state"] for group in _asset(report, "old_candidate_drafts")["groups"]} >= {"pending"}
    references = _asset(report, "new_candidate_position_refs")
    assert references["total"] >= 2
    assert len([group for group in references["groups"] if group["state"] == {"resolution": "missing"}]) == 1
    result_refs = _asset(report, "new_result_link_refs")
    assert result_refs["status"] == "ok"
    assert {tuple(sorted(group["state"].items())): group["count"] for group in result_refs["groups"]} == {
        (("kind", "position"), ("resolution", "missing")): 1,
        (("kind", "position"), ("resolution", "resolvable")): 2,
        (("kind", "position"), ("resolution", "wrong_owner")): 1,
        (("kind", "unsupported"), ("resolution", "unsupported")): 1,
    }
    execution = _asset(report, "old_execution_jobs")
    assert {tuple(sorted(group["state"].items())): group["count"] for group in execution["groups"]}[
        (("scope", "hr"), ("status", "queued"))
    ] >= 1
    queued_age = _asset(report, "old_hr_queued_age")
    assert {tuple(sorted(group["state"].items())): group["count"] for group in queued_age["groups"]}[
        (("age", "24h_plus"), ("cancel_requested", True))
    ] >= 1
    assert _asset(report, "active_hr_workers")["groups"] == [
        {"state": {"last_seen_age": "1h_to_24h"}, "count": 1}
    ]
    assert _asset(report, "old_hr_turn_attempts")["groups"] == [
        {"state": {"executor_kind": "legacy_api_v1", "status": "queued"}, "count": 2}
    ]
    assert {tuple(sorted(group["state"].items())): group["count"] for group in execution["groups"]}[
        (("scope", "other"), ("status", "queued"))
    ] >= 2
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
        assert _asset(report, "new_candidate_position_refs")["status"] == "missing_table"
    finally:
        with database.admin_connection() as connection:
            connection.execute("alter schema platform_hr_saved rename to platform_hr")
            connection.execute("alter table platform_hr_agent.results rename column kind_saved to kind")


@pytest.mark.postgres
def test_missing_new_schema_preserves_old_inventory(seeded_database):
    from tools.hr_agent.inventory import run_inventory

    database, _sentinel = seeded_database
    with database.admin_connection() as connection:
        connection.execute("alter schema platform_hr_agent rename to platform_hr_agent_saved")
    try:
        report = run_inventory(database.connection)
        assert _asset(report, "old_candidates")["status"] == "ok"
        assert _asset(report, "old_candidates")["total"] >= 1
        assert _asset(report, "new_results")["status"] == "missing_table"
        assert report["transaction_read_only"] is True
    finally:
        with database.admin_connection() as connection:
            connection.execute("alter schema platform_hr_agent_saved rename to platform_hr_agent")


@pytest.mark.postgres
def test_database_rejects_writes_in_same_read_only_mode(database):
    with database.connection() as connection:
        connection.execute("begin read only")
        assert connection.execute("show transaction_read_only").fetchone()[0] == "on"
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            connection.execute("update platform_hr_agent.works set state=state where false")
        connection.rollback()


@pytest.mark.postgres
def test_rls_tables_are_marked_scope_limited(database):
    from tools.hr_agent.inventory import run_inventory

    with database.admin_connection() as connection:
        connection.execute("alter table platform_hr.candidates enable row level security")
    try:
        report = run_inventory(database.connection)
        assert _asset(report, "old_candidates")["scope"] == "scope_limited"
    finally:
        with database.admin_connection() as connection:
            connection.execute("alter table platform_hr.candidates disable row level security")


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


@pytest.mark.postgres
def test_join_dependency_missing_column_and_permission_are_prechecked(database):
    from tools.hr_agent.inventory import run_inventory

    with database.admin_connection() as connection:
        connection.execute("alter table platform_hr.positions rename column owner_internal_user_id to owner_saved")
    try:
        report = run_inventory(database.connection)
        assert _asset(report, "new_result_link_refs")["status"] == "missing_column"
    finally:
        with database.admin_connection() as connection:
            connection.execute("alter table platform_hr.positions rename column owner_saved to owner_internal_user_id")

    with database.admin_connection() as connection:
        connection.execute("revoke select on platform_hr.positions from platform_control_app")
    try:
        report = run_inventory(database.connection)
        assert _asset(report, "new_candidate_position_refs")["status"] == "unreadable"
    finally:
        with database.admin_connection() as connection:
            connection.execute("grant select on platform_hr.positions to platform_control_app")

    with database.admin_connection() as connection:
        connection.execute(
            "alter table platform_control.direct_command_bindings rename to direct_command_bindings_saved"
        )
    try:
        report = run_inventory(database.connection)
        assert _asset(report, "old_hr_turn_attempts")["status"] == "missing_table"
        assert _asset(report, "old_hr_turn_attempts").get("total") is None
    finally:
        with database.admin_connection() as connection:
            connection.execute(
                "alter table platform_control.direct_command_bindings_saved rename to direct_command_bindings"
            )


@pytest.mark.postgres
def test_column_level_select_can_inventory_only_required_columns(seeded_database):
    from tools.hr_agent.inventory import run_inventory

    database, _sentinel = seeded_database
    role = "hr_inventory_column_reader"
    with database.admin_connection() as connection:
        connection.execute(f"drop role if exists {role}")
        connection.execute(f"create role {role} login")
        connection.execute(f"grant usage on schema platform_hr to {role}")
        connection.execute(f"grant select(candidate_id) on platform_hr.candidates to {role}")
    details = conninfo_to_dict(database.dsn)
    details["user"] = role
    try:
        report = run_inventory(lambda: psycopg.connect(make_conninfo(**details)))
        assert _asset(report, "old_candidates")["status"] == "ok"
        assert _asset(report, "old_candidates")["total"] >= 1
        assert _asset(report, "old_candidate_documents")["status"] == "unreadable"
    finally:
        with database.admin_connection() as connection:
            connection.execute(f"drop owned by {role}")
            connection.execute(f"drop role {role}")


def test_cli_rejects_unsafe_dsn_file_and_output(tmp_path, capsys, monkeypatch):
    from tools.hr_agent import inventory

    dsn = tmp_path / "dsn"
    dsn.write_text("PRIVATE-DSN-SENTINEL")
    dsn.chmod(0o644)
    with pytest.raises(SystemExit) as failure:
        inventory.main(["--dsn-file", str(dsn)])
    assert failure.value.code == 2
    assert "PRIVATE-DSN-SENTINEL" not in capsys.readouterr().err

    dsn.chmod(0o600)
    monkeypatch.setattr(inventory, "run_inventory", lambda factory: {"safe": True})
    output = tmp_path / "report.json"
    output.symlink_to(tmp_path / "target.json")
    with pytest.raises(SystemExit) as failure:
        inventory.main(["--dsn-file", str(dsn), "--output", str(output)])
    assert failure.value.code == 2


def test_cli_sanitizes_filesystem_errors(tmp_path, capsys, monkeypatch):
    from tools.hr_agent import inventory

    dsn = tmp_path / "dsn"
    dsn.write_text("PRIVATE-DSN-SENTINEL")
    dsn.chmod(0o600)
    monkeypatch.setattr(type(dsn), "read_text", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("PRIVATE-PATH-SENTINEL")))
    with pytest.raises(SystemExit):
        inventory.main(["--dsn-file", str(dsn)])
    assert "PRIVATE-PATH-SENTINEL" not in capsys.readouterr().err


def test_registry_never_selects_sensitive_columns():
    from tools.hr_agent.inventory import QUERY_REGISTRY

    forbidden = {
        "stable_name", "facts", "payload_ciphertext", "sealed_document",
        "manifest", "job", "object_ref_ciphertext", "original_name_ciphertext",
    }
    sql = " ".join(spec.sql.lower() for spec in QUERY_REGISTRY)
    assert not forbidden.intersection(sql.split())
    assert all(spec.allowed_states is not None for spec in QUERY_REGISTRY if spec.state_columns)
    assert "update " not in sql
    by_name = {spec.name: spec for spec in QUERY_REGISTRY}
    assert by_name["new_results"].allowed_states["kind"] == frozenset({
        "role_calibration", "jd", "requirements", "standard_proposal", "sourcing",
        "candidate_assessment", "interview_plan", "interview_record", "retrospective", "research",
    })
    assert by_name["old_candidate_drafts"].allowed_states["state"] == frozenset({
        "pending", "processing", "ready", "failed", "confirmed", "dismissed",
    })
    assert by_name["new_reference_edges"].allowed_states["source_kind"] == frozenset({
        "material", "method", "result", "intelligence", "standard",
    })
