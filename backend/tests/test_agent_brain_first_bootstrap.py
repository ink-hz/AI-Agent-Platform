from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
from uuid import UUID, uuid4

import pytest
import psycopg

from app.agent_brain.acceptance_grant import AcceptanceGrantInput, AcceptanceGrantRepository
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.bootstrap_registration import (
    BootstrapRegistrationError,
    BootstrapWorkerDocument,
    ensure_first_worker,
)
from app.execution_relay.content_crypto import ContentCodec
from test_control_plane_migration import control_database
from test_agent_brain_migration import _seed_active_directory


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy/cloud"
LOCAL = ROOT / "deploy/local-execution-worker"
CONTENT = CLOUD / "generate-content-keyring.py"
PROVISION = LOCAL / "provision.sh"
AGENTOPS = LOCAL / "provision-agentops.sh"
ACCEPT = CLOUD / "accept.sh"
REMOTE_STAGE = CLOUD / "remote-stage.sh"
INSTALL = LOCAL / "install.sh"

AGENTS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
    "agent-brain-bot",
)


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_content_keyring_generator_is_atomic_idempotent_and_codec_valid(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = private / "content-encryption-keyring"
    command = [sys.executable, str(CONTENT), str(target)]

    first = subprocess.run(command, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    original = target.read_bytes()
    second = subprocess.run(command, text=True, capture_output=True)

    assert second.returncode == 0, second.stderr
    assert target.read_bytes() == original
    assert _mode(target) == 0o600
    assert re.fullmatch(
        r"CONTENT_KEYRING_(?:CREATED|VALID) fingerprint=[0-9a-f]{64}\n",
        first.stdout,
    )
    assert re.fullmatch(
        r"CONTENT_KEYRING_VALID fingerprint=[0-9a-f]{64}\n", second.stdout
    )
    document = json.loads(original)
    assert document == {
        "active_version": 1,
        "keys": {"1": document["keys"]["1"]},
        "purpose": "platform-content-encryption",
    }
    assert len(base64.b64decode(document["keys"]["1"], validate=True)) == 32
    keyring = IdentityKeyring.from_file(
        target,
        expected_purpose="platform-content-encryption",
        expected_key_length=32,
    )
    assert ContentCodec(keyring).active_key_version == 1
    assert document["keys"]["1"] not in first.stdout + second.stdout
    source = CONTENT.read_text(encoding="utf-8")
    assert "IdentityKeyring.from_file" not in source
    assert "_validate_at(" in source


@pytest.mark.parametrize(
    "unsafe",
    ["parent-symlink", "target-symlink", "world-parent", "world-intermediate"],
)
def test_content_keyring_generator_rejects_unsafe_paths(
    tmp_path: Path, unsafe: str
) -> None:
    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    target = safe / "keyring"
    if unsafe == "parent-symlink":
        real = tmp_path / "real"
        real.mkdir(mode=0o700)
        safe.rmdir()
        safe.symlink_to(real, target_is_directory=True)
    elif unsafe == "target-symlink":
        target.symlink_to(tmp_path / "elsewhere")
    elif unsafe == "world-parent":
        safe.chmod(0o755)
    else:
        safe.chmod(0o777)
        private = safe / "private"
        private.mkdir(mode=0o700)
        target = private / "keyring"

    result = subprocess.run(
        [sys.executable, str(CONTENT), str(target)], text=True, capture_output=True
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "CONTENT_KEYRING_FAILED\n"


class _Cursor:
    def __init__(self, connection, query: str, parameters):
        self.connection = connection
        self.query = " ".join(query.split())
        self.parameters = parameters

    def fetchall(self):
        return self.connection.rows(self.query, self.parameters)

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


class _WorkerConnection:
    def __init__(self, state: dict[str, object], *, lose_response: bool = False):
        self.state = state
        self.lose_response = lose_response
        self.calls: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, parameters=None):
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, parameters))
        if "ensure_first_execution_worker_v33" in normalized:
            worker_id, key_id, public_key, agents, reference, request_id = parameters
            if not self.state:
                self.state.update(
                    worker_id=worker_id,
                    worker_status="active",
                    allowed_agent_ids=tuple(agents),
                    keys=((key_id, bytes(public_key), "active"),),
                    audit=(request_id, reference),
                )
            if self.lose_response:
                self.lose_response = False
                raise ConnectionError("response lost")
            exact = (
                self.state.get("worker_id") == worker_id
                and self.state.get("worker_status") == "active"
                and self.state.get("allowed_agent_ids") == tuple(agents)
                and self.state.get("keys") == ((key_id, bytes(public_key), "active"),)
            )
            if not exact:
                raise BootstrapRegistrationError("mismatch")
            return _StaticCursor([("existing" if self.state.get("audit") else "registered",)])
        return _Cursor(self, normalized, parameters)

    def rows(self, query: str, _parameters):
        if "from platform_control.execution_workers" in query:
            if not self.state:
                return []
            return [
                (
                    self.state["worker_id"],
                    self.state["worker_status"],
                    list(self.state["allowed_agent_ids"]),
                )
            ]
        if "from platform_control.execution_worker_keys" in query:
            return list(self.state.get("keys", ()))
        return []


def _worker_document() -> BootstrapWorkerDocument:
    return BootstrapWorkerDocument(
        worker_id="agentops-mac-primary",
        key_id="worker-v1",
        public_key=b"p" * 32,
        allowed_agent_ids=AGENTS,
    )


def test_first_worker_registration_handles_absent_exact_and_response_lost() -> None:
    state: dict[str, object] = {}
    connection = _WorkerConnection(state, lose_response=True)
    document = _worker_document()

    with pytest.raises(ConnectionError):
        ensure_first_worker(connection, document)
    outcome = ensure_first_worker(connection, document)
    replay = ensure_first_worker(connection, document)

    assert outcome == replay == "existing"
    registrations = [
        call for call in connection.calls if "ensure_first_execution_worker_v33" in call[0]
    ]
    assert len(registrations) == 3
    assert {call[1][-1] for call in registrations} == {
        UUID("8e03a2df-8413-4b38-9d51-6f970e1fd2a4")
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_status", "revoked"),
        ("allowed_agent_ids", ("hr-bot",)),
        ("keys", ()),
        ("keys", (("worker-v1", b"x" * 32, "active"),)),
        ("keys", (("worker-v1", b"p" * 32, "revoked"),)),
    ],
)
def test_first_worker_registration_rejects_partial_or_mismatch(field, value) -> None:
    document = _worker_document()
    state: dict[str, object] = {
        "worker_id": document.worker_id,
        "worker_status": "active",
        "allowed_agent_ids": document.allowed_agent_ids,
        "keys": ((document.key_id, document.public_key, "active"),),
    }
    state[field] = value
    with pytest.raises(BootstrapRegistrationError):
        ensure_first_worker(_WorkerConnection(state), document)


