from __future__ import annotations

import base64
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import psycopg
import pytest
import yaml

from test_control_plane_migration import control_database


ROOT = Path(__file__).parents[2]
LOCAL = ROOT / "deploy" / "local-execution-worker"
CLOUD = ROOT / "deploy" / "cloud"
CLOUD_ACCEPTANCE = CLOUD / "accept-dingtalk-production.sh"
CLOUD_ROTATOR = CLOUD / "execution-worker-key-rotation.py"
CLOUD_KEYRING_INSTALLER = CLOUD / "install-execution-worker-keyring.py"
CLOUD_REMOTE_STAGE = CLOUD / "remote-stage.sh"
CLOUD_DEPLOY_INPUT_LOCK = CLOUD / "deploy-input-lock.py"
GENERATOR = LOCAL / "generate-worker-key.py"
ROTATOR = LOCAL / "rotate-worker-key.py"
BOOTSTRAP = LOCAL / "bootstrap-worker-database.sh"
PLIST = LOCAL / "execution-worker-key-binding.plist.template"
INSTALLER = LOCAL / "install.sh"
WORKER_PM2 = LOCAL / "worker-pm2.sh"
WORKER_PM2_CONFIG = LOCAL / "execution-worker.ecosystem.config.cjs"
REMOVER = LOCAL / "remove.sh"
AGENTS = [
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
    "agent-brain-bot",
]


def _metabot_runtime_contract() -> str:
    ports = {
        "hr-bot": 9101,
        "fae-bot": 9105,
        "marketing-prospecting-bot": 9102,
        "marketing-inbound-bot": 9103,
        "marketing-voice-bot": 9104,
        "marketing-intelligence-bot": 9108,
        "marketing-gtm-bot": 9107,
        "agent-brain-bot": 9110,
    }
    bots = [
        {"name": name, "instance": {"apiPort": ports[name]}}
        for name in AGENTS
        if name != "agent-brain-bot"
    ]
    bots.append(
        {
            "name": "agent-brain-bot",
            "platform": "web",
            "platformOnly": True,
            "engine": "claude",
            "model": "claude-opus-5",
            "backend": "pty",
            "toolPolicy": "none",
            "workdir": "/Users/agentops/Developer/work/Orbbec-Agent-Team/bots/agent-brain",
            "instance": {
                "pm2Name": "metabot-agent-brain",
                "apiPort": 9110,
                "stateDir": "/Users/agentops/AgentRuntime/instances/agent-brain-bot/state",
                "configPath": "/Users/agentops/AgentRuntime/instances/agent-brain-bot/bots.json",
                "logDir": "/Users/agentops/AgentRuntime/instances/agent-brain-bot/logs",
            },
        }
    )
    return json.dumps({"schemaVersion": 2, "bots": bots}) + "\n"


def test_local_worker_command_assets_are_executable() -> None:
    for path in (GENERATOR, ROTATOR, BOOTSTRAP, INSTALLER, WORKER_PM2, REMOVER):
        assert os.access(path, os.X_OK)


def test_cloud_worker_key_mutation_helpers_are_executable_python() -> None:
    for path in (CLOUD_ROTATOR, CLOUD_KEYRING_INSTALLER, CLOUD_DEPLOY_INPUT_LOCK):
        assert os.access(path, os.X_OK)
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_cloud_stage_backfills_conversations_only_while_consumers_are_stopped() -> None:
    script = CLOUD_REMOTE_STAGE.read_text(encoding="utf-8")
    stopped = script.index(
        '"${previous_compose[@]}" stop "${previous_control_consumers[@]}"'
    )
    bootstrap = script.index("CONTROL_DATABASE_CREDENTIALS_READY version=2")
    backfill = script.index("app.agent_brain.conversation_backfill")
    exact_gate = script.index("AGENT_BRAIN_CONVERSATION_BACKFILL_OK\\ scanned=")
    service_start = script.index('"${compose[@]}" up -d --force-recreate "${active_control_secret_consumers[@]}"')

    assert stopped < bootstrap < backfill < exact_gate < service_start
    assert "quarantined=0" in script[backfill:service_start]
    assert "PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE" in script[bootstrap:service_start]
    assert "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE" in script[bootstrap:service_start]


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_removal_wrapper_has_exact_confirmation_private_inputs_and_no_recursive_scope() -> None:
    script = REMOVER.read_text(encoding="utf-8")
    lowered = script.lower()

    assert script.startswith("#!/bin/bash\nset -euo pipefail\numask 077\n")
    assert '[[ $# -eq 3' in script
    assert '"$3" == "--confirm-remove-agent-execution-worker"' in script
    assert '"$1" == /*' in script and '"$2" == /*' in script
    assert '"$(/usr/bin/id -un)" == "agentops"' in script
    assert "O_NOFOLLOW" in script and "dir_fd=" in script and "os.fstat" in script
    assert "0o700" in script and "0o600" in script
    assert "agent_execution_worker.dump" in script
    for rotation_asset in (
        "execution-worker-ed25519.next.key",
        "execution-worker-public.next.json",
        "execution-worker-key-binding.next.plist",
        "execution-worker-ed25519.previous.key",
        "execution-worker-public.previous.json",
        "execution-worker-key-binding.previous.plist",
        "execution-worker-key-rotation-state.json",
        "execution-worker-key-rotation.lock",
    ):
        assert rotation_asset in script
    assert "pg_shdepend" in lowered and "pg_auth_members" in lowered
    assert "cross-database dependency" in lowered
    for attribute in (
        "rolcanlogin",
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
        "rolinherit",
        "rolconnlimit",
        "rolvaliduntil",
        "rolconfig",
    ):
        assert attribute in script
    assert "membership.roleid=owner_role and membership.member=migrator_role" in script
    assert "or not exists (" in script
    assert "except\n    select 1 from pg_auth_members" not in lowered
    for exact in (
        "drop database agent_execution_worker",
        "drop role agent_execution_worker_runtime",
        "drop role agent_execution_worker_migrator",
        "drop role agent_execution_worker_owner",
    ):
        assert lowered.count(exact) == 1
    for forbidden in (
        "rm -r",
        "rm --recursive",
        "brew services",
        "pg_ctl",
        "launchctl unload postgresql",
        "drop database flywheel",
        "drop database postgres",
        "drop database template",
        "runtime_root/platform",
        "runtime_root/metabot",
        'rm -f -- "$runtime_root"',
        'rm -f -- "$private_root"',
    ):
        assert forbidden not in lowered
    subprocess.run(["/bin/bash", "-n", str(REMOVER)], check=True)


def test_removal_preflight_precedes_every_local_mutation() -> None:
    script = REMOVER.read_text(encoding="utf-8")
    preflight = script.index("EXECUTION_WORKER_REMOVAL_PREFLIGHT_OK")
    for mutation in (
        '"$worker_supervisor" restore absent',
        '"$worker_supervisor" save',
        'drop database agent_execution_worker',
        '/bin/rm -f -- "$plist"',
        '/bin/rm -f -- "$private_key"',
        '/bin/rm -f -- "$public_document"',
        '/bin/rm -f -- "$runtime_dsn"',
        '/bin/rm -f -- "$stdout_log"',
        '/bin/rm -f -- "$stderr_log"',
        '/bin/rm -f -- "$next_private_key"',
        '/bin/rm -f -- "$next_public_document"',
        '/bin/rm -f -- "$next_plist"',
        '/bin/rm -f -- "$previous_private_key"',
        '/bin/rm -f -- "$previous_public_document"',
        '/bin/rm -f -- "$previous_plist"',
        '/bin/rm -f -- "$rotation_state"',
    ):
        assert script.index(mutation) > preflight
    assert '/bin/rm -f -- "$rotation_lock"' not in script


def test_removal_validates_and_cleans_every_fixed_rotation_part() -> None:
    script = REMOVER.read_text(encoding="utf-8")
    fixed_parts = (
        ".execution-worker-ed25519.key.part",
        ".execution-worker-public.json.part",
        ".execution-worker-key-binding.plist.part",
        ".execution-worker-ed25519.next.key.part",
        ".execution-worker-public.next.json.part",
        ".execution-worker-key-binding.next.plist.part",
        ".execution-worker-ed25519.previous.key.part",
        ".execution-worker-public.previous.json.part",
        ".execution-worker-key-binding.previous.plist.part",
        ".execution-worker-key-rotation-state.json.part",
    )
    for fixed_part in fixed_parts:
        assert script.count(fixed_part) == 1
    assert '"${rotation_parts[@]}" <<\'PY\'' in script
    assert 'for part in "${rotation_parts[@]}"; do' in script
    assert 'metadata.st_size > 1_048_576' in script


WORKER_ROLES = (
    "agent_execution_worker_owner",
    "agent_execution_worker_migrator",
    "agent_execution_worker_runtime",
)


def _bootstrap_test_environment(control_database, tmp_path: Path):
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    platform = tmp_path / "platform"
    local = platform / "deploy/local-execution-worker"
    schema_target = platform / "backend/app/execution_relay/worker_schema.sql"
    local.mkdir(parents=True)
    schema_target.parent.mkdir(parents=True)
    schema_target.write_bytes(
        (ROOT / "backend/app/execution_relay/worker_schema.sql").read_bytes()
    )
    copied = local / BOOTSTRAP.name
    copied.write_text(
        BOOTSTRAP.read_text(encoding="utf-8").replace("agentops", current_user),
        encoding="utf-8",
    )
    copied.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    owner_dsn = private / "owner-dsn"
    owner_dsn.write_text(
        f"postgresql://control_test_admin:secret@127.0.0.1:{control_database['port']}/postgres\n"
    )
    owner_dsn.chmod(0o600)
    runtime_dsn = private / "runtime-dsn"
    environment = {
        **os.environ,
        "PLATFORM_LOCAL_POSTGRES17_PSQL": subprocess.check_output(
            ["/bin/sh", "-c", "command -v psql"], text=True
        ).strip(),
        "PLATFORM_LOCAL_PYTHON3": sys.executable,
    }
    return copied, owner_dsn, runtime_dsn, environment


def _run_bootstrap(paths, *, check: bool = False):
    copied, owner_dsn, runtime_dsn, environment = paths
    return subprocess.run(
        ["/bin/bash", str(copied), str(owner_dsn), str(runtime_dsn)],
        text=True,
        capture_output=True,
        env=environment,
        check=check,
    )


def _drop_worker_test_state(admin_url: str, *extra_databases: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute("drop server if exists execution_worker_collision_server cascade")
        connection.execute("drop foreign data wrapper if exists execution_worker_collision_fdw cascade")
        for database in (*extra_databases, "agent_execution_worker"):
            connection.execute(
                psycopg.sql.SQL("drop database if exists {}").format(
                    psycopg.sql.Identifier(database)
                )
            )
        for role in (
            *WORKER_ROLES,
            "execution_worker_intruder",
            "execution_worker_second_admin",
        ):
            connection.execute(
                psycopg.sql.SQL("drop role if exists {}").format(
                    psycopg.sql.Identifier(role)
                )
            )


def _removal_test_environment(control_database, tmp_path: Path):
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    runtime = tmp_path / "AgentRuntime"
    runtime.mkdir(mode=0o700)
    bootstrap_paths = _bootstrap_test_environment(control_database, runtime)
    bootstrap = _run_bootstrap(bootstrap_paths)
    assert bootstrap.returncode == 0, bootstrap.stderr

    private = runtime / "private"
    log = runtime / "log"
    launch_agents = tmp_path / "Library/LaunchAgents"
    log.mkdir(mode=0o700)
    launch_agents.mkdir(parents=True, mode=0o700)
    owner_dsn = bootstrap_paths[1]
    runtime_dsn = private / "execution-worker-postgres-dsn"
    bootstrap_paths[2].replace(runtime_dsn)
    backup = private / "agent_execution_worker.dump"
    pg_dump = subprocess.check_output(
        ["/bin/sh", "-c", "command -v pg_dump"], text=True
    ).strip()
    subprocess.run(
        [
            pg_dump,
            "--format=custom",
            "--file",
            str(backup),
            f"postgresql://control_test_admin:secret@127.0.0.1:"
            f"{control_database['port']}/agent_execution_worker",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    backup.chmod(0o600)

    assets = {
        "plist": private / "execution-worker-key-binding.plist",
        "private_key": private / "execution-worker-ed25519.key",
        "public_document": runtime / "execution-worker-public.json",
        "runtime_dsn": runtime_dsn,
        "stdout_log": log / "execution-worker.out.log",
        "stderr_log": log / "execution-worker.err.log",
        "next_private_key": private / "execution-worker-ed25519.next.key",
        "next_public_document": runtime / "execution-worker-public.next.json",
        "next_plist": private / "execution-worker-key-binding.next.plist",
        "previous_private_key": private / "execution-worker-ed25519.previous.key",
        "previous_public_document": runtime / "execution-worker-public.previous.json",
        "previous_plist": private / "execution-worker-key-binding.previous.plist",
        "rotation_state": private / "execution-worker-key-rotation-state.json",
        "rotation_lock": private / "execution-worker-key-rotation.lock",
    }
    for name, path in assets.items():
        if name != "runtime_dsn":
            path.write_text(
                "rotation-lock\n" if name == "rotation_lock" else f"{name}\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
    acceptance = private / "execution-relay-acceptance"
    acceptance.mkdir(mode=0o700)
    for name in ("control.json", "state.json", "completion-paused", "dispatching-paused"):
        residual = acceptance / name
        residual.write_text(f"{name}\n", encoding="utf-8")
        residual.chmod(0o600)
    shared_sentinel = private / "metabot-api-token"
    shared_sentinel.write_text("must-survive\n", encoding="utf-8")
    shared_sentinel.chmod(0o600)

    bootout_marker = tmp_path / "worker-booted-out"
    fake_supervisor = runtime / "platform/deploy/local-execution-worker/worker-pm2.sh"
    fake_supervisor.parent.mkdir(parents=True, exist_ok=True)
    fake_supervisor.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"if [[ \"$1\" == restore && \"$2\" == absent ]]; then /usr/bin/touch {bootout_marker}; exit 0; fi\n"
        "if [[ \"$1\" == save ]]; then exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_supervisor.chmod(0o700)

    remover = runtime / "platform/deploy/local-execution-worker/remove.sh"
    source = REMOVER.read_text(encoding="utf-8")
    source = source.replace("/Users/agentops/AgentRuntime", str(runtime))
    source = source.replace(
        "/Users/agentops/Library/LaunchAgents", str(launch_agents)
    )
    source = source.replace("agentops", current_user)
    remover.write_text(source, encoding="utf-8")
    remover.chmod(0o700)
    environment = {
        **bootstrap_paths[3],
        "PLATFORM_LOCAL_POSTGRES17_PG_RESTORE": subprocess.check_output(
            ["/bin/sh", "-c", "command -v pg_restore"], text=True
        ).strip(),
    }
    return {
        "runtime": runtime,
        "private": private,
        "owner_dsn": owner_dsn,
        "backup": backup,
        "assets": assets,
        "acceptance": acceptance,
        "shared_sentinel": shared_sentinel,
        "bootout_marker": bootout_marker,
        "remover": remover,
        "environment": environment,
    }


def _run_remover(paths):
    return subprocess.run(
        [
            "/bin/bash",
            str(paths["remover"]),
            str(paths["owner_dsn"]),
            str(paths["backup"]),
            "--confirm-remove-agent-execution-worker",
        ],
        text=True,
        capture_output=True,
        env=paths["environment"],
    )


@pytest.mark.postgres
def test_removal_wrapper_drops_only_dedicated_database_roles_and_files(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    sentinel_database = "flywheel_removal_sentinel"
    _drop_worker_test_state(admin_url, sentinel_database)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("create database {}").format(
                    psycopg.sql.Identifier(sentinel_database)
                )
            )
        sentinel_url = (
            f"postgresql://control_test_admin@127.0.0.1:"
            f"{control_database['port']}/{sentinel_database}"
        )
        with psycopg.connect(sentinel_url, autocommit=True) as connection:
            connection.execute("create table removal_sentinel(value integer)")
            connection.execute("insert into removal_sentinel values (42)")

        paths = _removal_test_environment(control_database, tmp_path)
        result = _run_remover(paths)

        assert result.returncode == 0, result.stderr
        assert result.stdout == "EXECUTION_WORKER_REMOVED\n"
        assert paths["bootout_marker"].is_file()
        assert all(
            not path.exists()
            for name, path in paths["assets"].items()
            if name != "rotation_lock"
        )
        assert paths["assets"]["rotation_lock"].read_bytes() == b"rotation-lock\n"
        assert _mode(paths["assets"]["rotation_lock"]) == 0o600
        assert not paths["acceptance"].exists()
        assert paths["shared_sentinel"].read_text(encoding="utf-8") == "must-survive\n"
        assert paths["runtime"].is_dir() and paths["private"].is_dir()
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select datname from pg_database "
                "where datname in ('agent_execution_worker','flywheel_removal_sentinel') "
                "order by datname"
            ).fetchall() == [(sentinel_database,)]
            assert connection.execute(
                "select count(*) from pg_roles where rolname=any(%s)",
                (list(WORKER_ROLES),),
            ).fetchone() == (0,)
            assert connection.execute("select current_database()").fetchone() == (
                "postgres",
            )
        with psycopg.connect(sentinel_url) as connection:
            assert connection.execute(
                "select value from removal_sentinel"
            ).fetchone() == (42,)
    finally:
        _drop_worker_test_state(admin_url, sentinel_database)


@pytest.mark.postgres
def test_removal_cross_database_dependency_fails_before_any_mutation(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    sentinel_database = "flywheel_removal_dependency"
    _drop_worker_test_state(admin_url, sentinel_database)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("create database {}").format(
                    psycopg.sql.Identifier(sentinel_database)
                )
            )
        paths = _removal_test_environment(control_database, tmp_path)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("grant connect on database {} to {}").format(
                    psycopg.sql.Identifier(sentinel_database),
                    psycopg.sql.Identifier("agent_execution_worker_runtime"),
                )
            )
        before = {name: path.read_bytes() for name, path in paths["assets"].items()}
        residuals = {
            path.name: path.read_bytes() for path in paths["acceptance"].iterdir()
        }

        result = _run_remover(paths)

        assert result.returncode != 0
        assert "execution worker cross-database dependency" in result.stderr
        assert result.stderr.endswith("EXECUTION_WORKER_REMOVAL_FAILED\n")
        assert not paths["bootout_marker"].exists()
        assert {name: path.read_bytes() for name, path in paths["assets"].items()} == before
        assert {
            path.name: path.read_bytes() for path in paths["acceptance"].iterdir()
        } == residuals
        assert paths["shared_sentinel"].read_text(encoding="utf-8") == "must-survive\n"
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select count(*) from pg_database where datname='agent_execution_worker'"
            ).fetchone() == (1,)
            assert connection.execute(
                "select count(*) from pg_roles where rolname=any(%s)",
                (list(WORKER_ROLES),),
            ).fetchone() == (3,)
    finally:
        _drop_worker_test_state(admin_url, sentinel_database)


@pytest.mark.postgres
@pytest.mark.parametrize("collision", ["external-connect", "external-owner"])
def test_removal_target_database_identity_or_acl_fails_before_any_mutation(
    control_database, tmp_path: Path, collision: str
) -> None:
    admin_url = control_database["cluster_admin"]
    sentinel_role = "execution_worker_removal_sentinel"
    _drop_worker_test_state(admin_url)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("drop role if exists {}").format(
                psycopg.sql.Identifier(sentinel_role)
            )
        )
    try:
        paths = _removal_test_environment(control_database, tmp_path)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("create role {} nologin").format(
                    psycopg.sql.Identifier(sentinel_role)
                )
            )
            statement = (
                "grant connect on database agent_execution_worker to {}"
                if collision == "external-connect"
                else "alter database agent_execution_worker owner to {}"
            )
            connection.execute(
                psycopg.sql.SQL(statement).format(
                    psycopg.sql.Identifier(sentinel_role)
                )
            )
        before = {name: path.read_bytes() for name, path in paths["assets"].items()}
        residuals = {
            path.name: path.read_bytes() for path in paths["acceptance"].iterdir()
        }

        result = _run_remover(paths)

        assert result.returncode != 0
        assert "execution worker target database identity or acl mismatch" in result.stderr
        assert result.stderr.endswith("EXECUTION_WORKER_REMOVAL_FAILED\n")
        assert not paths["bootout_marker"].exists()
        assert {name: path.read_bytes() for name, path in paths["assets"].items()} == before
        assert {
            path.name: path.read_bytes() for path in paths["acceptance"].iterdir()
        } == residuals
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select count(*) from pg_database where datname='agent_execution_worker'"
            ).fetchone() == (1,)
            assert connection.execute(
                "select count(*) from pg_roles where rolname=any(%s)",
                (list(WORKER_ROLES),),
            ).fetchone() == (3,)
    finally:
        _drop_worker_test_state(admin_url)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("drop role if exists {}").format(
                    psycopg.sql.Identifier(sentinel_role)
                )
            )


@pytest.mark.postgres
def test_removal_rejects_custom_dump_from_another_database_before_any_mutation(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    wrong_database = "execution_worker_wrong_backup"
    _drop_worker_test_state(admin_url, wrong_database)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("create database {}").format(
                    psycopg.sql.Identifier(wrong_database)
                )
            )
        wrong_url = (
            f"postgresql://control_test_admin@127.0.0.1:"
            f"{control_database['port']}/{wrong_database}"
        )
        with psycopg.connect(wrong_url, autocommit=True) as connection:
            connection.execute("create table unrelated_backup(value integer)")
            connection.execute("insert into unrelated_backup values (7)")
        paths = _removal_test_environment(control_database, tmp_path)
        pg_dump = subprocess.check_output(
            ["/bin/sh", "-c", "command -v pg_dump"], text=True
        ).strip()
        subprocess.run(
            [
                pg_dump,
                "--format=custom",
                "--file",
                str(paths["backup"]),
                wrong_url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        paths["backup"].chmod(0o600)
        before = {name: path.read_bytes() for name, path in paths["assets"].items()}

        result = _run_remover(paths)

        assert result.returncode != 0
        assert result.stderr.endswith("EXECUTION_WORKER_REMOVAL_FAILED\n")
        assert not paths["bootout_marker"].exists()
        assert {name: path.read_bytes() for name, path in paths["assets"].items()} == before
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select count(*) from pg_database where datname='agent_execution_worker'"
            ).fetchone() == (1,)
            assert connection.execute(
                "select count(*) from pg_roles where rolname=any(%s)",
                (list(WORKER_ROLES),),
            ).fetchone() == (3,)
    finally:
        _drop_worker_test_state(admin_url, wrong_database)


def test_key_generator_is_idempotent_private_and_exact(tmp_path: Path) -> None:
    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700)
    private = private_dir / "worker.key"
    public = tmp_path / "worker-public.json"
    command = [sys.executable, str(GENERATOR), str(private), str(public)]

    first = subprocess.run(command, text=True, capture_output=True, check=True)
    original = private.read_bytes()
    second = subprocess.run(command, text=True, capture_output=True, check=True)

    assert len(original) == 32
    assert _mode(private) == 0o600
    assert private.read_bytes() == original
    assert first.stdout == second.stdout
    assert re.fullmatch(r"WORKER_KEY_FINGERPRINT=[0-9a-f]{64}\n", first.stdout)
    assert first.stderr == second.stderr == ""
    assert base64.urlsafe_b64encode(original).decode().rstrip("=") not in first.stdout
    document = json.loads(public.read_text(encoding="utf-8"))
    assert document == {
        "worker_id": "agentops-mac-primary",
        "key_id": "worker-v1",
        "public_key_base64url": document["public_key_base64url"],
        "allowed_agent_ids": AGENTS,
    }
    assert len(base64.urlsafe_b64decode(document["public_key_base64url"] + "=")) == 32
    derived = Ed25519PrivateKey.from_private_bytes(original).public_key()
    assert derived.public_bytes_raw() == base64.urlsafe_b64decode(
        document["public_key_base64url"] + "="
    )


