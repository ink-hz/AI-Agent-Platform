from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"


def test_compose_is_isolated_loopback_only_and_hardened():
    value = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    services = value["services"]

    assert set(services) == {
        "platform-api", "platform-loopback", "platform-postgres"
    }
    assert "ports" not in services["platform-postgres"]
    assert "ports" not in services["platform-api"]
    assert services["platform-loopback"]["ports"] == ["127.0.0.1:8080:8080"]
    assert services["platform-loopback"]["command"] == [
        "python", "-m", "app.cloud_replica.loopback_proxy"
    ]
    assert services["platform-loopback"]["volumes"] == []
    assert services["platform-loopback"]["read_only"] is True
    assert services["platform-loopback"]["cap_drop"] == ["ALL"]
    assert set(services["platform-loopback"]["networks"]) == {
        "platform-edge", "platform-internal"
    }
    assert services["platform-api"]["networks"] == ["platform-internal"]
    assert services["platform-api"]["read_only"] is True
    assert services["platform-api"]["cap_drop"] == ["ALL"]
    assert services["platform-api"]["security_opt"] == ["no-new-privileges:true"]
    assert services["platform-api"]["user"] not in {"0", "root", "0:0"}
    assert services["platform-api"]["environment"]["PLATFORM_DEPLOYMENT_MODE"] == "cloud-replica"
    assert services["platform-api"]["environment"]["PLATFORM_CLOUD_AUTH_MODE"] == "${PLATFORM_CLOUD_AUTH_MODE:-ssh-tunnel}"
    assert services["platform-api"]["environment"]["PLATFORM_HOST"] == "127.0.0.1"
    assert services["platform-api"]["environment"]["PLATFORM_REVIEW_ENABLED"] == "0"
    assert services["platform-api"]["environment"]["PLATFORM_ATTACHMENT_ENABLED"] == "0"
    assert services["platform-api"]["volumes"] == [
        "platform-api-secrets:/run/secrets:ro"
    ]
    assert services["platform-postgres"]["volumes"] == [
        "platform-postgres-data:/var/lib/postgresql/data",
        "platform-postgres-secrets:/run/secrets:ro",
    ]
    serialized = (CLOUD / "compose.yaml").read_text(encoding="utf-8").lower()
    for forbidden in ("langfuse", "nginx", "ai-fae", "fae-backend"):
        assert forbidden not in serialized
    assert value["networks"]["platform-internal"]["internal"] is True
    assert value["networks"]["platform-edge"]["internal"] is False


def test_image_is_multistage_nonroot_and_contains_only_runtime_assets():
    dockerfile = (CLOUD / "Dockerfile").read_text(encoding="utf-8").lower()

    assert dockerfile.count("from ") >= 2
    assert "npm run build" in dockerfile
    assert "python:3.11" in dockerfile
    assert "https://mirrors.aliyun.com/pypi/simple/" in dockerfile
    assert dockerfile.index("run pip install") < dockerfile.index("arg release_sha")
    assert "user platform" in dockerfile
    assert "healthcheck" in dockerfile
    assert "uvicorn" in dockerfile
    for forbidden in ("copy .git", "copy backend/tests", "sensitive-dictionary", "identity-hmac"):
        assert forbidden not in dockerfile


def test_cloud_registry_and_contract_have_no_source_coordinates():
    registry = yaml.safe_load((CLOUD / "registry.yaml").read_text(encoding="utf-8"))
    contract = (CLOUD / "metabot.runtime-contract.json").read_text(encoding="utf-8")

    assert registry == {"version": 1, "agents": []}
    assert "http://" not in contract
    assert "https://" not in contract
    assert "47.106.112.69" not in contract