def test_remote_stage_migrates_brain_disabled_then_bootstraps_worker_exactly() -> None:
    source = REMOTE_STAGE.read_text(encoding="utf-8")
    migration = source.index("python -m app.cloud_replica.cli migrate")
    registration = source.index("app.execution_relay.bootstrap_registration")
    api_start = source.index(
        '"${compose[@]}" up -d --force-recreate', registration
    )

    assert migration < registration < api_start
    assert "PLATFORM_AGENT_BRAIN_ENABLED=0" in source[: registration + 1]
    assert "execution-worker-public-keyring.json" in source


def test_remote_stage_rejects_non_executable_bootstrap_helpers_before_mutation(
    tmp_path: Path,
) -> None:
    source = REMOTE_STAGE.read_text(encoding="utf-8")
    manifest_gate = '(cd "$release_path" && /usr/bin/sha256sum --check MANIFEST.sha256 >/dev/null) || fail'
    mutation = 'image_name="orbbec-agent-platform:$release_sha"'
    gate = source.split(manifest_gate, 1)[1].split("signing_public=", 1)[0]
    assert source.index(manifest_gate) < source.index("bootstrap-control-db.sh")
    assert source.index("bootstrap-dingtalk-production-secrets.sh") < source.index(mutation)

    release = tmp_path / "release"
    cloud = release / "deploy/cloud"
    cloud.mkdir(parents=True)
    for name in (
        "bootstrap-control-db.sh",
        "bootstrap-dingtalk-production-secrets.sh",
    ):
        helper = cloud / name
        helper.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        helper.chmod(0o700)
    (cloud / "bootstrap-control-db.sh").chmod(0o600)
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            "set -eEuo pipefail\nfail() { exit 91; }\n"
            f"release_path={shlex.quote(str(release))}\n{gate}",
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 91


@pytest.mark.parametrize("fail_migration", [False, True])
def test_remote_stage_bootstrap_transaction_executes_in_order_and_fails_closed(
    tmp_path: Path, fail_migration: bool
) -> None:
    source = REMOTE_STAGE.read_text(encoding="utf-8")
    start = 'PLATFORM_AGENT_BRAIN_ENABLED="${PLATFORM_AGENT_BRAIN_ENABLED:-0}"'
    block = start + source.split(start, 1)[1].split(
        '/usr/bin/test ! -e "$worker_keyring_previous"', 1
    )[0]
    fake_docker = tmp_path / "docker"
    log = tmp_path / "transaction.log"
    fake_docker.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        f"log={shlex.quote(str(log))}\n"
        "if [[ \"$1\" == compose ]]; then\n"
        "  case \" $* \" in *' ps -q platform-postgres '*) echo postgres-id;; esac\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$1\" == inspect ]]; then echo healthy; exit 0; fi\n"
        "if [[ \" $* \" == *' app.cloud_replica.cli migrate '* ]]; then\n"
        "  printf 'migrate:%s\\n' \"${PLATFORM_AGENT_BRAIN_ENABLED:-unset}\" >> \"$log\"\n"
        f"  [[ {1 if fail_migration else 0} == 0 ]] || exit 81\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \" $* \" == *' app.execution_relay.bootstrap_registration '* ]]; then\n"
        "  [[ \" $* \" == *' -e PLATFORM_AGENT_BRAIN_ENABLED=0 '* ]] || exit 82\n"
        "  echo register >> \"$log\"\n"
        "  echo 'EXECUTION_WORKER_BOOTSTRAP_OK status=registered fingerprint="
        + "f" * 64
        + "'\n  exit 0\nfi\nexit 83\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o700)
    release = tmp_path / "release"
    bootstrap = release / "deploy/cloud/bootstrap-control-db.sh"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        f"echo control >> {shlex.quote(str(log))}\n"
        "echo 'CONTROL_DATABASE_CREDENTIALS_READY version=2'\n",
        encoding="utf-8",
    )
    bootstrap.chmod(0o700)
    private = tmp_path / "private"
    stage = tmp_path / "stage"
    private.mkdir()
    stage.mkdir()
    environment_path = tmp_path / "release.env"
    script = "\n".join(
        (
            "set -eEuo pipefail",
            "fail() { exit 91; }",
            f"image_name=image:test",
            f"environment_path={shlex.quote(str(environment_path))}",
            f"release_path={shlex.quote(str(release))}",
            f"private_path={shlex.quote(str(private))}",
            f"stage_path={shlex.quote(str(stage))}",
            block.replace("/usr/bin/docker", str(fake_docker)).replace(
                '/bin/chown root:root "$environment_path"', ":"
            ),
        )
    )

    result = subprocess.run(["/bin/bash", "-c", script], text=True, capture_output=True)

    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    if fail_migration:
        assert result.returncode != 0
        assert lines == ["migrate:0"]
    else:
        assert result.returncode == 0, result.stderr
        assert lines == ["migrate:0", "control", "register"]


class _GrantConnection:
    def __init__(self, *, conflict: bool = False):
        self.conflict = conflict
        self.granted = False
        self.calls: list[tuple[str, object]] = []
        self.identity_reads = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, parameters=None):
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, parameters))
        if "grant_agent_brain_acceptance_v33" in normalized:
            if self.conflict:
                raise RuntimeError("collision")
            self.granted = True
            return _StaticCursor([(True, True)])
        if "has_agent_use_scope_v29" in normalized:
            agent = parameters[1]
            return _StaticCursor([(self.granted and agent == "hr-bot",)])
        if "from platform_control.audit_events" in normalized:
            return _StaticCursor([] if not self.granted else [(parameters[0],)])
        return _StaticCursor([])


