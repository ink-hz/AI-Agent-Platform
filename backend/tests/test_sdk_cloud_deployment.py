"""SDK credentials and switch stay scoped to the Platform API service."""

from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest
import yaml


COMPOSE = Path(__file__).parents[2] / "deploy" / "cloud" / "compose.yaml"
ZENTAO_COMPOSE = Path(__file__).parents[2] / "deploy" / "sdk" / "compose.yaml"
NATIVE_DOMAIN = Path(__file__).parents[2] / "deploy" / "sdk" / "zentao-domain.nginx.conf"


def test_zentao_uses_a_separate_https_origin_and_keeps_loopback_upstream() -> None:
    config = NATIVE_DOMAIN.read_text(encoding="utf-8")
    assert "server_name __SDK_DOMAIN__;" in config
    assert "listen 443 ssl;" in config
    assert "proxy_pass http://127.0.0.1:18131;" in config
    assert "proxy_set_header Authorization \"\";" in config
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
    assert "apilogin" in config
    assert "agent.orbbec.com.cn" not in config


def test_zen_sso_proxy_only_exposes_exact_post_handoff_and_keeps_apilogin_denied() -> None:
    config = NATIVE_DOMAIN.read_text(encoding="utf-8")
    assert "location = /sdk-sso/consume {" in config
    assert "proxy_pass http://127.0.0.1:8080/api/v1/sdk/sso/consume;" in config
    assert "proxy_set_header Cookie \"\";" in config
    assert "access_log off;" in config
    assert "client_max_body_size 1k;" in config
    assert "if ($arg_f = apilogin) { return 403; }" in config
    assert "location = /api.php { return 403; }" in config


def test_zentao_compose_requires_an_explicit_hardened_image() -> None:
    service = yaml.safe_load(ZENTAO_COMPOSE.read_text(encoding="utf-8"))["services"]["zentao"]
    image = service["image"]
    assert image == "${SDK_ZENTAO_IMAGE:?set an audited hardened ZenTao image}"
    assert service["entrypoint"] == ["/usr/local/bin/verify-and-start.sh"]
    dockerfile = (ZENTAO_COMPOSE.parent / "Dockerfile.hardened").read_text(encoding="utf-8")
    assert "COPY verify-and-start.sh /usr/local/bin/verify-and-start.sh" in dockerfile
    assert "chmod 755 /usr/local/bin/verify-and-start.sh" in dockerfile
def test_sdk_cloud_switch_and_password_are_scoped_to_platform_api() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    api = services["platform-api"]
    worker = services["platform-hr-web-worker"]
    environment = api["environment"]

    assert environment["PLATFORM_SDK_WORKBENCH_ENABLED"] == "${PLATFORM_SDK_WORKBENCH_ENABLED:-0}"
    assert environment["PLATFORM_SDK_ZENTAO_BASE_URL"] == "${PLATFORM_SDK_ZENTAO_BASE_URL:-}"
    assert environment["PLATFORM_SDK_ZENTAO_ACCOUNT"] == "${PLATFORM_SDK_ZENTAO_ACCOUNT:-}"
    assert environment["PLATFORM_SDK_ZENTAO_PASSWORD_FILE"] == "/run/sdk-secrets/password"
    assert environment["PLATFORM_SDK_ZENTAO_PRODUCT_ID"] == "${PLATFORM_SDK_ZENTAO_PRODUCT_ID:-0}"
    assert environment["PLATFORM_SDK_ZENTAO_EXECUTION_ID"] == "${PLATFORM_SDK_ZENTAO_EXECUTION_ID:-0}"
    assert environment["PLATFORM_SDK_SSO_ENABLED"] == "${PLATFORM_SDK_SSO_ENABLED:-0}"
    assert environment["PLATFORM_SDK_SSO_ENTRY_KEY_FILE"] == "/run/sdk-secrets/sso-entry-key"
    assert "platform-sdk-secrets:/run/sdk-secrets:ro" in api["volumes"]
    assert "platform-sdk-secrets:/run/sdk-secrets:ro" not in worker["volumes"]
    assert worker["environment"]["PLATFORM_SDK_WORKBENCH_ENABLED"] == "0"
    assert not worker["environment"]["PLATFORM_SDK_ZENTAO_ACCOUNT"]
    assert worker["environment"]["PLATFORM_SDK_SSO_ENABLED"] == "0"
    assert not worker["environment"]["PLATFORM_SDK_SSO_ENTRY_KEY_FILE"]
    for name, service in services.items():
        if name != "platform-api":
            assert "platform-sdk-secrets:/run/sdk-secrets:ro" not in service.get("volumes", [])


