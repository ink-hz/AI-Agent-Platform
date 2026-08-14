from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

from app.main import create_app


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"
OVERLAY = CLOUD / "compose.demo-preview.yaml"
BOOTSTRAP = CLOUD / "bootstrap-demo-preview-secrets.sh"


def _overlay() -> dict:
    return yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))


def test_demo_overlay_adds_only_isolated_preview_services_and_one_loopback_port() -> None:
    value = _overlay()
    services = value["services"]

    assert set(services) == {
        "platform-api-demo-preview",
        "platform-demo-preview-runner",
        "platform-loopback-demo-preview",
    }
    assert "platform-api" not in services
    assert "platform-loopback" not in services
    assert "platform-postgres" not in services
    assert "ports" not in services["platform-api-demo-preview"]
    assert "expose" not in services["platform-api-demo-preview"]
    assert "ports" not in services["platform-demo-preview-runner"]
    assert "expose" not in services["platform-demo-preview-runner"]
    assert services["platform-loopback-demo-preview"]["ports"] == [
        "127.0.0.1:8081:8080"
    ]
    assert services["platform-api-demo-preview"]["networks"] == {
        "platform-internal": {"ipv4_address": "172.30.0.5"},
        "platform-edge": {"ipv4_address": "172.31.0.5"},
    }
    assert services["platform-loopback-demo-preview"]["networks"][
        "platform-internal"
    ]["ipv4_address"] == "172.30.0.6"