class _StaticCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def test_acceptance_grant_uses_predeclared_ids_and_verifies_allow_and_deny() -> None:
    actor = uuid4()
    member = uuid4()
    grant_id = uuid4()
    request_id = uuid4()
    value = AcceptanceGrantInput(
        actor_internal_user_id=actor,
        member_internal_user_id=member,
        grant_id=grant_id,
        request_id=request_id,
    )
    connection = _GrantConnection()

    result = AcceptanceGrantRepository(connection).apply(value)

    assert result == {"hr-bot": True, "marketing-gtm-bot": False}
    grant_call = next(
        call for call in connection.calls if "grant_agent_brain_acceptance_v33" in call[0]
    )
    assert grant_call[1] == (
        grant_id,
        member,
        actor,
        "AGENT_BRAIN_ACCEPTANCE_001",
        request_id,
    )


def test_acceptance_grant_input_accepts_account_results_but_not_names_or_provider_ids() -> None:
    actor = uuid4()
    member = uuid4()
    grant_id = uuid4()
    request_id = uuid4()
    document = {
        "schema_version": 1,
        "actor": {
            "internal_user_id": str(actor), "display_name": "ignored",
            "role": "platform_owner", "departments": [],
            "observation_agent_ids": [], "directory_freshness": "fresh",
            "hard_stale_read_only": False, "csrf_token": "ignored",
        },
        "member": {
            "internal_user_id": str(member), "display_name": "ignored",
            "role": "member", "departments": [],
            "observation_agent_ids": [], "directory_freshness": "fresh",
            "hard_stale_read_only": False, "csrf_token": "ignored",
        },
        "grant_id": str(grant_id),
        "request_id": str(request_id),
    }
    assert AcceptanceGrantInput.from_document(document).member_internal_user_id == member
    admin_document = {
        **document,
        "actor": {**document["actor"], "role": "platform_admin"},
    }
    assert AcceptanceGrantInput.from_document(admin_document).actor_internal_user_id == actor
    for forbidden in (
        {**document, "member": {"display_name": "Somebody"}},
        {**document, "member": {"provider_user_id": "secret"}},
    ):
        with pytest.raises(ValueError):
            AcceptanceGrantInput.from_document(forbidden)


@pytest.mark.postgres
def test_first_worker_registration_real_database_is_idempotent(control_database) -> None:
    environment = control_database["environments"]["production"]
    document = BootstrapWorkerDocument(
        worker_id="agentops-mac-primary",
        key_id="worker-v1",
        public_key=os.urandom(32),
        allowed_agent_ids=AGENTS,
    )
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as connection:
        assert ensure_first_worker(connection, document) == "registered"
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as connection:
        assert ensure_first_worker(connection, document) == "existing"
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.ensure_first_execution_worker_v33("
                "%s,%s,%s,%s,null,%s)",
                (
                    document.worker_id,
                    document.key_id,
                    document.public_key,
                    list(document.allowed_agent_ids),
                    uuid4(),
                ),
            )


@pytest.mark.postgres
def test_bootstrap_functions_are_maintenance_only_and_mismatch_fails_closed(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        function_signatures = (
            "platform_control.ensure_first_execution_worker_v33(text,text,bytea,text[],text,uuid)",
            "platform_control.grant_agent_brain_acceptance_v33(uuid,uuid,uuid,text,uuid)",
        )
        with psycopg.connect(environment["admin"]) as connection:
            for role in environment["roles"]:
                privileges = [
                    connection.execute(
                        "select has_function_privilege(%s,%s,'execute')",
                        (role, signature),
                    ).fetchone()[0]
                    for signature in function_signatures
                ]
                expected = role in {
                    "platform_control_maintenance",
                    "platform_control_maintenance_preview",
                }
                assert privileges == [expected, expected]

    environment = control_database["environments"]["preview"]
    document = BootstrapWorkerDocument(
        worker_id="agentops-mac-primary",
        key_id="worker-v1",
        public_key=os.urandom(32),
        allowed_agent_ids=AGENTS,
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_workers "
            "(worker_id,allowed_agent_ids,status,revoked_at) "
            "values (%s,%s,'revoked',now())",
            (document.worker_id, list(document.allowed_agent_ids)),
        )
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance_preview"]
    ) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            ensure_first_worker(connection, document)


@pytest.mark.postgres
def test_acceptance_grant_real_database_replays_same_ids_and_rejects_conflict(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    actor, grant_id, request_id = (uuid4() for _ in range(3))
    with psycopg.connect(environment["admin"]) as connection:
        member, _root, _child, _generation = _seed_active_directory(connection)
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role) values "
            "(%s,'Bootstrap Owner','active','platform_owner')",
            (actor,),
        )
    value = AcceptanceGrantInput(actor, member, grant_id, request_id)
    maintenance = environment["urls"]["platform_control_maintenance"]
    for _ in range(2):
        with psycopg.connect(maintenance) as connection:
            assert AcceptanceGrantRepository(connection).apply(value) == {
                "hr-bot": True,
                "marketing-gtm-bot": False,
            }
    with psycopg.connect(maintenance) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            AcceptanceGrantRepository(connection).apply(
                AcceptanceGrantInput(actor, member, uuid4(), request_id)
            )


@pytest.mark.postgres
def test_acceptance_grant_replay_rejects_revoked_exact_grant_masked_by_broad_grant(
    control_database,
) -> None:
    environment = control_database["environments"]["preview"]
    actor, grant_id, request_id = (uuid4() for _ in range(3))
    with psycopg.connect(environment["admin"]) as connection:
        member, _root, _child, _generation = _seed_active_directory(connection)
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role) values "
            "(%s,'Bootstrap Owner','active','platform_owner')",
            (actor,),
        )
    value = AcceptanceGrantInput(actor, member, grant_id, request_id)
    maintenance = environment["urls"]["platform_control_maintenance_preview"]
    with psycopg.connect(maintenance) as connection:
        AcceptanceGrantRepository(connection).apply(value)
        connection.execute(
            "select platform_control.revoke_agent_use_scope_v29("
            "%s,%s,'AGENT_BRAIN_ACCEPTANCE_REVOKE_001',%s)",
            (grant_id, actor, uuid4()),
        )
        connection.execute(
            "select platform_control.grant_agent_use_scope_v29("
            "%s,'hr-bot','all_members',null,null,false,%s,"
            "'AGENT_BRAIN_ACCEPTANCE_BROAD_001',%s)",
            (uuid4(), actor, uuid4()),
        )
    with psycopg.connect(maintenance) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            AcceptanceGrantRepository(connection).apply(value)


