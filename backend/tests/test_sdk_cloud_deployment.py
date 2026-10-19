"""SDK credentials and switch stay scoped to the Platform API service."""

from pathlib import Path

import yaml


COMPOSE = Path(__file__).parents[2] / "deploy" / "cloud" / "compose.yaml"


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
    assert "platform-sdk-secrets:/run/sdk-secrets:ro" in api["volumes"]
    assert "platform-sdk-secrets:/run/sdk-secrets:ro" not in worker["volumes"]
    assert worker["environment"]["PLATFORM_SDK_WORKBENCH_ENABLED"] == "0"
    assert not worker["environment"]["PLATFORM_SDK_ZENTAO_ACCOUNT"]
    for name, service in services.items():
        if name != "platform-api":
            assert "platform-sdk-secrets:/run/sdk-secrets:ro" not in service.get("volumes", [])
