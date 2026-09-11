import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest
from app.control_plane.migrate import migrate_control_database
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from tests.hr_agent_support import hr_agent_database

ROOT = Path(__file__).parents[2]
HELPER = ROOT / "deploy/cloud/migrate-hr-agent.sh"
SUPERVISOR = ROOT / "deploy/cloud/hr_agent_migrate.py"


def _write_fake_docker(path: Path):
    path.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """
import json, os, pathlib, signal, sys, time, uuid
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from app.control_plane.migrate import migrate_control_database

args=sys.argv[1:]
if args[0]=='exec':
    args=args[2:]
    database=args[args.index('-d')+1]
    query=args[args.index('-c')+1] if '-c' in args else sys.stdin.read()
    values=conninfo_to_dict(os.environ['TEST_ADMIN_DSN']); values['dbname']=database
    with psycopg.connect(make_conninfo(**values),autocommit=True) as connection:
        cursor=connection.execute(query)
        while cursor.nextset():
            pass
        rows=cursor.fetchall() if cursor.description else []
        if rows:
            print('\\n'.join(str(row[0]) for row in rows))
elif args[0]=='create':
    state_path=pathlib.Path(os.environ['TEST_DOCKER_STATE'])
    name=args[args.index('--name')+1]
    environment={}; mounts=[]
    for index,value in enumerate(args):
        if value=='-e':
            key,item=args[index+1].split('=',1); environment[key]=item
        elif value=='-v': mounts.append(args[index+1])
    identifier=uuid.uuid4().hex
    state_path.write_text(json.dumps({'id':identifier,'name':name,'running':False,'exit':None,'environment':environment,'mounts':mounts}))
    pathlib.Path(os.environ['TEST_DOCKER_CALLS']).open('a').write('create '+name+'\\n')
    print(identifier)
elif args[0]=='start':
    state_path=pathlib.Path(os.environ['TEST_DOCKER_STATE']); state=json.loads(state_path.read_text())
    state['running']=True; state_path.write_text(json.dumps(state))
    pathlib.Path(os.environ['TEST_DOCKER_CALLS']).open('a').write('start '+args[-1]+'\\n')
    scenario=os.environ.get('TEST_DOCKER_SCENARIO','')
    with psycopg.connect(os.environ['TEST_ADMIN_DSN'],autocommit=True) as connection:
        held=connection.execute("select r.rolname from pg_auth_members m join pg_roles r on r.oid=m.roleid where r.rolname in ('platform_control_owner','platform_control_owner_preview') order by r.rolname").fetchall()
        assert held == [(state['environment']['PLATFORM_CONTROL_OWNER_ROLE'],)]
    if 'ignore-term' in scenario or 'timeout' in scenario:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
    secret_name=pathlib.Path(state['environment']['PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE']).name
    secret=pathlib.Path(os.environ['TEST_PRIVATE'])/secret_name
    role=state['environment']['PLATFORM_CONTROL_OWNER_ROLE']
    migration_dir=pathlib.Path(os.environ['TEST_RELEASE'])/'backend/control_migrations/hr_agent'
    migrate_control_database(secret.read_text(),migration_dir,owner_role=role)
    state=json.loads(state_path.read_text()); state['running']=False; state['exit']=0; state_path.write_text(json.dumps(state))
elif args[0]=='inspect':
    state=json.loads(pathlib.Path(os.environ['TEST_DOCKER_STATE']).read_text())
    print(json.dumps({'Running':state['running'],'ExitCode':state['exit']}))
elif args[0] in ('stop','kill','rm'):
    pathlib.Path(os.environ['TEST_DOCKER_CALLS']).open('a').write(args[0]+' '+args[-1]+'\\n')
    if 'inspect-failure' in os.environ.get('TEST_DOCKER_SCENARIO','') and args[0] in ('stop','kill','inspect'):
        raise SystemExit(1)
    if args[0] != 'rm':
        state_path=pathlib.Path(os.environ['TEST_DOCKER_STATE']); state=json.loads(state_path.read_text())
        state['running']=False; state['exit']=137; state_path.write_text(json.dumps(state))
elif args[0]=='run':
    environment={}
    index=1
    while index < len(args):
        if args[index]=='-e':
            name,value=args[index+1].split('=',1); environment[name]=value; index+=2
        elif args[index] in ('-v','--network','--user'):
            index+=2
        elif args[index].startswith('-'):
            index+=1
        else:
            break
    secret=pathlib.Path(os.environ['TEST_PRIVATE'])/pathlib.Path(environment['PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE']).name
    migration_dir=pathlib.Path(os.environ['TEST_RELEASE'])/'backend/control_migrations/hr_agent'
    migrate_control_database(secret.read_text(),migration_dir,owner_role=environment['PLATFORM_CONTROL_OWNER_ROLE'])
else:
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    path.chmod(0o700)


def _memberships(database):
    with database.admin_connection() as connection:
        return connection.execute(
            "select count(*) from pg_auth_members m join pg_roles r on r.oid=m.roleid "
            "where r.rolname in ('platform_control_owner','platform_control_owner_preview')"
        ).fetchone()[0]


def _prepare_preview_and_revoke(database):
    values = conninfo_to_dict(database.admin_dsn)
    values["dbname"] = "postgres"
    with psycopg.connect(make_conninfo(**values), autocommit=True) as connection:
        connection.execute(
            "create database agent_platform_control_preview "
            "owner platform_control_owner_preview"
        )
        connection.execute(
            "grant platform_control_owner_preview to platform_control_migrator_preview"
        )
    preview_dsn = database.migrator_dsn.replace(
        "user=platform_control_migrator", "user=platform_control_migrator_preview"
    ).replace("dbname=agent_platform_control", "dbname=agent_platform_control_preview")
    migrations = ROOT / "backend/control_migrations"
    migrate_control_database(
        preview_dsn, migrations, owner_role="platform_control_owner_preview"
    )
    with database.admin_connection() as connection:
        connection.execute(
            "revoke platform_control_owner from platform_control_migrator"
        )
        connection.execute(
            "revoke platform_control_owner_preview from platform_control_migrator_preview"
        )
    return preview_dsn


def test_helper_grants_only_transiently_and_applies_hr_migrations(tmp_path):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        private = tmp_path / "private"
        private.mkdir()
        (private / "control-migrator-database-url").write_text(database.migrator_dsn)
        (private / "preview-control-migrator-database-url").write_text(preview_dsn)
        for item in private.iterdir():
            item.chmod(0o600)
        fake = tmp_path / "docker"
        _write_fake_docker(fake)
        environment = {
            **os.environ,
            "HR_MIGRATION_DOCKER": str(fake),
            "TEST_ADMIN_DSN": database.admin_dsn,
            "TEST_PRIVATE": str(private),
            "TEST_RELEASE": str(ROOT),
            "TEST_DOCKER_STATE": str(tmp_path / "docker-state.json"),
            "TEST_DOCKER_CALLS": str(tmp_path / "docker-calls.log"),
            "PYTHONPATH": str(ROOT / "backend"),
        }
        result = subprocess.run(
            [str(HELPER), str(ROOT), str(private), "sha256:" + "a" * 64, "test-postgres",
             "--receipt-dir", str(tmp_path / "receipts")],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "HR_AGENT_MIGRATIONS_OK environments=2 cleanup=verified"
        assert _memberships(database) == 0
        receipts = list((tmp_path / "receipts").glob("*.json"))
        assert len(receipts) == 1
        assert receipts[0].stat().st_mode & 0o777 == 0o600
        receipt_text = receipts[0].read_text()
        receipt = __import__("json").loads(receipt_text)
        assert receipt["status"] == "completed"
        assert receipt["environment"] == "preview"
        assert receipt["cleanup_verified"] is True
        assert "user=platform_control_migrator" not in receipt_text
        calls = (tmp_path / "docker-calls.log").read_text().splitlines()
        assert [line.split()[0] for line in calls].count("create") == 2
        assert [line.split()[0] for line in calls].count("start") == 2
        with database.connection() as connection:
            assert (
                connection.execute(
                    "select count(*) from platform_control.schema_migrations "
                    "where version in (96,97,98,99,101)"
                ).fetchone()[0]
                == 5
            )


def test_helper_revokes_owner_membership_when_hr_migration_fails(tmp_path):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        private = tmp_path / "private"
        private.mkdir()
        (private / "control-migrator-database-url").write_text(database.migrator_dsn)
        (private / "preview-control-migrator-database-url").write_text(preview_dsn)
        for item in private.iterdir():
            item.chmod(0o600)
        fake = tmp_path / "docker"
        _write_fake_docker(fake)
        broken_release = tmp_path / "release"
        (broken_release / "backend/control_migrations/hr_agent").mkdir(parents=True)
        for source in (ROOT / "backend/control_migrations/hr_agent").glob("*.sql"):
            target = (
                broken_release / "backend/control_migrations/hr_agent" / source.name
            )
            target.write_bytes(source.read_bytes())
        (
            broken_release
            / "backend/control_migrations/hr_agent/096_hr_agent_runtime.sql"
        ).write_text("invalid sql")
        # Root gate files are immutable release inputs too.
        for name in (
            "100_attachment_erasure_worker_access.sql",
            "102_hr_execution_cutover.sql",
            "103_hr_execution_drain_occupancy.sql",
        ):
            target = broken_release / "backend/control_migrations" / name
            target.write_bytes(
                (ROOT / "backend/control_migrations" / name).read_bytes()
            )
        result = subprocess.run(
            [
                str(HELPER),
                str(broken_release),
                str(private),
                "sha256:" + "a" * 64,
                "test-postgres",
                "--receipt-dir",
                str(tmp_path / "receipts"),
            ],
            env={
                **os.environ,
                "HR_MIGRATION_DOCKER": str(fake),
                "TEST_ADMIN_DSN": database.admin_dsn,
                "TEST_PRIVATE": str(private),
                "TEST_RELEASE": str(broken_release),
                "TEST_DOCKER_STATE": str(tmp_path / "docker-state.json"),
                "TEST_DOCKER_CALLS": str(tmp_path / "docker-calls.log"),
                "PYTHONPATH": str(ROOT / "backend"),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        assert _memberships(database) == 0
        assert "invalid sql" not in result.stderr


def test_helper_never_runs_root_migration_directory():
    source = SUPERVISOR.read_text(encoding="utf-8")
    assert (
        "PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations/hr_agent"
        in source
    )
    assert "100_attachment_erasure_worker_access.sql" in source
    assert "102_hr_execution_cutover.sql" in source
    assert "103_hr_execution_drain_occupancy.sql" in source
    assert "finally:" in source
    assert "self.cleanup()" in source
    assert "revoke {self.granted_owner} from {self.granted_migrator}" in source


def test_wrapper_delegates_to_bounded_stdlib_supervisor():
    source = HELPER.read_text(encoding="utf-8")
    assert SUPERVISOR.is_file()
    assert "hr_agent_migrate.py" in source
    assert "--migration-timeout" in SUPERVISOR.read_text(encoding="utf-8")
    assert "--command-timeout" in SUPERVISOR.read_text(encoding="utf-8")
    assert "--receipt-dir" in SUPERVISOR.read_text(encoding="utf-8")


def test_supervisor_owns_named_container_lifecycle_and_redacted_receipt():
    source = SUPERVISOR.read_text(encoding="utf-8")
    for command in ("create", "start", "inspect", "stop", "kill", "rm"):
        assert f'"{command}"' in source or f"'{command}'" in source
    assert "--read-only" in source
    assert "--tmpfs" in source
    assert "statement_timeout" in source
    assert "cleanup_verified" in source
    for field in ("container_id", "container_name", "events"):
        assert field in source
    assert "chmod(0o600)" in source or "0o600" in source
    assert "TimeoutExpired" in source
    assert "128 +" in source or "128+" in source


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
def test_signal_stops_container_before_revoke(tmp_path, number):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        private = tmp_path / "private"; private.mkdir()
        for name, value in (("control-migrator-database-url", database.migrator_dsn),
                            ("preview-control-migrator-database-url", preview_dsn)):
            path = private / name; path.write_text(value); path.chmod(0o600)
        fake = tmp_path / "docker"; _write_fake_docker(fake)
        calls = tmp_path / "calls"; state = tmp_path / "state"
        receipts = tmp_path / "receipts"
        process = subprocess.Popen(
            [str(HELPER), str(ROOT), str(private), "sha256:" + "a" * 64,
             "test-postgres", "--migration-timeout", "20", "--command-timeout",
             "2", "--receipt-dir", str(receipts)],
            env={**os.environ, "HR_MIGRATION_DOCKER": str(fake),
                 "TEST_ADMIN_DSN": database.admin_dsn, "TEST_PRIVATE": str(private),
                 "TEST_RELEASE": str(ROOT), "TEST_DOCKER_STATE": str(state),
                 "TEST_DOCKER_CALLS": str(calls), "TEST_DOCKER_SCENARIO": "ignore-term",
                 "PYTHONPATH": str(ROOT / "backend")},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (
            not calls.exists() or "start " not in calls.read_text()
        ):
            time.sleep(0.02)
        assert calls.exists() and "start " in calls.read_text()
        process.send_signal(number)
        _, stderr = process.communicate(timeout=15)
        assert process.returncode == 128 + number
        assert _memberships(database) == 0
        operations = calls.read_text().splitlines()
        assert "stop" in [item.split()[0] for item in operations]
        receipt = __import__("json").loads(next(receipts.glob("*.json")).read_text())
        assert receipt["status"] == "interrupted"
        assert receipt["cleanup_verified"] is True
        assert "CLEANUP_UNRESOLVED" not in stderr


@pytest.mark.parametrize(
    ("scenario", "verified"),
    [("timeout", True), ("timeout,inspect-failure", False)],
)
def test_timeout_requires_confirmed_container_stop(tmp_path, scenario, verified):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        private = tmp_path / "private"; private.mkdir()
        for name, value in (("control-migrator-database-url", database.migrator_dsn),
                            ("preview-control-migrator-database-url", preview_dsn)):
            path = private / name; path.write_text(value); path.chmod(0o600)
        fake = tmp_path / "docker"; _write_fake_docker(fake)
        calls = tmp_path / "calls"; receipts = tmp_path / "receipts"
        result = subprocess.run(
            [str(HELPER), str(ROOT), str(private), "sha256:" + "a" * 64,
             "test-postgres", "--migration-timeout", "0.1", "--command-timeout",
             "1", "--receipt-dir", str(receipts)],
            env={**os.environ, "HR_MIGRATION_DOCKER": str(fake),
                 "TEST_ADMIN_DSN": database.admin_dsn, "TEST_PRIVATE": str(private),
                 "TEST_RELEASE": str(ROOT), "TEST_DOCKER_STATE": str(tmp_path / "state"),
                 "TEST_DOCKER_CALLS": str(calls), "TEST_DOCKER_SCENARIO": scenario,
                 "PYTHONPATH": str(ROOT / "backend")},
            capture_output=True, text=True, check=False, timeout=15,
        )
        assert result.returncode != 0
        assert _memberships(database) == 0
        receipt = __import__("json").loads(next(receipts.glob("*.json")).read_text())
        assert receipt["status"] == "failed"
        assert receipt["cleanup_verified"] is verified
        assert ("CLEANUP_UNRESOLVED" in result.stderr) is (not verified)


def test_second_supervisor_refuses_while_first_holds_owner(tmp_path):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        private = tmp_path / "private"; private.mkdir()
        for name, value in (("control-migrator-database-url", database.migrator_dsn),
                            ("preview-control-migrator-database-url", preview_dsn)):
            path = private / name; path.write_text(value); path.chmod(0o600)
        fake = tmp_path / "docker"; _write_fake_docker(fake)
        calls = tmp_path / "first-calls"
        common = {**os.environ, "HR_MIGRATION_DOCKER": str(fake),
                  "TEST_ADMIN_DSN": database.admin_dsn, "TEST_PRIVATE": str(private),
                  "TEST_RELEASE": str(ROOT), "PYTHONPATH": str(ROOT / "backend")}
        command = [str(HELPER), str(ROOT), str(private), "sha256:" + "a" * 64,
                   "test-postgres", "--migration-timeout", "20", "--command-timeout", "2"]
        first = subprocess.Popen(
            [*command, "--receipt-dir", str(tmp_path / "first-receipts")],
            env={**common, "TEST_DOCKER_STATE": str(tmp_path / "first-state"),
                 "TEST_DOCKER_CALLS": str(calls), "TEST_DOCKER_SCENARIO": "ignore-term"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (
            not calls.exists() or "start " not in calls.read_text()
        ):
            time.sleep(0.02)
        assert calls.exists() and "start " in calls.read_text()
        second_calls = tmp_path / "second-calls"
        second = subprocess.run(
            [*command, "--receipt-dir", str(tmp_path / "second-receipts")],
            env={**common, "TEST_DOCKER_STATE": str(tmp_path / "second-state"),
                 "TEST_DOCKER_CALLS": str(second_calls)},
            capture_output=True, text=True, check=False, timeout=10,
        )
        assert second.returncode != 0
        assert not second_calls.exists()
        assert _memberships(database) == 1
        first.send_signal(signal.SIGTERM)
        first.communicate(timeout=15)
        assert _memberships(database) == 0


def test_preexisting_migrator_session_blocks_before_grant(tmp_path):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        private = tmp_path / "private"; private.mkdir()
        for name, value in (("control-migrator-database-url", database.migrator_dsn),
                            ("preview-control-migrator-database-url", preview_dsn)):
            path = private / name; path.write_text(value); path.chmod(0o600)
        fake = tmp_path / "docker"; _write_fake_docker(fake)
        with psycopg.connect(database.migrator_dsn):
            result = subprocess.run(
                [str(HELPER), str(ROOT), str(private), "sha256:" + "a" * 64,
                 "test-postgres", "--receipt-dir", str(tmp_path / "receipts")],
                env={**os.environ, "HR_MIGRATION_DOCKER": str(fake),
                     "TEST_ADMIN_DSN": database.admin_dsn, "TEST_PRIVATE": str(private),
                     "TEST_RELEASE": str(ROOT), "TEST_DOCKER_STATE": str(tmp_path / "state"),
                     "TEST_DOCKER_CALLS": str(tmp_path / "calls"),
                     "PYTHONPATH": str(ROOT / "backend")},
                capture_output=True, text=True, check=False, timeout=10,
            )
        assert result.returncode != 0
        assert _memberships(database) == 0
        assert not (tmp_path / "calls").exists()


def test_signal_during_production_revoke_never_starts_preview(tmp_path):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        private = tmp_path / "private"; private.mkdir()
        for name, value in (("control-migrator-database-url", database.migrator_dsn),
                            ("preview-control-migrator-database-url", preview_dsn)):
            path = private / name; path.write_text(value); path.chmod(0o600)
        fake = tmp_path / "docker"; _write_fake_docker(fake)
        marker = tmp_path / "revoke-pending"
        source = fake.read_text()
        needle = "    values=conninfo_to_dict(os.environ['TEST_ADMIN_DSN']); values['dbname']=database"
        delay = """    marker=pathlib.Path(os.environ['TEST_REVOKE_MARKER'])
    if 'revoke platform_control_owner from platform_control_migrator' in query and not marker.exists():
        marker.touch(); time.sleep(0.7)