@pytest.mark.postgres
def test_acceptance_grant_allows_active_platform_admin_actor(control_database) -> None:
    environment = control_database["environments"]["preview"]
    actor, grant_id, request_id = (uuid4() for _ in range(3))
    with psycopg.connect(environment["admin"]) as connection:
        member, _root, _child, _generation = _seed_active_directory(connection)
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role) values "
            "(%s,'Bootstrap Admin','active','platform_admin')",
            (actor,),
        )
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance_preview"]
    ) as connection:
        assert AcceptanceGrantRepository(connection).apply(
            AcceptanceGrantInput(actor, member, grant_id, request_id)
        ) == {"hr-bot": True, "marketing-gtm-bot": False}
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select * from platform_control.grant_agent_brain_acceptance_v33("
                "%s,%s,%s,null,%s)",
                (uuid4(), member, actor, uuid4()),
            )


def test_local_provisioning_wrapper_has_narrow_hba_transaction_and_fixed_sudo() -> None:
    source = PROVISION.read_text(encoding="utf-8")
    helper = AGENTOPS.read_text(encoding="utf-8")
    combined = source + helper

    assert source.startswith("#!/bin/bash\nset -eEuo pipefail\numask 077\n")
    assert "/Users/neo/FlywheelData/socket" in source
    assert "/Users/agentops/AgentRuntime/deploy-tools/provision-agentops.sh" in source
    assert "/Users/agentops/AgentRuntime/deploy-tools/reliability/sanitized-pm2.sh" in source
    assert " archive --format=tar" in source and "release_sha" in source
    assert '"$metabot_release_sha:scripts/reliability/sanitized-pm2.sh"' in source
    assert "HOME=/Users/agentops" in source
    assert '-p 5432 -U neo' in source
    assert "current_setting('port')" in source
    assert '"$agentops_helper" finalize' in source
    assert "# BEGIN ORBBEC AGENT EXECUTION WORKER" in source
    assert "host agent_execution_worker agent_execution_worker_runtime 127.0.0.1/32 scram-sha-256" in source
    assert "host postgres" in source and "scram-sha-256" in source
    assert "trap cleanup ERR EXIT" in source
    assert "drop role if exists" in source.lower()
    assert "pg_reload_conf" in source
    assert "cmp -s" in source
    assert "/usr/bin/sudo -n -u agentops" in source
    assert '"$agentops_helper" prepare' in source
    assert '"$agentops_helper" install' in source
    assert "security " not in combined.lower()
    assert "read -s" not in combined
    assert "sudo -S" not in combined
    assert "su -" not in combined
    assert "/Users/agentops/Library/Application Support/MetaBotReliability/api-secret" in helper
    assert "127\\.0\\.0\\.1:9120" in helper
    assert "snapshot-except metabot-agent-brain" not in helper
    assert 'deploy_tools="$runtime/deploy-tools"' in helper
    assert 'snapshot="$deploy_tools/reliability/sanitized-pm2.sh"' in helper
    assert "helper_source=" not in helper
    assert ".platform-release.json" in helper
    assert '"$snapshot" jlist' in helper
    assert "restart_time" in helper and "pm_exec_path" in helper
    assert "worker_listener_pid" in helper and "launchd_pid" in helper
    assert "--checksum" in helper and "--delete" in helper
    assert "for relative in" not in helper
    assert "os.O_EXCL" in helper
    assert "O_NOFOLLOW" in helper
    assert '/bin/cat > "$owner_dsn"' not in helper
    assert "/usr/bin/printf '%s\\n' \"$owner_dsn\"" not in source
    assert '/usr/bin/printf "set password_encryption' not in source
    assert source.index("EXECUTION_WORKER_PROVISION_OK") < source.index("exit 0")
    assert 'echo EXECUTION_WORKER_PROVISION_OK' not in source.split("trap cleanup ERR EXIT", 1)[1]
    assert 'domain="user/$(/usr/bin/id -u)"' in INSTALL.read_text(encoding="utf-8")
    accept_source = ACCEPT.read_text(encoding="utf-8")
    assert 'worker_domain="user/$agentops_uid"' in accept_source
    assert '"$worker_domain/$worker_label"' in accept_source
    subprocess.run(["/bin/bash", "-n", str(PROVISION)], check=True)
    subprocess.run(["/bin/bash", "-n", str(AGENTOPS)], check=True)


def test_real_host_agentops_account_socket_home_and_launchd_domain() -> None:
    socket = Path("/Users/neo/FlywheelData/socket/.s.PGSQL.5432")
    psql = Path("/opt/homebrew/opt/postgresql@17/bin/psql")
    if not socket.exists() or not psql.exists() or not Path("/Users/agentops").is_dir():
        pytest.skip("production dual-account host boundary is unavailable")
    postgres = subprocess.run(
        [
            str(psql), "-h", str(socket.parent), "-p", "5432", "-U", "neo",
            "-XAt", "-d", "postgres", "-c",
            "select current_user || ':' || current_setting('port')",
        ],
        text=True,
        capture_output=True,
        env={**os.environ, "PGPORT": "65432", "PGUSER": "wrong-user"},
    )
    assert postgres.returncode == 0 and postgres.stdout.strip() == "neo:5432"
    base = [
        "/usr/bin/sudo", "-n", "-u", "agentops", "/usr/bin/env", "-i",
        "HOME=/Users/agentops", "USER=agentops", "LOGNAME=agentops",
        "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
    ]
    account = subprocess.run(
        [*base, "/usr/bin/printenv", "HOME"],
        text=True,
        capture_output=True,
    )
    identity = subprocess.run(
        [*base, "/usr/bin/id", "-un"],
        text=True,
        capture_output=True,
    )
    launchd = subprocess.run(
        [
            *base, "/bin/launchctl", "print", "user/502",
        ],
        text=True,
        capture_output=True,
    )
    assert account.returncode == 0 and account.stdout.strip() == "/Users/agentops"
    assert identity.returncode == 0 and identity.stdout.strip() == "agentops"
    assert launchd.returncode == 0, launchd.stderr