@pytest.mark.parametrize(
    "key_id",
    ["worker-v0", "worker-v01", "worker-v", "worker-v2-extra", "Worker-v2"],
)
def test_key_generator_supports_only_strict_positive_version_targets(
    tmp_path: Path, key_id: str
) -> None:
    private_dir = tmp_path / "private"
    public_dir = tmp_path / "public"
    private_dir.mkdir(mode=0o700)
    public_dir.mkdir(mode=0o700)
    private = private_dir / "worker.key"
    public = public_dir / "worker.json"

    invalid = subprocess.run(
        [sys.executable, str(GENERATOR), str(private), str(public), key_id],
        text=True,
        capture_output=True,
    )

    assert invalid.returncode == 1
    assert invalid.stderr == "WORKER_KEY_GENERATION_FAILED\n"
    assert not private.exists() and not public.exists()


def test_key_generator_writes_requested_worker_v2_identity(tmp_path: Path) -> None:
    private_dir = tmp_path / "private"
    public_dir = tmp_path / "public"
    private_dir.mkdir(mode=0o700)
    public_dir.mkdir(mode=0o700)
    private = private_dir / "worker.key"
    public = public_dir / "worker.json"

    result = subprocess.run(
        [sys.executable, str(GENERATOR), str(private), str(public), "worker-v2"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(public.read_text(encoding="utf-8"))["key_id"] == "worker-v2"
    assert _mode(private) == _mode(public) == 0o600


def test_key_generator_without_target_preserves_existing_worker_v2_identity(
    tmp_path: Path,
) -> None:
    private_dir = tmp_path / "private"
    public_dir = tmp_path / "public"
    private_dir.mkdir(mode=0o700)
    public_dir.mkdir(mode=0o700)
    private = private_dir / "worker.key"
    public = public_dir / "worker.json"
    created = subprocess.run(
        [sys.executable, str(GENERATOR), str(private), str(public), "worker-v2"],
        text=True,
        capture_output=True,
    )
    assert created.returncode == 0, created.stderr
    original_private = private.read_bytes()

    reinstalled = subprocess.run(
        [sys.executable, str(GENERATOR), str(private), str(public)],
        text=True,
        capture_output=True,
    )

    assert reinstalled.returncode == 0, reinstalled.stderr
    assert private.read_bytes() == original_private
    assert json.loads(public.read_text(encoding="utf-8"))["key_id"] == "worker-v2"


def test_rotator_cleans_fixed_private_part_after_real_generator_sigkill(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path)
    generator = paths["rotator"].with_name(GENERATOR.name)
    marker = tmp_path / "private-part-durable"
    source = generator.read_text(encoding="utf-8")
    source = source.replace(
        "                os.fsync(descriptor)\n",
        "                os.fsync(descriptor)\n"
        f"                Path({str(marker)!r}).touch()\n"
        "                import time\n"
        "                while True: time.sleep(0.01)\n",
        1,
    )
    generator.write_text(source, encoding="utf-8")
    generator.chmod(0o700)
    next_private = paths["managed"]["next_private"]
    next_public = paths["managed"]["next_public"]
    process = subprocess.Popen(
        [sys.executable, str(generator), str(next_private), str(next_public), "worker-v2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for_file(marker)
    process.kill()
    process.communicate(timeout=5)
    private_parts = tuple(next_private.parent.glob("*execution-worker-ed25519*.part"))
    assert private_parts

    aborted = _run_rotation(paths, "abort")

    assert aborted.returncode == 0, aborted.stderr
    assert not tuple(next_private.parent.glob("*execution-worker-ed25519*.part"))
    assert not next_private.exists() and not next_public.exists()


def test_key_generator_and_rotator_use_only_fixed_reserved_parts() -> None:
    generator = GENERATOR.read_text(encoding="utf-8")
    rotator = ROTATOR.read_text(encoding="utf-8")
    assert "secrets.token_hex" not in generator
    assert "secrets.token_hex" not in rotator
    assert 'f".{path.name}.part"' in generator
    assert 'f".{path.name}.part"' in rotator


def _rotation_test_environment(
    tmp_path: Path, *, loaded: bool = True, worker_state: str | None = None
):
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    runtime = tmp_path / "AgentRuntime"
    private = runtime / "private"
    local = runtime / "platform/deploy/local-execution-worker"
    launch_agents = tmp_path / "Library/LaunchAgents"
    for directory in (runtime, private, launch_agents):
        directory.mkdir(parents=True, mode=0o700)
        directory.chmod(0o700)
    local.mkdir(parents=True)
    generator = local / GENERATOR.name
    generator.write_bytes(GENERATOR.read_bytes())
    generator.chmod(0o700)
    canonical_private = private / "execution-worker-ed25519.key"
    canonical_public = runtime / "execution-worker-public.json"
    generated = subprocess.run(
        [sys.executable, str(generator), str(canonical_private), str(canonical_public)],
        text=True,
        capture_output=True,
    )
    assert generated.returncode == 0, generated.stderr
    canonical_plist = private / "execution-worker-key-binding.plist"
    canonical_plist.write_text(
        PLIST.read_text(encoding="utf-8").replace(
            "/Users/agentops/AgentRuntime", str(runtime)
        ),
        encoding="utf-8",
    )
    canonical_plist.chmod(0o600)

    launch_state = tmp_path / "launch-state"
    launch_state.write_text(
        worker_state if worker_state is not None else ("online" if loaded else "absent"),
        encoding="utf-8",
    )
    launch_fail = tmp_path / "launch-fail"
    launch_error = tmp_path / "launch-error"
    launch_log = tmp_path / "launch-log"
    fake_launchctl = local / "worker-pm2.sh"
    fake_launchctl.write_text(
        """#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_LAUNCH_LOG"
case "$1" in
  state)
    if [[ -e "$FAKE_LAUNCH_ERROR" ]]; then
      case "$(<"$FAKE_LAUNCH_ERROR")" in
        permission) printf 'Not privileged to inspect domain\n' >&2; exit 77 ;;
        transient) printf 'Input/output error\n' >&2; exit 74 ;;
      esac
    fi
    printf '%s\n' "$(<"$FAKE_LAUNCH_STATE")"
    ;;
  stop)
    [[ "$(<"$FAKE_LAUNCH_STATE")" == online ]]
    printf stopped > "$FAKE_LAUNCH_STATE"
    ;;
  restore)
    [[ "$2" == online || "$2" == stopped || "$2" == absent ]]
    if [[ -e "$FAKE_LAUNCH_FAIL" ]]; then
      /bin/rm -f -- "$FAKE_LAUNCH_FAIL"
      exit 71
    fi
    printf '%s' "$2" > "$FAKE_LAUNCH_STATE"
    ;;
  save) ;;
  *) exit 72 ;;
esac
""",
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o700)
    rotator = local / ROTATOR.name
    source = ROTATOR.read_text(encoding="utf-8")
    source = source.replace("/Users/agentops/AgentRuntime", str(runtime))
    source = source.replace(
        "/Users/agentops/Library/LaunchAgents", str(launch_agents)
    )
    source = source.replace(
        'REQUIRED_USER = "agentops"', f'REQUIRED_USER = "{current_user}"'
    )
    rotator.write_text(source, encoding="utf-8")
    rotator.chmod(0o700)
    environment = {
        **os.environ,
        "FAKE_LAUNCH_STATE": str(launch_state),
        "FAKE_LAUNCH_FAIL": str(launch_fail),
        "FAKE_LAUNCH_ERROR": str(launch_error),
        "FAKE_LAUNCH_LOG": str(launch_log),
    }
    managed = {
        "next_private": private / "execution-worker-ed25519.next.key",
        "next_public": runtime / "execution-worker-public.next.json",
        "next_plist": private / "execution-worker-key-binding.next.plist",
        "previous_private": private / "execution-worker-ed25519.previous.key",
        "previous_public": runtime / "execution-worker-public.previous.json",
        "previous_plist": private / "execution-worker-key-binding.previous.plist",
        "state": private / "execution-worker-key-rotation-state.json",
    }
    return {
        "rotator": rotator,
        "environment": environment,
        "launch_state": launch_state,
        "launch_fail": launch_fail,
        "launch_error": launch_error,
        "launch_log": launch_log,
        "rotation_lock": private / "execution-worker-key-rotation.lock",
        "canonical_private": canonical_private,
        "canonical_public": canonical_public,
        "canonical_plist": canonical_plist,
        "managed": managed,
    }


def _run_rotation(paths, action: str):
    return subprocess.run(
        [sys.executable, str(paths["rotator"]), action, "worker-v2"],
        text=True,
        capture_output=True,
        env=paths["environment"],
    )


def _assert_rotation_identity(paths, key_id: str) -> None:
    private = paths["canonical_private"].read_bytes()
    document = json.loads(paths["canonical_public"].read_text(encoding="utf-8"))
    value = plistlib.loads(paths["canonical_plist"].read_bytes())
    public = Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes_raw()
    assert document["key_id"] == key_id
    assert base64.urlsafe_b64encode(public).decode().rstrip("=") == document[
        "public_key_base64url"
    ]
    assert value["EnvironmentVariables"]["PLATFORM_WORKER_KEY_ID"] == key_id


def test_local_key_rotation_prepare_activate_finalize_is_consistent_and_atomic(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path)
    original = {
        name: paths[name].read_bytes()
        for name in ("canonical_private", "canonical_public", "canonical_plist")
    }

    prepared = _run_rotation(paths, "prepare")
    assert prepared.returncode == 0, prepared.stderr
    assert all(paths["managed"][name].is_file() for name in (
        "next_private", "next_public", "next_plist"
    ))
    activated = _run_rotation(paths, "activate")
    assert activated.returncode == 0, activated.stderr
    _assert_rotation_identity(paths, "worker-v2")
    assert paths["launch_state"].read_text(encoding="utf-8") == "online"
    assert "save" in paths["launch_log"].read_text(encoding="utf-8").splitlines()
    assert paths["managed"]["previous_private"].read_bytes() == original[
        "canonical_private"
    ]
    assert paths["managed"]["previous_public"].read_bytes() == original[
        "canonical_public"
    ]
    assert paths["managed"]["previous_plist"].read_bytes() == original[
        "canonical_plist"
    ]
    assert all(paths["managed"][name].is_file() for name in (
        "next_private", "next_public", "next_plist"
    ))
    finalized = _run_rotation(paths, "finalize")
    assert finalized.returncode == 0, finalized.stderr
    _assert_rotation_identity(paths, "worker-v2")
    assert not any(path.exists() for path in paths["managed"].values())


def test_local_key_rotation_activation_failure_restores_exact_previous_state(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path, loaded=True)
    original = {
        name: paths[name].read_bytes()
        for name in ("canonical_private", "canonical_public", "canonical_plist")
    }
    assert _run_rotation(paths, "prepare").returncode == 0
    paths["launch_fail"].write_text("bootstrap", encoding="utf-8")

    activated = _run_rotation(paths, "activate")

    assert activated.returncode == 1
    assert activated.stderr == "EXECUTION_WORKER_KEY_ROTATION_FAILED\n"
    assert {
        name: paths[name].read_bytes()
        for name in ("canonical_private", "canonical_public", "canonical_plist")
    } == original
    assert paths["launch_state"].read_text(encoding="utf-8") == "online"
    assert not any(paths["managed"][name].exists() for name in (
        "previous_private", "previous_public", "previous_plist", "state"
    ))
    assert not any(paths["managed"][name].exists() for name in (
        "next_private", "next_public", "next_plist"
    ))


@pytest.mark.parametrize(
    "worker_state", ["online", "stopped", "absent"], ids=["online", "stopped", "absent"]
)
def test_local_key_rotation_rollback_restores_exact_previous_worker_state(
    tmp_path: Path, worker_state: str
) -> None:
    paths = _rotation_test_environment(tmp_path, worker_state=worker_state)
    original = {
        name: paths[name].read_bytes()
        for name in ("canonical_private", "canonical_public", "canonical_plist")
    }
    assert _run_rotation(paths, "prepare").returncode == 0
    assert _run_rotation(paths, "activate").returncode == 0

    rolled_back = _run_rotation(paths, "rollback")

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert {
        name: paths[name].read_bytes()
        for name in ("canonical_private", "canonical_public", "canonical_plist")
    } == original
    _assert_rotation_identity(paths, "worker-v1")
    assert paths["launch_state"].read_text(encoding="utf-8") == worker_state
    assert paths["launch_log"].read_text(encoding="utf-8").splitlines()[-2:] == [
        "save",
        "state",
    ]
    assert not any(path.exists() for path in paths["managed"].values())


def test_local_key_rotation_rebuilds_stopped_worker_and_saves_new_definition(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path, worker_state="stopped")
    assert _run_rotation(paths, "prepare").returncode == 0

    activated = _run_rotation(paths, "activate")

    assert activated.returncode == 0, activated.stderr
    assert paths["launch_state"].read_text(encoding="utf-8") == "stopped"
    calls = paths["launch_log"].read_text(encoding="utf-8").splitlines()
    assert calls.count("restore stopped") == 1
    assert calls[-2:] == ["save", "state"]
    _assert_rotation_identity(paths, "worker-v2")


def test_local_key_rotation_abort_removes_only_prepared_next_assets(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path)
    original = {
        name: paths[name].read_bytes()
        for name in ("canonical_private", "canonical_public", "canonical_plist")
    }
    assert _run_rotation(paths, "prepare").returncode == 0

    aborted = _run_rotation(paths, "abort")

    assert aborted.returncode == 0, aborted.stderr
    assert {
        name: paths[name].read_bytes()
        for name in ("canonical_private", "canonical_public", "canonical_plist")
    } == original
    assert not any(paths["managed"][name].exists() for name in (
        "next_private", "next_public", "next_plist"
    ))


def test_local_key_rotation_rejects_concurrent_operation_without_changes(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path)
    lock_path = paths["rotation_lock"]
    lock_path.write_bytes(b"rotation-lock\n")
    lock_path.chmod(0o600)
    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        concurrent = _run_rotation(paths, "prepare")

        assert concurrent.returncode == 1
        assert concurrent.stderr == "EXECUTION_WORKER_KEY_ROTATION_FAILED\n"
        assert not any(paths["managed"][name].exists() for name in (
            "next_private", "next_public", "next_plist"
        ))
        _assert_rotation_identity(paths, "worker-v1")
    finally:
        os.close(descriptor)


def _remover_for_rotation_lock_test(
    paths, tmp_path: Path, *, block_psql: bool
) -> tuple[Path, Path, Path, dict[str, str], Path, Path]:
    runtime = paths["canonical_public"].parent
    launch_agents = paths["canonical_plist"].parent
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    remover = runtime / "platform/deploy/local-execution-worker/remove.sh"
    source = REMOVER.read_text(encoding="utf-8")
    source = source.replace("/Users/agentops/AgentRuntime", str(runtime))
    source = source.replace("/Users/agentops/Library/LaunchAgents", str(launch_agents))
    source = source.replace("agentops", current_user)
    remover.write_text(source, encoding="utf-8")
    remover.chmod(0o700)
    owner_dsn = runtime / "private/removal-owner-dsn"
    owner_dsn.write_text(
        "postgresql://owner:secret@127.0.0.1:5432/postgres\n", encoding="utf-8"
    )
    owner_dsn.chmod(0o600)
    backup = runtime / "private/agent_execution_worker.dump"
    backup.write_bytes(b"bounded-backup\n")
    backup.chmod(0o600)
    ready = tmp_path / "remove-lock-ready"
    release = tmp_path / "remove-lock-release"
    invoked = tmp_path / "remove-psql-invoked"
    fake_psql = tmp_path / "psql"
    if block_psql:
        body = (
            f"/usr/bin/touch {ready}\n"
            f"while [[ ! -e {release} ]]; do /bin/sleep 0.01; done\n"
            "exit 1\n"
        )
    else:
        body = f"/usr/bin/touch {invoked}\nexit 1\n"
    fake_psql.write_text("#!/bin/bash\nset -euo pipefail\n" + body, encoding="utf-8")
    fake_psql.chmod(0o700)
    environment = {
        **os.environ,
        "PLATFORM_LOCAL_PYTHON3": sys.executable,
        "PLATFORM_LOCAL_POSTGRES17_PSQL": str(fake_psql),
        "PLATFORM_LOCAL_POSTGRES17_PG_RESTORE": str(fake_psql),
    }
    return remover, owner_dsn, backup, environment, ready, release


def _wait_for_file(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


def test_remove_holds_rotation_lock_before_preflight_and_blocks_rotator(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path / "runtime")
    original = _rotation_components(paths, "canonical")
    remover, owner_dsn, backup, environment, ready, release = (
        _remover_for_rotation_lock_test(paths, tmp_path, block_psql=True)
    )
    process = subprocess.Popen(
        [
            "/bin/bash", str(remover), str(owner_dsn), str(backup),
            "--confirm-remove-agent-execution-worker",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    try:
        _wait_for_file(ready)
        concurrent = _run_rotation(paths, "prepare")
        assert concurrent.returncode == 1
        assert concurrent.stderr == "EXECUTION_WORKER_KEY_ROTATION_FAILED\n"
        assert _rotation_components(paths, "canonical") == original
        assert not any(path.exists() for path in paths["managed"].values())
        assert paths["launch_state"].read_text(encoding="utf-8") == "online"
    finally:
        release.touch()
        _stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 1
    assert stderr.endswith("EXECUTION_WORKER_REMOVAL_FAILED\n")


def test_rotator_holds_rotation_lock_and_remove_performs_zero_preflight_or_mutation(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path / "runtime")
    original = _rotation_components(paths, "canonical")
    generator_ready = tmp_path / "generator-ready"
    generator_release = tmp_path / "generator-release"
    generator = paths["rotator"].with_name(GENERATOR.name)
    generator.write_text(
        "from pathlib import Path\nimport time\n"
        f"Path({str(generator_ready)!r}).touch()\n"
        f"while not Path({str(generator_release)!r}).exists(): time.sleep(0.01)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    generator.chmod(0o700)
    remover, owner_dsn, backup, environment, _ready, _release = (
        _remover_for_rotation_lock_test(paths, tmp_path, block_psql=False)
    )
    psql_invoked = tmp_path / "remove-psql-invoked"
    rotation = subprocess.Popen(
        [sys.executable, str(paths["rotator"]), "prepare", "worker-v2"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=paths["environment"],
    )
    try:
        _wait_for_file(generator_ready)
        removed = subprocess.run(
            [
                "/bin/bash", str(remover), str(owner_dsn), str(backup),
                "--confirm-remove-agent-execution-worker",
            ],
            text=True,
            capture_output=True,
            env=environment,
            timeout=5,
        )
        assert removed.returncode == 1
        assert removed.stderr == "EXECUTION_WORKER_REMOVAL_FAILED\n"
        assert not psql_invoked.exists()
        assert _rotation_components(paths, "canonical") == original
        assert paths["launch_state"].read_text(encoding="utf-8") == "online"
    finally:
        generator_release.touch()
        rotation.communicate(timeout=5)


def _rotation_components(paths, prefix: str) -> tuple[bytes, bytes, bytes]:
    names = (
        f"{prefix}_private",
        f"{prefix}_public",
        f"{prefix}_plist",
    )
    if prefix == "canonical":
        return tuple(paths[name].read_bytes() for name in names)
    return tuple(paths["managed"][name].read_bytes() for name in names)


def _write_rotation_phase(
    paths, phase: str, previous_worker_state: str | None
) -> None:
    state_path = paths["managed"]["state"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["phase"] = phase
    state["previous_worker_state"] = previous_worker_state
    state_path.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    state_path.chmod(0o600)


@pytest.mark.parametrize(
    "replaced_components",
    [0, 1, 2, 3],
    ids=["after-state", "after-private", "after-public", "before-launch"],
)
def test_local_key_rotation_new_process_rollback_recovers_every_durable_boundary(
    tmp_path: Path, replaced_components: int
) -> None:
    paths = _rotation_test_environment(tmp_path, loaded=True)
    original = _rotation_components(paths, "canonical")
    assert _run_rotation(paths, "prepare").returncode == 0
    staged = _rotation_components(paths, "next")
    _write_rotation_phase(paths, "activating", "online")
    if replaced_components:
        for name, value in zip(
            ("previous_private", "previous_public", "previous_plist"),
            original,
            strict=True,
        ):
            paths["managed"][name].write_bytes(value)
            paths["managed"][name].chmod(0o600)
        paths["launch_state"].write_text("stopped", encoding="utf-8")
    for name, value in list(zip(
        ("canonical_private", "canonical_public", "canonical_plist"),
        staged,
        strict=True,
    ))[:replaced_components]:
        paths[name].write_bytes(value)
        paths[name].chmod(0o600)

    rolled_back = _run_rotation(paths, "rollback")

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert _rotation_components(paths, "canonical") == original
    assert paths["launch_state"].read_text(encoding="utf-8") == "online"
    assert not any(path.exists() for path in paths["managed"].values())


def test_local_key_rotation_mixed_recovery_rejects_unjournaled_component(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path, loaded=True)
    assert _run_rotation(paths, "prepare").returncode == 0
    original = _rotation_components(paths, "canonical")
    staged = _rotation_components(paths, "next")
    _write_rotation_phase(paths, "activating", "online")
    for name, value in zip(
        ("previous_private", "previous_public", "previous_plist"),
        original,
        strict=True,
    ):
        paths["managed"][name].write_bytes(value)
        paths["managed"][name].chmod(0o600)
    paths["canonical_private"].write_bytes(staged[0])
    paths["canonical_public"].write_bytes(b"{}\n")
    paths["launch_state"].write_text("stopped", encoding="utf-8")
    before = _rotation_components(paths, "canonical")

    rolled_back = _run_rotation(paths, "rollback")

    assert rolled_back.returncode == 1
    assert rolled_back.stderr == "EXECUTION_WORKER_KEY_ROTATION_FAILED\n"
    assert _rotation_components(paths, "canonical") == before
    assert paths["launch_state"].read_text(encoding="utf-8") == "stopped"


def test_local_key_rotation_corrupt_boundary_fails_before_creating_previous(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path, loaded=True)
    assert _run_rotation(paths, "prepare").returncode == 0
    _write_rotation_phase(paths, "activating", "online")
    paths["canonical_public"].write_bytes(b"{}\n")
    paths["canonical_public"].chmod(0o600)
    before = {
        name: path.read_bytes()
        for name, path in paths["managed"].items()
        if path.exists()
    }

    rolled_back = _run_rotation(paths, "rollback")

    assert rolled_back.returncode == 1
    assert not any(
        paths["managed"][name].exists()
        for name in ("previous_private", "previous_public", "previous_plist")
    )
    assert {
        name: path.read_bytes()
        for name, path in paths["managed"].items()
        if path.exists()
    } == before


@pytest.mark.parametrize("remaining", [1, 2], ids=["private-only", "private-public"])
def test_local_key_rotation_abort_cleans_partial_prepare_without_state(
    tmp_path: Path, remaining: int
) -> None:
    paths = _rotation_test_environment(tmp_path)
    original = _rotation_components(paths, "canonical")
    assert _run_rotation(paths, "prepare").returncode == 0
    paths["managed"]["state"].unlink()
    for name in ("next_private", "next_public", "next_plist")[remaining:]:
        paths["managed"][name].unlink()

    aborted = _run_rotation(paths, "abort")

    assert aborted.returncode == 0, aborted.stderr
    assert _rotation_components(paths, "canonical") == original
    assert not any(path.exists() for path in paths["managed"].values())


def test_local_key_rotation_abort_and_finalize_cleanup_are_retryable(
    tmp_path: Path,
) -> None:
    abort_paths = _rotation_test_environment(tmp_path / "abort")
    assert _run_rotation(abort_paths, "prepare").returncode == 0
    abort_paths["managed"]["next_private"].unlink()
    assert _run_rotation(abort_paths, "abort").returncode == 0
    assert not any(path.exists() for path in abort_paths["managed"].values())

    finalize_paths = _rotation_test_environment(tmp_path / "finalize")
    assert _run_rotation(finalize_paths, "prepare").returncode == 0
    assert _run_rotation(finalize_paths, "activate").returncode == 0
    _write_rotation_phase(finalize_paths, "finalized", "online")
    finalize_paths["managed"]["previous_private"].unlink()
    finalize_paths["managed"]["next_public"].unlink()

    finalized = _run_rotation(finalize_paths, "finalize")

    assert finalized.returncode == 0, finalized.stderr
    _assert_rotation_identity(finalize_paths, "worker-v2")
    assert not any(path.exists() for path in finalize_paths["managed"].values())


def test_local_key_rotation_rejects_boolean_schema_version_without_changes(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path)
    assert _run_rotation(paths, "prepare").returncode == 0
    state_path = paths["managed"]["state"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["schema_version"] = True
    state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    state_path.chmod(0o600)
    before = {
        name: path.read_bytes()
        for name, path in paths["managed"].items()
        if path.exists()
    }

    aborted = _run_rotation(paths, "abort")

    assert aborted.returncode == 1
    assert {
        name: path.read_bytes()
        for name, path in paths["managed"].items()
        if path.exists()
    } == before


@pytest.mark.parametrize("launch_error", ["permission", "transient"])
def test_local_key_rotation_launchctl_inspection_errors_fail_closed(
    tmp_path: Path, launch_error: str
) -> None:
    paths = _rotation_test_environment(tmp_path, loaded=True)
    original = _rotation_components(paths, "canonical")
    assert _run_rotation(paths, "prepare").returncode == 0
    paths["launch_error"].write_text(launch_error, encoding="utf-8")

    activated = _run_rotation(paths, "activate")

    assert activated.returncode == 1
    assert activated.stderr == "EXECUTION_WORKER_KEY_ROTATION_FAILED\n"
    assert _rotation_components(paths, "canonical") == original
    assert paths["launch_state"].read_text(encoding="utf-8") == "online"
    assert json.loads(paths["managed"]["state"].read_text())["phase"] == "prepared"


@pytest.mark.parametrize("target", ["private-parent", "private-file", "public-parent"])
def test_key_generator_rejects_symlinked_security_boundary(
    tmp_path: Path, target: str
) -> None:
    real_private = tmp_path / "real-private"
    real_public = tmp_path / "real-public"
    real_private.mkdir(mode=0o700)
    real_public.mkdir(mode=0o700)
    private_parent = real_private
    public_parent = real_public
    private = private_parent / "worker.key"
    if target == "private-parent":
        private_parent = tmp_path / "private-link"
        private_parent.symlink_to(real_private, target_is_directory=True)
        private = private_parent / "worker.key"
    elif target == "private-file":
        backing = real_private / "backing"
        backing.write_bytes(b"k" * 32)
        backing.chmod(0o600)
        private.symlink_to(backing)
    else:
        public_parent = tmp_path / "public-link"
        public_parent.symlink_to(real_public, target_is_directory=True)
    public = public_parent / "worker-public.json"

    result = subprocess.run(
        [sys.executable, str(GENERATOR), str(private), str(public)],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "WORKER_KEY_GENERATION_FAILED\n"


def test_key_generator_uses_dirfd_single_fd_reads_and_bounded_secure_writes() -> None:
    source = GENERATOR.read_text(encoding="utf-8")

    assert "dir_fd=" in source
    assert "O_NOFOLLOW" in source
    assert "O_DIRECTORY" in source
    assert "os.fstat(" in source
    assert "os.read(" in source
    assert "read_bytes(" not in source
    assert "os.replace(" in source and "src_dir_fd=" in source and "dst_dir_fd=" in source
    assert "finally:" in source and "os.unlink(" in source


def test_key_generator_forces_exact_modes_under_restrictive_caller_umask(
    tmp_path: Path,
) -> None:
    private_dir = tmp_path / "private"
    public_dir = tmp_path / "public"
    private_dir.mkdir(mode=0o700)
    public_dir.mkdir(mode=0o700)
    private = private_dir / "worker.key"
    public = public_dir / "worker.json"

    result = subprocess.run(
        [sys.executable, str(GENERATOR), str(private), str(public)],
        text=True,
        capture_output=True,
        preexec_fn=lambda: os.umask(0o777),
    )

    assert result.returncode == 0, result.stderr
    assert _mode(private) == _mode(public) == 0o600


def test_key_generator_first_private_write_failure_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch
) -> None:
    private_dir = tmp_path / "private"
    public_dir = tmp_path / "public"
    private_dir.mkdir(mode=0o700)
    public_dir.mkdir(mode=0o700)
    private = private_dir / "worker.key"
    public = public_dir / "worker.json"
    spec = importlib.util.spec_from_file_location("atomic_worker_key", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_write = module.os.write
    failed = False

    def fail_first_write(descriptor: int, value: bytes) -> int:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected private-key write failure")
        return original_write(descriptor, value)

    monkeypatch.setattr(module.os, "write", fail_first_write)
    assert module.main([str(private), str(public)]) == 1
    assert not private.exists()
    assert set(private_dir.iterdir()) == {
        private_dir / "execution-worker-key-rotation.lock"
    }

    monkeypatch.setattr(module.os, "write", original_write)
    assert module.main([str(private), str(public)]) == 0
    assert len(private.read_bytes()) == 32


def test_key_generator_concurrent_first_generation_publishes_one_complete_key(
    tmp_path: Path,
) -> None:
    private_dir = tmp_path / "private"
    public_dir = tmp_path / "public"
    private_dir.mkdir(mode=0o700)
    public_dir.mkdir(mode=0o700)
    private = private_dir / "worker.key"
    public = public_dir / "worker.json"
    command = [sys.executable, str(GENERATOR), str(private), str(public)]

    processes = [
        subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(8)
    ]
    results = [process.communicate(timeout=20) for process in processes]

    assert [process.returncode for process in processes] == [0] * len(processes)
    assert len({stdout for stdout, _stderr in results}) == 1
    assert all(stderr == "" for _stdout, stderr in results)
    assert len(private.read_bytes()) == 32
    assert set(private_dir.iterdir()) == {
        private,
        private_dir / "execution-worker-key-rotation.lock",
    }
    assert _mode(private_dir / "execution-worker-key-rotation.lock") == 0o600


def test_registration_cli_uses_only_v28_maintenance_functions(
    tmp_path: Path, monkeypatch
) -> None:
    module = importlib.import_module("app.execution_relay.register_worker")
    secret_dir = tmp_path / "private"
    secret_dir.mkdir(mode=0o700)
    dsn = secret_dir / "maintenance-dsn"
    dsn.write_text("postgresql://maintenance@127.0.0.1/control\n", encoding="utf-8")
    dsn.chmod(0o600)
    public = tmp_path / "public.json"
    public.write_text(
        json.dumps(
            {
                "worker_id": "agentops-mac-primary",
                "key_id": "worker-v1",
                "public_key_base64url": base64.urlsafe_b64encode(b"k" * 32)
                .decode()
                .rstrip("="),
                "allowed_agent_ids": AGENTS,
            }
        ),
        encoding="utf-8",
    )
    public.chmod(0o600)
    calls: list[tuple[str, tuple[object, ...]]] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, parameters):
            calls.append((query, parameters))

    monkeypatch.setenv(
        "PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", str(dsn)
    )
    monkeypatch.setattr(module.psycopg, "connect", lambda value: Connection())

    assert module.main(["register", str(public), "OPS_20260821"]) == 0
    assert module.main(
        ["add-key", "agentops-mac-primary", str(public), "OPS_20260822"]
    ) == 0
    assert module.main(
        ["revoke-key", "agentops-mac-primary", "worker-v1", "OPS_20260823"]
    ) == 0
    assert module.main(
        ["revoke-worker", "agentops-mac-primary", "OPS_20260824"]
    ) == 0
    assert [
        re.sub(r"\s+", " ", query).strip().split("(", 1)[0]
        for query, _parameters in calls
    ] == [
        "select platform_control.register_execution_worker_v28",
        "select platform_control.add_execution_worker_key_v28",
        "select platform_control.revoke_execution_worker_key_v28",
        "select platform_control.revoke_execution_worker_v28",
    ]
    assert all(parameters[-2] == f"OPS_2026082{index}" for index, (_, parameters) in enumerate(calls, 1))
    source = (ROOT / "backend/app/execution_relay/register_worker.py").read_text(
        encoding="utf-8"
    ).lower()
    assert not re.search(
        r"\b(insert|update|delete)\s+(into\s+|from\s+)?platform_control\.execution_worker",
        source,
    )


@pytest.mark.postgres
def test_v28_maintenance_bounds_dual_key_acceptance_and_rejects_reuse(
    control_database,
) -> None:
    maintenance = control_database["environments"]["production"]["urls"][
        "platform_control_maintenance"
    ]
    worker_id = "agentops-mac-primary"
    with psycopg.connect(maintenance) as connection:
        connection.execute(
            "select platform_control.register_execution_worker_v28(%s,%s,%s,%s,%s,%s)",
            (worker_id, "worker-v1", b"a" * 32, AGENTS, "OPS_20260821", uuid.uuid4()),
        )
        connection.execute(
            "select platform_control.add_execution_worker_key_v28(%s,%s,%s,%s,%s)",
            (worker_id, "worker-v2", b"b" * 32, "OPS_20260822", uuid.uuid4()),
        )
        connection.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.add_execution_worker_key_v28(%s,%s,%s,%s,%s)",
                (worker_id, "worker-v3", b"c" * 32, "OPS_20260823", uuid.uuid4()),
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.add_execution_worker_key_v28(%s,%s,%s,%s,%s)",
                (worker_id, "worker-v1", b"z" * 32, "OPS_20260824", uuid.uuid4()),
            )


@pytest.mark.parametrize(
    "reference",
    ["ops_20260821", "OPS-123", "1OPS_20260821", "OPS 20260821", "OPS_2026!"],
)
def test_registration_rejects_invalid_change_reference_before_database(
    tmp_path: Path, monkeypatch, reference: str
) -> None:
    module = importlib.import_module("app.execution_relay.register_worker")
    dsn = tmp_path / "maintenance-dsn"
    dsn.write_text("postgresql://maintenance/control", encoding="utf-8")
    dsn.chmod(0o600)
    monkeypatch.setenv(
        "PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", str(dsn)
    )
    monkeypatch.setattr(
        module.psycopg,
        "connect",
        lambda _value: pytest.fail("invalid reference reached the database"),
    )
    assert module.main(["revoke-worker", "agentops-mac-primary", reference]) == 1


@pytest.mark.parametrize("mutation", ["agent_set", "non_urlsafe_key", "extra"])
def test_registration_rejects_noncanonical_public_document_before_database(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    module = importlib.import_module("app.execution_relay.register_worker")
    document = {
        "worker_id": "agentops-mac-primary",
        "key_id": "worker-v1",
        "public_key_base64url": base64.urlsafe_b64encode(b"k" * 32)
        .decode()
        .rstrip("="),
        "allowed_agent_ids": AGENTS.copy(),
    }
    if mutation == "agent_set":
        document["allowed_agent_ids"] = AGENTS[:-1]
    elif mutation == "non_urlsafe_key":
        document["public_key_base64url"] = base64.b64encode(b"\xfb" * 32).decode().rstrip("=")
    else:
        document["extra"] = True
    public = tmp_path / "public.json"
    public.write_text(json.dumps(document), encoding="utf-8")
    public.chmod(0o600)
    dsn = tmp_path / "maintenance-dsn"
    dsn.write_text("postgresql://maintenance/control", encoding="utf-8")
    dsn.chmod(0o600)
    monkeypatch.setenv(
        "PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", str(dsn)
    )
    monkeypatch.setattr(
        module.psycopg,
        "connect",
        lambda _value: pytest.fail("invalid public document reached the database"),
    )

    assert module.main(["register", str(public), "OPS_20260821"]) == 1


def test_registration_rejects_noncanonical_base64url_reencoding_before_database(
    tmp_path: Path, monkeypatch
) -> None:
    module = importlib.import_module("app.execution_relay.register_worker")
    canonical = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    replacement = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"[
        ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_".index(canonical[-1]) + 1)
        % 64
    ]
    public_dir = tmp_path / "public"
    public_dir.mkdir(mode=0o700)
    public = public_dir / "public.json"
    public.write_text(
        json.dumps(
            {
                "worker_id": "agentops-mac-primary",
                "key_id": "worker-v1",
                "public_key_base64url": canonical[:-1] + replacement,
                "allowed_agent_ids": AGENTS,
            }
        ),
        encoding="utf-8",
    )
    public.chmod(0o600)
    dsn_dir = tmp_path / "private"
    dsn_dir.mkdir(mode=0o700)
    dsn = dsn_dir / "maintenance-dsn"
    dsn.write_text("postgresql://maintenance/control", encoding="utf-8")
    dsn.chmod(0o600)
    monkeypatch.setenv("PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", str(dsn))
    monkeypatch.setattr(
        module.psycopg,
        "connect",
        lambda _value: pytest.fail("noncanonical key reached the database"),
    )

    assert module.main(["register", str(public), "OPS_20260821"]) == 1


@pytest.mark.parametrize("kind", ["dsn-parent", "dsn-file", "public-parent", "public-file"])
def test_registration_rejects_symlinked_secret_boundaries_before_database(
    tmp_path: Path, monkeypatch, kind: str
) -> None:
    module = importlib.import_module("app.execution_relay.register_worker")
    dsn_real_dir = tmp_path / "dsn-real"
    public_real_dir = tmp_path / "public-real"
    dsn_real_dir.mkdir(mode=0o700)
    public_real_dir.mkdir(mode=0o700)
    dsn_real = dsn_real_dir / "maintenance-dsn"
    dsn_real.write_text("postgresql://maintenance/control", encoding="utf-8")
    dsn_real.chmod(0o600)
    public_real = public_real_dir / "public.json"
    public_real.write_text(
        json.dumps(
            {
                "worker_id": "agentops-mac-primary",
                "key_id": "worker-v1",
                "public_key_base64url": base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="),
                "allowed_agent_ids": AGENTS,
            }
        ),
        encoding="utf-8",
    )
    public_real.chmod(0o600)
    dsn = dsn_real
    public = public_real
    if kind == "dsn-parent":
        linked = tmp_path / "dsn-link"
        linked.symlink_to(dsn_real_dir, target_is_directory=True)
        dsn = linked / dsn_real.name
    elif kind == "dsn-file":
        dsn = dsn_real_dir / "dsn-link"
        dsn.symlink_to(dsn_real)
    elif kind == "public-parent":
        linked = tmp_path / "public-link"
        linked.symlink_to(public_real_dir, target_is_directory=True)
        public = linked / public_real.name
    else:
        public = public_real_dir / "public-link"
        public.symlink_to(public_real)
    monkeypatch.setenv("PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", str(dsn))
    monkeypatch.setattr(
        module.psycopg,
        "connect",
        lambda _value: pytest.fail("symlinked input reached the database"),
    )

    assert module.main(["register", str(public), "OPS_20260821"]) == 1


def test_registration_secure_reader_uses_parent_dirfd_and_one_open_fd() -> None:
    source = (ROOT / "backend/app/execution_relay/register_worker.py").read_text(
        encoding="utf-8"
    )

    assert "dir_fd=" in source
    assert "O_DIRECTORY" in source and "O_NOFOLLOW" in source
    assert "os.fstat(" in source and "os.read(" in source
    assert ".read_text(" not in source


@pytest.mark.parametrize(
    "mutation",
    ["dsn-parent-mode", "dsn-file-mode", "public-parent-mode", "public-file-mode", "dsn-size", "public-size", "owner"],
)
def test_registration_rejects_wrong_owner_mode_or_size_before_database(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    module = importlib.import_module("app.execution_relay.register_worker")
    dsn_dir = tmp_path / "dsn"
    public_dir = tmp_path / "public"
    dsn_dir.mkdir(mode=0o700)
    public_dir.mkdir(mode=0o700)
    dsn = dsn_dir / "maintenance-dsn"
    dsn.write_text("postgresql://maintenance/control")
    dsn.chmod(0o600)
    public = public_dir / "public.json"
    public.write_text(
        json.dumps(
            {
                "worker_id": "agentops-mac-primary",
                "key_id": "worker-v1",
                "public_key_base64url": base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="),
                "allowed_agent_ids": AGENTS,
            }
        )
    )
    public.chmod(0o600)
    if mutation == "dsn-parent-mode":
        dsn_dir.chmod(0o755)
    elif mutation == "dsn-file-mode":
        dsn.chmod(0o644)
    elif mutation == "public-parent-mode":
        public_dir.chmod(0o755)
    elif mutation == "public-file-mode":
        public.chmod(0o644)
    elif mutation == "dsn-size":
        dsn.write_bytes(b"x" * 16_385)
    elif mutation == "public-size":
        public.write_bytes(b"x" * 65_537)
    else:
        real_uid = os.getuid()
        monkeypatch.setattr(module.os, "getuid", lambda: real_uid + 1)
    monkeypatch.setenv("PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", str(dsn))
    monkeypatch.setattr(
        module.psycopg,
        "connect",
        lambda _value: pytest.fail("insecure file reached the database"),
    )

    assert module.main(["register", str(public), "OPS_20260821"]) == 1


def test_registration_reads_the_open_inode_if_path_is_swapped(
    tmp_path: Path, monkeypatch
) -> None:
    module = importlib.import_module("app.execution_relay.register_worker")
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    path = parent / "value"
    path.write_text("fixed-inode")
    path.chmod(0o600)
    moved = parent / "original"
    original_fstat = os.fstat
    calls = 0

    def swapping_fstat(descriptor: int):
        nonlocal calls
        calls += 1
        metadata = original_fstat(descriptor)
        if calls == 2:
            path.rename(moved)
            path.write_text("swapped-path")
            path.chmod(0o600)
        return metadata

    monkeypatch.setattr(module.os, "fstat", swapping_fstat)

    assert module._secure_text_file(str(path), maximum_size=64) == "fixed-inode"


def test_local_database_bootstrap_reuses_postgres17_without_flywheel_or_sqlite() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8")
    lowered = script.lower()

    assert "agent_execution_worker" in script
    for role in (
        "agent_execution_worker_owner",
        "agent_execution_worker_migrator",
        "agent_execution_worker_runtime",
    ):
        assert role in script
    assert "worker_schema.sql" in script
    assert "schema_migrations" in script
    assert "event_outbox" in script
    assert "0o600" in script and "0o700" in script
    assert "parent.st_uid != os.getuid()" in script
    assert "postgresql://agent_execution_worker_runtime:" in script
    assert "if [[ ! -e \"$runtime_dsn_file\" ]]" in script
    assert "grant select,insert on execution_worker.local_runs" in lowered
    assert "grant update(state,dispatched_at,terminal_at)" in lowered
    assert "grant update(delivered_at)" in lowered
    assert not re.search(r"grant[^\n;]*flywheel", lowered)
    for forbidden in (
        "sqlite",
        "initdb",
        "pg_ctl",
        "brew services",
        "create service",
        "drop database",
        "truncate ",
    ):
        assert forbidden not in lowered
    subprocess.run(["/bin/bash", "-n", str(BOOTSTRAP)], check=True)


def test_local_database_bootstrap_fails_closed_on_roles_memberships_and_acls() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8").lower()

    for attribute in (
        "rolcanlogin",
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
        "rolinherit",
        "rolconnlimit",
        "rolvaliduntil",
        "rolconfig",
    ):
        assert attribute in script
    assert "pg_auth_members" in script
    for membership_field in (
        "grantor",
        "admin_option",
        "inherit_option",
        "set_option",
    ):
        assert membership_field in script
    assert "with admin false" in script
    assert "with inherit false" in script
    assert "with set true" in script
    assert "granted by %i" not in script
    assert script.count(
        "grant agent_execution_worker_owner to agent_execution_worker_migrator"
    ) == 3
    assert "membership.grantor=10" in script
    assert "datacl" in script and "datdba" in script
    assert "aclexplode" in script
    assert "information_schema.role_table_grants" in script
    assert "information_schema.role_column_grants" in script
    assert "information_schema.role_usage_grants" in script
    assert "pg_default_acl" in script
    assert "cross-database worker privilege collision" in script
    assert "public" in script and "acl.grantee=0" in script
    assert "execution worker database grant mismatch" in script
    assert "execution worker unexpected schema grant" in script
    assert "execution worker unexpected table grant" in script
    assert "flywheel" not in script
    assert "trap database_exit exit" not in script
    assert script.index("# read-only collision audit") < script.index(
        "grant agent_execution_worker_owner to agent_execution_worker_migrator"
    )
    assert "|| true" not in script
    assert "read_text" not in script and "/usr/bin/sed" not in script
    assert "o_nofollow" in script and "dir_fd=" in script and "os.fstat" in script


def test_database_bootstrap_never_grants_current_user_temporary_membership() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8").lower()

    assert "grant agent_execution_worker_migrator to %i" not in script
    assert "cleanup_membership" not in script
    assert "cleanup_needed" not in script
    assert "trap database_exit exit" not in script
    assert "owner dsn role must be superuser" in script


@pytest.mark.parametrize(
    "mutation",
    ["parent-symlink", "file-symlink", "parent-mode", "file-mode", "size", "owner"],
)
def test_database_bootstrap_rejects_insecure_owner_dsn_before_database(
    tmp_path: Path, mutation: str
) -> None:
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    platform = tmp_path / "platform"
    local = platform / "deploy/local-execution-worker"
    schema = platform / "backend/app/execution_relay/worker_schema.sql"
    local.mkdir(parents=True)
    schema.parent.mkdir(parents=True)
    schema.write_text("select 1;\n")
    source = BOOTSTRAP.read_text(encoding="utf-8").replace("agentops", current_user)
    if mutation == "owner":
        source = source.replace(
            "parent.st_uid != os.getuid()", "parent.st_uid != (os.getuid() + 1)"
        )
    copied = local / BOOTSTRAP.name
    copied.write_text(source)
    copied.chmod(0o700)
    real_private = tmp_path / "real-private"
    real_private.mkdir(mode=0o700)
    owner_real = real_private / "owner-dsn"
    owner_real.write_text("postgresql://owner:secret@127.0.0.1:5432/postgres\n")
    owner_real.chmod(0o600)
    owner_dsn = owner_real
    if mutation == "parent-symlink":
        linked = tmp_path / "private-link"
        linked.symlink_to(real_private, target_is_directory=True)
        owner_dsn = linked / owner_real.name
    elif mutation == "file-symlink":
        owner_dsn = real_private / "owner-link"
        owner_dsn.symlink_to(owner_real)
    elif mutation == "parent-mode":
        real_private.chmod(0o755)
    elif mutation == "file-mode":
        owner_real.chmod(0o644)
    elif mutation == "size":
        owner_real.write_bytes(b"x" * 16_385)
    fake_psql = tmp_path / "psql"
    calls = tmp_path / "psql-called"
    fake_psql.write_text(
        """#!/bin/bash
if [[ "${1:-}" == --version ]]; then echo 'psql (PostgreSQL) 17.6'; exit 0; fi
printf called > "$FAKE_PSQL_CALLED"
exit 21
"""
    )
    fake_psql.chmod(0o700)

    result = subprocess.run(
        ["/bin/bash", str(copied), str(owner_dsn), str(real_private / "runtime-dsn")],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PLATFORM_LOCAL_POSTGRES17_PSQL": str(fake_psql),
            "PLATFORM_LOCAL_PYTHON3": sys.executable,
            "FAKE_PSQL_CALLED": str(calls),
        },
    )

    assert result.returncode == 1
    assert result.stderr == "EXECUTION_WORKER_DATABASE_BOOTSTRAP_FAILED\n"
    assert not calls.exists()


@pytest.mark.postgres
@pytest.mark.parametrize("collision", ["runtime-superuser", "membership", "database-acl"])
def test_database_preflight_rejects_privilege_collisions_before_outbox_access(
    control_database, collision: str
) -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8")
    preflight = script.split(
        "owner_psql -d postgres >/dev/null <<'SQL'", 1
    )[1].split("\nSQL", 1)[0]
    admin_url = control_database["cluster_admin"]
    port = control_database["port"]
    roles = (
        "agent_execution_worker_owner",
        "agent_execution_worker_migrator",
        "agent_execution_worker_runtime",
        "execution_worker_intruder",
    )
    with psycopg.connect(admin_url, autocommit=True) as connection:
        for role in roles:
            connection.execute(psycopg.sql.SQL("drop role if exists {}").format(psycopg.sql.Identifier(role)))
        connection.execute("drop database if exists agent_execution_worker")
        if collision == "runtime-superuser":
            connection.execute(
                "create role agent_execution_worker_runtime login superuser noinherit"
            )
        elif collision == "membership":
            connection.execute(
                "create role agent_execution_worker_runtime login nosuperuser nocreatedb "
                "nocreaterole noreplication nobypassrls noinherit"
            )
            connection.execute("create role execution_worker_intruder nologin noinherit")
            connection.execute(
                "grant execution_worker_intruder to agent_execution_worker_runtime"
            )
        else:
            connection.execute(
                "create role agent_execution_worker_owner nologin nosuperuser nocreatedb "
                "nocreaterole noreplication nobypassrls noinherit"
            )
            connection.execute(
                "create role agent_execution_worker_migrator nologin nosuperuser nocreatedb "
                "nocreaterole noreplication nobypassrls noinherit"
            )
            connection.execute(
                "create role agent_execution_worker_runtime login nosuperuser nocreatedb "
                "nocreaterole noreplication nobypassrls noinherit"
            )
            connection.execute("create role execution_worker_intruder nologin noinherit")
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator"
            )
            connection.execute(
                "create database agent_execution_worker owner agent_execution_worker_owner"
            )
            connection.execute("revoke all on database agent_execution_worker from public")
            connection.execute(
                "grant connect on database agent_execution_worker to "
                "agent_execution_worker_migrator,agent_execution_worker_runtime,execution_worker_intruder"
            )
    if collision == "database-acl":
        target_url = f"postgresql://control_test_admin@127.0.0.1:{port}/agent_execution_worker"
        with psycopg.connect(target_url, autocommit=True) as target:
            target.execute("create schema execution_worker")
            target.execute("create table execution_worker.event_outbox(marker integer)")
            target.execute("insert into execution_worker.event_outbox values (17)")
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            with pytest.raises(psycopg.errors.RaiseException):
                connection.execute(preflight)
        if collision == "database-acl":
            with psycopg.connect(target_url) as target:
                assert target.execute(
                    "select marker from execution_worker.event_outbox"
                ).fetchone() == (17,)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute("drop database if exists agent_execution_worker")
            for role in roles:
                connection.execute(
                    psycopg.sql.SQL("drop role if exists {}").format(
                        psycopg.sql.Identifier(role)
                    )
                )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "collision",
    [
        "membership-admin",
        "membership-inherit",
        "membership-set",
        "membership-grantor",
        "runtime-connlimit",
        "runtime-validuntil",
        "runtime-config",
        "runtime-database-config",
    ],
)
def test_bootstrap_rejects_exact_role_and_membership_collisions_before_outbox(
    control_database, tmp_path: Path, collision: str
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        first = _run_bootstrap(paths)
        assert first.returncode == 0, first.stderr
        runtime_dsn = paths[2]
        with psycopg.connect(runtime_dsn.read_text().strip()) as runtime:
            run_id = uuid.uuid4()
            runtime.execute(
                "insert into execution_worker.local_runs("
                "run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at"
                ") values (%s,%s,'hr-bot',9101,%s,'leased',now())",
                (run_id, uuid.uuid4(), b"r" * 32),
            )
            runtime.execute(
                "insert into execution_worker.event_outbox(run_id,seq,event_json) "
                "values (%s,1,'{\"marker\":true}')",
                (run_id,),
            )
            runtime.commit()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            if collision == "membership-admin":
                connection.execute(
                    "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                    "with admin true"
                )
            elif collision == "membership-inherit":
                connection.execute(
                    "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                    "with inherit true"
                )
            elif collision == "membership-set":
                connection.execute(
                    "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                    "with set false"
                )
            elif collision == "membership-grantor":
                connection.execute(
                    "create role execution_worker_intruder nologin noinherit"
                )
                connection.execute(
                    "grant agent_execution_worker_owner to execution_worker_intruder "
                    "with admin true"
                )
                connection.execute("set role execution_worker_intruder")
                connection.execute(
                    "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                    "with admin false granted by execution_worker_intruder"
                )
                connection.execute("reset role")
            elif collision == "runtime-connlimit":
                connection.execute(
                    "alter role agent_execution_worker_runtime connection limit 2"
                )
            elif collision == "runtime-validuntil":
                connection.execute(
                    "alter role agent_execution_worker_runtime valid until '2030-01-01'"
                )
            elif collision == "runtime-config":
                connection.execute(
                    "alter role agent_execution_worker_runtime set statement_timeout='1s'"
                )
            else:
                connection.execute(
                    "alter role agent_execution_worker_runtime in database postgres "
                    "set statement_timeout='1s'"
                )

        rerun = _run_bootstrap(paths)

        assert rerun.returncode != 0
        assert "role" in rerun.stderr.lower()
        with psycopg.connect(runtime_dsn.read_text().strip()) as runtime:
            assert runtime.execute(
                "select event_json from execution_worker.event_outbox"
            ).fetchone() == ({"marker": True},)
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
def test_bootstrap_persists_exact_pg17_roles_and_membership_options(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        result = _run_bootstrap(paths)
        assert result.returncode == 0, result.stderr
        with psycopg.connect(admin_url) as connection:
            roles = connection.execute(
                "select role.rolname,role.rolcanlogin,role.rolsuper,role.rolinherit,role.rolcreatedb,"
                "role.rolcreaterole,role.rolreplication,role.rolbypassrls,role.rolconnlimit,"
                "role.rolvaliduntil is null,role.rolconfig is null,"
                "case when role.rolname='agent_execution_worker_runtime' "
                    "then auth.rolpassword like 'SCRAM-SHA-256$%%' else auth.rolpassword is null end "
                "from pg_roles role join pg_authid auth on auth.oid=role.oid "
                "where role.rolname=any(%s) order by role.rolname",
                (list(WORKER_ROLES),),
            ).fetchall()
            assert roles == [
                ("agent_execution_worker_migrator", False, False, False, False, False, False, False, -1, True, True, True),
                ("agent_execution_worker_owner", False, False, False, False, False, False, False, -1, True, True, True),
                ("agent_execution_worker_runtime", True, False, False, False, False, False, False, -1, True, True, True),
            ]
            memberships = connection.execute(
                "select granted.rolname,member.rolname,membership.grantor,grantor.rolsuper,"
                "membership.admin_option,membership.inherit_option,membership.set_option "
                "from pg_auth_members membership "
                "join pg_roles granted on granted.oid=membership.roleid "
                "join pg_roles member on member.oid=membership.member "
                "join pg_roles grantor on grantor.oid=membership.grantor "
                "where granted.rolname like 'agent_execution_worker_%' "
                "or member.rolname like 'agent_execution_worker_%'"
            ).fetchall()
            assert memberships == [
                (
                    "agent_execution_worker_owner",
                    "agent_execution_worker_migrator",
                    10,
                    True,
                    False,
                    False,
                    True,
                )
            ]
            assert connection.execute(
                "select count(*) from pg_db_role_setting setting "
                "join pg_roles role on role.oid=setting.setrole "
                "where role.rolname=any(%s)",
                (list(WORKER_ROLES),),
            ).fetchone() == (0,)
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
def test_bootstrap_forces_scram_when_owner_session_defaults_to_md5(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(
            "alter role control_test_admin set password_encryption='md5'"
        )
    try:
        with psycopg.connect(admin_url) as connection:
            assert connection.execute("show password_encryption").fetchone() == ("md5",)

        first = _run_bootstrap(paths)

        assert first.returncode == 0, first.stderr
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select rolpassword like 'SCRAM-SHA-256$%%' from pg_authid "
                "where rolname='agent_execution_worker_runtime'"
            ).fetchone() == (True,)
        original_runtime_dsn = paths[2].read_bytes()
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            run_id = uuid.uuid4()
            runtime.execute(
                "insert into execution_worker.local_runs("
                "run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at"
                ") values (%s,%s,'hr-bot',9101,%s,'leased',now())",
                (run_id, uuid.uuid4(), b"s" * 32),
            )
            runtime.execute(
                "insert into execution_worker.event_outbox(run_id,seq,event_json) "
                "values (%s,1,'{\"scram_marker\":true}')",
                (run_id,),
            )
            runtime.commit()

        second = _run_bootstrap(paths)

        assert second.returncode == 0, second.stderr
        assert paths[2].read_bytes() == original_runtime_dsn
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            assert runtime.execute(
                "select event_json from execution_worker.event_outbox where run_id=%s",
                (run_id,),
            ).fetchone() == ({"scram_marker": True},)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "alter role control_test_admin reset password_encryption"
            )
        _drop_worker_test_state(admin_url)


def test_bootstrap_sets_scram_before_role_password_statements() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8").lower()

    setting = script.index("set password_encryption='scram-sha-256'")
    assert setting < script.index("create role agent_execution_worker_runtime")
    assert setting < script.index("alter role agent_execution_worker_runtime password")


@pytest.mark.postgres
def test_bootstrap_allows_alternate_superuser_rerun_and_preserves_outbox(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        first = _run_bootstrap(paths)
        assert first.returncode == 0, first.stderr
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            run_id = uuid.uuid4()
            runtime.execute(
                "insert into execution_worker.local_runs("
                "run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at"
                ") values (%s,%s,'hr-bot',9101,%s,'leased',now())",
                (run_id, uuid.uuid4(), b"i" * 32),
            )
            runtime.execute(
                "insert into execution_worker.event_outbox(run_id,seq,event_json) "
                "values (%s,1,'{\"identity_marker\":true}')",
                (run_id,),
            )
            runtime.commit()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "create role execution_worker_second_admin login superuser "
                "password 'second-admin-secret'"
            )
        paths[1].write_text(
            "postgresql://execution_worker_second_admin:second-admin-secret@"
            f"127.0.0.1:{control_database['port']}/postgres\n"
        )
        paths[1].chmod(0o600)

        original_runtime_dsn = paths[2].read_bytes()
        rerun = _run_bootstrap(paths)

        assert rerun.returncode == 0, rerun.stderr
        assert paths[2].read_bytes() == original_runtime_dsn
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            assert runtime.execute(
                "select event_json from execution_worker.event_outbox where run_id=%s",
                (run_id,),
            ).fetchone() == ({"identity_marker": True},)
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select membership.grantor,membership.admin_option,"
                "membership.inherit_option,membership.set_option "
                "from pg_auth_members membership "
                "join pg_roles granted on granted.oid=membership.roleid "
                "join pg_roles member on member.oid=membership.member "
                "where granted.rolname='agent_execution_worker_owner' "
                "and member.rolname='agent_execution_worker_migrator'"
            ).fetchall() == [(10, False, False, True)]
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
def test_bootstrap_clean_install_with_alternate_superuser_has_exact_grantor(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "create role execution_worker_second_admin login superuser "
                "password 'second-admin-secret'"
            )
        paths[1].write_text(
            "postgresql://execution_worker_second_admin:second-admin-secret@"
            f"127.0.0.1:{control_database['port']}/postgres\n"
        )
        paths[1].chmod(0o600)

        result = _run_bootstrap(paths)

        assert result.returncode == 0, result.stderr
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select membership.grantor,grantor.rolsuper,membership.admin_option,"
                "membership.inherit_option,membership.set_option "
                "from pg_auth_members membership "
                "join pg_roles granted on granted.oid=membership.roleid "
                "join pg_roles member on member.oid=membership.member "
                "join pg_roles grantor on grantor.oid=membership.grantor "
                "where granted.rolname='agent_execution_worker_owner' "
                "and member.rolname='agent_execution_worker_migrator'"
            ).fetchall() == [
                (10, True, False, False, True)
            ]
            assert connection.execute(
                "select count(*) from pg_auth_members membership "
                "join pg_roles member on member.oid=membership.member "
                "where member.rolname='execution_worker_second_admin'"
            ).fetchone() == (0,)
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
def test_bootstrap_rejects_non_superuser_owner_before_worker_mutation(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "create role execution_worker_second_admin login nosuperuser "
                "password 'second-admin-secret'"
            )
        paths[1].write_text(
            "postgresql://execution_worker_second_admin:second-admin-secret@"
            f"127.0.0.1:{control_database['port']}/postgres\n"
        )
        paths[1].chmod(0o600)

        result = _run_bootstrap(paths)

        assert result.returncode != 0
        assert "owner dsn role must be superuser" in result.stderr
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select count(*) from pg_roles where rolname=any(%s)",
                (list(WORKER_ROLES),),
            ).fetchone() == (0,)
            assert connection.execute(
                "select 1 from pg_database where datname='agent_execution_worker'"
            ).fetchone() is None
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "database_name",
    ["flywheel collision", "dbname=postgres host=127_0_0_1"],
)
def test_bootstrap_rejects_unsafe_other_database_name_before_mutation(
    control_database, tmp_path: Path, database_name: str
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url, database_name)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("create database {}").format(
                    psycopg.sql.Identifier(database_name)
                )
            )

        result = _run_bootstrap(paths)

        assert result.returncode != 0
        assert "unsafe database name collision" in result.stderr
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select count(*) from pg_roles where rolname=any(%s)",
                (list(WORKER_ROLES),),
            ).fetchone() == (0,)
            assert connection.execute(
                "select 1 from pg_database where datname=%s", (database_name,)
            ).fetchone() == (1,)
    finally:
        _drop_worker_test_state(admin_url, database_name)


@pytest.mark.postgres
def test_bootstrap_self_heals_open_target_missing_safe_column_grant(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        first = _run_bootstrap(paths)
        assert first.returncode == 0, first.stderr
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            run_id = uuid.uuid4()
            runtime.execute(
                "insert into execution_worker.local_runs("
                "run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at"
                ") values (%s,%s,'hr-bot',9101,%s,'leased',now())",
                (run_id, uuid.uuid4(), b"p" * 32),
            )
            runtime.execute(
                "insert into execution_worker.event_outbox(run_id,seq,event_json) "
                "values (%s,1,'{\"partial_open_marker\":true}')",
                (run_id,),
            )
            runtime.commit()
        target_url = (
            f"postgresql://control_test_admin@127.0.0.1:"
            f"{control_database['port']}/agent_execution_worker"
        )
        with psycopg.connect(target_url, autocommit=True) as connection:
            connection.execute("set role agent_execution_worker_owner")
            connection.execute(
                "revoke update(delivered_at) on execution_worker.event_outbox "
                "from agent_execution_worker_runtime"
            )
            connection.execute("reset role")

        rerun = _run_bootstrap(paths)

        assert rerun.returncode == 0, rerun.stderr
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            assert runtime.execute(
                "select event_json from execution_worker.event_outbox where run_id=%s",
                (run_id,),
            ).fetchone() == ({"partial_open_marker": True},)
            assert runtime.execute(
                "select has_column_privilege(current_user,"
                "'execution_worker.event_outbox','delivered_at','UPDATE')"
            ).fetchone() == (True,)
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "collision",
    [
        "schema",
        "sequence",
        "view",
        "function",
        "type",
        "large-object-acl",
        "foreign-server",
        "public-schema-runtime",
        "composite-type-acl",
        "extra-column-update",
        "column-select",
        "update-grant-option",
    ],
)
def test_bootstrap_rejects_target_inventory_collision_before_schema_access(
    control_database, tmp_path: Path, collision: str
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        first = _run_bootstrap(paths)
        assert first.returncode == 0, first.stderr
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            run_id = uuid.uuid4()
            runtime.execute(
                "insert into execution_worker.local_runs("
                "run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at"
                ") values (%s,%s,'hr-bot',9101,%s,'leased',now())",
                (run_id, uuid.uuid4(), b"t" * 32),
            )
            runtime.execute(
                "insert into execution_worker.event_outbox(run_id,seq,event_json) "
                "values (%s,1,'{\"target_inventory_marker\":true}')",
                (run_id,),
            )
            runtime.commit()
        target_url = (
            f"postgresql://control_test_admin@127.0.0.1:"
            f"{control_database['port']}/agent_execution_worker"
        )
        with psycopg.connect(target_url, autocommit=True) as connection:
            if collision == "foreign-server":
                connection.execute(
                    "create foreign data wrapper execution_worker_collision_fdw"
                )
                connection.execute(
                    "create server execution_worker_collision_server "
                    "foreign data wrapper execution_worker_collision_fdw"
                )
            else:
                connection.execute("set role agent_execution_worker_owner")
            if collision == "schema":
                connection.execute("create schema unexpected_worker_schema")
            elif collision == "sequence":
                connection.execute("create sequence execution_worker.unexpected_sequence")
            elif collision == "view":
                connection.execute(
                    "create view execution_worker.unexpected_view as select 1 as value"
                )
            elif collision == "function":
                connection.execute(
                    "create function execution_worker.unexpected_function() "
                    "returns integer language sql as 'select 1'"
                )
            elif collision == "type":
                connection.execute(
                    "create type execution_worker.unexpected_type as enum ('value')"
                )
            elif collision == "large-object-acl":
                large_object_oid = connection.execute("select lo_create(0)").fetchone()[0]
                connection.execute(
                    psycopg.sql.SQL(
                        "grant select on large object {} to agent_execution_worker_runtime"
                    ).format(psycopg.sql.Literal(large_object_oid))
                )
            elif collision == "public-schema-runtime":
                connection.execute(
                    "grant usage on schema public to agent_execution_worker_runtime"
                )
            elif collision == "composite-type-acl":
                connection.execute(
                    "grant usage on type execution_worker.local_runs "
                    "to agent_execution_worker_runtime"
                )
            elif collision == "extra-column-update":
                connection.execute(
                    "grant update(job_id) on execution_worker.local_runs "
                    "to agent_execution_worker_runtime"
                )
            elif collision == "column-select":
                connection.execute(
                    "grant select(state) on execution_worker.local_runs "
                    "to agent_execution_worker_runtime"
                )
            else:
                connection.execute(
                    "grant update(state) on execution_worker.local_runs "
                    "to agent_execution_worker_runtime with grant option"
                )
            if collision != "foreign-server":
                connection.execute("reset role")

        rerun = _run_bootstrap(paths)

        assert rerun.returncode != 0
        assert "target database inventory collision" in rerun.stderr
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            assert runtime.execute(
                "select event_json from execution_worker.event_outbox where run_id=%s",
                (run_id,),
            ).fetchone() == ({"target_inventory_marker": True},)
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
def test_bootstrap_self_heals_database_left_locked_before_public_revoke(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "create role agent_execution_worker_owner nologin noinherit "
                "nosuperuser nocreatedb nocreaterole noreplication nobypassrls"
            )
            connection.execute(
                "create role agent_execution_worker_migrator nologin noinherit "
                "nosuperuser nocreatedb nocreaterole noreplication nobypassrls"
            )
            connection.execute(
                "create role agent_execution_worker_runtime login noinherit "
                "nosuperuser nocreatedb nocreaterole noreplication nobypassrls "
                "password 'partial-bootstrap-password'"
            )
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                "with admin false"
            )
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                "with inherit false"
            )
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                "with set true granted by current_user"
            )
            connection.execute(
                "create database agent_execution_worker "
                "owner agent_execution_worker_owner template template0 encoding 'UTF8' "
                "allow_connections false"
            )

        result = _run_bootstrap(paths)

        assert result.returncode == 0, result.stderr
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select datallowconn from pg_database "
                "where datname='agent_execution_worker'"
            ).fetchone() == (True,)
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
def test_bootstrap_self_heals_open_database_left_before_schema(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "create role agent_execution_worker_owner nologin noinherit "
                "nosuperuser nocreatedb nocreaterole noreplication nobypassrls"
            )
            connection.execute(
                "create role agent_execution_worker_migrator nologin noinherit "
                "nosuperuser nocreatedb nocreaterole noreplication nobypassrls"
            )
            connection.execute(
                "create role agent_execution_worker_runtime login noinherit "
                "nosuperuser nocreatedb nocreaterole noreplication nobypassrls "
                "password 'open-partial-password'"
            )
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                "with admin false"
            )
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                "with inherit false"
            )
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                "with set true"
            )
            connection.execute(
                "create database agent_execution_worker "
                "owner agent_execution_worker_owner template template0 encoding 'UTF8' "
                "allow_connections false"
            )
            connection.execute(
                "revoke all on database agent_execution_worker from public"
            )
            connection.execute(
                "grant connect on database agent_execution_worker to "
                "agent_execution_worker_migrator,agent_execution_worker_runtime"
            )
            connection.execute(
                "alter database agent_execution_worker allow_connections true"
            )

        result = _run_bootstrap(paths)

        assert result.returncode == 0, result.stderr
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            assert runtime.execute(
                "select version from execution_worker.schema_migrations where singleton"
            ).fetchone() == (1,)
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
def test_bootstrap_rejects_open_default_acl_database_as_collision(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "create role agent_execution_worker_owner nologin noinherit "
                "nosuperuser nocreatedb nocreaterole noreplication nobypassrls"
            )
            connection.execute(
                "create role agent_execution_worker_migrator nologin noinherit "
                "nosuperuser nocreatedb nocreaterole noreplication nobypassrls"
            )
            connection.execute(
                "create role agent_execution_worker_runtime login noinherit "
                "nosuperuser nocreatedb nocreaterole noreplication nobypassrls "
                "password 'malicious-collision-password'"
            )
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                "with admin false"
            )
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                "with inherit false"
            )
            connection.execute(
                "grant agent_execution_worker_owner to agent_execution_worker_migrator "
                "with set true granted by current_user"
            )
            connection.execute(
                "create database agent_execution_worker "
                "owner agent_execution_worker_owner template template0 encoding 'UTF8'"
            )

        result = _run_bootstrap(paths)

        assert result.returncode != 0
        assert "database or acl collision" in result.stderr
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select datallowconn from pg_database "
                "where datname='agent_execution_worker'"
            ).fetchone() == (True,)
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
def test_bootstrap_grants_only_required_runtime_update_columns(
    control_database, tmp_path: Path
) -> None:
    admin_url = control_database["cluster_admin"]
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url)
    try:
        result = _run_bootstrap(paths)
        assert result.returncode == 0, result.stderr
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            table_grants = runtime.execute(
                "select table_name,privilege_type "
                "from information_schema.role_table_grants "
                "where grantee=current_user and table_schema='execution_worker' "
                "order by table_name,privilege_type"
            ).fetchall()
            column_updates = runtime.execute(
                "select table_name,column_name "
                "from information_schema.role_column_grants "
                "where grantee=current_user and table_schema='execution_worker' "
                "and privilege_type='UPDATE' order by table_name,column_name"
            ).fetchall()
            assert all(privilege != "UPDATE" for _table, privilege in table_grants)
            assert column_updates == [
                ("event_outbox", "delivered_at"),
                ("local_runs", "dispatched_at"),
                ("local_runs", "state"),
                ("local_runs", "terminal_at"),
            ]
            assert runtime.execute(
                "select count(*) from pg_default_acl acl "
                "where acl.defaclrole in (select oid from pg_roles where rolname=any(%s)) "
                "or exists (select 1 from aclexplode(acl.defaclacl) item "
                "where item.grantee in (select oid from pg_roles where rolname=any(%s)))",
                (list(WORKER_ROLES), list(WORKER_ROLES)),
            ).fetchone() == (0,)
    finally:
        _drop_worker_test_state(admin_url)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "collision",
    [
        "database-owner",
        "database-acl",
        "table-acl",
        "column-acl",
        "default-acl",
        "foreign-server-acl",
    ],
)
def test_bootstrap_rejects_worker_privilege_in_any_other_database(
    control_database, tmp_path: Path, collision: str
) -> None:
    admin_url = control_database["cluster_admin"]
    extra_database = "flywheel_collision_test"
    paths = _bootstrap_test_environment(control_database, tmp_path)
    _drop_worker_test_state(admin_url, extra_database)
    try:
        first = _run_bootstrap(paths)
        assert first.returncode == 0, first.stderr
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            run_id = uuid.uuid4()
            runtime.execute(
                "insert into execution_worker.local_runs("
                "run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at"
                ") values (%s,%s,'hr-bot',9101,%s,'leased',now())",
                (run_id, uuid.uuid4(), b"c" * 32),
            )
            runtime.execute(
                "insert into execution_worker.event_outbox(run_id,seq,event_json) "
                "values (%s,1,'{\"cross_database_marker\":true}')",
                (run_id,),
            )
            runtime.commit()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            if collision == "database-owner":
                connection.execute(
                    "create database flywheel_collision_test "
                    "owner agent_execution_worker_owner"
                )
            else:
                connection.execute("create database flywheel_collision_test")
                if collision == "database-acl":
                    connection.execute(
                        "grant connect on database flywheel_collision_test "
                        "to agent_execution_worker_runtime"
                    )
                elif collision == "foreign-server-acl":
                    connection.execute(
                        "create foreign data wrapper execution_worker_collision_fdw"
                    )
                    connection.execute(
                        "create server execution_worker_collision_server "
                        "foreign data wrapper execution_worker_collision_fdw"
                    )
                    connection.execute(
                        "grant usage on foreign server execution_worker_collision_server "
                        "to agent_execution_worker_runtime"
                    )
        if collision in {"table-acl", "column-acl", "default-acl"}:
            extra_url = (
                f"postgresql://control_test_admin@127.0.0.1:"
                f"{control_database['port']}/{extra_database}"
            )
            with psycopg.connect(extra_url, autocommit=True) as connection:
                connection.execute("create table collision_table(value integer)")
                if collision == "table-acl":
                    connection.execute(
                        "grant select on collision_table "
                        "to agent_execution_worker_runtime"
                    )
                elif collision == "column-acl":
                    connection.execute(
                        "grant update(value) on collision_table "
                        "to agent_execution_worker_runtime"
                    )
                else:
                    connection.execute(
                        "alter default privileges grant select on tables "
                        "to agent_execution_worker_runtime"
                    )

        rerun = _run_bootstrap(paths)

        assert rerun.returncode != 0
        assert "cross-database worker privilege collision" in rerun.stderr
        with psycopg.connect(paths[2].read_text().strip()) as runtime:
            assert runtime.execute(
                "select event_json from execution_worker.event_outbox "
                "where run_id=%s",
                (run_id,),
            ).fetchone() == ({"cross_database_marker": True},)
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select 1 from pg_database where datname=%s",
                (extra_database,),
            ).fetchone() == (1,)
    finally:
        _drop_worker_test_state(admin_url, extra_database)


