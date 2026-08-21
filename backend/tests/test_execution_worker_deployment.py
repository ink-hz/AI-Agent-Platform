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
AGENTS = [
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
]


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


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
    assert "grant select,insert,update" in lowered
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
    ):
        assert attribute in script
    assert "pg_auth_members" in script
    assert "datacl" in script and "datdba" in script
    assert "aclexplode" in script
    assert "information_schema.role_table_grants" in script
    assert "information_schema.role_usage_grants" in script
    assert "public" in script and "acl.grantee=0" in script
    assert "execution worker database grant mismatch" in script
    assert "execution worker unexpected schema grant" in script
    assert "execution worker unexpected table grant" in script
    assert "flywheel" not in script
    assert script.index("trap database_exit exit") < script.index("# read-only collision audit")
    assert "|| true" not in script
    assert "read_text" not in script and "/usr/bin/sed" not in script
    assert "o_nofollow" in script and "dir_fd=" in script and "os.fstat" in script


def test_database_membership_cleanup_retries_and_verifies_without_swallowing_failure() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8").lower()

    assert "for cleanup_attempt in 1 2" in script
    assert "execution_worker_database_membership_rollback_failed" in script
    assert "membership_count" in script
    assert "|| true" not in script


@pytest.mark.parametrize("failure", ["early", "first-revoke"])
def test_database_membership_trap_handles_early_and_transient_revoke_failures(
    tmp_path: Path, failure: str
) -> None:
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    platform = tmp_path / "platform"
    local = platform / "deploy/local-execution-worker"
    schema = platform / "backend/app/execution_relay/worker_schema.sql"
    local.mkdir(parents=True)
    schema.parent.mkdir(parents=True)
    schema.write_text("select 1;\n")
    copied = local / BOOTSTRAP.name
    copied.write_text(
        BOOTSTRAP.read_text(encoding="utf-8").replace("agentops", current_user)
    )
    copied.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    owner_dsn = private / "owner-dsn"
    owner_dsn.write_text("postgresql://owner:secret@127.0.0.1:5432/postgres\n")
    owner_dsn.chmod(0o600)
    runtime_dsn = private / "runtime-dsn"
    counter = tmp_path / "counter"
    membership = tmp_path / "membership"
    counter.write_text("0")
    membership.write_text("clean")
    fake_psql = tmp_path / "psql"
    fake_psql.write_text(
        """#!/bin/bash
set -eu
if [[ "${1:-}" == --version ]]; then
  echo 'psql (PostgreSQL) 17.6'
  exit 0
fi
input="$(</dev/stdin)"
call=$(( $(<"$FAKE_COUNTER") + 1 ))
printf '%s' "$call" > "$FAKE_COUNTER"
if [[ "$FAKE_FAILURE" == early && "$call" == 1 ]]; then exit 7; fi
if [[ "$FAKE_FAILURE" == first-revoke ]]; then
  if [[ "$call" == 2 ]]; then printf member > "$FAKE_MEMBERSHIP"; fi
  if [[ "$call" == 3 ]]; then exit 9; fi
  if [[ "$call" == 4 ]]; then exit 8; fi
  if [[ "$call" == 5 && "$input" == *revoke* ]]; then
    printf clean > "$FAKE_MEMBERSHIP"
  fi
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_psql.chmod(0o700)

    result = subprocess.run(
        ["/bin/bash", str(copied), str(owner_dsn), str(runtime_dsn)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PLATFORM_LOCAL_POSTGRES17_PSQL": str(fake_psql),
            "PLATFORM_LOCAL_PYTHON3": sys.executable,
            "FAKE_COUNTER": str(counter),
            "FAKE_MEMBERSHIP": str(membership),
            "FAKE_FAILURE": failure,
        },
    )

    assert result.returncode != 0
    assert membership.read_text() == "clean"
    assert int(counter.read_text()) == (1 if failure == "early" else 5)
    assert "MEMBERSHIP_ROLLBACK_FAILED" not in result.stderr


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