def _provision_harness(
    tmp_path: Path,
    *,
    fail_install: bool,
    fail_finalize: bool = False,
    fail_stage: str = "",
    original: bytes = b"# user-owned header\nlocal all all trust\n",
):
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    root = tmp_path / "repo"
    local = root / "deploy/local-execution-worker"
    local.mkdir(parents=True)
    pm2_source = root / "scripts/reliability/sanitized-pm2.sh"
    pm2_source.parent.mkdir(parents=True)
    pm2_source.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    pm2_source.chmod(0o700)
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    hba = tmp_path / "pg_hba.conf"
    hba.write_bytes(original)
    hba.chmod(0o600)
    log = tmp_path / "commands"
    fake_psql = tmp_path / "psql"
    fake_psql.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"$HARNESS_LOG\"\n"
        "if [[ \"$1\" == '--version' ]]; then echo 'psql (PostgreSQL) 17.9'; exit 0; fi\n"
        "if [[ ! -t 0 ]]; then IFS= read -r sql || true; "
        "[[ \"$sql\" == drop\\ role\\ if\\ exists* ]] && echo drop-role >> \"$HARNESS_LOG\"; "
        "[[ \"$sql\" == set\\ password_encryption* && \"${FAKE_FAIL_STAGE:-}\" == role ]] && exit 73; "
        "if [[ \"$sql\" == set\\ password_encryption* && \"${FAKE_FAIL_STAGE:-}\" == role-response-loss ]]; then echo role-created-before-loss >> \"$HARNESS_LOG\"; exit 74; fi; fi\n"
        "case \"$*\" in\n"
        "  *\"select current_user || ':' || current_setting('port')\"*) echo neo:5432;;\n"
        "  *'show port'*) echo 5432;;\n"
        f"  *'show hba_file'*) echo {hba};;\n"
        "  *'select pg_reload_conf()'*) if [[ \"${FAKE_FAIL_STAGE:-}\" == reload && ! -e \"$FAKE_RELOAD_MARKER\" ]]; then : > \"$FAKE_RELOAD_MARKER\"; echo f; else echo t; fi;;\n"
        "  *'select count(*) from pg_hba_file_rules where error is not null'*) echo 0;;\n"
        "  *'pg_hba_file_rules'*) if grep -q '^host postgres agent_execution_bootstrap_' \"$FAKE_HBA\"; then [[ \"${FAKE_FAIL_STAGE:-}\" == validate ]] && echo 0:0:1 || echo 0:1:1; elif [[ \"${FAKE_FINALIZE_FAIL:-0}\" == 1 ]]; then echo 0:0:0; else echo 0:1:0; fi;;\n"
        "  *) :;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o700)
    fake_helper = local / "provision-agentops.sh"
    install_action = (
        "exit 71"
        if fail_install
        else "if [[ \"${FAKE_FAIL_STAGE:-}\" == restore ]]; then "
        "/bin/rm -f \"$FAKE_HBA\"; /bin/ln -s \"$FAKE_RESTORE_TARGET\" "
        "\"$FAKE_HBA\"; exit 71; fi; "
        "if [[ \"${FAKE_FAIL_STAGE:-}\" == concurrent-edit ]]; then "
        "printf '# concurrent owner edit\\n' >> \"$FAKE_HBA\"; exit 71; fi; "
        "echo EXECUTION_WORKER_AGENTOPS_READY"
    )
    fake_helper.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        f"HARNESS_LOG={shlex.quote(str(log))}\n"
        f"FAKE_FAIL_STAGE={shlex.quote(fail_stage)}\n"
        f"FAKE_HBA={shlex.quote(str(hba))}\n"
        f"FAKE_RESTORE_TARGET={shlex.quote(str(tmp_path / 'restore-target'))}\n"
        "printf 'helper:%s\\n' \"$1\" >> \"$HARNESS_LOG\"\n"
        "case \"$1\" in\n"
        " stage) /bin/cat >/dev/null; echo EXECUTION_WORKER_AGENTOPS_STAGED;;\n"
        " prepare) IFS= read -r secret; [[ \"$secret\" == postgresql://* ]]; [[ \"${FAKE_FAIL_STAGE:-}\" != prepare ]];;\n"
        f" install) {install_action};;\n"
        " commit) :;; finalize) :;; rollback) :;; cleanup) :;; *) exit 72;; esac\n",
        encoding="utf-8",
    )
    fake_helper.chmod(0o700)
    fake_sudo = tmp_path / "sudo"
    fake_sudo.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "[[ \"$1 $2 $3\" == '-n -u agentops' ]] || exit 90\n"
        "shift 3\nexec \"$@\"\n",
        encoding="utf-8",
    )
    fake_sudo.chmod(0o700)
    copied = local / "provision.sh"
    source = PROVISION.read_text(encoding="utf-8")
    source = source.replace('"$(/usr/bin/id -un)" == "neo"', f'"$(/usr/bin/id -un)" == "{current_user}"')
    source = source.replace(
        "psql_bin=/opt/homebrew/opt/postgresql@17/bin/psql", f"psql_bin={fake_psql}"
    ).replace(
        "metabot_repository=/Users/neo/Developer/work/Orbbec-Agent-Team",
        f"metabot_repository={root}",
    ).replace(
        "psql_socket=/Users/neo/FlywheelData/socket", f"psql_socket={runtime_root}"
    ).replace(
        "agentops_helper=/Users/agentops/AgentRuntime/deploy-tools/provision-agentops.sh",
        f"agentops_helper={fake_helper}",
    ).replace(
        "agentops_pm2_tool=/Users/agentops/AgentRuntime/deploy-tools/reliability/sanitized-pm2.sh",
        f"agentops_pm2_tool={tmp_path / 'agentops-tools/reliability/sanitized-pm2.sh'}",
    ).replace(
        "/Users/neo/FlywheelData", str(runtime_root)
    ).replace(
        '-S "$psql_socket/.s.PGSQL.5432"', '-x "$psql_bin"'
    ).replace("/usr/bin/sudo", str(fake_sudo))
    if fail_stage == "postrename":
        source = source.replace(
            "try: os.fsync(directory)",
            'try: raise OSError("injected post-rename failure")',
            1,
        )
    copied.write_text(source, encoding="utf-8")
    copied.chmod(0o700)
    subprocess.run(["/usr/bin/git", "init", "-q", str(root)], check=True)
    subprocess.run(["/usr/bin/git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "/usr/bin/git", "-C", str(root), "-c", "user.name=Task9B",
            "-c", "user.email=task9b@example.invalid", "commit", "-qm", "fixture",
        ],
        check=True,
    )
    if fail_stage == "write":
        (tmp_path / ".pg_hba.conf.agent-worker.part").write_text("occupied")
    restore_target = tmp_path / "restore-target"
    restore_target.write_text("do-not-touch", encoding="utf-8")
    reload_marker = tmp_path / "reload-marker"
    result = subprocess.run(
        ["/bin/bash", str(copied)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HARNESS_LOG": str(log),
            "FAKE_HBA": str(hba),
            "FAKE_FINALIZE_FAIL": "1" if fail_finalize else "0",
            "FAKE_FAIL_STAGE": fail_stage,
            "FAKE_RELOAD_MARKER": str(reload_marker),
            "FAKE_RESTORE_TARGET": str(restore_target),
        },
    )
    return result, hba, original, log