def test_local_database_bootstrap_normalizes_localhost_runtime_dsn() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8")

    assert "runtime_host=127.0.0.1" in script
    assert "parsed.hostname not in {\"127.0.0.1\", \"localhost\"}" in script
    assert "@127.0.0.1:" in script
    writer = re.search(
        r'value = f"postgresql://agent_execution_worker_runtime:[^\n]+', script
    )
    assert writer is not None and "localhost" not in writer.group(0)


@pytest.mark.postgres
def test_database_bootstrap_first_run_and_localhost_rerun_preserve_password_and_outbox(
    control_database, tmp_path: Path
) -> None:
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    platform = tmp_path / "platform"
    local = platform / "deploy/local-execution-worker"
    schema_target = platform / "backend/app/execution_relay/worker_schema.sql"
    local.mkdir(parents=True)
    schema_target.parent.mkdir(parents=True)
    schema_target.write_bytes(
        (ROOT / "backend/app/execution_relay/worker_schema.sql").read_bytes()
    )
    copied = local / BOOTSTRAP.name
    copied.write_text(
        BOOTSTRAP.read_text(encoding="utf-8").replace("agentops", current_user),
        encoding="utf-8",
    )
    copied.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    owner_dsn = private / "owner-dsn"
    port = control_database["port"]
    owner_dsn.write_text(
        f"postgresql://control_test_admin:secret@localhost:{port}/postgres\n"
    )
    owner_dsn.chmod(0o600)
    runtime_dsn = private / "runtime-dsn"
    environment = {
        **os.environ,
        "PLATFORM_LOCAL_POSTGRES17_PSQL": subprocess.check_output(
            ["/bin/sh", "-c", "command -v psql"], text=True
        ).strip(),
        "PLATFORM_LOCAL_PYTHON3": sys.executable,
    }
    admin_url = control_database["cluster_admin"]
    roles = (
        "agent_execution_worker_owner",
        "agent_execution_worker_migrator",
        "agent_execution_worker_runtime",
    )
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute("drop database if exists agent_execution_worker")
        for role in roles:
            connection.execute(
                psycopg.sql.SQL("drop role if exists {}").format(
                    psycopg.sql.Identifier(role)
                )
            )
    try:
        first = subprocess.run(
            ["/bin/bash", str(copied), str(owner_dsn), str(runtime_dsn)],
            text=True,
            capture_output=True,
            env=environment,
        )
        assert first.returncode == 0, first.stderr
        original_dsn = runtime_dsn.read_bytes()
        assert b"@127.0.0.1:" in original_dsn
        with psycopg.connect(runtime_dsn.read_text().strip()) as runtime:
            run_id = uuid.uuid4()
            runtime.execute(
                "insert into execution_worker.local_runs("
                "run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at"
                ") values (%s,%s,'hr-bot',9101,%s,'leased',now())",
                (run_id, uuid.uuid4(), b"x" * 32),
            )
            runtime.execute(
                "insert into execution_worker.event_outbox(run_id,seq,event_json) "
                "values (%s,1,'{}')",
                (run_id,),
            )
            runtime.commit()
        second = subprocess.run(
            ["/bin/bash", str(copied), str(owner_dsn), str(runtime_dsn)],
            text=True,
            capture_output=True,
            env=environment,
        )
        assert second.returncode == 0, second.stderr
        assert first.stdout == second.stdout == "EXECUTION_WORKER_DATABASE_READY version=1\n"
        assert runtime_dsn.read_bytes() == original_dsn
        with psycopg.connect(runtime_dsn.read_text().strip()) as runtime:
            assert runtime.execute(
                "select count(*) from execution_worker.event_outbox"
            ).fetchone() == (1,)
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select count(*) from pg_auth_members membership "
                "join pg_roles role on role.oid=membership.roleid "
                "join pg_roles member on member.oid=membership.member "
                "where role.rolname='agent_execution_worker_migrator' "
                "and member.rolname=current_user"
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            if connection.execute(
                "select 1 from pg_roles where rolname='agent_execution_worker_migrator'"
            ).fetchone():
                connection.execute(
                    "revoke agent_execution_worker_migrator from control_test_admin"
                )
            connection.execute("drop database if exists agent_execution_worker")
            for role in roles:
                connection.execute(
                    psycopg.sql.SQL("drop role if exists {}").format(
                        psycopg.sql.Identifier(role)
                    )
                )


