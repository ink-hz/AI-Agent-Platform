from __future__ import annotations

import base64
import importlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import psycopg
import pytest
import yaml

from test_control_plane_migration import control_database


ROOT = Path(__file__).parents[2]
LOCAL = ROOT / "deploy" / "local-execution-worker"
CLOUD = ROOT / "deploy" / "cloud"
GENERATOR = LOCAL / "generate-worker-key.py"
BOOTSTRAP = LOCAL / "bootstrap-worker-database.sh"
PLIST = LOCAL / "com.orbbec.agent-execution-worker.plist.template"
INSTALLER = LOCAL / "install.sh"
REMOVER = LOCAL / "remove.sh"
AGENTS = [
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
]


def test_local_worker_command_assets_are_executable() -> None:
    for path in (GENERATOR, BOOTSTRAP, INSTALLER, REMOVER):
        assert os.access(path, os.X_OK)


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
        '/bin/launchctl bootout',
        'drop database agent_execution_worker',
        '/bin/rm -f -- "$plist"',
        '/bin/rm -f -- "$private_key"',
        '/bin/rm -f -- "$public_document"',
        '/bin/rm -f -- "$runtime_dsn"',
        '/bin/rm -f -- "$stdout_log"',
        '/bin/rm -f -- "$stderr_log"',
    ):
        assert script.index(mutation) > preflight


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
        "plist": launch_agents / "com.orbbec.agent-execution-worker.plist",
        "private_key": private / "execution-worker-ed25519.key",
        "public_document": runtime / "execution-worker-public.json",
        "runtime_dsn": runtime_dsn,
        "stdout_log": log / "execution-worker.out.log",
        "stderr_log": log / "execution-worker.err.log",
    }
    for name, path in assets.items():
        if name != "runtime_dsn":
            path.write_text(f"{name}\n", encoding="utf-8")
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

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(mode=0o700)
    bootout_marker = tmp_path / "worker-booted-out"
    fake_launchctl = fake_bin / "launchctl"
    fake_launchctl.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "if [[ \"$1\" == \"print\" ]]; then exit 0; fi\n"
        f"if [[ \"$1\" == \"bootout\" ]]; then /usr/bin/touch {bootout_marker}; exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o700)

    remover = runtime / "platform/deploy/local-execution-worker/remove.sh"
    source = REMOVER.read_text(encoding="utf-8")
    source = source.replace("/Users/agentops/AgentRuntime", str(runtime))
    source = source.replace(
        "/Users/agentops/Library/LaunchAgents", str(launch_agents)
    )
    source = source.replace("/bin/launchctl", str(fake_launchctl))
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
        assert all(not path.exists() for path in paths["assets"].values())
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
    assert list(private_dir.iterdir()) == []

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
    assert list(private_dir.iterdir()) == [private]