def test_local_provision_success_removes_bootstrap_hba_and_role(tmp_path: Path) -> None:
    result, hba, original, log = _provision_harness(tmp_path, fail_install=False)
    assert result.returncode == 0, result.stderr
    expected = (
        b"# BEGIN ORBBEC AGENT EXECUTION WORKER\n"
        b"host agent_execution_worker agent_execution_worker_runtime "
        b"127.0.0.1/32 scram-sha-256\n"
        b"# END ORBBEC AGENT EXECUTION WORKER\n"
    ) + original
    assert hba.read_bytes() == expected
    calls = log.read_text(encoding="utf-8")
    assert "helper:prepare" in calls and "helper:install" in calls
    assert "helper:commit" in calls and "helper:finalize" in calls and "drop-role" in calls
    assert (tmp_path / "agentops-tools/reliability/sanitized-pm2.sh").read_text(
        encoding="utf-8"
    ) == "#!/bin/bash\nexit 0\n"
    assert "postgresql://" not in result.stdout + result.stderr + calls


def test_local_provision_preserves_non_managed_bytes_without_trailing_newline(
    tmp_path: Path,
) -> None:
    original = b"# exact-without-newline"
    result, hba, _ignored, _log = _provision_harness(
        tmp_path, fail_install=False, original=original
    )
    assert result.returncode == 0, result.stderr
    managed = (
        b"# BEGIN ORBBEC AGENT EXECUTION WORKER\n"
        b"host agent_execution_worker agent_execution_worker_runtime "
        b"127.0.0.1/32 scram-sha-256\n"
        b"# END ORBBEC AGENT EXECUTION WORKER\n"
    )
    assert hba.read_bytes() == managed + original


def test_local_provision_failure_restores_hba_and_cleans_bootstrap(tmp_path: Path) -> None:
    result, hba, original, log = _provision_harness(tmp_path, fail_install=True)
    assert result.returncode != 0
    assert hba.read_bytes() == original
    calls = log.read_text(encoding="utf-8")
    assert "helper:rollback" in calls and "helper:cleanup" in calls and "drop-role" in calls
    assert "postgresql://" not in result.stdout + result.stderr + calls


def test_local_provision_finalize_failure_removes_temporary_hba_and_restores(
    tmp_path: Path,
) -> None:
    result, hba, original, log = _provision_harness(
        tmp_path, fail_install=False, fail_finalize=True
    )
    assert result.returncode != 0
    assert hba.read_bytes() == original
    calls = log.read_text(encoding="utf-8")
    assert "helper:rollback" in calls and "helper:cleanup" in calls and "drop-role" in calls
    assert "postgresql://" not in result.stdout + result.stderr + calls


@pytest.mark.parametrize(
    "stage",
    [
        "role",
        "role-response-loss",
        "write",
        "postrename",
        "reload",
        "validate",
        "prepare",
    ],
)
def test_local_provision_failure_injection_restores_every_preinstall_boundary(
    tmp_path: Path, stage: str
) -> None:
    result, hba, original, log = _provision_harness(
        tmp_path, fail_install=False, fail_stage=stage
    )
    assert result.returncode != 0
    assert hba.read_bytes() == original
    calls = log.read_text(encoding="utf-8")
    assert "helper:cleanup" in calls
    assert "drop-role" in calls
    if stage == "role-response-loss":
        assert "role-created-before-loss" in calls
    assert "postgresql://" not in result.stdout + result.stderr + calls


def test_local_provision_preserves_backup_when_atomic_restore_is_impossible(
    tmp_path: Path,
) -> None:
    result, hba, _original, log = _provision_harness(
        tmp_path, fail_install=False, fail_stage="restore"
    )
    assert result.returncode != 0
    assert hba.is_symlink()
    assert list(tmp_path.glob(".pg_hba.agent-worker.*"))
    assert "EXECUTION_WORKER_HBA_BACKUP_PRESERVED" in result.stderr
    assert "drop-role" in log.read_text(encoding="utf-8")


def test_local_provision_never_overwrites_concurrent_hba_owner_edit(tmp_path: Path) -> None:
    result, hba, original, log = _provision_harness(
        tmp_path, fail_install=False, fail_stage="concurrent-edit"
    )
    assert result.returncode != 0
    assert hba.read_bytes().endswith(b"# concurrent owner edit\n")
    assert hba.read_bytes() != original
    assert list(tmp_path.glob(".pg_hba.agent-worker.*"))
    assert "EXECUTION_WORKER_HBA_BACKUP_PRESERVED" in result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "helper:rollback" in calls and "drop-role" in calls