def test_zentao_only_shares_a_dedicated_private_network_with_platform_api() -> None:
    cloud = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    zentao = yaml.safe_load(ZENTAO_COMPOSE.read_text(encoding="utf-8"))
    shared = "sdk-bridge"
    assert set(cloud["services"]["platform-api"]["networks"]) == {
        "platform-internal", "platform-edge", "voc-extension", shared,
    }
    for name, service in cloud["services"].items():
        if name != "platform-api":
            assert shared not in service.get("networks", {})
    assert set(zentao["services"]["zentao"]["networks"]) == {"sdk-private", shared, "sdk-admin"}
    assert zentao["services"]["zentao-db"]["networks"] == ["sdk-private"]
    assert zentao["services"]["zentao"]["ports"] == ["127.0.0.1:18131:80"]
    assert zentao["networks"]["sdk-admin"]["internal"] is False
    assert cloud["networks"][shared]["internal"] is True
    assert zentao["networks"][shared]["external"] is True
    assert cloud["networks"][shared]["name"] == zentao["networks"][shared]["name"]


@pytest.mark.parametrize("enabled", [False, True])
def test_rendered_compose_scopes_sdk_activation_to_api(enabled: bool) -> None:
    compose_command = shutil.which("docker-compose")
    if compose_command is None:
        pytest.skip("Docker Compose CLI unavailable")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLATFORM_SDK_")}
    environment["PLATFORM_IMAGE"] = "orbbec-agent-platform:test"
    if enabled:
        environment.update({
            "PLATFORM_SDK_WORKBENCH_ENABLED": "1",
            "PLATFORM_SDK_ZENTAO_BASE_URL": "http://zentao:80",
            "PLATFORM_SDK_ZENTAO_ACCOUNT": "sdk-api",
            "PLATFORM_SDK_ZENTAO_PRODUCT_ID": "21",
            "PLATFORM_SDK_ZENTAO_EXECUTION_ID": "34",
        })
    result = subprocess.run(
        [compose_command, "--profile", "*", "-f", str(COMPOSE), "config", "--format", "json"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    api = services["platform-api"]
    worker = services["platform-hr-web-worker"]
    assert api["environment"]["PLATFORM_SDK_WORKBENCH_ENABLED"] == ("1" if enabled else "0")
    assert api["environment"]["PLATFORM_SDK_ZENTAO_BASE_URL"] == ("http://zentao:80" if enabled else "")
    assert api["environment"]["PLATFORM_SDK_ZENTAO_ACCOUNT"] == ("sdk-api" if enabled else "")
    assert api["environment"]["PLATFORM_SDK_ZENTAO_PRODUCT_ID"] == ("21" if enabled else "0")
    assert api["environment"]["PLATFORM_SDK_ZENTAO_EXECUTION_ID"] == ("34" if enabled else "0")
    assert any(mount["target"] == "/run/sdk-secrets" and mount["read_only"] for mount in api["volumes"])
    assert worker["environment"]["PLATFORM_SDK_WORKBENCH_ENABLED"] == "0"
    assert all(mount["target"] != "/run/sdk-secrets" for mount in worker["volumes"])


def test_release_preflights_sdk_password_before_stopping_the_old_api() -> None:
    stage = (COMPOSE.parent / "remote-stage.sh").read_text(encoding="utf-8")
    check = stage.index("orbbec-agent-platform-sdk-secrets:/run/sdk-secrets:ro")
    stop = stage.index('"${previous_compose[@]}" stop "${previous_control_consumers[@]}"')
    assert check < stop
    assert '10001:10001:600' in stage[:stop]