"""
        fake.write_text(source.replace(needle, delay + needle, 1)); fake.chmod(0o700)
        calls = tmp_path / "calls"; receipts = tmp_path / "receipts"
        process = subprocess.Popen(
            [str(HELPER), str(ROOT), str(private), "sha256:" + "a" * 64,
             "test-postgres", "--receipt-dir", str(receipts), "--command-timeout",
             "3", "--migration-timeout", "20"],
            env={**os.environ, "HR_MIGRATION_DOCKER": str(fake),
                 "TEST_ADMIN_DSN": database.admin_dsn, "TEST_PRIVATE": str(private),
                 "TEST_RELEASE": str(ROOT), "TEST_DOCKER_STATE": str(tmp_path / "state"),
                 "TEST_DOCKER_CALLS": str(calls), "TEST_REVOKE_MARKER": str(marker),
                 "PYTHONPATH": str(ROOT / "backend")},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 12
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=15)
        assert process.returncode == 128 + signal.SIGTERM
        assert sum(line.startswith("create ") for line in calls.read_text().splitlines()) == 1
        assert _memberships(database) == 0


def test_dirty_at_rest_receipt_is_never_cleanup_verified(tmp_path):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        with database.admin_connection() as connection:
            connection.execute("grant platform_control_owner to platform_control_migrator")
        private = tmp_path / "private"; private.mkdir()
        for name, value in (("control-migrator-database-url", database.migrator_dsn),
                            ("preview-control-migrator-database-url", preview_dsn)):
            path = private / name; path.write_text(value); path.chmod(0o600)
        fake = tmp_path / "docker"; _write_fake_docker(fake)
        receipts = tmp_path / "receipts"
        result = subprocess.run(
            [str(HELPER), str(ROOT), str(private), "sha256:" + "a" * 64,
             "test-postgres", "--receipt-dir", str(receipts)],
            env={**os.environ, "HR_MIGRATION_DOCKER": str(fake),
                 "TEST_ADMIN_DSN": database.admin_dsn, "TEST_PRIVATE": str(private),
                 "TEST_RELEASE": str(ROOT), "TEST_DOCKER_STATE": str(tmp_path / "state"),
                 "TEST_DOCKER_CALLS": str(tmp_path / "calls"),
                 "PYTHONPATH": str(ROOT / "backend")},
            capture_output=True, text=True, check=False, timeout=10,
        )
        receipt = __import__("json").loads(next(receipts.glob("*.json")).read_text())
        assert result.returncode != 0
        assert receipt["failure_code"] == "at_rest_not_clean"
        assert receipt["cleanup_verified"] is False