def test_agentops_stage_retries_after_interrupted_venv_without_mutable_worktree(
    tmp_path: Path,
) -> None:
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    release_sha = "a" * 40
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
        for name, raw in (
            ("backend/requirements.txt", b""),
            ("committed-release.txt", b"reviewed-commit\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(raw))
    archive_raw = archive_buffer.getvalue()
    archive_sha = hashlib.sha256(archive_raw).hexdigest()

    stale = runtime / f".platform.first-bootstrap.{release_sha}"
    stale.mkdir()
    (stale / "stale").write_text("interrupted", encoding="utf-8")
    transient_marker = tmp_path / "venv-failed-once"
    fake_python = tmp_path / "python3.11"
    fake_python.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        f"if [[ ! -e {shlex.quote(str(transient_marker))} ]]; then "
        f": > {shlex.quote(str(transient_marker))}; exit 71; fi\n"
        "exec /opt/homebrew/bin/python3.11 \"$@\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o700)
    copied = tmp_path / "provision-agentops.sh"
    source = AGENTOPS.read_text(encoding="utf-8")
    source = source.replace(
        '"$(/usr/bin/id -un)" == "agentops"',
        f'"$(/usr/bin/id -un)" == "{current_user}"',
    ).replace(
        '[[ "${HOME:-}" == /Users/agentops && "${USER:-}" == agentops && "${LOGNAME:-}" == agentops ]] || fail',
        '[[ -n "${HOME:-}" && -n "${USER:-}" && -n "${LOGNAME:-}" ]] || fail',
    ).replace(
        "cd /Users/agentops || fail", f"cd {shlex.quote(str(tmp_path))} || fail"
    ).replace(
        "runtime=/Users/agentops/AgentRuntime", f"runtime={runtime}"
    ).replace(
        "/opt/homebrew/bin/python3.11", str(fake_python)
    )
    copied.write_text(source, encoding="utf-8")
    copied.chmod(0o700)
    command = ["/bin/bash", str(copied), "stage", release_sha, archive_sha]

    first = subprocess.run(command, input=archive_raw, capture_output=True)

    platform = runtime / "platform"
    venv_stage = platform / "backend" / f".venv.first-bootstrap.{release_sha}"
    staged_archive = runtime / f".platform-release.{release_sha}.tar"
    assert first.returncode != 0
    assert (platform / "committed-release.txt").read_bytes() == b"reviewed-commit\n"
    assert not stale.exists() and not venv_stage.exists() and not staged_archive.exists()

    second = subprocess.run(command, input=archive_raw, capture_output=True)

    assert second.returncode == 0, second.stderr.decode()
    assert b"EXECUTION_WORKER_AGENTOPS_STAGED" in second.stdout
    assert (platform / "committed-release.txt").read_bytes() == b"reviewed-commit\n"
    assert (platform / "backend/.venv/.orbbec-release").read_text().strip() == release_sha
    assert not stale.exists() and not venv_stage.exists() and not staged_archive.exists()


def _agentops_install_harness(
    tmp_path: Path, failure: str
) -> tuple[subprocess.CompletedProcess, Path, Path, Path, dict[str, str]]:
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    runtime = tmp_path / "runtime"
    private = runtime / "private"
    platform = runtime / "platform"
    local = platform / "deploy/local-execution-worker"
    local.mkdir(parents=True)
    private.mkdir(parents=True)
    owner_dsn = private / "postgres-owner-dsn"
    owner_dsn.write_text("opaque", encoding="utf-8")
    owner_dsn.chmod(0o600)
    before = private / "worker-provision-nonbrain-before.json"
    before.write_text("[]\n", encoding="utf-8")
    before.chmod(0o600)
    brain = {
        "name": "metabot-agent-brain",
        "pid": 100,
        "pm_id": 8,
        "status": "online",
        "restart_time": 2,
        "created_at": 1234,
        "pm_exec_path": "/reviewed/brain.js",
        "pm_cwd": "/reviewed/brain",
        "args": [],
    }
    brain_before = private / "worker-provision-brain-before.txt"
    brain_before.write_text(json.dumps(brain, sort_keys=True, separators=(",", ":")) + "\n")
    brain_before.chmod(0o600)
    worker_plist = tmp_path / "com.orbbec.agent-execution-worker.plist"
    worker_plist.write_text("OLD-WORKER-PLIST\n", encoding="utf-8")
    worker_plist.chmod(0o600)
    launchd_state = tmp_path / "launchd-state"
    launchd_state.write_text("loaded\n", encoding="utf-8")
    install = local / "install.sh"
    install.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "printf 'NEW-WORKER-PLIST\\n' > \"$FAKE_WORKER_PLIST\"\n"
        "[[ \"${FAKE_FAILURE:-}\" != install ]]\n",
        encoding="utf-8",
    )
    install.chmod(0o700)
    snapshot = tmp_path / "snapshot"
    raw_brain = {
        "name": "metabot-agent-brain",
        "pid": 101 if failure == "pm2" else 100,
        "pm_id": 8,
        "pm2_env": {
            "status": "online",
            "restart_time": 3 if failure == "pm2" else 2,
            "created_at": 1234,
            "pm_exec_path": "/reviewed/brain.js",
            "pm_cwd": "/reviewed/brain",
            "args": [],
        },
    }
    snapshot.write_text(
        "#!/bin/bash\n"
        "[[ \"$1\" == snapshot-except ]] && { echo '[]'; exit; }\n"
        f"[[ \"$1\" == jlist ]] && {{ echo '{json.dumps([raw_brain])}'; exit; }}\n"
        "exit 1\n",
        encoding="utf-8",
    )
    snapshot.chmod(0o700)
    fake_lsof = tmp_path / "lsof"
    fake_lsof.write_text(
        "#!/bin/bash\n"
        "case \"$*\" in *9110*) printf 'p%s\\nn127.0.0.1:9110\\n' \"${FAKE_BRAIN_PID:-100}\"; "
        "[[ \"${FAKE_WILDCARD:-0}\" == 1 ]] && printf 'p300\\nn*:9110\\n' || true;; "
        "*9120*) printf 'p%s\\nn127.0.0.1:9120\\n' \"${FAKE_WORKER_PID:-200}\";; *) exit 1;; esac\n",
        encoding="utf-8",
    )
    fake_lsof.chmod(0o700)
    fake_launchctl = tmp_path / "launchctl"
    fake_launchctl.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "case \"$1\" in\n"
        " print) [[ \"$(<\"$FAKE_LAUNCHD_STATE\")\" == loaded ]] || exit 3; "
        "echo '    pid = '${FAKE_LAUNCHD_PID:-200}';';;\n"
        " bootout) printf 'unloaded\\n' > \"$FAKE_LAUNCHD_STATE\";;\n"
        " bootstrap) printf 'loaded\\n' > \"$FAKE_LAUNCHD_STATE\";;\n"
        " enable) :;; *) exit 4;; esac\n",
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o700)
    copied = tmp_path / "provision-agentops.sh"
    source = AGENTOPS.read_text(encoding="utf-8")
    source = source.replace('"$(/usr/bin/id -un)" == "agentops"', f'"$(/usr/bin/id -un)" == "{current_user}"')
    source = source.replace(
        '[[ "${HOME:-}" == /Users/agentops && "${USER:-}" == agentops && "${LOGNAME:-}" == agentops ]] || fail',
        '[[ -n "${HOME:-}" && -n "${USER:-}" && -n "${LOGNAME:-}" ]] || fail',
    )
    source = source.replace("cd /Users/agentops || fail", f"cd {shlex.quote(str(tmp_path))} || fail")
    source = source.replace("runtime=/Users/agentops/AgentRuntime", f"runtime={runtime}")
    source = source.replace(
        'snapshot="$deploy_tools/reliability/sanitized-pm2.sh"',
        f"snapshot={snapshot}",
    )
    source = source.replace(
        "worker_plist=/Users/agentops/Library/LaunchAgents/com.orbbec.agent-execution-worker.plist",
        f"worker_plist={worker_plist}",
    )
    source = source.replace('== "700 agentops"', f'== "700 {current_user}"')
    source = source.replace("/usr/sbin/lsof", str(fake_lsof))
    source = source.replace("/bin/launchctl", str(fake_launchctl))
    if failure == "receipt-copy":
        source = source.replace(
            '/bin/cp "$worker_plist" "$receipt_part/previous.plist"',
            "/usr/bin/false",
        )
    copied.write_text(source, encoding="utf-8")
    copied.chmod(0o700)
    env = {
        **os.environ,
        "FAKE_FAILURE": failure,
        "FAKE_BRAIN_PID": "999" if failure == "brain-listener" else "100",
        "FAKE_WORKER_PID": "201" if failure == "worker-listener" else "200",
        "FAKE_LAUNCHD_PID": "202" if failure == "launchd" else "200",
        "FAKE_WILDCARD": "1" if failure == "wildcard-listener" else "0",
        "FAKE_WORKER_PLIST": str(worker_plist),
        "FAKE_LAUNCHD_STATE": str(launchd_state),
    }
    result = subprocess.run(
        ["/bin/bash", str(copied), "install"],
        text=True,
        capture_output=True,
        env=env,
    )
    return result, worker_plist, launchd_state, copied, env