def test_key_binding_manifest_is_non_executable_and_bounded() -> None:
    value = plistlib.loads(PLIST.read_bytes())
    raw = PLIST.read_text(encoding="utf-8")

    assert value["Label"] == "orbbec-agent-execution-worker"
    assert set(value) == {"Label", "EnvironmentVariables"}
    environment = value["EnvironmentVariables"]
    assert environment == {
        "PLATFORM_WORKER_ID": "agentops-mac-primary",
        "PLATFORM_WORKER_KEY_ID": "worker-v1",
        "PLATFORM_WORKER_PRIVATE_KEY_FILE": "/Users/agentops/AgentRuntime/private/execution-worker-ed25519.key",
    }
    for launch_key in ("ProgramArguments", "RunAtLoad", "KeepAlive", "ProcessType"):
        assert launch_key not in value
    for forbidden in ("begin private key", "postgresql://", "bearer ", "password="):
        assert forbidden not in raw.lower()


def test_cloud_production_acceptance_uses_canonical_current_worker_key() -> None:
    script = CLOUD_ACCEPTANCE.read_text(encoding="utf-8")
    assert "execution-worker-public-keyring.json" in script
    assert "worker-v[1-9][0-9]*" in script
    assert '-v expected_key_id="$expected_key_id"' in script
    assert "worker_key.key_id=:'expected_key_id'" in script
    assert 'value["key_id"] != "worker-v1"' not in script
    assert '"$expected_key_id" == "worker-v1"' not in script
    assert "worker_key.key_id='worker-v1'" not in script


