from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy/cloud"
DATA_MOUNT = (
    "/data/orbbec-agent-platform/hr-intelligence:"
    "/data/agent-platform/hr-intelligence"
)


def test_api_reads_persistent_panorama_evidence_from_the_data_disk() -> None:
    compose = yaml.safe_load((CLOUD / "compose.yaml").read_text("utf-8"))

    assert f"{DATA_MOUNT}:ro" in compose["services"]["platform-api"]["volumes"]


def test_operator_compose_is_isolated_from_hr_bot_and_writes_only_data_disk() -> None:
    override = yaml.safe_load(
        (CLOUD / "compose.hr-intelligence.yaml").read_text("utf-8")
    )
    service = override["services"]["platform-hr-intelligence"]

    assert service["command"] == [
        "python",
        "-m",
        "app.hr.panorama_cli",
        "status",
        "--current",
    ]
    assert service["read_only"] is True
    assert service["user"] == "10001:10001"
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in service
    assert set(service["networks"]) == {"platform-internal", "platform-edge"}
    assert service["volumes"] == [
        "platform-api-secrets:/run/control-secrets:ro",
        "platform-brain-secrets:/run/brain-secrets:ro",
        f"{DATA_MOUNT}:rw",
    ]
    assert service["environment"] == {
        "PLATFORM_CONTROL_DATABASE_URL_FILE": (
            "/run/control-secrets/control-database-url"
        ),
        "PLATFORM_HR_PANORAMA_OWNER_ID": "${PLATFORM_HR_PANORAMA_OWNER_ID:-}",
        "PLATFORM_HR_PANORAMA_SOURCE_CATALOG": (
            "/app/backend/app/hr/panorama_source_catalog.v1.json"
        ),
        "PLATFORM_BRAIN_MODEL_MANIFEST": "/app/brain-model.release.json",
        "PLATFORM_BRAIN_PROVIDER_BASE_URL": (
            "${PLATFORM_BRAIN_PROVIDER_BASE_URL:-https://cc.nexcor.ai}"
        ),
        "PLATFORM_BRAIN_PROVIDER_AUTH_SCHEME": (
            "${PLATFORM_BRAIN_PROVIDER_AUTH_SCHEME:-bearer}"
        ),
        "PLATFORM_BRAIN_PROVIDER_API_KEY_FILE": (
            "/run/brain-secrets/brain-provider-api-key"
        ),
    }


def test_operator_script_uses_a_data_disk_lock_and_no_release_virtualenv() -> None:
    script = (CLOUD / "hr-panorama-producer.sh").read_text("utf-8")

    assert "compose.hr-intelligence.yaml" in script
    assert "/data/orbbec-agent-platform/hr-intelligence/producer.lock" in script
    assert "flock -n 9" in script
    assert "python -m app.hr.panorama_cli" in script
    assert ".venv" not in script
    assert "/tmp" not in script
    assert "system prune" not in script


def test_remote_stage_prepares_only_the_scoped_persistent_data_directory() -> None:
    stage = (CLOUD / "remote-stage.sh").read_text("utf-8")

    assert (
        "hr_intelligence_data_path=/data/orbbec-agent-platform/hr-intelligence"
        in stage
    )
    assert '"$hr_intelligence_data_path"' in stage
    assert "/usr/bin/install -d -o 10001 -g 10001 -m 0750" in stage
    assert '"$hr_intelligence_data_path/evidence"' in stage
    assert "read_previous_panorama_owner" in stage
    assert "PLATFORM_HR_PANORAMA_OWNER_ID=%s" in stage


def test_runbook_uses_the_hardened_operator_entrypoint() -> None:
    runbook = (ROOT / "docs/runbooks/hr-panorama-producer.md").read_text("utf-8")

    assert "deploy/cloud/hr-panorama-producer.sh seed-sources" in runbook
    assert "deploy/cloud/hr-panorama-producer.sh run --trigger schedule" in runbook
    assert "deploy/cloud/hr-panorama-producer.sh status --current" in runbook
    assert ".venv/bin/python -m app.hr.panorama_cli" not in runbook