@pytest.mark.parametrize(
    "failure",
    [
        "",
        "install",
        "pm2",
        "brain-listener",
        "worker-listener",
        "wildcard-listener",
        "launchd",
        "receipt-copy",
    ],
)
def test_agentops_install_executable_process_identity_gates(
    tmp_path: Path, failure: str
) -> None:
    result, worker_plist, launchd_state, _, _ = _agentops_install_harness(tmp_path, failure)
    assert (result.returncode == 0) is (failure == "")
    assert "opaque" not in result.stdout + result.stderr
    if failure:
        assert worker_plist.read_text(encoding="utf-8") == "OLD-WORKER-PLIST\n"
        assert launchd_state.read_text(encoding="utf-8") == "loaded\n"


def test_agentops_commit_response_loss_can_rollback_then_finalize_is_idempotent(
    tmp_path: Path,
) -> None:
    result, worker_plist, launchd_state, helper, env = _agentops_install_harness(
        tmp_path, ""
    )
    assert result.returncode == 0, result.stderr

    committed = subprocess.run(
        ["/bin/bash", str(helper), "commit"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert committed.returncode == 0, committed.stderr
    receipt = tmp_path / "runtime/private/worker-provision-receipt"
    assert (receipt / "committed").read_text(encoding="utf-8") == "v1\n"

    # A lost coordinator response must leave enough state for the caller to abort.
    rolled_back = subprocess.run(
        ["/bin/bash", str(helper), "rollback"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert rolled_back.returncode == 0, rolled_back.stderr
    assert worker_plist.read_text(encoding="utf-8") == "OLD-WORKER-PLIST\n"
    assert launchd_state.read_text(encoding="utf-8") == "loaded\n"
    assert not receipt.exists()

    for _ in range(2):
        finalized = subprocess.run(
            ["/bin/bash", str(helper), "finalize"],
            text=True,
            capture_output=True,
            env=env,
        )
        assert finalized.returncode == 0, finalized.stderr


def test_acceptance_coordinator_keeps_cloud_key_neo_owned_and_commands_fixed() -> None:
    source = ACCEPT.read_text(encoding="utf-8")
    assert '"$(/usr/bin/id -un)" == "neo"' in source
    assert "cloud_admin_key=/Users/neo/.ssh/orbbec_aliyun_ed25519" in source
    assert "/Users/agentops/AgentRuntime/private/cloud-admin" not in source
    assert "cloud_admin_key" not in source.split("keys = {", 1)[1].split("}", 1)[0]
    assert "/usr/bin/sudo -n -u agentops" in source
    assert "sudo -n -u agentops /bin/bash -c" not in source
    assert "sudo -n -u agentops /bin/zsh -c" not in source
    assert "cp " + "/Users/neo/.ssh/orbbec_aliyun_ed25519" not in source
    assert "order by created_at desc" in source
    assert "order by activated_at" not in source
    subprocess.run(["/bin/bash", "-n", str(ACCEPT)], check=True)


def test_acceptance_coordinator_rejects_config_command_injection_before_ssh(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "injected"
    config = tmp_path / "acceptance.json"
    inert = str(tmp_path / "not-present")
    config.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "member_cookie_file": inert,
                "owner_cookie_file": inert,
                "hr_prompt_file": inert,
                "interruption_prompt_file": inert,
                "relay_acceptance_config": inert,
                "evidence_file": inert,
                "cloud_admin_host": f"$(touch {marker})",
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)

    result = subprocess.run(
        ["/bin/bash", str(ACCEPT), str(config), "preflight"],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "root@47.106.112.69" not in result.stdout + result.stderr


def test_runbook_documents_bootstrap_files_and_backup_absolute_paths() -> None:
    runbook = (ROOT / "docs/runbooks/cloud-platform.md").read_text(encoding="utf-8")
    for required in (
        "generate-content-keyring.py",
        "CLOUD_CONTENT_ENCRYPTION_KEYRING=/absolute/",
        "CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING=/absolute/",
        "offline backup",
        "provision.sh",
        "acceptance-grant",
        "--user 10001:10001",
        "CLOUD_CONTENT_ENCRYPTION_KEYRING=/absolute/private/path/",
        "CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING=/absolute/private/path/",
    ):
        assert required in runbook
    assert "config has schema version `1`" not in runbook
