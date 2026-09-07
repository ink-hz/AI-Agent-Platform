from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy/cloud"
DATA_MOUNT = (
    "/data/orbbec-agent-platform/hr-intelligence:/data/agent-platform/hr-intelligence"
)


def test_api_reads_persistent_panorama_evidence_from_the_data_disk() -> None:
    compose = yaml.safe_load((CLOUD / "compose.yaml").read_text("utf-8"))

    assert f"{DATA_MOUNT}:ro" in compose["services"]["platform-api"]["volumes"]


def test_import_compose_is_internal_only_and_mounts_bundle_read_only() -> None:
    override = yaml.safe_load(
        (CLOUD / "compose.hr-intelligence-import.yaml").read_text("utf-8")
    )
    service = override["services"]["platform-hr-intelligence-import"]

    assert service["read_only"] is True
    assert service["healthcheck"] == {"disable": True}
    assert service["user"] == "10001:10001"
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in service
    assert service["networks"] == ["platform-internal"]
    assert service["volumes"] == [
        "platform-api-secrets:/run/control-secrets:ro",
        "${PLATFORM_HR_INTELLIGENCE_BUNDLE_PATH}:/bundle:ro",
        f"{DATA_MOUNT}:rw",
    ]
    assert service["environment"] == {
        "PLATFORM_CONTROL_DATABASE_URL_FILE": (
            "/run/control-secrets/control-database-url"
        ),
        "PLATFORM_HR_PANORAMA_OWNER_ID": "${PLATFORM_HR_PANORAMA_OWNER_ID:-}",
        "PLATFORM_HR_INTELLIGENCE_BUNDLE_PATH": "/bundle",
        "PLATFORM_HR_INTELLIGENCE_ROOT": "/data/agent-platform/hr-intelligence",
    }


def test_import_script_uses_exact_data_disk_staging_and_cleanup_trap() -> None:
    script = (CLOUD / "import-hr-intelligence.sh").read_text("utf-8")

    assert "compose.hr-intelligence-import.yaml" in script
    assert "/data/staging/orbbec-agent-platform/" in script
    assert "trap cleanup" in script
    assert "python -m app.hr.intelligence_import" in script
    assert '/usr/bin/chown -R 10001:10001 -- "$staging"' in script
    assert script.index("sha256sum -c checksums.sha256") < script.index(
        '/usr/bin/chown -R 10001:10001 -- "$staging"'
    ) < script.index('mv "$staging" "$final"')
    assert 'backend_python="$repository_root/backend/.venv/bin/python"' in script
    assert "rev-parse --path-format=absolute --git-common-dir" in script
    assert "local_python=/usr/bin/python3" not in script
    assert "/tmp" not in script
    assert "system prune" not in script


def test_remote_stage_prepares_only_the_scoped_persistent_data_directory() -> None:
    stage = (CLOUD / "remote-stage.sh").read_text("utf-8")

    assert (
        "hr_intelligence_data_path=/data/orbbec-agent-platform/hr-intelligence" in stage
    )
    assert '"$hr_intelligence_data_path"' in stage
    assert "/usr/bin/install -d -o 10001 -g 10001 -m 0750" in stage
    assert '"$hr_intelligence_data_path/evidence"' in stage
    assert "read_previous_panorama_owner" in stage
    assert "PLATFORM_HR_PANORAMA_OWNER_ID=%s" in stage


def test_runbook_describes_local_bundle_and_direct_owner_release() -> None:
    runbook = (ROOT / "docs/runbooks/hr-intelligence-bundle.md").read_text("utf-8")

    assert "LOCAL_ONLY_COLLECTION=true" in runbook
    assert "Owner 明确发出上线指令即构成发布授权" in runbook
    assert "APPROVE_RELEASE_SHA" not in runbook
    assert "APPROVE_HR_BUNDLE_ID" not in runbook
    assert "deploy/cloud/import-hr-intelligence.sh" in runbook
    assert "app.hr.panorama_cli" not in runbook
