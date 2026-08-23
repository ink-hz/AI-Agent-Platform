from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
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


@pytest.mark.parametrize("unsafe", ["parent-symlink", "target-symlink", "world-parent"])
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
    else:
        safe.chmod(0o755)

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


def test_local_provisioning_wrapper_has_narrow_hba_transaction_and_fixed_sudo() -> None:
    source = PROVISION.read_text(encoding="utf-8")
    helper = AGENTOPS.read_text(encoding="utf-8")
    combined = source + helper

    assert source.startswith("#!/bin/bash\nset -eEuo pipefail\numask 077\n")
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
    assert "127.0.0.1:9120" in helper
    assert "snapshot-except metabot-agent-brain" in helper
    subprocess.run(["/bin/bash", "-n", str(PROVISION)], check=True)
    subprocess.run(["/bin/bash", "-n", str(AGENTOPS)], check=True)


def _provision_harness(tmp_path: Path, *, fail_install: bool):
    current_user = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    root = tmp_path / "repo"
    local = root / "deploy/local-execution-worker"
    local.mkdir(parents=True)
    hba = tmp_path / "pg_hba.conf"
    original = b"# user-owned header\nlocal all all trust\n"
    hba.write_bytes(original)
    hba.chmod(0o600)
    log = tmp_path / "commands"
    fake_psql = tmp_path / "psql"
    fake_psql.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"$HARNESS_LOG\"\n"
        "if [[ \"$1\" == '--version' ]]; then echo 'psql (PostgreSQL) 17.9'; exit 0; fi\n"
        "if [[ ! -t 0 ]]; then IFS= read -r sql || true; "
        "[[ \"$sql\" == drop\\ role\\ if\\ exists* ]] && echo drop-role >> \"$HARNESS_LOG\"; fi\n"
        "case \"$*\" in\n"
        "  *'show port'*) echo 5432;;\n"
        f"  *'show hba_file'*) echo {hba};;\n"
        "  *'select pg_reload_conf()'*) echo t;;\n"
        "  *'select count(*) from pg_hba_file_rules where error is not null'*) echo 0;;\n"
        "  *'pg_hba_file_rules'*) if grep -q '^host postgres agent_execution_bootstrap_' \"$FAKE_HBA\"; then echo 0:1:1; else echo 0:1:0; fi;;\n"
        "  *) :;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o700)
    fake_helper = local / "provision-agentops.sh"
    fake_helper.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "printf 'helper:%s\\n' \"$1\" >> \"$HARNESS_LOG\"\n"
        "case \"$1\" in\n"
        " prepare) IFS= read -r secret; [[ \"$secret\" == postgresql://* ]];;\n"
        f" install) {'exit 71' if fail_install else 'echo EXECUTION_WORKER_AGENTOPS_READY'};;\n"
        " cleanup) :;; *) exit 72;; esac\n",
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
        "psql=/opt/homebrew/opt/postgresql@17/bin/psql", f"psql={fake_psql}"
    ).replace("/usr/bin/sudo", str(fake_sudo))
    copied.write_text(source, encoding="utf-8")
    copied.chmod(0o700)
    result = subprocess.run(
        ["/bin/bash", str(copied)],
        text=True,
        capture_output=True,
        env={**os.environ, "HARNESS_LOG": str(log), "FAKE_HBA": str(hba)},
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
    assert "helper:cleanup" in calls and "drop-role" in calls
    assert "postgresql://" not in result.stdout + result.stderr + calls


def test_local_provision_failure_restores_hba_and_cleans_bootstrap(tmp_path: Path) -> None:
    result, hba, original, log = _provision_harness(tmp_path, fail_install=True)
    assert result.returncode != 0
    assert hba.read_bytes() == original
    calls = log.read_text(encoding="utf-8")
    assert "helper:cleanup" in calls and "drop-role" in calls
    assert "postgresql://" not in result.stdout + result.stderr + calls


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
    subprocess.run(["/bin/bash", "-n", str(ACCEPT)], check=True)


def test_runbook_documents_bootstrap_files_and_backup_absolute_paths() -> None:
    runbook = (ROOT / "docs/runbooks/cloud-platform.md").read_text(encoding="utf-8")
    for required in (
        "generate-content-keyring.py",
        "CLOUD_CONTENT_ENCRYPTION_KEYRING=/absolute/",
        "CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING=/absolute/",
        "offline backup",
        "provision.sh",
        "acceptance-grant",
    ):
        assert required in runbook