def test_demo_api_keeps_internal_database_path_and_gains_external_egress() -> None:
    base = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    api = _overlay()["services"]["platform-api-demo-preview"]

    assert base["networks"]["platform-internal"]["internal"] is True
    assert base["networks"]["platform-edge"]["internal"] is False
    assert set(api["networks"]) == {"platform-internal", "platform-edge"}
    assert api["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.5"
    assert api["networks"]["platform-edge"]["ipv4_address"] == "172.31.0.5"
    assert api["environment"]["PLATFORM_TRUSTED_PROXY_CIDRS"] == "172.30.0.6/32"
    assert "ports" not in api
    assert "expose" not in api
    assert api.get("network_mode") != "host"


def test_demo_runner_is_profiled_secure_and_uses_both_compose_networks() -> None:
    value = _overlay()
    api = value["services"]["platform-api-demo-preview"]
    runner = value["services"]["platform-demo-preview-runner"]

    assert runner["profiles"] == ["demo-preview-tools"]
    assert runner["image"] == "${PLATFORM_IMAGE}"
    assert runner["user"] == "0:0"
    assert runner["read_only"] is True
    assert runner["cap_drop"] == ["ALL"]
    assert runner["security_opt"] == ["no-new-privileges:true"]
    assert runner["tmpfs"] == [
        "/tmp:rw,noexec,nosuid,size=32m,uid=0,gid=0,mode=0700"
    ]
    assert runner["volumes"] == api["volumes"]
    assert runner["networks"] == {
        "platform-internal": {},
        "platform-edge": {},
    }
    assert runner["command"] == ["/bin/false"]
    assert "restart" not in runner
    assert "ports" not in runner
    assert "expose" not in runner
    assert "depends_on" not in runner
    assert "ipv4_address" not in yaml.safe_dump(runner["networks"])
    assert value["x-demo-preview-image-smoke"]["runner_service"] == (
        "platform-demo-preview-runner"
    )


def test_demo_overlay_uses_only_the_dedicated_external_secret_volume() -> None:
    value = _overlay()
    services = value["services"]
    volume_name = "orbbec-agent-platform-demo-preview-secrets"

    assert value["volumes"] == {
        volume_name: {"external": True, "name": volume_name}
    }
    assert services["platform-api-demo-preview"]["volumes"] == [
        f"{volume_name}:/run/demo-preview-secrets:ro"
    ]
    assert services["platform-demo-preview-runner"]["volumes"] == [
        f"{volume_name}:/run/demo-preview-secrets:ro"
    ]
    assert services["platform-loopback-demo-preview"]["volumes"] == []
    serialized = OVERLAY.read_text(encoding="utf-8")
    for forbidden in (
        "platform-api-secrets",
        "platform-postgres-secrets",
        "/run/secrets/control-database-url",
        "/run/secrets/identity-hmac-keyring",
        "/run/secrets/identity-encryption-keyring",
    ):
        assert forbidden not in serialized


def test_demo_api_has_exact_preview_identity_and_proxy_contract() -> None:
    service = _overlay()["services"]["platform-api-demo-preview"]
    environment = service["environment"]

    assert service["image"] == "${PLATFORM_IMAGE}"
    assert environment["PLATFORM_IDENTITY_MODE"] == "preview"
    assert environment["PLATFORM_PUBLIC_BASE_URL"] == "https://agent.orbbec.com.cn"
    assert environment["PLATFORM_ROUTE_PREFIX"] == "/_preview/dingtalk-r1/"
    assert environment["PLATFORM_COOKIE_NAME"] == "platform_preview_session"
    assert environment["PLATFORM_DINGTALK_LOGIN_FLOW"] == "qr"
    assert environment["PLATFORM_TRUSTED_PROXY_CIDRS"] == "172.30.0.6/32"
    assert environment["PLATFORM_FLYWHEEL_ENABLED"] == "0"
    assert environment["PLATFORM_REVIEW_ENABLED"] == "0"
    assert environment["PLATFORM_ATTACHMENT_ENABLED"] == "0"
    assert environment["PLATFORM_CONTROL_DATABASE_URL_FILE"].endswith(
        "/preview-control-database-url"
    )
    assert environment["PLATFORM_CONTROL_AUDIT_DATABASE_URL_FILE"].endswith(
        "/preview-control-audit-database-url"
    )
    assert environment["PLATFORM_IDENTITY_HMAC_KEYRING_FILE"].endswith(
        "/preview-identity-hmac-keyring"
    )
    assert environment["PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE"].endswith(
        "/preview-identity-encryption-keyring"
    )
    assert environment["PLATFORM_RATE_LIMIT_HMAC_KEYRING_FILE"].endswith(
        "/preview-rate-limit-hmac-keyring"
    )
    command = " ".join(service["command"])
    assert "--no-proxy-headers" in command
    assert "--no-access-log" in command
    assert "PLATFORM_DINGTALK_APP_KEY" in command
    assert "PLATFORM_DINGTALK_AGENT_ID" in command
    assert "PLATFORM_DINGTALK_CORP_ID" in command


def test_demo_services_are_nonroot_readonly_capability_free_and_healthy() -> None:
    services = _overlay()["services"]
    api = services["platform-api-demo-preview"]
    loopback = services["platform-loopback-demo-preview"]

    for service in (api, loopback):
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["healthcheck"]["retries"] > 0
    assert api["tmpfs"] == [
        "/tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=10001,mode=0700"
    ]
    assert loopback["command"] == [
        "uvicorn",
        "app.cloud_replica.loopback_proxy:create_app",
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--no-proxy-headers",
        "--no-access-log",
    ]
    assert loopback["environment"] == {
        "PLATFORM_LOOPBACK_TARGET_BASE_URL": "http://platform-api-demo-preview:8080",
        "PLATFORM_LOOPBACK_TRUSTED_PROXY_CIDRS": "127.0.0.1/32,172.31.0.1/32",
        "PLATFORM_LOOPBACK_SOURCE_ADDRESS": "172.30.0.6",
    }
    health = " ".join(loopback["healthcheck"]["test"])
    assert "X-Real-IP" in health
    assert "127.0.0.1" in health
    assert "X-Forwarded-Proto" in health
    assert "http" in health


def test_demo_overlay_has_no_stream_reconciler_or_directory_schedule() -> None:
    serialized = OVERLAY.read_text(encoding="utf-8").lower()
    for forbidden in (
        "dingtalk-stream",
        "stream-ingest",
        "directory-reconcile",
        "reconcile_interval",
        "schedule",
        "cron",
    ):
        assert forbidden not in serialized


def test_secret_bootstrap_has_fixed_root_only_idempotent_boundary() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8")

    assert "EUID" in script and '-eq 0' in script
    assert "/opt/orbbec-agent-platform/private/demo-preview" in script
    assert "orbbec-agent-platform-demo-preview-secrets" in script
    assert "docker volume inspect" in script
    assert "docker volume create" in script
    assert "10001" in script
    assert "0o400" in script or "0400" in script
    assert "0o600" in script or "0600" in script
    assert "stat.S_ISREG" in script
    assert "is_symlink" in script or "S_ISLNK" in script
    assert "st_uid != 0" in script
    assert "os.replace" in script
    assert "provider-encryption" in script
    assert "provider-lookup-hmac" in script
    assert "rate-limit-hmac" in script
    assert "overlaps" in script
    for expected in (
        "dingtalk-app-key",
        "dingtalk-agent-id",
        "dingtalk-corp-id",
        "dingtalk-app-secret",
        "preview-control-database-url",
        "preview-control-audit-database-url",
        "preview-control-directory-worker-database-url",
        "preview-control-migrator-database-url",
        "preview-identity-hmac-keyring",
        "preview-identity-encryption-keyring",
        "preview-rate-limit-hmac-keyring",
        "demo-userids",
    ):
        assert expected in script
    for role in (
        "platform_control_app_preview",
        "platform_audit_append_preview",
        "platform_directory_worker_preview",
        "platform_control_migrator_preview",
    ):
        assert role in script
    assert "agent_platform_control_preview" in script


def test_runtime_api_cannot_reach_offline_migrator_worker_or_allowlist_secrets() -> None:
    value = _overlay()
    api = value["services"]["platform-api-demo-preview"]
    api_contract = yaml.safe_dump(api, sort_keys=True)
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")

    assert "/run/demo-preview-secrets/runtime/" in api_contract
    for offline_name in (
        "preview-control-directory-worker-database-url",
        "preview-control-migrator-database-url",
        "demo-userids",
    ):
        assert offline_name not in api_contract
    assert "RUNTIME_NAMES" in bootstrap
    assert "OFFLINE_NAMES" in bootstrap
    assert "os.chmod(runtime, 0o750)" in bootstrap
    assert "os.chmod(offline, 0o700)" in bootstrap
    assert "os.chown(runtime, 0, 10001)" in bootstrap
    assert "os.chown(offline, 0, 0)" in bootstrap
    assert value["x-demo-preview-image-smoke"]["run_user"] == "0:0"


def test_secret_bootstrap_reads_each_source_once_through_a_verified_dirfd() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8")

    assert '[[ ! -L "$private_path" ]]' in script
    assert 'getattr(os, "O_DIRECTORY", 0)' in script
    assert 'getattr(os, "O_NOFOLLOW", 0)' in script
    assert "dir_fd=source_fd" in script
    assert "os.fstat(descriptor)" in script
    assert "opened.st_uid != 0" in script
    assert "stat.S_IMODE(opened.st_mode) != 0o600" in script
    assert "payloads[name]" in script
    embedded = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    for forbidden in (
        "SOURCE / name",
        "path.read_bytes()",
        "path.read_text(",
        "source.open(",
    ):
        assert forbidden not in embedded


def test_secret_bootstrap_never_uses_interactive_or_secret_echo_paths() -> None:
    lowered = BOOTSTRAP.read_text(encoding="utf-8").lower()

    for forbidden in (
        "set -x",
        "security ",
        "keychain",
        "read -p",
        "cat $",
        "echo $",
        "docker compose down",
        "docker stop",
        "docker rm",
    ):
        assert forbidden not in lowered


def test_runtime_image_contains_control_migrations_for_preview_migrate_smoke() -> None:
    dockerfile = (CLOUD / "Dockerfile").read_text(encoding="utf-8")

    assert "backend/control_migrations" in dockerfile
    assert "./control_migrations" in dockerfile
    assert "USER platform:platform" in dockerfile
    assert '"--no-proxy-headers"' in dockerfile


def test_static_image_smoke_contract_migrates_bootstraps_and_checks_minimal_health() -> None:
    smoke = _overlay()["x-demo-preview-image-smoke"]

    migrate = " ".join(smoke["migrate"])
    bootstrap = " ".join(smoke["bootstrap"])
    assert "python -m app.control_plane.migrate" in migrate
    assert "preview-control-migrator-database-url" in migrate
    assert "platform_control_owner_preview" in migrate
    assert "/app/backend/control_migrations" in migrate
    assert "python -m app.control_plane.demo_bootstrap" in bootstrap
    assert "preview-control-directory-worker-database-url" in bootstrap
    assert "--userid-file" in bootstrap and "demo-userids" in bootstrap
    assert smoke["api_service"] == "platform-api-demo-preview"
    assert smoke["runner_service"] == "platform-demo-preview-runner"
    assert smoke["loopback_service"] == "platform-loopback-demo-preview"
    assert smoke["health_url"] == (
        "http://127.0.0.1:8081/_preview/dingtalk-r1/api/health"
    )
    assert smoke["expected_health"] == {"status": "ok"}


def test_real_create_app_smoke_accepts_separate_preview_app_and_audit_roles(
    tmp_path: Path, monkeypatch
) -> None:
    def secret(name: str, value: str) -> Path:
        path = tmp_path / name
        path.write_text(value + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def keyring(
        name: str, purpose: str, byte: bytes, *, transition: bool = False
    ) -> Path:
        document = {
            "purpose": purpose,
            "active_version": 1,
            "keys": {"1": base64.b64encode(byte * 32).decode("ascii")},
        }
        if transition:
            document["transition_versions"] = [1]
        return secret(
            name,
            json.dumps(document),
        )

    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}\n', encoding="utf-8")
    static = tmp_path / "static"
    static.mkdir()
    app_dsn = secret(
        "app-dsn",
        "postgresql://platform_control_app_preview@127.0.0.1/"
        "agent_platform_control_preview",
    )
    audit_dsn = secret(
        "audit-dsn",
        "postgresql://platform_audit_append_preview@127.0.0.1/"
        "agent_platform_control_preview",
    )
    environment = {
        "PLATFORM_DEPLOYMENT_MODE": "local",
        "PLATFORM_FLYWHEEL_ENABLED": "0",
        "PLATFORM_REVIEW_ENABLED": "0",
        "PLATFORM_ATTACHMENT_ENABLED": "0",
        "PLATFORM_STATIC_DIR": str(static),
        "PLATFORM_IDENTITY_MODE": "preview",
        "PLATFORM_PUBLIC_BASE_URL": "https://agent.orbbec.com.cn",
        "PLATFORM_ROUTE_PREFIX": "/_preview/dingtalk-r1/",
        "PLATFORM_COOKIE_NAME": "platform_preview_session",
        "PLATFORM_DINGTALK_LOGIN_FLOW": "qr",
        "PLATFORM_DINGTALK_APP_KEY": "synthetic-app-key",
        "PLATFORM_DINGTALK_AGENT_ID": "synthetic-agent-id",
        "PLATFORM_DINGTALK_CORP_ID": "synthetic-corp-id",
        "PLATFORM_CONTROL_DATABASE_URL_FILE": str(app_dsn),
        "PLATFORM_CONTROL_AUDIT_DATABASE_URL_FILE": str(audit_dsn),
        "PLATFORM_DINGTALK_APP_SECRET_FILE": str(
            secret("app-secret", "synthetic-app-secret")
        ),
        "PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE": str(
            keyring("encryption", "provider-encryption", b"e")
        ),
        "PLATFORM_IDENTITY_HMAC_KEYRING_FILE": str(
            keyring("lookup", "provider-lookup-hmac", b"h", transition=True)
        ),
        "PLATFORM_RATE_LIMIT_HMAC_KEYRING_FILE": str(
            keyring("rate", "rate-limit-hmac", b"r")
        ),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
    )
    try:
        assert app.state.system_health_audit.environment == "preview"
        assert app.state.identity_auth.in_client_enabled is False
    finally:
        asyncio.run(app.state.identity_auth.aclose())


def test_compose_overlay_statically_merges_without_replacing_root_services() -> None:
    base = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    overlay = _overlay()
    merged_services = {**base["services"], **overlay["services"]}

    assert set(base["services"]).issubset(merged_services)
    assert merged_services["platform-api"] == base["services"]["platform-api"]
    assert merged_services["platform-loopback"] == base["services"]["platform-loopback"]
    assert merged_services["platform-postgres"] == base["services"]["platform-postgres"]


def test_merged_compose_static_addresses_are_unique_and_never_use_host_network() -> None:
    base = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    overlay = _overlay()
    merged_services = {**base["services"], **overlay["services"]}
    addresses: dict[str, str] = {}

    for service_name, service in merged_services.items():
        assert service.get("network_mode") != "host"
        networks = service.get("networks", {})
        if not isinstance(networks, dict):
            continue
        for network_config in networks.values():
            if not isinstance(network_config, dict):
                continue
            address = network_config.get("ipv4_address")
            if address is None:
                continue
            assert address not in addresses, (
                f"{address} is shared by {addresses[address]} and {service_name}"
            )
            addresses[address] = service_name

    assert addresses["172.30.0.5"] == "platform-api-demo-preview"
    assert addresses["172.31.0.5"] == "platform-api-demo-preview"
    assert "--network host" not in OVERLAY.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker unavailable")
def test_compose_config_and_immutable_image_command_smoke_when_docker_available() -> None:
    image = "orbbec-agent-platform-demo-preview:test"
    environment = {**os.environ, "PLATFORM_IMAGE": image}
    try:
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(CLOUD / "compose.yaml"),
                "-f",
                str(OVERLAY),
                "config",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert not re.search(r"(?:0\.0\.0\.0|\[::\]):8081", completed.stdout)
        built = subprocess.run(
            [
                "docker",
                "build",
                "--file",
                str(CLOUD / "Dockerfile"),
                "--build-arg",
                "RELEASE_SHA=" + "0" * 40,
                "--tag",
                image,
                ".",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert built.returncode == 0, built.stderr
        smoke = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                image,
                "python",
                "-c",
                "from pathlib import Path; from app.main import create_app; "
                "app=create_app(start_poller=False); "
                "assert app.title == 'Orbbec AI Agent Platform'; "
                "assert Path('/app/backend/control_migrations/019_demo_preview_bootstrap.sql').is_file()",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert smoke.returncode == 0, smoke.stderr
    finally:
        subprocess.run(
            ["docker", "image", "rm", "--force", image],
            env=environment,
            capture_output=True,
            check=False,
        )