def test_registration_cli_uses_only_v27_maintenance_functions(
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
        "select platform_control.register_execution_worker_v27",
        "select platform_control.add_execution_worker_key_v27",
        "select platform_control.revoke_execution_worker_key_v27",
        "select platform_control.revoke_execution_worker_v27",
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
def test_v27_maintenance_bounds_dual_key_acceptance_and_rejects_reuse(
    control_database,
) -> None:
    maintenance = control_database["environments"]["production"]["urls"][
        "platform_control_maintenance"
    ]
    worker_id = "agentops-mac-primary"
    with psycopg.connect(maintenance) as connection:
        connection.execute(
            "select platform_control.register_execution_worker_v27(%s,%s,%s,%s,%s,%s)",
            (worker_id, "worker-v1", b"a" * 32, AGENTS, "OPS_20260821", uuid.uuid4()),
        )
        connection.execute(
            "select platform_control.add_execution_worker_key_v27(%s,%s,%s,%s,%s)",
            (worker_id, "worker-v2", b"b" * 32, "OPS_20260822", uuid.uuid4()),
        )
        connection.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.add_execution_worker_key_v27(%s,%s,%s,%s,%s)",
                (worker_id, "worker-v3", b"c" * 32, "OPS_20260823", uuid.uuid4()),
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.add_execution_worker_key_v27(%s,%s,%s,%s,%s)",
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


def test_launchagent_is_agentops_loopback_and_bounded() -> None:
    value = plistlib.loads(PLIST.read_bytes())
    raw = PLIST.read_text(encoding="utf-8")

    assert value["Label"] == "com.orbbec.agent-execution-worker"
    assert value["RunAtLoad"] is True
    assert value["KeepAlive"] is True
    assert value["ThrottleInterval"] >= 10
    assert value["ProcessType"] == "Background"
    assert value["ProgramArguments"][-2:] == [
        "-m",
        "app.execution_relay.worker",
    ]
    assert value["ProgramArguments"][0].startswith("/Users/agentops/")
    environment = value["EnvironmentVariables"]
    assert environment == {
        "PLATFORM_WORKER_ID": "agentops-mac-primary",
        "PLATFORM_WORKER_KEY_ID": "worker-v1",
        "PLATFORM_WORKER_PRIVATE_KEY_FILE": "/Users/agentops/AgentRuntime/private/execution-worker-ed25519.key",
        "PLATFORM_WORKER_DATABASE_URL_FILE": "/Users/agentops/AgentRuntime/private/execution-worker-postgres-dsn",
        "PLATFORM_WORKER_CALLBACK_PORT": "9120",
        "PLATFORM_WORKER_CLOUD_URL": "https://agent.orbbec.com.cn",
        "PLATFORM_METABOT_RUNTIME_CONTRACT": "/Users/agentops/AgentRuntime/metabot/runtime-contract.json",
        "PLATFORM_METABOT_API_SECRET_FILE": "/Users/agentops/AgentRuntime/private/metabot-api-token",
    }
    assert "NetworkState" not in raw
    assert value["StandardOutPath"] != value["StandardErrorPath"]
    for forbidden in ("begin private key", "postgresql://", "bearer ", "password="):
        assert forbidden not in raw.lower()


def test_installer_is_noninteractive_agentops_only_and_permission_gated() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    lowered = script.lower()

    assert '"$(/usr/bin/id -un)" == "agentops"' in script
    assert "plutil -lint" in script
    assert "launchctl bootstrap" in script
    assert "bootstrap-worker-database.sh" in script
    assert "generate-worker-key.py" in script
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


def test_installer_preserves_loaded_state_and_has_strict_rollback() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert "previous_plist" in script
    assert "previous_loaded" in script
    assert "launchctl print" in script
    assert "rollback_install" in script
    assert "trap install_exit EXIT" in script
    assert "execution_worker_install_rollback_failed" in script.lower()
    assert "|| true" not in script
    assert script.index("previous_plist") < script.index("launchctl bootout")


@pytest.mark.parametrize(
    ("old_plist", "old_loaded"),
    [(True, True), (True, False), (False, False)],
    ids=["old-loaded", "old-unloaded", "fresh"],
)
def test_installer_rolls_back_plist_and_exact_loaded_state_with_fake_launchctl(
    tmp_path: Path, old_plist: bool, old_loaded: bool
) -> None:
    runtime = tmp_path / "runtime"
    platform = runtime / "platform"
    local = platform / "deploy/local-execution-worker"
    python = platform / "backend/.venv/bin/python"
    local.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    python.chmod(0o700)
    (local / "bootstrap-worker-database.sh").write_text(
        "#!/bin/bash\nexit 0\n", encoding="utf-8"
    )
    (local / "bootstrap-worker-database.sh").chmod(0o700)
    (local / PLIST.name).write_bytes(PLIST.read_bytes())
    private = runtime / "private"
    metabot = runtime / "metabot"
    private.mkdir(parents=True, mode=0o700)
    metabot.mkdir(mode=0o700)
    runtime.chmod(0o700)
    private.chmod(0o700)
    owner_dsn = private / "owner-dsn"
    owner_dsn.write_text("postgresql://owner:secret@127.0.0.1:5432/postgres\n")
    owner_dsn.chmod(0o600)
    (private / "metabot-api-token").write_text("token")
    (private / "metabot-api-token").chmod(0o600)
    (metabot / "runtime-contract.json").write_text("{}")
    home = tmp_path / "home"
    launch_agents = home / "Library/LaunchAgents"
    launch_agents.mkdir(parents=True)
    target = launch_agents / "com.orbbec.agent-execution-worker.plist"
    old_value = b"<?xml version='1.0'?><plist><!-- OLD --><dict/></plist>\n"
    if old_plist:
        target.write_bytes(old_value)
        target.chmod(0o600)
    state = tmp_path / "launchctl-state"
    state.write_text("loaded" if old_loaded else "unloaded")
    log = tmp_path / "launchctl-log"
    fake_launchctl = tmp_path / "launchctl"
    fake_launchctl.write_text(
        """#!/bin/bash
set -eu
echo "$1" >> "$FAKE_LAUNCHCTL_LOG"
case "$1" in
  print) [[ "$(<"$FAKE_LAUNCHCTL_STATE")" == loaded ]] ;;
  bootout) printf unloaded > "$FAKE_LAUNCHCTL_STATE" ;;
  bootstrap)
    if [[ "${FAIL_NEW_BOOTSTRAP:-0}" == 1 ]] && ! grep -q OLD "$3"; then exit 19; fi
    printf loaded > "$FAKE_LAUNCHCTL_STATE"
    ;;
  enable) ;;
  *) exit 20 ;;
esac
""",
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o700)
    installer_source = INSTALLER.read_text(encoding="utf-8")
    installer_source = installer_source.replace(
        "runtime_root=/Users/agentops/AgentRuntime", f"runtime_root={runtime}"
    ).replace("/bin/launchctl", str(fake_launchctl))
    installer_source = installer_source.replace(
        '"$(/usr/bin/id -un)" == "agentops"',
        f'"$(/usr/bin/id -un)" == "{subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()}"',
    )
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    installer_source = installer_source.replace("700 agentops", f"700 {current_user}")
    installer_source = installer_source.replace("600 agentops", f"600 {current_user}")
    copied_installer = local / "install.sh"
    copied_installer.write_text(installer_source, encoding="utf-8")
    copied_installer.chmod(0o700)

    result = subprocess.run(
        ["/bin/bash", str(copied_installer), str(owner_dsn)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(home),
            "FAKE_LAUNCHCTL_STATE": str(state),
            "FAKE_LAUNCHCTL_LOG": str(log),
            "FAIL_NEW_BOOTSTRAP": "1",
        },
    )

    assert result.returncode == 1
    assert state.read_text() == ("loaded" if old_loaded else "unloaded")
    if old_plist:
        assert target.read_bytes() == old_value
    else:
        assert not target.exists()


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
    lowered = script.lower()

    assert "CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING" in script
    assert "execution-worker-public-keyring.json" in script
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
    assert "worker-v1" in script
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
