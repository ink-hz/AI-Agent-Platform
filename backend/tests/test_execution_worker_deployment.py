from __future__ import annotations

import base64
import importlib
import json
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
    assert "chmod 600" in script and "chmod 700" in script
    assert "chown agentops" in script
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