def test_local_key_rotator_is_strict_bounded_and_noninteractive() -> None:
    source = ROTATOR.read_text(encoding="utf-8")
    lowered = source.lower()
    assert source.startswith("#!/usr/bin/env python3\n")
    assert 're.compile(r"worker-v[1-9][0-9]*\\Z")' in source
    for action in ("prepare", "abort", "activate", "rollback", "finalize"):
        assert f'"{action}"' in source
    for asset in (
        "execution-worker-ed25519.next.key",
        "execution-worker-public.next.json",
        'execution-worker-key-binding.next.plist',
        "execution-worker-ed25519.previous.key",
        "execution-worker-public.previous.json",
        'execution-worker-key-binding.previous.plist',
        "execution-worker-key-rotation-state.json",
        "execution-worker-key-rotation.lock",
    ):
        assert asset in source
    assert "pwd.getpwuid(os.getuid()).pw_name" in source
    assert "os.replace(" in source and "os.fsync(" in source
    assert "O_NOFOLLOW" in source and "0o600" in source and "0o700" in source
    for forbidden in ("keychain", "/usr/bin/security", "sudo", "osascript", "password"):
        assert forbidden not in lowered
    compile(source, str(ROTATOR), "exec")


def test_cloud_key_rotation_is_locked_and_prepared_before_database_mutation() -> None:
    runbook = (ROOT / "docs/runbooks/agent-execution-relay.md").read_text(
        encoding="utf-8"
    )
    section = runbook.split("## Key rotation", 1)[1].split(
        "## Worker revocation", 1
    )[0]
    helper = CLOUD_ROTATOR.read_text(encoding="utf-8")
    prepare = section.index('"$cloud_rotator" prepare worker-v2')
    activate = section.index('"$cloud_rotator" activate worker-v2')
    assert prepare < activate
    assert helper.index("lock = _acquire_lock()") < helper.index('"prepare": _prepare')
    assert helper.index('_write_state(state, "prepared")') < helper.index(
        '"add-key", WORKER_ID'
    )
    assert 'if STATE.exists() or STATE.is_symlink()' in helper
    assert "_validate_document(path, key_id, digest, fingerprint)" in helper
    assert "After an SSH disconnect" in section
    assert '"$cloud_rotator" recover worker-v2' in section
    assert '"$cloud_rotator" rollback worker-v2' in section
    assert '"${maintenance[@]}"' not in section


def test_cloud_key_rotation_commit_boundary_forbids_unsafe_rollback() -> None:
    runbook = (ROOT / "docs/runbooks/agent-execution-relay.md").read_text(
        encoding="utf-8"
    )
    section = runbook.split("## Key rotation", 1)[1].split(
        "## Worker revocation", 1
    )[0]
    helper = CLOUD_ROTATOR.read_text(encoding="utf-8")
    accepted = helper.index('_write_state(value, "accepted")')
    committing = helper.index('_write_state(value, "committing")')
    revoke_old = helper.index('"revoke-key", WORKER_ID', committing)
    old_revoked = helper.index('_write_state(value, "old_revoked")')
    assert accepted < committing < revoke_old < old_revoked
    assert 'value["phase"] not in {"accepted", "committing", "old_revoked"}' in helper
    assert "resume forward only" in section
    assert "If local `finalize` fails, rerun only `finalize worker-v2`" in section
    assert "do not run local" in section and "`rollback`" in section


def _cloud_rotation_test_environment(tmp_path: Path):
    current_uid = os.getuid()
    platform_root = tmp_path / "platform"
    private_root = platform_root / "private"
    incoming_root = tmp_path / "incoming"
    releases_root = platform_root / "releases"
    release = releases_root / ("a" * 40)
    for directory in (platform_root, private_root, incoming_root, releases_root, release):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    release.chmod(0o700)
    current = platform_root / "current"
    current.symlink_to(release)
    compose = release / "deploy/cloud/compose.yaml"
    compose.parent.mkdir(parents=True, mode=0o700)
    (release / "deploy").chmod(0o700)
    compose.parent.chmod(0o700)
    compose.write_text("services: {}\n", encoding="utf-8")
    compose.chmod(0o600)
    for required in (private_root / "platform.env", private_root / "control-maintenance-database-url"):
        required.write_text("bounded-test-value\n", encoding="utf-8")
        required.chmod(0o600)

    def public_document(key_id: str, byte: int) -> tuple[bytes, str]:
        public = bytes([byte]) * 32
        encoded = base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")
        document = {
            "worker_id": "agentops-mac-primary",
            "key_id": key_id,
            "public_key_base64url": encoded,
            "allowed_agent_ids": AGENTS,
        }
        return (json.dumps(document, sort_keys=True) + "\n").encode(), hashlib.sha256(public).hexdigest()

    current, current_fingerprint = public_document("worker-v1", 1)
    target, target_fingerprint = public_document("worker-v2", 2)
    keyring = private_root / "execution-worker-public-keyring.json"
    keyring.write_bytes(current)
    keyring.chmod(0o600)
    incoming = incoming_root / "execution-worker-public-worker-v2.json"
    incoming.write_bytes(target)
    incoming.chmod(0o600)
    database = tmp_path / "database.json"
    database.write_text(
        json.dumps({"worker-v1": {"status": "active", "fingerprint": current_fingerprint}}),
        encoding="utf-8",
    )
    maintenance_log = tmp_path / "maintenance.log"
    fail_after = tmp_path / "fail-after"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

if any(name in os.environ for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "PYTHONPATH")):
    raise SystemExit(97)
arguments = sys.argv[1:]
if arguments[0] == "compose":
    print("platform-api-container")
    raise SystemExit(0)
if arguments[0] == "inspect":
    print("platform-api-image")
    raise SystemExit(0)
database_path = Path(os.environ["FAKE_ROTATION_DATABASE"])
database = json.loads(database_path.read_text())
log = Path(os.environ["FAKE_ROTATION_LOG"])
if "--inspect-execution-worker-key" in arguments:
    key_id = arguments[-1]
    value = database.get(key_id)
    print(json.dumps({"key_id": key_id, "status": "absent", "public_key_sha256": None} if value is None else {"key_id": key_id, "status": value["status"], "public_key_sha256": value["fingerprint"]}, sort_keys=True))
    raise SystemExit(0)
if "--inspect-execution-worker" in arguments:
    statuses = {value["status"] for value in database.values()}
    status = "revoked" if statuses and statuses == {"revoked"} else "active"
    print(json.dumps({"worker_id": "agentops-mac-primary", "status": status}, sort_keys=True))
    raise SystemExit(0)
if "--inspect-execution-worker-inventory" in arguments:
    print(json.dumps([
        {"key_id": key_id, "status": value["status"], "public_key_sha256": value["fingerprint"]}
        for key_id, value in sorted(database.items())
    ], sort_keys=True))
    raise SystemExit(0)
if "add-key" in arguments:
    action = "add"
    key_id = "worker-v2"
    existing = database.get(key_id)
    fingerprint = os.environ["FAKE_ROTATION_TARGET_FINGERPRINT"]
    if existing is not None and existing != {"status": "active", "fingerprint": fingerprint}:
        raise SystemExit(1)
    database[key_id] = {"status": "active", "fingerprint": fingerprint}
elif "revoke-key" in arguments:
    action = "revoke-" + arguments[-2]
    key_id = arguments[-2]
    existing = database.get(key_id)
    if existing is None or existing["status"] != "active":
        raise SystemExit(1)
    existing["status"] = "revoked"
elif "revoke-worker" in arguments:
    action = "revoke-worker"
    if not any(existing["status"] == "active" for existing in database.values()):
        raise SystemExit(1)
    for existing in database.values():
        if existing["status"] == "active":
            existing["status"] = "revoked"
else:
    raise SystemExit(1)
database_path.write_text(json.dumps(database))
with log.open("a") as stream:
    stream.write(action + "\\n")
fail_after = Path(os.environ["FAKE_ROTATION_FAIL_AFTER"])
failure = fail_after.read_text().strip() if fail_after.exists() else ""
if failure == "kill-" + action:
    fail_after.unlink()
    os.kill(os.getpid(), 9)
if failure == action:
    fail_after.unlink()
    raise SystemExit(71)
print("EXECUTION_WORKER_MAINTENANCE_OK")
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o700)
    helper = tmp_path / CLOUD_ROTATOR.name
    source = CLOUD_ROTATOR.read_text(encoding="utf-8")
    source = source.replace("REQUIRED_UID = 0", f"REQUIRED_UID = {current_uid}")
    source = source.replace(
        'PLATFORM_ROOT = Path("/opt/orbbec-agent-platform")',
        f"PLATFORM_ROOT = Path({str(platform_root)!r})",
    )
    source = source.replace(
        'INCOMING_ROOT = Path("/root")', f"INCOMING_ROOT = Path({str(incoming_root)!r})"
    )
    source = source.replace('DOCKER = "/usr/bin/docker"', f"DOCKER = {str(fake_docker)!r}")
    source = source.replace(
        'HOST_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}',
        "HOST_ENV = " + repr({
            "PATH": "/usr/bin:/bin",
            "LC_ALL": "C.UTF-8",
            "FAKE_ROTATION_DATABASE": str(database),
            "FAKE_ROTATION_LOG": str(maintenance_log),
            "FAKE_ROTATION_FAIL_AFTER": str(fail_after),
            "FAKE_ROTATION_TARGET_FINGERPRINT": target_fingerprint,
        }),
    )
    source = source.replace(
        'print("EXECUTION_WORKER_CLOUD_ROTATION_FAILED", file=sys.stderr)',
        'import traceback; traceback.print_exc()',
    )
    helper.write_text(source, encoding="utf-8")
    helper.chmod(0o700)
    environment = {
        **os.environ,
        "DOCKER_HOST": "tcp://attacker.invalid:2375",
        "DOCKER_CONTEXT": "attacker",
        "PYTHONPATH": str(tmp_path / "attacker-python"),
        "FAKE_ROTATION_DATABASE": str(database),
        "FAKE_ROTATION_LOG": str(maintenance_log),
        "FAKE_ROTATION_FAIL_AFTER": str(fail_after),
        "FAKE_ROTATION_TARGET_FINGERPRINT": target_fingerprint,
    }
    return {
        "helper": helper,
        "environment": environment,
        "database": database,
        "log": maintenance_log,
        "fail_after": fail_after,
        "keyring": keyring,
        "current": current,
        "target": target,
        "state": private_root / "execution-worker-key-rotation-state.json",
        "previous": private_root / "execution-worker-public-keyring.previous.json",
        "state_part": private_root / "execution-worker-key-rotation-state.json.part",
        "deploy_state": private_root / "execution-worker-keyring-deploy-state.json",
        "keyring_part": private_root / "execution-worker-public-keyring.json.part",
        "staged": private_root / "execution-worker-public-keyring.next.json",
        "lock": private_root / "execution-worker-key-rotation.lock",
    }


def _run_cloud_rotation(paths, action: str):
    return subprocess.run(
        [sys.executable, str(paths["helper"]), action, "worker-v2"],
        text=True,
        capture_output=True,
        env=paths["environment"],
    )


def _run_cloud_worker_revoke(paths):
    return subprocess.run(
        [sys.executable, str(paths["helper"]), "revoke-worker"],
        text=True,
        capture_output=True,
        env=paths["environment"],
    )


def test_cloud_rotation_helper_is_root_only_fixed_and_bounded() -> None:
    source = CLOUD_ROTATOR.read_text(encoding="utf-8")
    installer = CLOUD_KEYRING_INSTALLER.read_text(encoding="utf-8")
    assert source.startswith("#!/usr/bin/python3\n")
    assert installer.startswith("#!/usr/bin/python3\n")
    assert "REQUIRED_UID = 0" in source
    assert 'PLATFORM_ROOT = Path("/opt/orbbec-agent-platform")' in source
    assert 'INCOMING_ROOT = Path("/root")' in source
    assert 'DOCKER = "/usr/bin/docker"' in source
    for action in ("prepare", "activate", "mark-accepted", "commit", "rollback", "finalize", "recover"):
        assert f'"{action}"' in source
    assert "os.environ" not in source
    assert "DOCKER_HOST" not in source and "DOCKER_CONTEXT" not in source
    assert "PYTHONPATH" not in source
    assert "env=HOST_ENV" in source
    assert "O_NOFOLLOW" in source and "fcntl.flock" in source


def test_cloud_rotation_helper_recovers_add_and_commit_response_loss(tmp_path: Path) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    assert _run_cloud_rotation(paths, "prepare").returncode == 0
    paths["fail_after"].write_text("add", encoding="utf-8")
    assert _run_cloud_rotation(paths, "activate").returncode == 1
    assert json.loads(paths["state"].read_text())["phase"] == "adding"
    assert _run_cloud_rotation(paths, "recover").returncode == 0
    assert paths["keyring"].read_bytes() == paths["target"]
    assert _run_cloud_rotation(paths, "mark-accepted").returncode == 0
    paths["fail_after"].write_text("revoke-worker-v1", encoding="utf-8")
    assert _run_cloud_rotation(paths, "commit").returncode == 1
    assert json.loads(paths["state"].read_text())["phase"] == "committing"
    assert _run_cloud_rotation(paths, "recover").returncode == 0
    assert json.loads(paths["state"].read_text())["phase"] == "old_revoked"
    assert paths["log"].read_text().splitlines() == ["add", "revoke-worker-v1"]