def test_local_deploy_preflight_is_clean_noninteractive_and_manifest_bound():
    script = (CLOUD / "deploy.sh").read_text(encoding="utf-8")

    assert "git status --porcelain" in script
    assert "git rev-parse HEAD" in script
    assert "refs/remotes/origin/master" in script
    assert '"$release_sha" == "$remote_master_sha"' in script
    assert "MANIFEST.sha256" in script
    assert "BatchMode=yes" in script
    assert "IdentitiesOnly=yes" in script
    assert "security " not in script
    assert "sudo" not in script


def test_remote_stage_preflight_and_postflight_preserve_existing_services():
    script = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")

    for evidence in (
        "ai-fae-backend",
        "StartedAt",
        "fae_health_digest",
        "nginx_digest",
        "public_listener_digest",
        "10737418240",
        "127.0.0.1:8080",
        "0.0.0.0:8080",
        "[::]:8080",
        "CLOUD_PLATFORM_DEPLOY_OK release=",
        "mode=$cloud_auth_mode",
        "platform-loopback",
    ):
        assert evidence in script
    for forbidden in (
        "systemctl restart nginx",
        "systemctl reload nginx",
        "docker restart ai-fae-backend",
        "docker stop ai-fae-backend",
        "docker compose down",
    ):
        assert forbidden not in script
    assert 'previous_release=""' in script
    assert 'if [[ -L "$root_path/current" ]]' in script
    assert '[[ -f "$previous_release/deploy/cloud/compose.yaml" ]] || fail' in script
    assert 'PLATFORM_CLOUD_AUTH_MODE=%s' in script
    assert 'cloud_auth_mode="ssh-tunnel"' in script
    assert 'value["freshness"] in {"current","stale","unavailable"}' in script
    assert '"freshness":"unavailable"' not in script


def test_raw_key_files_inside_runtime_volumes_use_reader_contract_mode():
    stage = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")

    for key_name in (
        "replica-database-url",
        "replica-encryption-key",
        "replica-signing-public-key",
    ):
        assert f"chmod 600 /target/{key_name}" in stage


def test_control_database_bootstrap_is_isolated_and_least_privilege():
    bootstrap_path = CLOUD / "bootstrap-control-db.sh"
    assert bootstrap_path.is_file()
    script = bootstrap_path.read_text(encoding="utf-8")

    assert "agent_platform_control" in script
    assert "agent_platform_control_preview" in script
    assert "template0" in script
    for role in (
        "platform_control_migrator",
        "platform_control_app",
        "platform_directory_worker",
        "platform_stream_ingest",
        "platform_audit_append",
        "platform_control_maintenance",
    ):
        assert role in script
    for secret in (
        "control-migrator-database-url",
        "control-database-url",
        "control-directory-worker-database-url",
        "control-stream-ingest-database-url",
        "control-audit-database-url",
        "control-maintenance-database-url",
    ):
        assert secret in script
    assert "chmod 600" in script
    assert "revoke connect on database" in script.lower()
    assert "revoke all on schema public from public" in script.lower()
    assert "revoke create on schema public from public" in script.lower()
    assert "revoke all on schema platform_control from public" in script.lower()
    assert "app.control_plane.migrate" in script
    assert "PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE" in script
    assert "platform_replica" not in script
    assert "replica-database-url" not in script

    lowered = script.lower()
    for forbidden in (
        "create extension postgres_fdw",
        "create extension dblink",
        "login password '$",
        "login password \"$",
        "echo $",
    ):
        assert forbidden not in lowered


def test_remote_stage_calls_control_bootstrap_without_replacing_replica():
    stage = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")

    assert '"$release_path/deploy/cloud/bootstrap-control-db.sh"' in stage
    assert stage.count("bootstrap-control-db.sh") == 1
    assert "replica-database-url" in stage
    assert "platform_replica_reader" in stage
    assert "platform_replica_importer" in stage
    assert (
        "postgresql://platform_replica_reader:%s@platform-postgres:5432/"
        "agent_platform\\n"
    ) in stage
    assert (
        "postgresql://platform_replica_importer:%s@platform-postgres:5432/"
        "agent_platform\\n"
    ) in stage
    assert "publish-agent-domain.sh" not in stage