def test_cloud_rotation_helper_rollback_is_reentrant_and_never_burns_absent_key(
    tmp_path: Path,
) -> None:
    absent = _cloud_rotation_test_environment(tmp_path / "absent")
    assert _run_cloud_rotation(absent, "prepare").returncode == 0
    assert _run_cloud_rotation(absent, "rollback").returncode == 0
    assert not absent["log"].exists()
    assert absent["keyring"].read_bytes() == absent["current"]
    assert not absent["state"].exists() and not absent["previous"].exists()

    active = _cloud_rotation_test_environment(tmp_path / "active")
    assert _run_cloud_rotation(active, "prepare").returncode == 0
    assert _run_cloud_rotation(active, "activate").returncode == 0
    active["fail_after"].write_text("revoke-worker-v2", encoding="utf-8")
    assert _run_cloud_rotation(active, "rollback").returncode == 1
    assert json.loads(active["state"].read_text())["phase"] == "revoking"
    assert active["previous"].read_bytes() == active["current"]
    assert active["keyring"].read_bytes() == active["current"]
    assert _run_cloud_rotation(active, "recover").returncode == 0
    assert active["log"].read_text().splitlines() == ["add", "revoke-worker-v2"]
    assert not active["state"].exists() and not active["previous"].exists()


def test_cloud_rotation_helper_recovers_pre_state_renames_and_zero_byte_parts(
    tmp_path: Path,
) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    paths["previous"].write_bytes(paths["current"])
    paths["previous"].chmod(0o600)
    paths["staged"].write_bytes(paths["target"])
    paths["staged"].chmod(0o600)
    paths["state_part"].touch(mode=0o600)
    paths["keyring_part"].touch(mode=0o600)

    prepared = _run_cloud_rotation(paths, "prepare")

    assert prepared.returncode == 0, prepared.stderr
    assert json.loads(paths["state"].read_text())["phase"] == "prepared"
    assert not paths["state_part"].exists() and not paths["keyring_part"].exists()


def test_cloud_rotation_helper_recover_rejects_stable_waiting_phase(
    tmp_path: Path,
) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    assert _run_cloud_rotation(paths, "prepare").returncode == 0

    recovered = _run_cloud_rotation(paths, "recover")

    assert recovered.returncode == 1
    assert json.loads(paths["state"].read_text())["phase"] == "prepared"


def test_cloud_rotation_helper_finalize_cleans_missing_residuals_and_state_last(
    tmp_path: Path,
) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    for action in ("prepare", "activate", "mark-accepted", "commit"):
        result = _run_cloud_rotation(paths, action)
        assert result.returncode == 0, result.stderr
    paths["previous"].unlink()
    paths["keyring_part"].touch(mode=0o600)

    finalized = _run_cloud_rotation(paths, "finalize")

    assert finalized.returncode == 0, finalized.stderr
    assert paths["keyring"].read_bytes() == paths["target"]
    assert not paths["state"].exists()
    assert not paths["keyring_part"].exists()


@pytest.mark.parametrize("killed_after", ["previous", "staged", "state"])
def test_cloud_rollback_cleanup_recovers_after_each_unlink(
    tmp_path: Path, killed_after: str
) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    assert _run_cloud_rotation(paths, "prepare").returncode == 0
    original = paths["helper"].read_text(encoding="utf-8")
    source = original
    ordered = {
        "previous": "    _unlink(PREVIOUS)\n",
        "staged": "    _unlink(STAGED)\n",
        "state": "    _unlink(STATE)\n",
    }
    needle = ordered[killed_after]
    source = source.replace(
        needle,
        needle + "    os.kill(os.getpid(), 9)\n",
        1,
    )
    paths["helper"].write_text(source, encoding="utf-8")
    killed = _run_cloud_rotation(paths, "rollback")
    assert killed.returncode < 0
    paths["helper"].write_text(original, encoding="utf-8")

    recovered = _run_cloud_rotation(
        paths, "rollback" if killed_after == "state" else "recover"
    )
    assert recovered.returncode == 0, recovered.stderr
    assert not paths["state"].exists()
    assert not paths["previous"].exists()


def test_cloud_rollback_revoked_phase_does_not_require_deleted_previous(
    tmp_path: Path,
) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    assert _run_cloud_rotation(paths, "prepare").returncode == 0
    original = paths["helper"].read_text(encoding="utf-8")
    paths["helper"].write_text(
        original.replace(
            "def _cleanup(value: dict[str, object]) -> None:\n",
            "def _cleanup(value: dict[str, object]) -> None:\n    raise RotationError\n",
            1,
        ),
        encoding="utf-8",
    )
    assert _run_cloud_rotation(paths, "rollback").returncode == 1
    assert json.loads(paths["state"].read_text())["phase"] == "revoked"
    paths["previous"].unlink()
    paths["helper"].write_text(original, encoding="utf-8")

    recovered = _run_cloud_rotation(paths, "recover")

    assert recovered.returncode == 0, recovered.stderr
    assert not paths["state"].exists()


@pytest.mark.parametrize("killed_after", ["previous", "staged", "state"])
def test_cloud_finalize_cleanup_recovers_after_each_unlink(
    tmp_path: Path, killed_after: str
) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    for action in ("prepare", "activate", "mark-accepted", "commit"):
        assert _run_cloud_rotation(paths, action).returncode == 0
    original = paths["helper"].read_text(encoding="utf-8")
    needle = {
        "previous": "    _unlink(PREVIOUS)\n",
        "staged": "    _unlink(STAGED)\n",
        "state": "    _unlink(STATE)\n",
    }[killed_after]
    paths["helper"].write_text(
        original.replace(needle, needle + "    os.kill(os.getpid(), 9)\n", 1),
        encoding="utf-8",
    )

    killed = _run_cloud_rotation(paths, "finalize")

    assert killed.returncode < 0
    paths["helper"].write_text(original, encoding="utf-8")
    recovered = _run_cloud_rotation(
        paths, "finalize" if killed_after == "state" else "recover"
    )
    assert recovered.returncode == 0, recovered.stderr
    assert paths["keyring"].read_bytes() == paths["target"]
    assert not paths["state"].exists()
    assert not paths["previous"].exists()


def test_cloud_no_state_finalize_rejects_dual_active_inventory(
    tmp_path: Path,
) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    assert _run_cloud_rotation(paths, "prepare").returncode == 0
    assert _run_cloud_rotation(paths, "activate").returncode == 0
    paths["state"].unlink()
    paths["previous"].unlink()
    paths["staged"].unlink()

    finalized = _run_cloud_rotation(paths, "finalize")

    assert finalized.returncode == 1


def test_cloud_no_state_rollback_rejects_unrelated_active_key(
    tmp_path: Path,
) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    database = json.loads(paths["database"].read_text())
    database["worker-v3"] = {"status": "active", "fingerprint": "3" * 64}
    paths["database"].write_text(json.dumps(database), encoding="utf-8")

    rolled_back = _run_cloud_rotation(paths, "rollback")

    assert rolled_back.returncode == 1


def test_runbook_public_handoff_validators_execute_with_exact_schema_and_base64(
    tmp_path: Path,
) -> None:
    runbook = (ROOT / "docs/runbooks/agent-execution-relay.md").read_text(
        encoding="utf-8"
    )
    snippets = [
        source
        for source in re.findall(r"<<'PY'\n(.*?)\nPY", runbook, re.DOTALL)
        if "hashlib.sha256(public).hexdigest()" in source
    ]
    assert len(snippets) == 2
    public = bytes([9]) * 32
    encoded = base64.urlsafe_b64encode(public).decode().rstrip("=")
    document = {
        "worker_id": "agentops-mac-primary",
        "key_id": "worker-v2",
        "public_key_base64url": encoded,
        "allowed_agent_ids": AGENTS,
    }
    source_path = tmp_path / "source.json"
    staged_path = tmp_path / "staged.json"
    raw = (json.dumps(document) + "\n").encode()
    source_path.write_bytes(raw)
    staged_path.write_bytes(raw)

    first = subprocess.run(
        [sys.executable, "-c", snippets[0], str(source_path), str(staged_path), "worker-v2"],
        text=True,
        capture_output=True,
    )
    second = subprocess.run(
        [sys.executable, "-c", snippets[1], str(staged_path), "worker-v2"],
        text=True,
        capture_output=True,
    )
    fingerprint = hashlib.sha256(public).hexdigest() + "\n"
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout == fingerprint

    for mutation in (
        {**document, "extra": True},
        {**document, "allowed_agent_ids": list(reversed(AGENTS))},
        {**document, "public_key_base64url": encoded[:-1] + "B"},
    ):
        invalid = (json.dumps(mutation) + "\n").encode()
        source_path.write_bytes(invalid)
        staged_path.write_bytes(invalid)
        for command in (
            [sys.executable, "-c", snippets[0], str(source_path), str(staged_path), "worker-v2"],
            [sys.executable, "-c", snippets[1], str(staged_path), "worker-v2"],
        ):
            assert subprocess.run(command, capture_output=True).returncode != 0


def test_runbook_public_handoff_shell_blocks_fail_before_atomic_rename(
    tmp_path: Path,
) -> None:
    runbook = (ROOT / "docs/runbooks/agent-execution-relay.md").read_text(
        encoding="utf-8"
    )
    blocks = re.findall(r"```bash\n(.*?)\n```", runbook, re.DOTALL)
    agent_block = next(block for block in blocks if "source_public=" in block)
    neo_block = next(block for block in blocks if "neo_secret_root=" in block)
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()

    source_root = tmp_path / "agent-runtime"
    source_root.mkdir(mode=0o700)
    source_public = source_root / "execution-worker-public.json"
    source_public.write_text("{}\n", encoding="utf-8")
    source_public.chmod(0o600)
    handoff_root = tmp_path / "handoff"
    handoff_root.mkdir(mode=0o755)
    handoff = handoff_root / "current.json"
    handoff.write_bytes(b"old-handoff\n")
    handoff.chmod(0o444)
    agent_script = (
        agent_block.replace("/Users/agentops/AgentRuntime/execution-worker-public.json", str(source_public))
        .replace("/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python", sys.executable)
        .replace("/Users/agentops/AgentRuntime", str(source_root))
        .replace("/Users/Shared/OrbbecAI-Agent-Platform/execution-worker-public/current.json", str(handoff))
        .replace("/Users/Shared/OrbbecAI-Agent-Platform/execution-worker-public", str(handoff_root))
        .replace("agentops", current_user)
    )

    failed_agent = subprocess.run(
        ["/bin/bash", "-c", agent_script], text=True, capture_output=True
    )

    assert failed_agent.returncode != 0
    assert handoff.read_bytes() == b"old-handoff\n"
    assert not (handoff_root / "current.json.part").exists()

    neo_secret_root = tmp_path / "neo-secrets"
    neo_secret_root.mkdir(mode=0o700)
    neo_keyring = neo_secret_root / "execution-worker-public-keyring.json"
    neo_keyring.write_bytes(b"old-neo-keyring\n")
    neo_keyring.chmod(0o600)
    neo_script = (
        neo_block.replace("/Users/Shared/OrbbecAI-Agent-Platform/execution-worker-public/current.json", str(handoff))
        .replace("/Users/Shared/OrbbecAI-Agent-Platform/execution-worker-public", str(handoff_root))
        .replace('"/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/secrets"', f'"{neo_secret_root}"')
        .replace("/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python", sys.executable)
        .replace("agentops", current_user)
        .replace(" neo", f" {current_user}")
    )

    failed_neo = subprocess.run(
        ["/bin/bash", "-c", neo_script], text=True, capture_output=True
    )

    assert failed_neo.returncode != 0
    assert neo_keyring.read_bytes() == b"old-neo-keyring\n"
    assert not (neo_secret_root / "execution-worker-public-keyring.json.part").exists()


def test_cloud_worker_revocation_fails_closed_on_rotation_state_and_shared_lock(
    tmp_path: Path,
) -> None:
    active = _cloud_rotation_test_environment(tmp_path / "active")
    assert _run_cloud_rotation(active, "prepare").returncode == 0
    blocked = _run_cloud_worker_revoke(active)
    assert blocked.returncode == 1
    assert json.loads(active["database"].read_text())["worker-v1"]["status"] == "active"

    concurrent = _cloud_rotation_test_environment(tmp_path / "concurrent")
    descriptor = os.open(concurrent["lock"], os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = _run_cloud_worker_revoke(concurrent)
        assert blocked.returncode == 1
        assert json.loads(concurrent["database"].read_text())["worker-v1"]["status"] == "active"
    finally:
        os.close(descriptor)

    revoked = _run_cloud_worker_revoke(concurrent)
    assert revoked.returncode == 0, revoked.stderr
    assert json.loads(concurrent["database"].read_text())["worker-v1"]["status"] == "revoked"

    deploy_residue = _cloud_rotation_test_environment(tmp_path / "deploy-residue")
    deploy_residue["deploy_state"].write_text("{}\n", encoding="utf-8")
    deploy_residue["deploy_state"].chmod(0o600)
    assert _run_cloud_worker_revoke(deploy_residue).returncode == 1
    assert _run_cloud_rotation(deploy_residue, "prepare").returncode == 1
    assert json.loads(deploy_residue["database"].read_text())["worker-v1"]["status"] == "active"


def test_cloud_worker_revocation_recovers_killed_maintenance_response(
    tmp_path: Path,
) -> None:
    paths = _cloud_rotation_test_environment(tmp_path)
    paths["fail_after"].write_text("kill-revoke-worker", encoding="utf-8")

    revoked = _run_cloud_worker_revoke(paths)
    repeated = _run_cloud_worker_revoke(paths)

    assert revoked.returncode == repeated.returncode == 0
    assert json.loads(paths["database"].read_text())["worker-v1"]["status"] == "revoked"
    assert paths["log"].read_text().splitlines() == ["revoke-worker"]


def _cloud_keyring_installer_environment(tmp_path: Path):
    platform = tmp_path / "platform"
    private = platform / "private"
    platform.mkdir(mode=0o700)
    private.mkdir(mode=0o700)
    helper = tmp_path / CLOUD_KEYRING_INSTALLER.name
    source = CLOUD_KEYRING_INSTALLER.read_text(encoding="utf-8")
    source = source.replace("REQUIRED_UID = 0", f"REQUIRED_UID = {os.getuid()}")
    source = source.replace(
        'PLATFORM_ROOT = Path("/opt/orbbec-agent-platform")',
        f"PLATFORM_ROOT = Path({str(platform)!r})",
    )
    staging = tmp_path / "data" / "staging" / "orbbec-agent-platform"
    source = source.replace(
        'STAGING_ROOT = Path("/data/staging/orbbec-agent-platform")',
        f"STAGING_ROOT = Path({str(staging)!r})",
    )
    remote = tmp_path / "remote-stage.sh"
    ready = tmp_path / "cutover-ready"
    release = tmp_path / "cutover-release"
    remote.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "[[ -z ${DOCKER_HOST+x} && -z ${DOCKER_CONTEXT+x} && -z ${PYTHONPATH+x} ]]\n"
        f"/usr/bin/touch {ready}\n"
        f"while [[ ! -e {release} ]]; do /bin/sleep 0.01; done\n",
        encoding="utf-8",
    )
    remote.chmod(0o700)
    source = source.replace(
        'REMOTE_STAGE = Path("/opt/orbbec-agent-platform/bin/remote-stage.sh")',
        f"REMOTE_STAGE = Path({str(remote)!r})",
    )
    helper.write_text(source, encoding="utf-8")
    helper.chmod(0o700)
    deployment_id = "e" * 32
    deploy_input = private / "deploy-input.lock"
    deploy_input.mkdir(mode=0o700)
    deploy_owner = deploy_input / "owner.json"
    deploy_owner.write_text(
        json.dumps(
            {"deployment_id": deployment_id, "release_sha": "b" * 40},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    deploy_owner.chmod(0o600)
    public = bytes([7]) * 32
    document = (
        json.dumps(
            {
                "worker_id": "agentops-mac-primary",
                "key_id": "worker-v2",
                "public_key_base64url": base64.urlsafe_b64encode(public)
                .decode()
                .rstrip("="),
                "allowed_agent_ids": AGENTS,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    return {
        "helper": helper,
        "private": private,
        "document": document,
        "keyring": private / "execution-worker-public-keyring.json",
        "state": private / "execution-worker-key-rotation-state.json",
        "deploy_state": private / "execution-worker-keyring-deploy-state.json",
        "lock": private / "execution-worker-key-rotation.lock",
        "staged": staging / deployment_id / "execution-worker-public-keyring.json",
        "ready": ready,
        "release": release,
        "deployment_id": deployment_id,
    }


def _run_cloud_keyring_installer(paths, action: str = "stage"):
    arguments = [sys.executable, str(paths["helper"]), action, "b" * 40]
    if action == "cutover":
        arguments.append("c" * 64)
    arguments.append(paths["deployment_id"])
    return subprocess.run(
        arguments,
        input=paths["document"] if action == "stage" else None,
        capture_output=True,
    )


def test_cloud_deploy_stages_keyring_without_mutating_canonical(
    tmp_path: Path,
) -> None:
    paths = _cloud_keyring_installer_environment(tmp_path)
    paths["keyring"].write_bytes(b"old\n")
    paths["keyring"].chmod(0o600)

    staged = _run_cloud_keyring_installer(paths)

    assert staged.returncode == 0, staged.stderr
    assert paths["keyring"].read_bytes() == b"old\n"
    assert paths["staged"].read_bytes() == paths["document"]
    assert _mode(paths["staged"]) == 0o600


def test_cloud_deploy_cutover_holds_shared_lock_and_fails_on_active_state(
    tmp_path: Path,
) -> None:
    paths = _cloud_keyring_installer_environment(tmp_path)
    assert _run_cloud_keyring_installer(paths).returncode == 0
    paths["state"].write_text("{}\n", encoding="utf-8")
    paths["state"].chmod(0o600)

    active = _run_cloud_keyring_installer(paths, "cutover")

    assert active.returncode == 1
    paths["state"].unlink()
    process = subprocess.Popen(
        [
            sys.executable,
            str(paths["helper"]),
            "cutover",
            "b" * 40,
            "c" * 64,
            paths["deployment_id"],
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "DOCKER_HOST": "tcp://attacker.invalid:2375",
            "DOCKER_CONTEXT": "attacker",
            "PYTHONPATH": str(tmp_path / "attacker-python"),
        },
    )
    _wait_for_file(paths["ready"])
    descriptor = os.open(paths["lock"], os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)
    paths["release"].touch()
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, (stdout, stderr)
    descriptor = os.open(paths["lock"], os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)


def test_cloud_deploy_stage_cannot_replace_same_release_during_cutover(
    tmp_path: Path,
) -> None:
    paths = _cloud_keyring_installer_environment(tmp_path)
    assert _run_cloud_keyring_installer(paths).returncode == 0
    original = paths["staged"].read_bytes()
    process = subprocess.Popen(
        [
            sys.executable,
            str(paths["helper"]),
            "cutover",
            "b" * 40,
            "c" * 64,
            paths["deployment_id"],
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for_file(paths["ready"])
    replacement = paths["document"].replace(b"worker-v2", b"worker-v3")

    concurrent_stage = subprocess.run(
        [
            sys.executable,
            str(paths["helper"]),
            "stage",
            "b" * 40,
            paths["deployment_id"],
        ],
        input=replacement,
        capture_output=True,
    )

    assert concurrent_stage.returncode == 1
    assert paths["staged"].read_bytes() == original
    paths["release"].touch()
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, (stdout, stderr)


def test_cloud_deploy_stage_rejects_completed_deploy_journal_without_mutation(
    tmp_path: Path,
) -> None:
    paths = _cloud_keyring_installer_environment(tmp_path)
    assert _run_cloud_keyring_installer(paths).returncode == 0
    original = paths["staged"].read_bytes()
    paths["deploy_state"].write_text('{"phase":"completed"}\n', encoding="utf-8")
    paths["deploy_state"].chmod(0o600)

    repeated = _run_cloud_keyring_installer(paths)

    assert repeated.returncode == 1
    assert paths["staged"].read_bytes() == original


@pytest.mark.parametrize(
    "residual",
    ["state", "deploy_state", "deploy_state_part", "deploy_backup"],
)
def test_cloud_deploy_discard_is_token_bound_and_rejects_deploy_residuals(
    tmp_path: Path, residual: str
) -> None:
    paths = _cloud_keyring_installer_environment(tmp_path)
    assert _run_cloud_keyring_installer(paths).returncode == 0
    original = paths["staged"].read_bytes()
    paths["deployment_id"] = "f" * 32
    assert _run_cloud_keyring_installer(paths, "discard").returncode == 1
    assert paths["staged"].read_bytes() == original
    paths["deployment_id"] = "e" * 32
    residual_path = paths["private"] / {
        "state": "execution-worker-key-rotation-state.json",
        "deploy_state": "execution-worker-keyring-deploy-state.json",
        "deploy_state_part": "execution-worker-keyring-deploy-state.json.part",
        "deploy_backup": "execution-worker-public-keyring.deploy.previous.json",
    }[residual]
    residual_path.write_text("{}\n", encoding="utf-8")
    residual_path.chmod(0o600)

    rejected = _run_cloud_keyring_installer(paths, "discard")

    assert rejected.returncode == 1
    assert paths["staged"].read_bytes() == original
    residual_path.unlink()
    assert _run_cloud_keyring_installer(paths, "discard").returncode == 0
    assert not paths["staged"].exists()


def test_cloud_deploy_discard_holds_rotation_lock_and_is_reentrant(
    tmp_path: Path,
) -> None:
    paths = _cloud_keyring_installer_environment(tmp_path)
    assert _run_cloud_keyring_installer(paths).returncode == 0
    descriptor = os.open(paths["lock"], os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert _run_cloud_keyring_installer(paths, "discard").returncode == 1
        assert paths["staged"].exists()
    finally:
        os.close(descriptor)

    assert _run_cloud_keyring_installer(paths, "discard").returncode == 0
    assert not paths["staged"].exists()
    assert _run_cloud_keyring_installer(paths, "discard").returncode == 0


def test_cloud_deploy_discard_response_loss_fsyncs_absent_target(
    tmp_path: Path,
) -> None:
    paths = _cloud_keyring_installer_environment(tmp_path)
    assert _run_cloud_keyring_installer(paths).returncode == 0
    original = paths["helper"].read_text(encoding="utf-8")
    needle = "            target.unlink()\n"
    assert needle in original
    paths["helper"].write_text(
        original.replace(
            needle, needle + "            os.kill(os.getpid(), 9)\n", 1
        ),
        encoding="utf-8",
    )

    killed = _run_cloud_keyring_installer(paths, "discard")

    assert killed.returncode < 0
    assert not paths["staged"].exists()
    paths["helper"].write_text(original, encoding="utf-8")
    discard = original.split("def _discard", 1)[1].split("def _cutover", 1)[0]
    assert (
        "        except FileNotFoundError:\n"
        "            pass\n"
        "        _fsync_directory(release_root)\n"
    ) in discard
    assert _run_cloud_keyring_installer(paths, "discard").returncode == 0


def _cloud_deploy_input_lock_environment(tmp_path: Path) -> tuple[Path, Path]:
    platform = tmp_path / "platform"
    private = platform / "private"
    platform.mkdir(mode=0o700)
    private.mkdir(mode=0o700)
    helper = tmp_path / "deploy-input-lock.py"
    source = CLOUD_DEPLOY_INPUT_LOCK.read_text(encoding="utf-8")
    source = source.replace("REQUIRED_UID = 0", f"REQUIRED_UID = {os.getuid()}")
    source = source.replace(
        'PLATFORM_ROOT = Path("/opt/orbbec-agent-platform")',
        f"PLATFORM_ROOT = Path({str(platform)!r})",
    )
    helper.write_text(source, encoding="utf-8")
    helper.chmod(0o700)
    return helper, platform


def _run_cloud_deploy_input_lock(helper: Path, action: str, deployment_id: str):
    return subprocess.run(
        [sys.executable, str(helper), action, "b" * 40, deployment_id],
        text=True,
        capture_output=True,
    )


def test_cloud_concurrent_deploy_fails_before_any_fixed_part_write(
    tmp_path: Path,
) -> None:
    helper, platform = _cloud_deploy_input_lock_environment(tmp_path)
    first_id = "1" * 32
    second_id = "2" * 32
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 0

    second = _run_cloud_deploy_input_lock(helper, "acquire", second_id)

    assert second.returncode == 1
    assert not list(platform.rglob("*.part"))
    deploy = (CLOUD / "deploy.sh").read_text(encoding="utf-8")
    acquire = deploy.index('/usr/bin/python3 - acquire "$release_sha" "$deployment_id"')
    assert acquire < deploy.index("deploy-input-lock.py.part")
    assert acquire < deploy.index("remote-stage.sh.part")


def test_cloud_deploy_input_cleanup_requires_exact_owner_token(
    tmp_path: Path,
) -> None:
    helper, _platform = _cloud_deploy_input_lock_environment(tmp_path)
    first_id = "1" * 32
    second_id = "2" * 32
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 0

    wrong_release = _run_cloud_deploy_input_lock(helper, "release", second_id)
    still_blocked = _run_cloud_deploy_input_lock(helper, "acquire", second_id)

    assert wrong_release.returncode == still_blocked.returncode == 1
    assert _run_cloud_deploy_input_lock(helper, "validate", first_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "release", first_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "acquire", second_id).returncode == 0


def test_cloud_deploy_input_acquire_is_idempotent_only_for_exact_active_token(
    tmp_path: Path,
) -> None:
    helper, _platform = _cloud_deploy_input_lock_environment(tmp_path)
    first_id = "1" * 32
    second_id = "2" * 32
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "acquire", second_id).returncode == 1
    assert _run_cloud_deploy_input_lock(helper, "validate", first_id).returncode == 0


@pytest.mark.parametrize(
    "killed_after",
    ["mkdir", "part_open", "part_fsync", "publish", "active_rename"],
)
def test_cloud_deploy_input_acquire_recovers_exact_preparing_boundary(
    tmp_path: Path, killed_after: str
) -> None:
    helper, platform = _cloud_deploy_input_lock_environment(tmp_path)
    first_id = "1" * 32
    second_id = "2" * 32
    original = helper.read_text(encoding="utf-8")
    needle = {
        "mkdir": "        preparing.mkdir(mode=0o700)\n",
        "part_open": "            os.fchmod(descriptor, 0o600)\n",
        "part_fsync": "            os.fsync(descriptor)\n",
        "publish": "        os.replace(preparing_part, preparing_state)\n",
        "active_rename": "    os.replace(preparing, LOCK_ROOT)\n",
    }[killed_after]
    before_acquire, acquire_and_after = original.split("def _acquire", 1)
    acquire, after_acquire = acquire_and_after.split("def _release", 1)
    assert needle in acquire
    indentation = needle[: len(needle) - len(needle.lstrip())]
    helper.write_text(
        before_acquire
        + "def _acquire"
        + acquire.replace(
            needle, needle + indentation + "os.kill(os.getpid(), 9)\n", 1
        )
        + "def _release"
        + after_acquire,
        encoding="utf-8",
    )

    killed = _run_cloud_deploy_input_lock(helper, "acquire", first_id)

    assert killed.returncode < 0
    helper.write_text(original, encoding="utf-8")
    assert _run_cloud_deploy_input_lock(helper, "acquire", second_id).returncode == 1
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "validate", first_id).returncode == 0
    assert not list((platform / "private").glob("deploy-input.preparing-*"))
    if killed_after == "active_rename":
        acquire = original.split("def _acquire", 1)[1].split("def _release", 1)[0]
        assert (
            "        _validate(release_sha, deployment_id)\n"
            "        _fsync(PRIVATE_ROOT)\n"
            "        return\n"
        ) in acquire


def test_cloud_deploy_input_published_recovery_fsyncs_preparing_before_active(
    tmp_path: Path,
) -> None:
    helper, platform = _cloud_deploy_input_lock_environment(tmp_path)
    deployment_id = "1" * 32
    original = helper.read_text(encoding="utf-8")
    before_acquire, acquire_and_after = original.split("def _acquire", 1)
    acquire, after_acquire = acquire_and_after.split("def _release", 1)
    published = "        os.replace(preparing_part, preparing_state)\n"
    assert published in acquire
    helper.write_text(
        before_acquire
        + "def _acquire"
        + acquire.replace(
            published,
            published + "        os.kill(os.getpid(), 9)\n",
            1,
        )
        + "def _release"
        + after_acquire,
        encoding="utf-8",
    )
    killed = _run_cloud_deploy_input_lock(helper, "acquire", deployment_id)
    assert killed.returncode < 0
    private = platform / "private"
    preparing = private / f"deploy-input.preparing-{'b' * 40}-{deployment_id}"
    assert (preparing / "owner.json").exists()
    trace = tmp_path / "fsync-trace"
    fsync_definition = "def _fsync(path: Path) -> None:\n"
    traced = original.replace(
        fsync_definition,
        fsync_definition
        + f"    with open({str(trace)!r}, 'a', encoding='utf-8') as output:\n"
        + "        output.write(str(path) + '\\n')\n",
        1,
    )
    helper.write_text(traced, encoding="utf-8")

    recovered = _run_cloud_deploy_input_lock(helper, "acquire", deployment_id)

    assert recovered.returncode == 0, recovered.stderr
    assert trace.read_text(encoding="utf-8").splitlines()[-2:] == [
        str(preparing),
        str(private),
    ]
    assert _run_cloud_deploy_input_lock(helper, "validate", deployment_id).returncode == 0


def test_cloud_deploy_input_rejects_multiple_or_anomalous_preparing(
    tmp_path: Path,
) -> None:
    helper, platform = _cloud_deploy_input_lock_environment(tmp_path)
    private = platform / "private"
    first_id = "1" * 32
    exact = private / f"deploy-input.preparing-{'b' * 40}-{first_id}"
    other = private / f"deploy-input.preparing-{'b' * 40}-{'2' * 32}"
    exact.mkdir(mode=0o700)
    other.mkdir(mode=0o700)
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 1
    assert not (private / "deploy-input.lock").exists()
    other.rmdir()
    (exact / "unexpected").write_text("x", encoding="utf-8")
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 1
    assert not (private / "deploy-input.lock").exists()


@pytest.mark.parametrize("part", [b"{", b"attacker"])
def test_cloud_deploy_input_only_recovers_valid_preparing_part_prefix(
    tmp_path: Path, part: bytes
) -> None:
    helper, platform = _cloud_deploy_input_lock_environment(tmp_path)
    private = platform / "private"
    deployment_id = "1" * 32
    preparing = private / f"deploy-input.preparing-{'b' * 40}-{deployment_id}"
    preparing.mkdir(mode=0o700)
    owner_part = preparing / "owner.json.part"
    owner_part.write_bytes(part)
    owner_part.chmod(0o600)

    acquired = _run_cloud_deploy_input_lock(helper, "acquire", deployment_id)

    if part == b"{":
        assert acquired.returncode == 0
        assert _run_cloud_deploy_input_lock(helper, "validate", deployment_id).returncode == 0
    else:
        assert acquired.returncode == 1
        assert preparing.exists()
        assert not (private / "deploy-input.lock").exists()


@pytest.mark.parametrize("killed_after", ["active_rename", "completed_rename"])
def test_cloud_deploy_input_release_recovers_after_each_persistent_boundary(
    tmp_path: Path, killed_after: str
) -> None:
    helper, _platform = _cloud_deploy_input_lock_environment(tmp_path)
    deployment_id = "1" * 32
    assert _run_cloud_deploy_input_lock(helper, "acquire", deployment_id).returncode == 0
    original = helper.read_text(encoding="utf-8")
    needle = {
        "active_rename": "        os.replace(LOCK_ROOT, tombstone)\n",
        "completed_rename": "        os.replace(tombstone, completed)\n",
    }[killed_after]
    assert needle in original
    indentation = needle[: len(needle) - len(needle.lstrip())]
    helper.write_text(
        original.replace(
            needle, needle + indentation + "os.kill(os.getpid(), 9)\n", 1
        ),
        encoding="utf-8",
    )

    killed = _run_cloud_deploy_input_lock(helper, "release", deployment_id)

    assert killed.returncode < 0
    helper.write_text(original, encoding="utf-8")
    assert _run_cloud_deploy_input_lock(helper, "release", "2" * 32).returncode == 1
    assert _run_cloud_deploy_input_lock(helper, "release", deployment_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "release", deployment_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "release", "2" * 32).returncode == 1
    assert _run_cloud_deploy_input_lock(helper, "acquire", "2" * 32).returncode == 0


@pytest.mark.parametrize("killed_after", ["rename", "unlink", "rmdir"])
def test_cloud_deploy_input_acquire_clears_receipt_after_each_boundary(
    tmp_path: Path, killed_after: str
) -> None:
    helper, _platform = _cloud_deploy_input_lock_environment(tmp_path)
    first_id = "1" * 32
    second_id = "2" * 32
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "release", first_id).returncode == 0
    original = helper.read_text(encoding="utf-8")
    needle = {
        "rename": "        os.replace(receipt, clearing_receipt)\n",
        "unlink": "            clearing_state.unlink()\n",
        "rmdir": "        clearing_receipt.rmdir()\n",
    }[killed_after]
    assert needle in original
    indentation = needle[: len(needle) - len(needle.lstrip())]
    helper.write_text(
        original.replace(
            needle, needle + indentation + "os.kill(os.getpid(), 9)\n", 1
        ),
        encoding="utf-8",
    )

    killed = _run_cloud_deploy_input_lock(helper, "acquire", second_id)

    assert killed.returncode < 0
    helper.write_text(original, encoding="utf-8")
    assert _run_cloud_deploy_input_lock(helper, "release", first_id).returncode == 1
    assert _run_cloud_deploy_input_lock(helper, "acquire", second_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "validate", second_id).returncode == 0


def test_cloud_deploy_input_next_acquire_validates_receipt_under_transaction_lock(
    tmp_path: Path,
) -> None:
    helper, platform = _cloud_deploy_input_lock_environment(tmp_path)
    first_id = "1" * 32
    second_id = "2" * 32
    assert _run_cloud_deploy_input_lock(helper, "acquire", first_id).returncode == 0
    assert _run_cloud_deploy_input_lock(helper, "release", first_id).returncode == 0
    receipt = platform / "private" / f"deploy-input.completed-{'b' * 40}-{first_id}"
    owner = receipt / "owner.json"
    original = owner.read_bytes()
    owner.write_bytes(b"{}\n")
    assert _run_cloud_deploy_input_lock(helper, "acquire", second_id).returncode == 1
    assert receipt.exists()
    assert not (platform / "private" / "deploy-input.lock").exists()
    owner.write_bytes(original)
    transaction = os.open(
        platform / "private" / "deploy-input.transaction.lock", os.O_RDWR
    )
    fcntl.flock(transaction, fcntl.LOCK_EX)
    process = subprocess.Popen(
        [sys.executable, str(helper), "acquire", "b" * 40, second_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.1)
        assert process.poll() is None
        assert receipt.exists()
        assert not (platform / "private" / "deploy-input.lock").exists()
    finally:
        fcntl.flock(transaction, fcntl.LOCK_UN)
        os.close(transaction)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, (stdout, stderr)
    assert not receipt.exists()


def test_cloud_deploy_retains_input_token_until_exact_cutover_success() -> None:
    deploy = (CLOUD / "deploy.sh").read_text(encoding="utf-8")
    cleanup = deploy.split("cleanup() {", 1)[1].split("}\ntrap cleanup EXIT", 1)[0]
    assert "remote_operation_uncertain=0" in deploy
    assert 'cutover_started=0' in deploy
    assert 'cutover_confirmed=0' in deploy
    assert '"$remote_operation_uncertain" == "0"' in cleanup
    assert '"$cutover_started" == "0"' in cleanup
    assert '"$cutover_confirmed" == "1"' in cleanup
    post_acquire = deploy.split("deploy_input_acquired=1", 1)[1]
    assert post_acquire.count("run_remote_operation /usr/bin/ssh") == 7
    started = deploy.index("cutover_started=1")
    stage = deploy.index('install-execution-worker-keyring.py" stage')
    cutover = deploy.index("install-execution-worker-keyring.py\" cutover")
    exact = deploy.index('CLOUD_PLATFORM_DEPLOY_OK release=$release_sha mode=dingtalk')
    confirmed = deploy.index("cutover_confirmed=1")
    assert started < stage < cutover < exact < confirmed


def test_cloud_deploy_input_runbook_recovers_exact_single_tombstone() -> None:
    runbook = (ROOT / "docs/runbooks/agent-execution-relay.md").read_text(
        encoding="utf-8"
    )
    section = runbook.split("A cloud deploy holds", 1)[1].split("## Status", 1)[0]
    assert "deploy-input.preparing-*" in section
    assert "^deploy-input\\.preparing-([0-9a-f]{40})-([0-9a-f]{32})$" in section
    assert '"$helper" acquire "$release_sha" "$deployment_id"' in section
    assert "deploy-input.releasing-*" in section
    assert '[[ "${#tombstones[@]}" == "1" ]]' in section
    assert "^deploy-input\\.(releasing|completed)-([0-9a-f]{40})-([0-9a-f]{32})$" in section
    assert '[[ ! -e "$owner" && ! -L "$owner" ]]' in section
    assert '[[ "$owner_release_sha" == "$release_sha" ]]' in section
    assert '[[ "$owner_deployment_id" == "$deployment_id" ]]' in section
    assert "deploy_outcome=completed" in section
    assert "deploy_outcome=rolled-back" in section
    assert '[[ ! -e "$target_release" && ! -L "$target_release" ]]' in section
    assert 'for residual in "$deploy_state" "$deploy_state_part" "$deploy_backup"' in section
    assert '"$installer" discard "$release_sha" "$deployment_id"' in section
    assert 'for staged_residual in "$staged_keyring" "$staged_keyring_part"' in section
    assert '[[ ! -e "$current" && ! -L "$current" ]]' in section
    assert "sport = :8080" in section
    assert "com.docker.compose.project=orbbec-agent-platform" in section
    assert '"$running_services" == "platform-postgres"' in section
    outcome = section.index('[[ "$deploy_outcome" == "completed" || "$deploy_outcome" == "rolled-back" ]]')
    assert outcome < section.index("http://127.0.0.1:8080/api/health")
    assert "/bin/rm" not in section
    pgrep = next(line for line in section.splitlines() if "pgrep -f" in line)
    for process in ("/bin/cat", "install-execution-worker-keyring.py", "remote-stage.sh"):
        assert process in pgrep


@pytest.mark.parametrize(
    (
        "remote_operation_uncertain",
        "cutover_started",
        "cutover_confirmed",
        "released",
    ),
    [(0, 0, 0, True), (1, 0, 0, False), (0, 1, 0, False), (1, 1, 1, True)],
    ids=[
        "pre-remote-failure",
        "uncertain-upload",
        "stage-or-cutover-started",
        "confirmed-cutover",
    ],
)
def test_cloud_deploy_exit_cleanup_executes_fail_closed_release_policy(
    tmp_path: Path,
    remote_operation_uncertain: int,
    cutover_started: int,
    cutover_confirmed: int,
    released: bool,
) -> None:
    deploy = (CLOUD / "deploy.sh").read_text(encoding="utf-8")
    cleanup = "cleanup() {" + deploy.split("cleanup() {", 1)[1].split(
        "}\ntrap cleanup EXIT", 1
    )[0] + "}\n"
    marker = tmp_path / "released"
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text(
        "#!/bin/bash\n/bin/cat >/dev/null\n/usr/bin/touch \"$RELEASE_MARKER\"\n",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o700)
    cleanup = cleanup.replace("/usr/bin/ssh", str(fake_ssh))
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    shell = f"""set -u
artifact_root={str(artifact)!r}
deploy_input_acquired=1
remote_operation_uncertain={remote_operation_uncertain}
cutover_started={cutover_started}
cutover_confirmed={cutover_confirmed}
ssh_options=(test-option)
CLOUD_ADMIN_HOST=cloud.example
release_sha={'b' * 40}
deployment_id={'1' * 32}
repository_root={str(ROOT)!r}
{cleanup}
false
cleanup
"""

    result = subprocess.run(
        ["/bin/bash", "-c", shell],
        text=True,
        capture_output=True,
        env={**os.environ, "RELEASE_MARKER": str(marker)},
    )

    assert result.returncode == 1
    assert marker.exists() is released
    assert not artifact.exists()


def test_cloud_deploy_signal_keeps_token_while_remote_child_is_orphaned(
    tmp_path: Path,
) -> None:
    deploy = (CLOUD / "deploy.sh").read_text(encoding="utf-8")
    remote_operation = "run_remote_operation() {" + deploy.split(
        "run_remote_operation() {", 1
    )[1].split("}\n", 1)[0] + "}\n"
    cleanup = "cleanup() {" + deploy.split("cleanup() {", 1)[1].split(
        "}\ntrap cleanup EXIT", 1
    )[0] + "}\n"
    released = tmp_path / "released"
    release_remote = tmp_path / "release-remote"
    remote_pid = tmp_path / "remote-pid"
    remote_started = tmp_path / "remote-started"
    fake_release_ssh = tmp_path / "release-ssh"
    fake_release_ssh.write_text(
        "#!/bin/bash\n/bin/cat >/dev/null\n/usr/bin/touch \"$RELEASE_MARKER\"\n",
        encoding="utf-8",
    )
    fake_release_ssh.chmod(0o700)
    cleanup = cleanup.replace("/usr/bin/ssh", str(fake_release_ssh))
    fake_operation = tmp_path / "operation-ssh"
    fake_operation.write_text(
        "#!/usr/bin/python3\n"
        "import os, pathlib, signal, time\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.setsid()\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"    pathlib.Path({str(remote_pid)!r}).write_text(str(os.getpid()))\n"
        f"    pathlib.Path({str(remote_started)!r}).touch()\n"
        f"    while not pathlib.Path({str(release_remote)!r}).exists(): time.sleep(0.01)\n"
        "    raise SystemExit(0)\n"
        "os.waitpid(child, 0)\n",
        encoding="utf-8",
    )
    fake_operation.chmod(0o700)
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    shell = f"""set -u
artifact_root={str(artifact)!r}
deploy_input_acquired=1
remote_operation_uncertain=0
cutover_started=0
cutover_confirmed=0
ssh_options=(test-option)
CLOUD_ADMIN_HOST=cloud.example
release_sha={'b' * 40}
deployment_id={'1' * 32}
repository_root={str(ROOT)!r}
{remote_operation}
{cleanup}
trap cleanup EXIT
run_remote_operation {str(fake_operation)!r}
"""
    process = subprocess.Popen(
        ["/bin/bash", "-c", shell],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "RELEASE_MARKER": str(released)},
        start_new_session=True,
    )
    try:
        _wait_for_file(remote_started)
        orphan_pid = int(remote_pid.read_text())
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
        os.kill(orphan_pid, 0)
        assert not released.exists()
        assert not artifact.exists()
    finally:
        release_remote.touch()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_cloud_deploy_real_git_archive_extracts_root_only_sensitive_assets(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    release.mkdir(mode=0o700)
    archive = subprocess.Popen(
        ["git", "archive", "HEAD"], cwd=ROOT, stdout=subprocess.PIPE
    )
    assert archive.stdout is not None
    extracted = subprocess.run(
        ["/usr/bin/tar", "-x", "-C", str(release)],
        stdin=archive.stdout,
        capture_output=True,
        preexec_fn=lambda: os.umask(0o077),
    )
    archive.stdout.close()
    assert archive.wait(timeout=5) == 0

    assert extracted.returncode == 0, extracted.stderr
    assert _mode(release / "deploy") == 0o700
    assert _mode(release / "deploy/cloud") == 0o700
    assert _mode(release / "deploy/cloud/compose.yaml") == 0o600
    helper = CLOUD_ROTATOR.read_text(encoding="utf-8")
    assert "_secure_code_directory(deploy, {0o700})" in helper
    assert "_secure_code_directory(cloud, {0o700})" in helper
    assert "stat.S_IMODE(metadata.st_mode) != 0o600" in helper


def test_cloud_remote_stage_journals_before_switch_and_restores_state_last(
    tmp_path: Path,
) -> None:
    source = CLOUD_REMOTE_STAGE.read_text(encoding="utf-8")
    inherited_lock = source.index("PLATFORM_EXECUTION_WORKER_DEPLOY_LOCK_FD")
    archive_read = source.index('of="$archive_path.part"')
    journal = source.index("write_deploy_state keyring_switching")
    switch = source.index('"$staged_worker_keyring" "$worker_keyring_part"')
    restore = source.index("if ! restore_worker_keyring; then")
    service_rollback = source.index('if [[ "$api_stopped" -eq 1 ]]', restore)
    assert inherited_lock < archive_read
    assert source.index("completed_deploy_recovered") < archive_read
    assert journal < switch
    assert restore < service_rollback
    cleanup = source.split("cleanup_worker_keyring_deploy() {", 1)[1].split("}\n", 1)[0]
    assert cleanup.index('"$worker_keyring_previous"') < cleanup.index('"$deploy_state"')


def test_cloud_worker_activation_probe_uses_read_only_app_role(
    tmp_path: Path,
) -> None:
    source = CLOUD_REMOTE_STAGE.read_text(encoding="utf-8")
    probe = source.split("completed_worker_key_active() {", 1)[1].split(
        "restore_worker_keyring() {", 1
    )[0]

    assert "PLATFORM_CONTROL_DATABASE_URL_FILE=/run/control-secrets/control-database-url" in probe
    assert "PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE" not in probe

    private = tmp_path / "private"
    stage = tmp_path / "stage"
    private.mkdir(mode=0o700)
    stage.mkdir(mode=0o700)
    canonical = private / "execution-worker-public-keyring.json"
    previous = private / "execution-worker-public-keyring.deploy.previous.json"
    staged = stage / "execution-worker-public-keyring.json"
    state = private / "execution-worker-keyring-deploy-state.json"
    canonical.write_bytes(b"new-keyring\n")
    previous.write_bytes(b"old-keyring\n")
    staged.write_bytes(b"new-keyring\n")
    for path in (canonical, previous, staged):
        path.chmod(0o600)
    release_sha = "d" * 40
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "keyring_switched",
                "release_sha": release_sha,
                "previous_sha256": hashlib.sha256(previous.read_bytes()).hexdigest(),
                "next_sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    state.chmod(0o600)
    functions = source.split("fsync_private() {", 1)[1].split(
        '\nif [[ -e "$deploy_state"', 1
    )[0]
    functions = "fsync_private() {" + functions
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    functions = functions.replace(
        "/usr/bin/stat -c '%a %U'", "/usr/bin/stat -f '%Lp %Su'"
    ).replace("600 root", f"600 {current_user}")
    functions = functions.replace(
        "/usr/bin/install -o root -g root -m 600", "/usr/bin/install -m 600"
    )
    shell = f"""set -euo pipefail
private_path={str(private)!r}
deploy_state={str(state)!r}
deploy_state_part={str(private / 'execution-worker-keyring-deploy-state.json.part')!r}
worker_keyring={str(canonical)!r}
worker_keyring_part={str(private / 'execution-worker-public-keyring.json.part')!r}
worker_keyring_previous={str(previous)!r}
staged_worker_keyring={str(staged)!r}
release_sha={release_sha}
{functions}
restore_worker_keyring
"""

    restored = subprocess.run(
        ["/bin/bash", "-c", shell], text=True, capture_output=True
    )

    assert restored.returncode == 0, restored.stderr
    assert canonical.read_bytes() == b"old-keyring\n"
    assert not state.exists() and not previous.exists() and not staged.exists()


def test_cloud_cutover_retry_for_existing_release_fails_before_archive_mutation(
    tmp_path: Path,
) -> None:
    source = CLOUD_REMOTE_STAGE.read_text(encoding="utf-8")
    body = source.split('if [[ "$completed_deploy_recovered" == "1" ]]', 1)[1]
    body = 'if [[ "$completed_deploy_recovered" == "1" ]]' + body.split(
        "rollback() {", 1
    )[0]
    release = tmp_path / ("b" * 40)
    release.mkdir(mode=0o700)
    archive = tmp_path / "release.tar.gz"
    shell = f"""set -euo pipefail
fail() {{ exit 73; }}
completed_deploy_recovered=0
release_path={str(release)!r}
archive_path={str(archive)!r}
expected_digest={'c' * 64}
{body}
"""

    retried = subprocess.run(
        ["/bin/bash", "-c", shell], input=b"replacement-archive", capture_output=True
    )

    assert retried.returncode == 73
    assert not archive.exists() and not Path(str(archive) + ".part").exists()


@pytest.mark.parametrize(
    "killed_after", ["state_part", "previous", "staged", "state"]
)
def test_cloud_remote_completed_cleanup_recovers_after_each_unlink(
    tmp_path: Path, killed_after: str
) -> None:
    source = CLOUD_REMOTE_STAGE.read_text(encoding="utf-8")
    private = tmp_path / "private"
    stage = tmp_path / "stage"
    private.mkdir(mode=0o700)
    stage.mkdir(mode=0o700)
    canonical = private / "execution-worker-public-keyring.json"
    previous = private / "execution-worker-public-keyring.deploy.previous.json"
    staged = stage / "execution-worker-public-keyring.json"
    state = private / "execution-worker-keyring-deploy-state.json"
    public = bytes([8]) * 32
    document = (
        json.dumps(
            {
                "worker_id": "agentops-mac-primary",
                "key_id": "worker-v2",
                "public_key_base64url": base64.urlsafe_b64encode(public)
                .decode()
                .rstrip("="),
                "allowed_agent_ids": AGENTS,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    canonical.write_bytes(document)
    previous.write_bytes(b"old-keyring\n")
    staged.write_bytes(document)
    for path in (canonical, previous, staged):
        path.chmod(0o600)
    release_sha = "d" * 40
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "completed",
                "release_sha": release_sha,
                "previous_sha256": hashlib.sha256(previous.read_bytes()).hexdigest(),
                "next_sha256": hashlib.sha256(document).hexdigest(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    state.chmod(0o600)
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()

    def shell_functions(value: str) -> str:
        functions = value.split("fsync_private() {", 1)[1].split(
            '\nif [[ -e "$deploy_state"', 1
        )[0]
        functions = "fsync_private() {" + functions
        functions = functions.replace(
            "/usr/bin/stat -c '%a %U'", "/usr/bin/stat -f '%Lp %Su'"
        ).replace("600 root", f"600 {current_user}")
        return functions.replace(
            "/usr/bin/install -o root -g root -m 600", "/usr/bin/install -m 600"
        )

    needle = {
        "state_part": '  /bin/rm -f -- "$deploy_state_part"\n',
        "previous": '  /bin/rm -f -- "$worker_keyring_previous"\n',
        "staged": '  /bin/rm -f -- "$staged_worker_keyring"\n',
        "state": '  /bin/rm -f -- "$deploy_state"\n',
    }[killed_after]
    assert needle in source
    killed_source = source.replace(
        needle, needle + "  /bin/kill -9 $$\n", 1
    )

    def run(value: str) -> subprocess.CompletedProcess[str]:
        shell = f"""set -euo pipefail
private_path={str(private)!r}
deploy_state={str(state)!r}
deploy_state_part={str(private / 'execution-worker-keyring-deploy-state.json.part')!r}
worker_keyring={str(canonical)!r}
worker_keyring_part={str(private / 'execution-worker-public-keyring.json.part')!r}
worker_keyring_previous={str(previous)!r}
staged_worker_keyring={str(staged)!r}
release_sha={release_sha}
{shell_functions(value)}
completed_worker_key_active() {{ return 0; }}
restore_worker_keyring
"""
        return subprocess.run(["/bin/bash", "-c", shell], capture_output=True, text=True)

    killed = run(killed_source)
    assert killed.returncode < 0
    recovered = run(source)
    assert recovered.returncode == 0, recovered.stderr
    assert canonical.read_bytes() == document
    assert not state.exists() and not previous.exists() and not staged.exists()


def test_installer_is_noninteractive_agentops_only_and_permission_gated() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    lowered = script.lower()

    assert '"$(/usr/bin/id -un)" == "agentops"' in script
    assert 'worker_supervisor="$script_dir/worker-pm2.sh"' in script
    assert '"$worker_supervisor" start' in script
    assert "launchctl" not in lowered
    assert "bootstrap-worker-database.sh" in script
    assert "generate-worker-key.py" in script
    assert "execution-worker-public.json" in script
    assert "plistlib" in script
    assert '/bin/cp "$script_dir/execution-worker-key-binding.plist.template"' not in script
    assert 'key_manifest_part="$private_root/.execution-worker-key-binding.plist.part"' in script
    assert 'key_manifest_part="$key_manifest.part.$$"' not in script
    assert "stat -f '%lp %su'" in lowered
    for forbidden in (
        "/usr/bin/security",
        "keychain",
        "ssh -r",
        "/usr/bin/sudo",
        "/usr/bin/su ",
        "osascript",
        "read -s",
        "sqlite",
    ):
        assert forbidden not in lowered
    subprocess.run(["/bin/bash", "-n", str(INSTALLER)], check=True)


def test_installer_requires_exact_agent_brain_runtime_map_before_mutation() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    validation = script.index("EXECUTION_WORKER_RUNTIME_MAP_OK")
    assert validation < script.index('"$script_dir/generate-worker-key.py"')
    assert validation < script.index('"$script_dir/bootstrap-worker-database.sh"')
    assert repr(AGENTS) in script
    for rejected in (
        "test-bot",
        "feishu-default",
        "codex-assistant",
        "ai-admin-agent",
        "ai-fae-agent",
    ):
        assert rejected in script
    for exact_brain_value in (
        '"platform": "web"',
        '"platformOnly": True',
        '"engine": "claude"',
        '"model": "claude-opus-5"',
        '"backend": "pty"',
        '"toolPolicy": "none"',
        '"apiPort": 9110',
        '"pm2Name": "metabot-agent-brain"',
    ):
        assert exact_brain_value in script


def test_installer_acquires_rotation_lock_before_generator_or_database() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    acquired = script.index("PLATFORM_EXECUTION_WORKER_ROTATION_LOCK_FD")
    assert acquired < script.index('"$script_dir/generate-worker-key.py"')
    assert acquired < script.index('"$script_dir/bootstrap-worker-database.sh"')
    assert 'execution-worker-key-rotation.lock' in script
    assert "fcntl.flock" in script


def test_installer_holds_rotation_lock_while_rotator_fails_without_mutation(
    tmp_path: Path,
) -> None:
    paths = _rotation_test_environment(tmp_path)
    runtime = paths["canonical_public"].parent
    private = paths["canonical_private"].parent
    platform = runtime / "platform"
    local = platform / "deploy/local-execution-worker"
    installer = local / "install.sh"
    source = INSTALLER.read_text(encoding="utf-8")
    source = source.replace("runtime_root=/Users/agentops/AgentRuntime", f"runtime_root={runtime}")
    harness_home = paths["canonical_plist"].parents[2]
    source = source.replace(
        '[[ "${HOME:-}" == /Users/agentops ]] || fail',
        f'[[ "${{HOME:-}}" == {harness_home} ]] || fail',
    ).replace(
        "/Users/agentops/Library/LaunchAgents",
        str(paths["canonical_plist"].parent),
    )
    source = source.replace(
        '"$(/usr/bin/id -un)" == "agentops"',
        f'"$(/usr/bin/id -un)" == "{subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()}"',
    )
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    source = source.replace("700 agentops", f"700 {current_user}")
    source = source.replace("600 agentops", f"600 {current_user}")
    installer.write_text(source, encoding="utf-8")
    installer.chmod(0o700)
    (runtime / "metabot").mkdir(mode=0o700)
    (runtime / "metabot/runtime-contract.json").write_text(
        _metabot_runtime_contract()
    )
    (private / "metabot-api-token").write_text("token\n")
    (private / "metabot-api-token").chmod(0o600)
    owner_dsn = private / "owner-dsn"
    owner_dsn.write_text("postgresql://owner:secret@127.0.0.1:5432/postgres\n")
    owner_dsn.chmod(0o600)
    marker = tmp_path / "installer-generator-ready"
    release = tmp_path / "installer-generator-release"
    python = platform / "backend/.venv/bin/python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        f"if [[ \"${{1:-}}\" != *generate-worker-key.py ]]; then exec {sys.executable} \"$@\"; fi\n"
        f"/usr/bin/touch {marker}\n"
        f"while [[ ! -e {release} ]]; do /bin/sleep 0.01; done\n"
        "exit 1\n",
        encoding="utf-8",
    )
    python.chmod(0o700)
    worker_supervisor = local / "worker-pm2.sh"
    worker_supervisor.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    worker_supervisor.chmod(0o700)
    before = _rotation_components(paths, "canonical")
    process = subprocess.Popen(
        ["/bin/bash", str(installer), str(owner_dsn)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "HOME": str(paths["canonical_plist"].parents[2])},
    )
    _wait_for_file(marker)

    blocked = _run_rotation(paths, "prepare")

    assert blocked.returncode == 1
    assert _rotation_components(paths, "canonical") == before
    assert not any(paths["managed"][name].exists() for name in ("next_private", "next_public", "next_plist"))
    release.touch()
    process.communicate(timeout=5)


def test_agentops_provision_owns_exact_pm2_state_and_dump_rollback() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    provision = (LOCAL / "provision-agentops.sh").read_text(encoding="utf-8")

    assert "launchctl" not in installer.lower()
    assert "previous.dump" in provision
    assert "prior_state" in provision
    assert '"$worker_supervisor" restore "$prior_state"' in provision
    assert '"$worker_supervisor" save' in provision
    assert '"$prior_state" == absent' in provision
    assert '"$prior_state" == online' in provision
    assert '"$prior_state" == stopped' in provision
    assert provision.index('"$worker_supervisor" save') < provision.index(
        'printf \'v1\\n\' > "$receipt/committed.part"'
    )


def test_worker_pm2_wrapper_uses_fixed_identity_and_exact_state_machine(
    tmp_path: Path,
) -> None:
    source = WORKER_PM2.read_text(encoding="utf-8")
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    npm_root = tmp_path / ".npm-global"
    npm_bin = npm_root / "bin"
    package_bin = npm_root / "lib/node_modules/pm2/bin"
    npm_bin.mkdir(parents=True, mode=0o700)
    package_bin.mkdir(parents=True, mode=0o700)
    for directory in (
        npm_root,
        npm_root / "lib",
        npm_root / "lib/node_modules",
        npm_root / "lib/node_modules/pm2",
        package_bin,
        npm_bin,
    ):
        directory.chmod(0o700)
    fake_pm2 = package_bin / "pm2"
    (npm_bin / "pm2").symlink_to("../lib/node_modules/pm2/bin/pm2")
    state = tmp_path / "state"
    start_phase = tmp_path / "start-phase"
    readiness_counter = tmp_path / "readiness-counter"
    jlist_delay = tmp_path / "jlist-delay"
    log = tmp_path / "calls"
    config = tmp_path / "execution-worker.ecosystem.config.cjs"
    copied = tmp_path / "worker-pm2.sh"
    state.write_text("absent", encoding="utf-8")
    start_phase.write_text("online", encoding="utf-8")
    readiness_counter.write_text("0", encoding="utf-8")
    jlist_delay.write_text("", encoding="utf-8")
    config.write_text("module.exports = {};\n", encoding="utf-8")
    config.chmod(0o600)
    fake_pm2.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        f"state={str(state)!r}\nstart_phase={str(start_phase)!r}\n"
        f"readiness_counter={str(readiness_counter)!r}\n"
        f"jlist_delay={str(jlist_delay)!r}\nlog={str(log)!r}\n"
        "echo \"$*\" >> \"$log\"\n"
        "case \"$1\" in\n"
        "  jlist)\n"
        "    if [[ -s \"$jlist_delay\" ]]; then delay=\"$(<\"$jlist_delay\")\"; "
        "printf '' > \"$jlist_delay\"; /bin/sleep \"$delay\"; fi\n"
        "    if [[ \"$(<\"$state\")\" == launching ]]; then "
        "count=\"$(<\"$readiness_counter\")\"; count=$((count + 1)); "
        "printf '%s' \"$count\" > \"$readiness_counter\"; "
        "[[ \"$count\" -lt 3 ]] || printf online > \"$state\"; fi\n"
        "    if [[ \"$(<\"$state\")\" == absent ]]; then echo '[]'; else\n"
        "      printf '[{\"name\":\"orbbec-agent-execution-worker\",\"pid\":43210,\"pm2_env\":{\"status\":\"%s\",\"pm_exec_path\":\"/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python\",\"pm_cwd\":\"/Users/agentops/AgentRuntime/platform/backend\",\"args\":[\"-m\",\"app.execution_relay.worker\"]}}]\\n' \"$(<\"$state\")\"; fi ;;\n"
        "  delete) printf absent > \"$state\" ;;\n"
        "  start) printf 0 > \"$readiness_counter\"; /bin/cat \"$start_phase\" > \"$state\" ;;\n"
        "  stop) printf stopped > \"$state\" ;;\n"
        "  save) ;;\n"
        "  *) exit 91 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_pm2.chmod(0o700)
    source = source.replace(
        '"$(/usr/bin/id -un)" == agentops',
        f'"$(/usr/bin/id -un)" == {current_user}',
    ).replace(
        '"${HOME:-}" == /Users/agentops',
        f'"${{HOME:-}}" == {tmp_path}',
    ).replace(
        "cd /Users/agentops || fail",
        f"cd {tmp_path} || fail",
    ).replace(
        '== "600 agentops"',
        f'== "600 {current_user}"',
    ).replace(
        "pm2_home=/Users/agentops", f"pm2_home={tmp_path}"
    ).replace(
        "pm2_root=/Users/agentops/.npm-global", f"pm2_root={npm_root}"
    ).replace(
        "pm2=/Users/agentops/.npm-global/lib/node_modules/pm2/bin/pm2",
        f"pm2={fake_pm2}",
    ).replace(
        "config=/Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/execution-worker.ecosystem.config.cjs",
        f"config={config}",
    ).replace(
        'restore_timeout_seconds=60',
        'restore_timeout_seconds=2',
    ).replace(
        '/bin/sleep "$restore_interval_seconds"',
        '/bin/sleep 0.01',
    )
    copied.write_text(source, encoding="utf-8")
    copied.chmod(0o700)
    environment = {**os.environ, "HOME": str(tmp_path)}

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(copied), *arguments],
            text=True,
            capture_output=True,
            env=environment,
        )

    assert run("state").stdout.strip() == "absent"
    assert json.loads(run("readiness").stdout) == {"phase": "failed"}
    assert run("start").returncode == 0
    assert json.loads(run("readiness").stdout) == {"phase": "online", "pid": 43210}
    identity = json.loads(run("inspect").stdout)
    assert identity == {
        "name": "orbbec-agent-execution-worker",
        "pid": 43210,
        "status": "online",
        "pm_exec_path": "/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python",
        "pm_cwd": "/Users/agentops/AgentRuntime/platform/backend",
        "args": ["-m", "app.execution_relay.worker"],
    }
    assert run("stop").returncode == 0
    assert run("state").stdout.strip() == "stopped"
    assert json.loads(run("readiness").stdout) == {"phase": "failed"}
    state.write_text("launching", encoding="utf-8")
    assert json.loads(run("readiness").stdout) == {"phase": "starting"}
    state.write_text("waiting restart", encoding="utf-8")
    assert json.loads(run("readiness").stdout) == {"phase": "failed"}
    state.write_text("errored", encoding="utf-8")
    assert json.loads(run("readiness").stdout) == {"phase": "failed"}
    state.write_text("stopped", encoding="utf-8")
    for prior in ("absent", "online", "stopped"):
        assert run("restore", prior).returncode == 0
        assert run("state").stdout.strip() == prior
    assert run("restore", "online").returncode == 0
    assert run("save").returncode == 0
    start_phase.write_text("launching", encoding="utf-8")
    assert run("start").returncode == 0
    assert json.loads(run("readiness").stdout) == {"phase": "starting"}
    state.write_text("online", encoding="utf-8")
    assert json.loads(run("readiness").stdout) == {"phase": "online", "pid": 43210}
    assert run("restore", "online").returncode == 0
    assert run("state").stdout.strip() == "online"
    start_phase.write_text("waiting restart", encoding="utf-8")
    assert run("restore", "online").returncode == 1
    start_phase.write_text("online", encoding="utf-8")
    jlist_delay.write_text("2.1", encoding="utf-8")
    assert run("restore", "online").returncode == 1
    start_phase.write_text("online", encoding="utf-8")
    assert run("start", "different-config").returncode == 1
    assert run("restore", "invalid-name").returncode == 1
    calls = log.read_text(encoding="utf-8")
    assert "start " + str(config) + " --only orbbec-agent-execution-worker --update-env" in calls
    assert "different-config" not in calls
    assert (npm_bin / "pm2").is_symlink()

    npm_root.chmod(0o770)
    assert run("state").returncode == 1
    npm_root.chmod(0o700)
    external_pm2 = tmp_path / "external-pm2"
    external_pm2.write_bytes(fake_pm2.read_bytes())
    external_pm2.chmod(0o700)
    fake_pm2.unlink()
    fake_pm2.symlink_to(external_pm2)
    assert run("state").returncode == 1


def test_worker_pm2_uses_canonical_npm_package_executable() -> None:
    source = WORKER_PM2.read_text(encoding="utf-8")

    assert "pm2=/Users/agentops/.npm-global/lib/node_modules/pm2/bin/pm2" in source
    assert "pm2=/Users/agentops/.npm-global/bin/pm2" not in source
    assert "stat.S_ISREG" in source
    assert "path.is_symlink()" in source
    assert "metadata.st_uid != os.getuid()" in source
    assert "stat.S_IMODE(metadata.st_mode) & 0o022" in source
    assert "readiness)" in source
    assert '"launching"' in source
    assert 'phase:"starting"' in source
    assert 'phase:"failed"' in source
    assert "restore_interval_seconds=5" in source
    assert "restore_timeout_seconds=60" in source


def test_worker_pm2_config_has_only_the_fixed_worker_runtime() -> None:
    source = WORKER_PM2_CONFIG.read_text(encoding="utf-8")

    assert "name: 'orbbec-agent-execution-worker'" in source
    assert "script: '/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python'" in source
    assert "args: ['-m', 'app.execution_relay.worker']" in source
    assert "cwd: '/Users/agentops/AgentRuntime/platform/backend'" in source
    assert "PLATFORM_WORKER_CALLBACK_PORT: '9120'" in source
    assert "PLATFORM_WORKER_KEY_ID: workerDocument.key_id" in source
    assert "readFileSync" in source and "execution-worker-public.json" in source
    assert "process.env" not in source
    assert "exec_mode" not in source
    assert stat.S_IMODE(WORKER_PM2_CONFIG.stat().st_mode) == 0o644


def test_cloud_compose_enables_relay_with_read_only_keyring_and_no_port() -> None:
    value = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    api = value["services"]["platform-api"]
    assert api["environment"]["PLATFORM_EXECUTION_RELAY_ENABLED"] == "1"
    assert api["environment"]["PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE"] == (
        "/run/secrets/content-encryption-keyring"
    )
    assert "platform-api-secrets:/run/secrets:ro" in api["volumes"]
    serialized = json.dumps(value)
    for port in range(9101, 9109):
        assert f'"{port}:' not in serialized
        assert f':{port}"' not in serialized


def test_cloud_deploy_transfers_only_public_worker_document() -> None:
    script = (CLOUD / "deploy.sh").read_text(encoding="utf-8")
    installer = CLOUD_KEYRING_INSTALLER.read_text(encoding="utf-8")
    lowered = (script + installer).lower()

    assert "CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING" in script
    assert "install-execution-worker-keyring.py" in script
    assert "execution-worker-public-keyring.json" in installer
    assert "execution-worker-key-rotation.lock" in installer
    assert "execution-worker-key-rotation-state.json" in installer
    assert "content-encryption-keyring" in script
    assert "BatchMode=yes" in script
    assert "chmod 600" in script
    assert "worker-private" not in lowered
    assert "execution-worker-ed25519.key" not in lowered


def test_production_acceptance_gates_worker_identity_freshness_and_public_ports() -> None:
    script = (CLOUD / "accept-dingtalk-production.sh").read_text(
        encoding="utf-8"
    )
    lowered = script.lower()

    assert "execution-worker-public-keyring.json" in script
    assert "execution_workers" in script
    assert "execution_worker_keys" in script
    assert "status='active'" in lowered
    assert "interval '60 seconds'" in lowered
    assert "public_key_sha256" in lowered
    assert "agentops-mac-primary" in script
    assert "worker-v[1-9][0-9]*" in script
    assert "worker_key.key_id=:'expected_key_id'" in script
    assert "worker_key.key_id='worker-v1'" not in script
    assert "9101-9108" in script
    assert r"0\.0\.0\.0" in script and r"\[::\]" in script
    assert script.index("execution_workers") < script.index(
        'echo "DINGTALK_PRODUCTION_ACCEPTANCE_OK'
    )


def test_production_acceptance_requires_exact_agent_order_and_database_value() -> None:
    script = (CLOUD / "accept-dingtalk-production.sh").read_text(encoding="utf-8")

    assert repr(AGENTS) in script
    assert "allowed_agent_ids" in script
    assert "array_to_json(worker.allowed_agent_ids)::text" in script
    assert "expected_agents_json" in script


def test_production_acceptance_uses_explicit_python_conditions() -> None:
    script = (CLOUD / "accept-dingtalk-production.sh").read_text(encoding="utf-8")

    assert not re.search(r"\bassert\b", script)


@pytest.mark.parametrize(
    "listener",
    [
        "0.0.0.0:9101",
        "127.0.0.1:9102",
        "10.20.30.40:9103",
        "*:9104",
        "[::]:9105",
        "[::1]:9106",
        "[fd00::10]:9107",
        "::ffff:10.0.0.1:9108",
    ],
)
def test_production_acceptance_listener_match_rejects_every_address(
    listener: str,
) -> None:
    pattern = re.compile(r"(?:^|[:])910[1-8]$")

    assert pattern.search(listener)
    script = (CLOUD / "accept-dingtalk-production.sh").read_text(encoding="utf-8")
    assert "grep -Eq '.*:910[1-8]$'" in script


def test_local_worker_assets_never_use_keychain_tunnels_or_password_prompts() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (GENERATOR, BOOTSTRAP, PLIST, INSTALLER)
    ).lower()
    for forbidden in (
        "/usr/bin/security",
        "keychain",
        "ssh -r",
        "password prompt",
        "read -s",
        "sqlite",
    ):
        assert forbidden not in combined
