import json
import os
import signal
import subprocess
import textwrap
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"


def _bash_array(script: str, name: str) -> tuple[str, ...]:
    lines = script.split(f"{name}=(\n", 1)[1].splitlines()
    body = []
    for line in lines:
        if line.strip() == ")":
            break
        if line.strip():
            body.append(line.strip())
    return tuple(body)


def _bash_function(script: str, name: str, next_name: str) -> str:
    return (
        f"{name}() {{"
        + script.split(f"{name}() {{", 1)[1].split(f"\n{next_name}() {{", 1)[0]
    )


def _bash_heredoc_function(script: str, name: str, terminator: str = "PY") -> str:
    start = f"{name}() {{"
    body = script.split(start, 1)[1].split(f"\n{terminator}\n}}", 1)[0]
    return f"{start}{body}\n{terminator}\n}}"


def test_compose_is_isolated_loopback_only_and_hardened():
    value = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    services = value["services"]

    assert set(services) == {
        "platform-api",
        "platform-attachment-storage-init",
        "platform-attachments",
        "platform-clamav",
        "platform-loopback",
        "platform-minio",
        "platform-postgres",
        "platform-directory",
        "platform-dingtalk-stream",
        "platform-brain",
    }
    assert "ports" not in services["platform-postgres"]
    assert "ports" not in services["platform-api"]
    assert services["platform-loopback"]["ports"] == ["127.0.0.1:8080:8080"]
    assert services["platform-loopback"]["command"] == [
        "uvicorn",
        "app.cloud_replica.loopback_proxy:create_app",
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--no-proxy-headers",
    ]
    assert services["platform-loopback"]["volumes"] == []
    assert services["platform-loopback"]["read_only"] is True
    assert services["platform-loopback"]["cap_drop"] == ["ALL"]
    assert set(services["platform-loopback"]["networks"]) == {
        "platform-edge",
        "platform-internal",
    }
    assert (
        services["platform-postgres"]["networks"]["platform-internal"]["ipv4_address"]
        == "172.30.0.2"
    )
    assert (
        services["platform-loopback"]["networks"]["platform-internal"]["ipv4_address"]
        == "172.30.0.3"
    )
    assert (
        services["platform-api"]["networks"]["platform-internal"]["ipv4_address"]
        == "172.30.0.4"
    )
    assert services["platform-api"]["read_only"] is True
    assert services["platform-api"]["cap_drop"] == ["ALL"]
    assert services["platform-api"]["security_opt"] == ["no-new-privileges:true"]
    assert services["platform-api"]["user"] not in {"0", "root", "0:0"}
    assert (
        services["platform-api"]["environment"]["PLATFORM_DEPLOYMENT_MODE"]
        == "cloud-replica"
    )
    assert (
        services["platform-api"]["environment"]["PLATFORM_CLOUD_AUTH_MODE"]
        == "dingtalk"
    )
    assert services["platform-api"]["environment"]["PLATFORM_HOST"] == "127.0.0.1"
    assert services["platform-api"]["environment"]["PLATFORM_REVIEW_ENABLED"] == "0"
    assert services["platform-api"]["environment"]["PLATFORM_ATTACHMENT_ENABLED"] == "0"
    assert (
        services["platform-api"]["environment"][
            "PLATFORM_CONVERSATION_ATTACHMENT_ENABLED"
        ]
        == "1"
    )
    assert (
        services["platform-api"]["environment"]["PLATFORM_ATTACHMENT_S3_ENDPOINT"]
        == "http://platform-minio:9000"
    )
    assert (
        services["platform-api"]["environment"]["PLATFORM_VOC_EXTENSION_ENABLED"] == "1"
    )
    assert (
        services["platform-api"]["environment"]["PLATFORM_VOC_EXTENSION_BASE_URL"]
        == "http://172.29.0.3:18130"
    )
    assert (
        services["platform-api"]["environment"][
            "PLATFORM_VOC_EXTENSION_SIGNING_KEY_FILE"
        ]
        == "/run/secrets/voc-extension-signing-key"
    )
    assert (
        services["platform-api"]["environment"]["PLATFORM_VOC_SERVICE_BEARER_FILE"]
        == "/run/secrets/voc-service-bearer"
    )
    assert (
        services["platform-api"]["environment"]["PLATFORM_AGENT_BRAIN_V2_ENABLED"]
        == "${PLATFORM_AGENT_BRAIN_V2_ENABLED:-0}"
    )
    assert "ports" not in services["platform-brain"]
    assert services["platform-brain"]["read_only"] is True
    assert services["platform-brain"]["user"] == "10001:10001"
    assert services["platform-brain"]["cap_drop"] == ["ALL"]
    assert services["platform-brain"]["security_opt"] == ["no-new-privileges:true"]
    assert set(services["platform-brain"]["networks"]) == {
        "platform-edge",
        "platform-internal",
        "voc-extension",
    }
    assert (
        services["platform-brain"]["networks"]["voc-extension"]["ipv4_address"]
        == "172.29.0.4"
    )
    assert (
        services["platform-brain"]["environment"]["PLATFORM_VOC_EXTENSION_BASE_URL"]
        == "http://172.29.0.3:18130"
    )
    assert (
        services["platform-brain"]["environment"][
            "PLATFORM_VOC_EXTENSION_SIGNING_KEY_FILE"
        ]
        == "/run/secrets/voc-extension-signing-key"
    )
    assert (
        services["platform-api"]["environment"]["PLATFORM_TRUSTED_PROXY_CIDRS"]
        == "172.30.0.3/32"
    )
    assert (
        services["platform-loopback"]["environment"][
            "PLATFORM_LOOPBACK_TRUSTED_PROXY_CIDRS"
        ]
        == "127.0.0.1/32,172.31.0.1/32,172.31.0.8/32"
    )
    assert services["platform-api"]["volumes"] == [
        "platform-api-secrets:/run/secrets:ro"
    ]
    assert services["platform-postgres"]["volumes"] == [
        "/data/orbbec-agent-platform/postgres:/var/lib/postgresql/data",
        "platform-postgres-secrets:/run/secrets:ro",
    ]
    assert "ports" not in services["platform-minio"]
    assert services["platform-minio"]["networks"] == {
        "platform-internal": {"ipv4_address": "172.30.0.8"}
    }
    assert services["platform-minio"]["volumes"] == [
        "/data/orbbec-agent-platform/attachments:/data",
        "platform-attachment-storage-secrets:/run/secrets:ro",
    ]
    storage_init = services["platform-attachment-storage-init"]
    assert storage_init["read_only"] is True
    assert storage_init["environment"]["MC_CONFIG_DIR"] == "/tmp/.mc"
    assert "ports" not in services["platform-clamav"]
    assert services["platform-clamav"]["networks"] == {
        "platform-internal": {"ipv4_address": "172.30.0.9"}
    }
    assert services["platform-clamav"]["cap_drop"] == ["ALL"]
    assert services["platform-clamav"]["cap_add"] == [
        "CHOWN",
        "SETGID",
        "SETUID",
    ]
    worker = services["platform-attachments"]
    assert worker["command"] == [
        "python", "-m", "app.attachments.worker_runtime", "all"
    ]
    assert set(worker["networks"]) == {"platform-internal"}
    assert "platform-edge" not in worker["networks"]
    assert worker["healthcheck"]["test"] == [
        "CMD", "python", "-m", "app.attachments.worker_runtime", "healthcheck"
    ]
    serialized = (CLOUD / "compose.yaml").read_text(encoding="utf-8").lower()
    for forbidden in ("langfuse", "nginx", "ai-fae", "fae-backend"):
        assert forbidden not in serialized
    assert value["networks"]["platform-internal"]["internal"] is True
    assert value["networks"]["voc-extension"] == {
        "name": "orbbec-agent-voc-extension",
        "internal": True,
        "ipam": {"config": [{"subnet": "172.29.0.0/29", "gateway": "172.29.0.1"}]},
    }
    assert (
        services["platform-api"]["networks"]["voc-extension"]["ipv4_address"]
        == "172.29.0.2"
    )
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
    assert '"--no-proxy-headers"' in dockerfile
    assert "brain-model.release.json" in dockerfile
    for runtime_package in ("bubblewrap", "clamav", "libmagic1", "poppler-utils"):
        assert runtime_package in dockerfile
    for forbidden in (
        "copy .git",
        "copy backend/tests",
        "sensitive-dictionary",
        "identity-hmac",
    ):
        assert forbidden not in dockerfile


def test_release_scripts_enforce_data_disk_and_bounded_retention() -> None:
    deploy = (CLOUD / "deploy.sh").read_text(encoding="utf-8")
    stage = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")

    for required in (
        "df -B1 / /data",
        "26843545600",  # 25 GiB pre-deploy root availability
        "21474836480",  # 20 GiB predicted post-staging availability
        "/data/staging/orbbec-agent-platform/",
        "/data/archive/orbbec-agent-platform/releases",
        "deployment_id",
    ):
        assert required in deploy or required in stage
    assert "current + two rollback" in stage
    assert "archive retention: ten releases or thirty days" in stage
    assert "docker system prune" not in deploy
    assert "docker system prune" not in stage
    assert "rm -rf /data/staging" not in deploy
    assert "rm -rf /data/staging" not in stage
    assert '"$release_path/PREVIOUS_RELEASE"' not in stage
    assert '"$release_path/PREVIOUS_PLATFORM_ENV"' not in stage


def test_backups_cover_control_database_and_attachment_objects_on_data_disk() -> None:
    backup = (CLOUD / "backup.sh").read_text(encoding="utf-8")

    assert "backup_path=/data/orbbec-agent-platform/backups" in backup
    assert "attachment_path=/data/orbbec-agent-platform/attachments" in backup
    assert "agent_platform_control" in backup
    assert 'object_backup_name="attachments-$timestamp.orb"' in backup
    assert "docker volume create orbbec-agent-platform-backups" not in backup


def test_cloud_registry_and_contract_have_no_source_coordinates():
    registry = yaml.safe_load((CLOUD / "registry.yaml").read_text(encoding="utf-8"))
    contract = (CLOUD / "metabot.runtime-contract.json").read_text(encoding="utf-8")

    assert registry == {"version": 1, "agents": []}
    assert "http://" not in contract
    assert "https://" not in contract
    assert "47.106.112.69" not in contract


def test_formal_edge_routes_each_fae_family_to_exactly_one_owner():
    value = (CLOUD / "agent-domain.nginx.conf").read_text(encoding="utf-8")
    route_owners = {
        "location ^~ /fae/manage/ {": "proxy_pass http://127.0.0.1:8080;",
        "location = /fae/api/chat {": "proxy_pass http://127.0.0.1:8000;",
        "location = /fae/api/attachments {": "proxy_pass http://127.0.0.1:8000;",
        "location ^~ /fae/api/ {": "proxy_pass http://127.0.0.1:8000;",
        "location ^~ /fae/assets/ {": "proxy_pass http://127.0.0.1:8000;",
        "location ^~ /fae/ {": "proxy_pass http://127.0.0.1:8000;",
    }

    for selector, owner in route_owners.items():
        assert value.count(selector) == 1
        start = value.index(selector)
        end = value.find("\n    location ", start + len(selector))
        block = value[start:] if end < 0 else value[start:end]
        assert block.count("proxy_pass ") == 1
        assert owner in block

    assert value.index("location ^~ /fae/manage/ {") < value.index(
        "location ^~ /fae/ {"
    )
    assert value.index("location ^~ /fae/ {") < value.index(
        "location / {", value.index("location ^~ /fae/ {")
    )


def test_local_deploy_preflight_is_clean_noninteractive_and_manifest_bound():
    script = (CLOUD / "deploy.sh").read_text(encoding="utf-8")

    assert 'backend_python="$repository_root/backend/.venv/bin/python"' in script
    assert "git rev-parse --path-format=absolute --git-common-dir" in script
    assert '$(/usr/bin/dirname "$common_git")/backend/.venv/bin/python' in script
    assert script.count('"$backend_python"') >= 3
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
            "21474836480",
        "127.0.0.1:8080",
        "0.0.0.0:8080",
        "[::]:8080",
        "CLOUD_PLATFORM_DEPLOY_OK release=",
        "mode=dingtalk",
        "up -d --force-recreate platform-postgres",
        'docker rm -f "$container_id"',
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
    assert "PLATFORM_CLOUD_AUTH_MODE=dingtalk" in script
    assert 'cloud_auth_mode="ssh-tunnel"' in script
    assert "/api/deployment" not in script
    assert "sync-identity-policy" in script
    assert script.index("sync-identity-policy") < script.index(
        'up -d --force-recreate "${active_control_secret_consumers[@]}"'
    )
    for runtime_value in (
        "PLATFORM_DEPLOYMENT_MODE=cloud-replica",
        "PLATFORM_CLOUD_AUTH_MODE=dingtalk",
        "PLATFORM_IDENTITY_MODE=production",
    ):
        assert runtime_value in script
    assert '"freshness":"unavailable"' not in script


def test_remote_stage_requires_consecutive_loopback_health_checks():
    script = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")

    assert "loopback_health_streak=0" in script
    assert "loopback_health_streak=$((loopback_health_streak + 1))" in script
    assert "loopback_health_streak=0" in script
    assert '[[ "$loopback_health_streak" -ge 3 ]] || fail' in script
    assert (
        "/usr/bin/curl --silent --show-error --fail --max-time 2 "
        "http://127.0.0.1:8080/api/health >/dev/null || fail"
    ) not in script


def test_remote_stage_enforces_data_disk_and_bounded_release_retention():
    script = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")

    assert 'data_path="/data/orbbec-agent-platform"' in script
    assert 'staging_root="/data/staging/orbbec-agent-platform"' in script
    assert 'archive_releases="/data/archive/orbbec-agent-platform/releases"' in script
    assert 'stage_path="$staging_root/$deployment_id"' in script
    assert 'staging_path="$root_path/staging"' not in script
    assert 'cleanup_stage' in script
    assert 'find "$stage_path" -depth -delete' in script
    assert 'df -B1 / /data' in script
    assert "26843545600" in script  # 25 GiB preflight floor
    assert "21474836480" in script  # 20 GiB projected floor
    assert "root_used_percent" in script
    assert '"$root_used_percent" -le 75' in script
    assert "retain_release_history" in script
    assert "retain_platform_images" in script
    assert "docker system prune" not in script


def test_platform_growing_volumes_are_bound_to_data_disk():
    stage = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")
    backup = (CLOUD / "backup.sh").read_text(encoding="utf-8")

    assert 'postgres_data="$data_path/postgres"' in stage
    assert 'ensure_bind_volume orbbec-agent-platform-postgres-data "$postgres_data"' in stage
    assert 'backup_data="$data_path/backups"' in stage
    assert 'ensure_bind_volume orbbec-agent-platform-backups "$backup_data"' in stage
    assert '/data/orbbec-agent-platform/backups' in backup


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
    production_roles = (
        "platform_control_migrator",
        "platform_control_app",
        "platform_directory_worker",
        "platform_stream_ingest",
        "platform_audit_append",
        "platform_control_maintenance",
    )
    preview_roles = tuple(f"{role}_preview" for role in production_roles)
    assert _bash_array(script, "roles") == production_roles + preview_roles
    production_passwords = (
        "control-migrator-password",
        "control-app-password",
        "control-directory-worker-password",
        "control-stream-ingest-password",
        "control-audit-append-password",
        "control-maintenance-password",
    )
    preview_passwords = tuple(
        f"preview-{password}" for password in production_passwords
    )
    assert _bash_array(script, "password_names") == (
        production_passwords + preview_passwords
    )
    production_dsns = (
        "control-migrator-database-url",
        "control-database-url",
        "control-directory-worker-database-url",
        "control-stream-ingest-database-url",
        "control-audit-database-url",
        "control-maintenance-database-url",
    )
    preview_dsns = tuple(f"preview-{dsn}" for dsn in production_dsns)
    assert _bash_array(script, "dsn_names") == production_dsns + preview_dsns
    assert _bash_array(script, "database_names") == (
        ("agent_platform_control",) * 6 + ("agent_platform_control_preview",) * 6
    )
    assert len(set(production_passwords + preview_passwords)) == 12
    assert len(set(production_dsns + preview_dsns)) == 12
    assert "chmod 600" in script
    assert "revoke connect on database" in script.lower()
    assert "revoke all on schema public from public" in script.lower()
    assert "revoke create on schema public from public" in script.lower()
    assert "revoke all on schema platform_control from public" in script.lower()
    assert "app.control_plane.migrate" in script
    assert "PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE" in script
    assert "PLATFORM_CONTROL_OWNER_ROLE" in script
    assert "platform_control_owner" in script
    assert "platform_control_owner_preview" in script
    assert "trap revoke_owner_memberships EXIT" in script
    assert "revoke_owner_memberships" in script
    assert "grant platform_control_owner to platform_control_migrator" in script
    assert (
        "grant platform_control_owner_preview to platform_control_migrator_preview"
    ) in script
    assert "revoke platform_control_owner from platform_control_migrator" in script
    assert (
        "revoke platform_control_owner_preview from platform_control_migrator_preview"
    ) in script
    assert "join pg_roles member on member.oid = membership.member" in script
    assert "join pg_roles granted on granted.oid = membership.roleid" in script
    assert "where member.rolname = any" in script
    assert "where granted.rolname in" in script
    assert script.count("select format('revoke %I from %I'") == 2
    assert "nosuperuser" in script.lower()
    assert "nocreatedb" in script.lower()
    assert "nocreaterole" in script.lower()
    assert "noreplication" in script.lower()
    assert "nobypassrls" in script.lower()
    assert "noinherit" in script.lower()
    assert "-O platform_control_migrator" not in script
    assert 'preview_dsn="$private_path/preview-${dsn_names[$index]}"' not in script
    assert "platform_replica" not in script
    assert "replica-database-url" not in script

    lowered = script.lower()
    for forbidden in (
        "create extension postgres_fdw",
        "create extension dblink",
        "login password '$",
        'login password "$',
        "echo $",
        "superuser password",
    ):
        assert forbidden not in lowered

    trap_index = script.index("trap revoke_owner_memberships EXIT")
    grant_index = script.index(
        "grant platform_control_owner to platform_control_migrator;"
    )
    migration_index = script.index("python -m app.control_plane.migrate")
    cleanup_index = script.index("revoke_owner_memberships || fail")
    assert trap_index < grant_index < migration_index < cleanup_index


def test_control_migrator_uses_only_the_validated_environment_owner_role():
    runner = (ROOT / "backend" / "app" / "control_plane" / "migrate.py").read_text(
        encoding="utf-8"
    )

    assert '"platform_control_owner"' in runner
    assert '"platform_control_owner_preview"' in runner
    assert "owner_role" in runner
    assert "set local role" in runner.lower()
    assert "PLATFORM_CONTROL_OWNER_ROLE" in runner


def test_remote_stage_calls_control_bootstrap_without_replacing_replica():
    stage = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")

    assert '"$release_path/deploy/cloud/bootstrap-control-db.sh"' in stage
    validation = (
        "for bootstrap_helper in \\\n"
        '  "$release_path/deploy/cloud/bootstrap-control-db.sh" \\\n'
    )
    invocation = (
        'control_bootstrap_result="$("$release_path/deploy/cloud/'
        'bootstrap-control-db.sh" \\\n'
    )
    assert stage.count(validation) == 1
    assert stage.count(invocation) == 1
    assert stage.index(validation) < stage.index(invocation)
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


FAE_LIVE_REQUESTS = (
    "owner|GET|https://agent.orbbec.com.cn/api/v1/account",
    "member|GET|https://agent.orbbec.com.cn/api/v1/account",
    "viewer|GET|https://agent.orbbec.com.cn/api/v1/account",
    "member|GET|https://agent.orbbec.com.cn/fae/",
    "member|GET|https://agent.orbbec.com.cn/fae/conversations/fae:owned-1",
    "owner|GET|https://agent.orbbec.com.cn/fae/manage/",
    "owner|GET|https://agent.orbbec.com.cn/fae/manage/sessions",
    "owner|GET|https://agent.orbbec.com.cn/fae/manage/issues",
    "owner|GET|https://agent.orbbec.com.cn/api/fae/overview",
    "owner|GET|https://agent.orbbec.com.cn/api/fae/sessions?limit=1",
    "owner|GET|https://agent.orbbec.com.cn/api/fae/issues",
    "owner|GET|https://agent.orbbec.com.cn/api/fae/reports/latest",
    "owner|GET|https://agent.orbbec.com.cn/api/admin/fae/overview",
    "owner|GET|https://agent.orbbec.com.cn/fae/manage/reports",
    "member|GET|https://agent.orbbec.com.cn/api/fae/overview",
    "member|GET|https://agent.orbbec.com.cn/api/fae/sessions?limit=1",
    "member|GET|https://agent.orbbec.com.cn/api/fae/issues",
    "viewer|GET|https://agent.orbbec.com.cn/fae/manage/",
    "viewer|GET|https://agent.orbbec.com.cn/api/fae/overview",
    "viewer|GET|https://agent.orbbec.com.cn/api/fae/sessions?limit=1",
    "viewer|GET|https://agent.orbbec.com.cn/api/fae/issues",
    "owner|POST|https://agent.orbbec.com.cn/api/fae/issues",
)


def test_fae_cloud_acceptance_requires_the_bounded_issue_page_contract():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")

    assert 'if [[ "$path" == "/api/fae/issues" ]]' in script
    assert 'value.get("items")' in script
    assert 'value.get("total")' in script
    assert 'value.get("limit")' in script
    assert 'value.get("offset")' in script
    assert 'value.get("has_more")' in script


def test_fae_cloud_acceptance_uses_canonical_routes_with_bounded_compatibility():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    contract = _bash_function(
        script,
        "verify_fae_workbench_cloud_contract",
        "verify_markdown_rendering",
    )

    for path in (
        "/fae/",
        "/fae/conversations/fae:owned-1",
        "/fae/manage/",
        "/fae/manage/sessions",
        "/fae/manage/issues",
        "/fae/manage/reports",
        "/api/fae/overview",
        "/api/fae/sessions?limit=1",
        "/api/fae/issues",
        "/api/fae/reports/latest",
    ):
        assert path in contract
    compatibility = _bash_function(
        script,
        "verify_fae_reports_compatibility",
        "verify_fae_viewer_denied",
    )
    assert compatibility.count("https://agent.orbbec.com.cn/admin/fae/reports") == 1
    assert contract.count("verify_fae_reports_compatibility") == 1
    assert contract.count("/api/admin/fae/overview") == 1
    assert "/usr/bin/cmp" in contract
    assert "fae-canonical-overview.json" in contract
    assert "verify_fae_reports_compatibility" in contract
    for old_path in (
        "/admin/fae/sessions",
        "/admin/fae/issues",
        "/api/admin/fae/sessions",
        "/api/admin/fae/issues",
        "/api/admin/fae/reports",
    ):
        assert old_path not in contract


def test_cloud_acceptance_gates_scoped_history_and_cross_identity_deep_links():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    history = _bash_function(
        script,
        "verify_platform_workspace_history",
        "verify_markdown_rendering",
    )

    assert "direct_agent_id=$agent_id" in history
    for agent_id in (
        "hr-bot",
        "marketing-prospecting-bot",
        "marketing-inbound-bot",
        "marketing-voice-bot",
        "marketing-intelligence-bot",
        "marketing-gtm-bot",
    ):
        assert agent_id in history
    assert "if not items:" in history
    assert "if items:" not in history
    assert 'if [[ -n "$conversation_id" ]]' not in history
    assert '"$base/api/v1/conversations/$conversation_id"' in history
    assert '[[ "$status_code" == "404" ]]' in history
    assert "verify_platform_workspace_history" in _bash_function(
        script, "accept_v2_real", "enable_with_rollback"
    )


def test_route_transaction_compares_explicit_workspace_response_snapshots():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    snapshot = _bash_function(
        script,
        "workspace_non_regression_snapshot",
        "remote_fae_snapshot",
    )
    enable = _bash_function(script, "enable_with_rollback", 'case "$action" in')

    expected_routes = (
        "https://agent.orbbec.com.cn/",
        "https://agent.orbbec.com.cn/office/",
        "https://agent.orbbec.com.cn/office/?view=services",
        "https://fae.orbbec.com.cn/",
        "https://agent.orbbec.com.cn/voc/",
    )
    for url in expected_routes:
        assert url in snapshot
    assert "/usr/sbin/nginx" in snapshot
    assert '"-T"' in snapshot
    assert "agent.orbbec.com.cn" in snapshot
    assert "/etc/nginx/sites-available/agent-domain.conf" not in snapshot
    assert "proxy_pass" in snapshot
    for fabricated_owner in ("'platform'", "'ai-admin'", "'ai-fae-public'", "'voc'"):
        assert fabricated_owner not in snapshot
    for field in (
        "status",
        "location",
        "content_marker",
        "upstream_owner",
        "strict-transport-security",
        "content-security-policy",
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
        "permissions-policy",
    ):
        assert field in snapshot
    assert enable.count("workspace_non_regression_snapshot") == 2
    assert enable.index(
        'workspace_snapshot_before="$(workspace_non_regression_snapshot)"'
    ) < enable.index("publish_formal_nginx") < enable.index(
        'workspace_snapshot_after="$(workspace_non_regression_snapshot)"'
    )
    assert '[[ "$workspace_snapshot_after" == "$workspace_snapshot_before" ]]' in enable


def _run_workspace_snapshot(tmp_path: Path, active_config: Path):
    tmp_path.mkdir(parents=True)
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    snapshot = _bash_function(
        script,
        "workspace_non_regression_snapshot",
        "remote_fae_snapshot",
    )
    fake_nginx = tmp_path / "nginx"
    fake_nginx.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "[[ \"$1\" == \"-T\" ]]\n"
        f"cat -- {active_config}\n"
        "printf '%s\\n' 'server {' '  listen 443 ssl;' "
        "'  server_name fae.orbbec.com.cn;' '  location / {' "
        "'    proxy_pass http://127.0.0.1:8000;' '  }' '}'\n",
        encoding="utf-8",
    )
    fake_nginx.chmod(0o700)
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import pathlib
            import sys

            arguments = sys.argv[1:]
            headers = pathlib.Path(arguments[arguments.index("-D") + 1])
            body = pathlib.Path(arguments[arguments.index("-o") + 1])
            url = arguments[-1]
            headers.write_text(
                "HTTP/2 200\\r\\n"
                "Strict-Transport-Security: max-age=31536000\\r\\n"
                "Content-Security-Policy: default-src 'none'\\r\\n"
                "X-Content-Type-Options: nosniff\\r\\n"
                "X-Frame-Options: DENY\\r\\n"
                "Referrer-Policy: no-referrer\\r\\n"
                "Permissions-Policy: camera=()\\r\\n\\r\\n",
                encoding="iso-8859-1",
            )
            body.write_text(f"body:{url}", encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    fake_curl.chmod(0o700)
    snapshot = snapshot.replace("/usr/sbin/nginx", str(fake_nginx)).replace(
        "/usr/bin/curl", str(fake_curl)
    ).replace("remote /usr/bin/python3", 'remote "$python"')
    harness = tmp_path / "workspace-snapshot.sh"
    harness.write_text(
        f"""#!/bin/bash
set -euo pipefail
python={ROOT / 'backend/.venv/bin/python'}
remote() {{ "$@"; }}
{snapshot}
workspace_non_regression_snapshot
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["/bin/bash", str(harness)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return tuple(json.loads(line) for line in result.stdout.splitlines())


def test_workspace_snapshot_observes_active_nginx_owners_instead_of_labels(tmp_path):
    active = tmp_path / "agent-domain.conf"
    active.write_text(
        (CLOUD / "agent-domain.nginx.conf")
        .read_text(encoding="utf-8")
        .replace("__AGENT_DOMAIN__", "agent.orbbec.com.cn"),
        encoding="utf-8",
    )

    before = _run_workspace_snapshot(tmp_path / "before", active)
    active.write_text(
        active.read_text(encoding="utf-8").replace(
            "proxy_pass http://127.0.0.1:8011;",
            "proxy_pass http://127.0.0.1:8012;",
        ),
        encoding="utf-8",
    )
    after = _run_workspace_snapshot(tmp_path / "after", active)

    assert [item["upstream_owner"] for item in before] == [
        "http://127.0.0.1:8080",
        "http://127.0.0.1:8011",
        "http://127.0.0.1:8011",
        before[3]["upstream_owner"],
        "http://172.29.0.3:18130",
    ]
    assert before[3]["upstream_owner"].startswith("nginx-server-sha256:")
    changed = {
        index
        for index, (old, new) in enumerate(zip(before, after, strict=True))
        if old != new
    }
    assert changed == {1, 2}
    assert {after[index]["upstream_owner"] for index in changed} == {
        "http://127.0.0.1:8012"
    }


def test_snapshot_regression_restores_the_published_nginx_transaction():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    publish = _bash_function(
        script,
        "publish_formal_nginx",
        "rollback_formal_nginx_transaction",
    )
    rollback = _bash_function(
        script,
        "rollback_formal_nginx_transaction",
        "commit_formal_nginx_transaction",
    )
    commit = _bash_function(
        script,
        "commit_formal_nginx_transaction",
        "cookie_config",
    )
    enable = _bash_function(script, "enable_with_rollback", 'case "$action" in')

    assert 'nginx_transaction_published="0"' in enable
    failure_handler = enable.split("enable_failure_rollback() {", 1)[1].split(
        "  }", 1
    )[0]
    assert '"$nginx_transaction_published" == "1"' in failure_handler
    assert "rollback_formal_nginx_transaction" in failure_handler
    assert failure_handler.index("rollback_formal_nginx_transaction") < (
        failure_handler.index("remote_feature 0")
    )
    assert enable.index('nginx_transaction_published="1"') < enable.index(
        "publish_formal_nginx"
    ) < enable.index(
        'workspace_snapshot_after="$(workspace_non_regression_snapshot)"'
    ) < enable.index("remote_feature 1") < enable.index(
        "commit_formal_nginx_transaction"
    )
    assert 'publish_formal_nginx "$nginx_transaction_id"' in enable
    assert 'rollback_formal_nginx_transaction "$nginx_transaction_id"' in enable
    assert 'commit_formal_nginx_transaction "$nginx_transaction_id"' in enable
    assert "agent-domain.transaction.before.conf" in rollback
    assert "agent-domain.transaction.id" in rollback
    assert "agent-domain.transaction.lock" in publish
    assert "agent-domain.transaction.lock" in rollback
    assert "/usr/bin/flock -x" in publish
    assert "/usr/bin/flock -x" in rollback
    assert "exit 0" in rollback
    assert "/usr/sbin/nginx -t" in rollback
    assert "/bin/systemctl reload nginx" in rollback
    assert "agent-domain.transaction.before.conf" in commit
    assert "agent-domain.transaction.id" in commit
    assert (
        '/bin/rm -f -- "$transaction_before" "$enabled_transaction_before"'
        in commit
    )
    assert "transaction_before" not in publish.rsplit("trap - ERR EXIT", 1)[1]
    restore_handler = publish.split("restore_nginx() {", 1)[1].split("}\ntrap", 1)[0]
    assert "|| true" not in restore_handler


def test_formal_nginx_transaction_preserves_legacy_regular_enabled_file():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    publish = _bash_function(
        script,
        "publish_formal_nginx",
        "rollback_formal_nginx_transaction",
    )
    rollback = _bash_function(
        script,
        "rollback_formal_nginx_transaction",
        "commit_formal_nginx_transaction",
    )
    commit = _bash_function(
        script,
        "commit_formal_nginx_transaction",
        "cookie_config",
    )

    assert 'enabled_before_kind="regular"' in publish
    assert 'enabled_before_kind="symlink"' in publish
    assert "agent-domain.transaction.before.enabled.conf" in publish
    assert "agent-domain.transaction.before.enabled.kind" in publish
    assert 'case "$enabled_before_kind" in' in publish
    assert 'case "$enabled_before_kind" in' in rollback
    assert '/bin/rm -f -- "$enabled"' in rollback
    assert '"$enabled_transaction_before_config" "$enabled.part.restore"' in rollback
    assert '"$enabled_transaction_before_kind"' in commit
    assert '"$enabled_transaction_before_config"' in commit


def test_routes_action_publishes_edge_config_without_changing_brain_feature():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    routes = _bash_function(script, "publish_routes_only", "enable_with_rollback")
    dispatcher = script.split('case "$action" in', 1)[1]

    assert '"routes"' in script.split("repository_root=", 1)[0]
    assert "routes)" in dispatcher
    assert "acquire_action_lock" in dispatcher.split("routes)", 1)[1].split(";;", 1)[0]
    assert "publish_routes_only" in dispatcher.split("routes)", 1)[1].split(";;", 1)[0]
    assert "publish_formal_nginx" in routes
    assert "rollback_formal_nginx_transaction" in routes
    assert "commit_formal_nginx_transaction" in routes
    assert "route_non_regression_snapshot" in routes
    assert "verify_canonical_workspace_routes" in routes
    assert "remote_feature" not in routes


def _run_nginx_snapshot_transaction(
    tmp_path: Path,
    *,
    changed: bool,
    publish_fails: bool = False,
    feature_enable_fails: bool = False,
):
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    enable = "enable_with_rollback() {" + script.split(
        "enable_with_rollback() {", 1
    )[1].split('\n}\n\ncase "$action" in', 1)[0] + "\n}"
    events = tmp_path / "events"
    snapshot_state = tmp_path / "snapshot-state"
    harness = tmp_path / "nginx-snapshot-transaction.sh"
    harness.write_text(
        f"""#!/bin/bash
set -eEuo pipefail
fail() {{ return 91; }}
action_lock_exit() {{ :; }}
local_runtime_preflight() {{ :; }}
run_relay_canary() {{ :; }}
prepare_v2_reference_evidence() {{ :; }}
v2_cutover_gates() {{ :; }}
remote_feature() {{
  printf 'remote_feature:%s\\n' "$1" >> {events}
  if [[ "$1" == "1" && "{'1' if feature_enable_fails else '0'}" == "1" ]]; then
    return 74
  fi
}}
publish_formal_nginx() {{
  printf 'publish\\n' >> {events}
  {'return 73' if publish_fails else ':'}
}}
rollback_formal_nginx_transaction() {{ printf 'rollback\\n' >> {events}; }}
commit_formal_nginx_transaction() {{ printf 'commit\\n' >> {events}; }}
release_action_lock() {{ printf 'release\\n' >> {events}; }}
workspace_non_regression_snapshot() {{
  if [[ ! -e {snapshot_state} ]]; then
    : > {snapshot_state}
    printf 'before\\n'
  else
    printf '{'after' if changed else 'before'}\\n'
  fi
}}
{enable}
enable_with_rollback
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["/bin/bash", str(harness)],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, tuple(events.read_text(encoding="utf-8").splitlines())


def test_changed_post_snapshot_executes_nginx_rollback_before_failing(tmp_path):
    result, events = _run_nginx_snapshot_transaction(tmp_path, changed=True)

    assert result.returncode == 91
    assert events == (
        "remote_feature:0",
        "publish",
        "rollback",
        "remote_feature:0",
        "release",
    )


def test_publish_failure_executes_armed_nginx_rollback(tmp_path):
    result, events = _run_nginx_snapshot_transaction(
        tmp_path, changed=False, publish_fails=True
    )

    assert result.returncode == 73
    assert events == (
        "remote_feature:0",
        "publish",
        "rollback",
        "remote_feature:0",
        "release",
    )


def test_equal_post_snapshot_commits_nginx_after_enabling_feature(tmp_path):
    result, events = _run_nginx_snapshot_transaction(tmp_path, changed=False)

    assert result.returncode == 0, result.stderr
    assert events == (
        "remote_feature:0",
        "publish",
        "remote_feature:1",
        "commit",
    )


def test_feature_enable_failure_rolls_nginx_back_before_feature_cleanup(tmp_path):
    result, events = _run_nginx_snapshot_transaction(
        tmp_path, changed=False, feature_enable_fails=True
    )

    assert result.returncode == 74
    assert events == (
        "remote_feature:0",
        "publish",
        "remote_feature:1",
        "rollback",
        "remote_feature:0",
        "release",
    )


def test_voc_acceptance_proves_management_scope_is_independent_of_fae():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    contract = _bash_function(
        script,
        "verify_standalone_voc_release",
        "local_runtime_preflight",
    )

    assert contract.count('"$base/voc/api/v1/admin/vocs"') == 3
    assert '"${curl_owner[@]}"' in contract
    assert '"${curl_viewer[@]}"' in contract
    assert '"${curl_member[@]}"' in contract
    assert contract.count('[[ "$status_code" == "200" ]]') >= 2
    assert '[[ "$status_code" == "403" ]]' in contract


def test_acceptance_requires_the_canonical_internal_fae_launch_fragment():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    acceptance = _bash_function(script, "accept_v2_real", "enable_with_rollback")

    assert "('https','agent.orbbec.com.cn','/fae/')" in acceptance
    assert "parsed.query or parsed.fragment.count('=') != 1" in acceptance
    assert "fragment_key,fragment_code=parsed.fragment.split('=',1)" in acceptance
    assert "fragment_key != 'platform_launch'" in acceptance
    assert "urllib.parse.unquote(fragment_code) != fragment_code" in acceptance
    assert "re.fullmatch(r'[A-Za-z0-9_-]{32,256}', fragment_code)" in acceptance
    assert "fae.orbbec.com.cn','/enterprise/launch" not in acceptance


def test_acceptance_exchanges_two_platform_launches_and_proves_fae_subject_history():
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    history = _bash_function(
        script,
        "verify_fae_internal_history",
        "verify_markdown_rendering",
    )
    acceptance = _bash_function(script, "accept_v2_real", "enable_with_rollback")

    assert '"${curl_owner[@]}"' in history
    assert '"$base/api/v1/agents/ai-fae-agent/launch"' in history
    assert "for role in member owner" in history
    assert history.count('"$base/fae/api/enterprise/session"') == 1
    assert history.count('"$base/fae/api/authenticated/conversations?limit=30"') == 1
    assert '"$base/fae/api/authenticated/conversations/$member_session_id"' in history
    assert "__Host-fae_enterprise_session" in history
    assert '[[ "$status_code" == "201" ]]' in history
    assert '[[ "$status_code" == "200" ]]' in history
    assert '[[ "$status_code" == "404" ]]' in history
    assert '{"detail": "conversation not found"}' in history
    assert "member_internal_user_id == owner_internal_user_id" in history
    assert 'session["display_name"] != platform_account.get("display_name")' in history
    assert "not member_session_ids or not owner_session_ids" in history
    assert "set(member_session_ids).isdisjoint(owner_session_ids)" in history
    assert "for role in member owner" in history
    assert '"$base/fae/api/authenticated/conversations/$owner_session_id"' in history
    assert "/usr/bin/cmp -s" in history
    assert 'verify_fae_internal_history "$temporary/fae-launch.json"' in acceptance


def _write_fae_history_curl_stub(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            from pathlib import Path
            import sys

            arguments = sys.argv[1:]
            output = Path(arguments[arguments.index("-o") + 1])
            method = arguments[arguments.index("-X") + 1] if "-X" in arguments else "GET"
            url = next(value for value in reversed(arguments) if value.startswith("https://"))
            role = "unknown"
            if "--config" in arguments:
                role = Path(arguments[arguments.index("--config") + 1]).stem
            if "--cookie-jar" in arguments:
                jar = Path(arguments[arguments.index("--cookie-jar") + 1])
                role = "member" if "member" in jar.name else "owner"
            elif "--cookie" in arguments:
                jar = Path(arguments[arguments.index("--cookie") + 1])
                role = "member" if "member" in jar.name else "owner"

            status = 200
            body = {}
            member_session = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            owner_session = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            if url.endswith("/api/v1/agents/ai-fae-agent/launch"):
                body = {
                    "launch_url": "https://agent.orbbec.com.cn/fae/"
                    + "#platform_launch=" + "o" * 43,
                }
            elif url.endswith("/fae/api/enterprise/session"):
                status = 201
                body = {
                    "authenticated": True,
                    "authentication_mode": "platform_enterprise",
                    "display_name": role,
                    "partner_display_name": None,
                    "csrf_token": role + "-csrf",
                }
                jar.write_text(
                    "# Netscape HTTP Cookie File\\n"
                    "#HttpOnly_agent.orbbec.com.cn\\tFALSE\\t/\\tTRUE\\t0\\t"
                    "__Host-fae_enterprise_session\\t" + role + "-token\\n",
                    encoding="utf-8",
                )
            elif url.endswith("/fae/api/authenticated/conversations?limit=30"):
                selected = member_session if role == "member" else owner_session
                items = [{
                    "session_id": selected,
                    "title": role + " history",
                    "channel": "fae",
                    "created_at": "2026-09-03T00:00:00+00:00",
                    "last_active_at": "2026-09-03T00:00:01+00:00",
                }]
                leak = os.environ.get("FAE_HISTORY_LEAK")
                if role == "owner" and leak == "member_into_owner":
                    items.append({**items[0], "session_id": member_session})
                if role == "member" and leak == "owner_into_member":
                    items.append({**items[0], "session_id": owner_session})
                body = {"items": items, "next_cursor": None}
            elif url.endswith("/fae/api/authenticated/conversations/" + member_session):
                if role == "owner":
                    status = 404
                    body = {"detail": "conversation not found"}
                else:
                    body = {
                        "session_id": member_session,
                        "channel": "fae",
                        "messages": [],
                        "current_schema": None,
                        "attachments": [],
                    }
            elif url.endswith("/fae/api/authenticated/conversations/" + owner_session):
                if role == "member":
                    status = 404
                    body = {"detail": "conversation not found"}
                else:
                    body = {
                        "session_id": owner_session,
                        "channel": "fae",
                        "messages": [],
                        "current_schema": None,
                        "attachments": [],
                    }
            else:
                raise SystemExit(81)

            output.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
            with Path(os.environ["FAE_HISTORY_LOG"]).open("a", encoding="utf-8") as log:
                log.write(f"{role}|{method}|{url}\\n")
            print(status, end="")
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o700)


def _run_fae_history_contract(
    tmp_path: Path, *, leaks_member_history: str | None = None
):
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    history = _bash_function(
        script,
        "verify_fae_internal_history",
        "verify_markdown_rendering",
    )
    stub = tmp_path / "curl"
    _write_fae_history_curl_stub(stub)
    history = history.replace("/usr/bin/curl", str(stub))
    temporary = tmp_path / "private"
    temporary.mkdir(mode=0o700)
    (temporary / "fae-member-account.json").write_text(
        '{"internal_user_id":"11111111-1111-4111-8111-111111111111",'
        '"display_name":"member"}',
        encoding="utf-8",
    )
    (temporary / "fae-owner-account.json").write_text(
        '{"internal_user_id":"22222222-2222-4222-8222-222222222222",'
        '"display_name":"owner"}',
        encoding="utf-8",
    )
    member_launch = temporary / "fae-launch.json"
    member_launch.write_text(
        json.dumps(
            {
                "launch_url": (
                    "https://agent.orbbec.com.cn/fae/"
                    f"#platform_launch={'m' * 43}"
                )
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "requests.log"
    harness = tmp_path / "history-contract.sh"
    harness.write_text(
        f"""#!/bin/bash
set -eEuo pipefail
umask 077
fail() {{ exit 91; }}
python={ROOT / 'backend/.venv/bin/python'}
base=https://agent.orbbec.com.cn
temporary={temporary}
curl_owner=({stub} --config owner)
{history}
verify_fae_internal_history {member_launch}
""",
        encoding="utf-8",
    )
    environment = {**os.environ, "FAE_HISTORY_LOG": str(log)}
    if leaks_member_history:
        environment["FAE_HISTORY_LEAK"] = leaks_member_history
    result = subprocess.run(
        ["/bin/bash", str(harness)],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    requests = tuple(log.read_text(encoding="utf-8").splitlines())
    return result, requests


def test_fae_history_contract_executes_two_exchanges_and_cross_subject_probe(tmp_path):
    result, requests = _run_fae_history_contract(tmp_path)

    assert result.returncode == 0, result.stderr
    assert len(requests) == 9
    assert sum("/fae/api/enterprise/session" in item for item in requests) == 2
    assert sum("/fae/api/authenticated/conversations?limit=30" in item for item in requests) == 2
    assert sum("/fae/api/authenticated/conversations/aaaaaaaa" in item for item in requests) == 2
    assert sum("/fae/api/authenticated/conversations/bbbbbbbb" in item for item in requests) == 2


@pytest.mark.parametrize("leak_direction", ("member_into_owner", "owner_into_member"))
def test_fae_history_contract_rejects_cross_subject_list_leak(
    tmp_path, leak_direction
):
    result, _requests = _run_fae_history_contract(
        tmp_path, leaks_member_history=leak_direction
    )

    assert result.returncode == 91


def _write_fae_curl_stub(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            from pathlib import Path
            import re
            import sys

            arguments = sys.argv[1:]
            output = "/dev/null"
            config = None
            method = "GET"
            headers = []
            url = None
            index = 0
            while index < len(arguments):
                value = arguments[index]
                if value in {"--config", "-o", "-w", "-H", "--data-binary"}:
                    selected = arguments[index + 1]
                    if value == "--config": config = selected
                    elif value == "-o": output = selected
                    elif value == "-H": headers.append(selected)
                    index += 2
                elif value == "-X":
                    method = arguments[index + 1]
                    index += 2
                elif value.startswith("http"):
                    url = value
                    index += 1
                else:
                    index += 1
            if config is None or url is None:
                raise SystemExit(81)
            raw = Path(config).read_text(encoding="utf-8")
            match = re.search(r"__Host-platform_session=([a-z]+)-session", raw)
            if match is None:
                raise SystemExit(82)
            role = match.group(1)
            csrf = f"{role}-csrf"
            required = (
                f'header = "Cookie: __Host-platform_session={role}-session; '
                f'__Host-platform_csrf={csrf}"',
                'header = "Origin: https://agent.orbbec.com.cn"',
                f'header = "X-CSRF-Token: {csrf}"',
            )
            if raw.splitlines() != list(required):
                raise SystemExit(83)
            state = Path(os.environ["STUB_STATE"])
            count = int(state.read_text(encoding="ascii")) if state.exists() else 0
            count += 1
            state.write_text(str(count), encoding="ascii")
            with Path(os.environ["STUB_LOG"]).open("a", encoding="utf-8") as stream:
                stream.write(f"{role}|{method}|{url}\\n")

            status = 200
            body = "{}"
            account_roles = {
                "owner": "platform_owner",
                "member": "member",
                "viewer": "management_viewer",
            }
            if url.endswith("/api/v1/account"):
                account_role = os.environ.get(f"STUB_{role.upper()}_ROLE", account_roles[role])
                body = json.dumps({
                    "internal_user_id": {
                        "owner": "11111111-1111-4111-8111-111111111111",
                        "member": "22222222-2222-4222-8222-222222222222",
                        "viewer": "33333333-3333-4333-8333-333333333333",
                    }[role],
                    "display_name": role,
                    "role": account_role,
                    "departments": [],
                    "gender": None,
                    "observation_agent_ids": ["ai-fae-agent"] if role == "viewer" else [],
                    "real_name": None,
                    "mobile": None,
                    "primary_department": None,
                    "directory_freshness": "fresh",
                    "hard_stale_read_only": False,
                    "csrf_token": csrf,
                }, separators=(",", ":"))
            elif role == "member" and (
                url.endswith("/fae/") or "/fae/conversations/" in url
            ):
                body = "<html><body>FAE direct workspace</body></html>"
            elif role == "viewer" and url.endswith("/fae/manage/"):
                body = "<html><body><div id=app></div></body></html>"
            elif role in {"member", "viewer"}:
                status = 403
                body = '{"detail":"fae workbench access required"}'
            elif method == "GET" and url.endswith("/api/fae/issues"):
                body = json.dumps({
                    "items": [], "total": 0, "limit": 100,
                    "offset": 0, "has_more": False,
                }, separators=(",", ":"))
            elif method == "POST" and url.endswith("/api/fae/issues"):
                if "Content-Type: application/json" not in headers:
                    raise SystemExit(84)
                status = 403
                body = '{"detail":"cloud_review_read_only"}'
            elif url.endswith("/api/fae/reports/latest"):
                body = json.dumps({
                    "schema_name": "fae.analysis-report", "status": "ready",
                    "source": {"agent_id": "ai-fae-agent", "session_count": 692,
                               "turn_count": 1492, "reviewed_session_count": 654},
                    "metrics": [
                        {"dimension": "usage"}, {"dimension": "business_value"},
                        {"dimension": "answer_effectiveness"},
                        {"dimension": "insights_improvement"},
                    ],
                }, separators=(",", ":"))
            elif (
                url.endswith("/fae/manage/reports")
                or url.endswith("/admin/fae/reports")
            ):
                body = "<html><body><div id=app></div></body></html>"

            if count == int(os.environ.get("STUB_STATUS_AT", "0")):
                status = int(os.environ["STUB_STATUS"])
            if count == int(os.environ.get("STUB_BODY_AT", "0")):
                body = os.environ["STUB_BODY"]

            if output != "/dev/null":
                Path(output).write_text(body, encoding="utf-8")
            sys.stdout.write(str(status))
            if count == int(os.environ.get("STUB_FAIL_AT", "0")):
                raise SystemExit(7)
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o700)


def _run_fae_live_contract(
    tmp_path: Path,
    *,
    fail_at: int = 0,
    role_overrides: dict[str, str] | None = None,
    stub_environment: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], tuple[str, ...], bool]:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    cookie = _bash_function(script, "cookie_config", "verify_fae_reports_ready")
    contract = _bash_function(
        script,
        "verify_fae_workbench_cloud_contract",
        "verify_markdown_rendering",
    )
    stub = tmp_path / "curl-stub"
    _write_fae_curl_stub(stub)
    log = tmp_path / "requests.log"
    state = tmp_path / "requests.count"
    viewer_rendered = tmp_path / "viewer-rendered"
    for role in ("owner", "member", "viewer"):
        cookie_file = tmp_path / f"{role}.cookie"
        cookie_file.write_text(
            f"__Host-platform_session={role}-session; "
            f"__Host-platform_csrf={role}-csrf\n",
            encoding="utf-8",
        )
        cookie_file.chmod(0o600)
    harness = tmp_path / "fae-contract.sh"
    harness.write_text(
        f"""#!/bin/bash
set -euo pipefail
fail() {{ exit 91; }}
python={ROOT / 'backend/.venv/bin/python'}
base=https://agent.orbbec.com.cn
temporary={tmp_path}
require_private_file() {{
  [[ -f "$1" && ! -L "$1" ]]
  [[ "$(/usr/bin/stat -f '%Lp %u' "$1")" == "600 $(/usr/bin/id -u)" ]]
}}
{cookie}
cookie_config {tmp_path / 'owner.cookie'} {tmp_path / 'owner.curl'} {tmp_path / 'owner.browser.json'}
cookie_config {tmp_path / 'member.cookie'} {tmp_path / 'member.curl'} {tmp_path / 'member.browser.json'}
cookie_config {tmp_path / 'viewer.cookie'} {tmp_path / 'viewer.curl'} {tmp_path / 'viewer.browser.json'}
curl_owner=({stub} --config {tmp_path / 'owner.curl'})
curl_member=({stub} --config {tmp_path / 'member.curl'})
curl_viewer=({stub} --config {tmp_path / 'viewer.curl'})
verify_fae_reports_ready() {{ :; }}
verify_fae_reports_compatibility() {{ :; }}
verify_fae_viewer_denied() {{ printf rendered > {viewer_rendered}; }}
{contract}
verify_fae_workbench_cloud_contract
""",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "STUB_LOG": str(log),
        "STUB_STATE": str(state),
        "STUB_FAIL_AT": str(fail_at),
    }
    for role, value in (role_overrides or {}).items():
        environment[f"STUB_{role.upper()}_ROLE"] = value
    environment.update(stub_environment or {})
    result = subprocess.run(
        ["/bin/bash", str(harness)],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    requests = tuple(log.read_text(encoding="utf-8").splitlines()) if log.exists() else ()
    return result, requests, viewer_rendered.exists()


def test_fae_live_contract_validates_exact_identities_and_request_matrix(tmp_path):
    result, requests, viewer_rendered = _run_fae_live_contract(tmp_path)

    assert result.returncode == 0, result.stderr
    assert requests == FAE_LIVE_REQUESTS
    assert viewer_rendered is True


@pytest.mark.parametrize(
    ("role", "masquerading_role", "request_count"),
    (
        ("owner", "platform_admin", 1),
        ("member", "management_viewer", 2),
        ("viewer", "member", 3),
    ),
)
def test_fae_live_contract_rejects_mislabeled_identity_before_fae_requests(
    tmp_path, role, masquerading_role, request_count
):
    result, requests, _viewer_rendered = _run_fae_live_contract(
        tmp_path, role_overrides={role: masquerading_role}
    )

    assert result.returncode == 91
    assert requests == FAE_LIVE_REQUESTS[:request_count]


@pytest.mark.parametrize("failure_position", range(1, len(FAE_LIVE_REQUESTS) + 1))
def test_fae_live_contract_fails_closed_at_every_curl_position(
    tmp_path, failure_position
):
    result, requests, _viewer_rendered = _run_fae_live_contract(tmp_path, fail_at=failure_position)

    assert result.returncode == 91
    assert requests == FAE_LIVE_REQUESTS[:failure_position]


@pytest.mark.parametrize(
    "stub_environment",
    (
        {"STUB_STATUS_AT": "6", "STUB_STATUS": "403"},
        {"STUB_BODY_AT": "9", "STUB_BODY": "not-json"},
        {"STUB_BODY_AT": "12", "STUB_BODY": "{}"},
        {"STUB_BODY_AT": "13", "STUB_BODY": '{"different":true}'},
        {"STUB_BODY_AT": "14", "STUB_BODY": "not-html"},
        {"STUB_STATUS_AT": "15", "STUB_STATUS": "200"},
        {"STUB_STATUS_AT": "18", "STUB_STATUS": "403"},
        {"STUB_STATUS_AT": "19", "STUB_STATUS": "200"},
        {"STUB_BODY_AT": "22", "STUB_BODY": "{}"},
    ),
)
def test_fae_live_contract_rejects_wrong_status_or_body(tmp_path, stub_environment):
    result, _requests, _viewer_rendered = _run_fae_live_contract(
        tmp_path, stub_environment=stub_environment
    )

    assert result.returncode == 91


def _run_config_value(tmp_path: Path, value: dict, field: str) -> subprocess.CompletedProcess[str]:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    function = _bash_heredoc_function(script, "config_value")
    config = tmp_path / "acceptance.json"
    config.write_text(json.dumps(value), encoding="utf-8")
    harness = tmp_path / "config-value.sh"
    harness.write_text(
        f"""#!/bin/bash
set -euo pipefail
python={ROOT / 'backend/.venv/bin/python'}
config_path={config}
{function}
config_value {field}
""",
        encoding="utf-8",
    )
    return subprocess.run(
        ["/bin/bash", str(harness)], text=True, capture_output=True, check=False
    )


def _acceptance_config(tmp_path: Path, schema_version: int) -> dict:
    inert = str(tmp_path / "private-input")
    value = {
        "schema_version": schema_version,
        "member_cookie_file": inert,
        "owner_cookie_file": inert,
        "hr_prompt_file": inert,
        "interruption_prompt_file": inert,
        "relay_acceptance_config": inert,
        "evidence_file": inert,
    }
    if schema_version == 3:
        value["viewer_cookie_file"] = inert
    return value


def test_acceptance_config_schema_v3_requires_exact_viewer_cookie_field(tmp_path):
    valid = _acceptance_config(tmp_path, 3)
    viewer = _run_config_value(tmp_path, valid, "viewer_cookie_file")

    assert viewer.returncode == 0, viewer.stderr
    assert viewer.stdout.strip() == valid["viewer_cookie_file"]

    for invalid in (
        {key: value for key, value in valid.items() if key != "viewer_cookie_file"},
        {**valid, "unexpected": str(tmp_path / "unexpected")},
    ):
        result = _run_config_value(tmp_path, invalid, "member_cookie_file")
        assert result.returncode != 0


def test_acceptance_config_schema_v2_remains_valid_for_legacy_actions(tmp_path):
    valid = _acceptance_config(tmp_path, 2)
    member = _run_config_value(tmp_path, valid, "member_cookie_file")

    assert member.returncode == 0, member.stderr
    assert member.stdout.strip() == valid["member_cookie_file"]


@pytest.mark.parametrize(
    ("schema_version", "action", "expected"),
    (
        (2, "preflight", 0),
        (2, "reference", 0),
        (2, "rollback", 0),
        (2, "release", 91),
        (2, "accept", 91),
        (2, "restore", 91),
        (3, "release", 0),
        (3, "accept", 0),
        (3, "restore", 0),
    ),
)
def test_fae_actions_require_schema_v3_before_lock_or_remote_work(
    tmp_path, schema_version, action, expected
):
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    assert "require_action_identity_schema()" in script
    function = _bash_function(script, "require_action_identity_schema", "require_private_file")
    harness = tmp_path / "identity-schema.sh"
    harness.write_text(
        f"""#!/bin/bash
set -euo pipefail
fail() {{ exit 91; }}
config_schema_version={schema_version}
viewer_cookie_file={tmp_path / 'viewer.cookie' if schema_version == 3 else ''}
action={action}
{function}
require_action_identity_schema
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["/bin/bash", str(harness)], text=True, capture_output=True, check=False
    )

    assert result.returncode == expected
    if action in {"release", "accept", "restore"}:
        case_body = script.split(f"  {action})", 1)[1].split("    ;;", 1)[0]
        assert case_body.index("require_action_identity_schema") < case_body.index(
            "acquire_action_lock"
        )


def test_fae_report_dom_predicate_requires_exact_url_and_complete_report_shape():
    probe = CLOUD / "fae-reports-placeholder-probe.js"
    assert probe.is_file()
    expected_url = "https://agent.orbbec.com.cn/fae/manage/reports"
    report = (
        '<section class="fae-workbench"><aside class="fae-workbench__sidebar">'
        '<a aria-current="page" href="/fae/manage/reports">分析报告</a></aside>'
        '<div class="fae-workbench__content">'
        '<article class="fae-report" data-report-id="production-20260831">'
        '<header class="fae-report-hero"><h1>FAE 生产成果</h1></header>'
        '<section data-dimension="usage"><article data-metric="m1">1</article></section>'
        '<section data-dimension="business_value"><article data-metric="m2">2</article></section>'
        '<section data-dimension="answer_effectiveness"><article data-metric="m3">3</article></section>'
        '<section data-dimension="insights_improvement"><article data-metric="m4">4</article></section>'
        '<section class="fae-report-cases">典型案例待业务批准</section>'
        "</article></div></section>"
    )
    fixtures = (
        (expected_url, report),
        (
            expected_url,
            report.replace('data-dimension="insights_improvement"', 'data-dimension="usage"'),
        ),
        (
            expected_url,
            report.replace('<article data-metric="m4">4</article>', ''),
        ),
        ("https://example.com/fae/manage/reports", report),
        ("https://agent.orbbec.com.cn/fae/manage/reports/weekly", report),
        (
            expected_url,
            report.replace('href="/fae/manage/reports"', 'href="/fae/manage"'),
        ),
        (
            expected_url,
            report.replace("FAE 生产成果", "demo report"),
        ),
        (expected_url, report + report),
        (
            expected_url,
            report.replace('class="fae-report"', 'class="fae-report" data-fae-reports-state="integration-pending"'),
        ),
    )
    program = f"""
const {{ JSDOM }} = require({json.dumps(str(ROOT / 'webui/node_modules/jsdom'))});
const {{ reportExpression }} = require({json.dumps(str(probe))});
const expected = {json.dumps(expected_url, ensure_ascii=False)};
const fixtures = {json.dumps(fixtures, ensure_ascii=False)};
const results = fixtures.map(([url, html]) => {{
  const dom = new JSDOM(html, {{ url, runScripts: "outside-only" }});
  return dom.window.eval(reportExpression(expected));
}});
process.stdout.write(JSON.stringify(results));
"""
    result = subprocess.run(
        ["/opt/homebrew/bin/node", "-e", program],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        True, False, False, False, False, False, False, False, False,
    ]


def test_fae_viewer_dom_predicate_requires_exact_denial_only_shape():
    probe = CLOUD / "fae-reports-placeholder-probe.js"
    expected_url = "https://agent.orbbec.com.cn/fae/manage/"
    denied = (
        '<main><section class="permission-state" role="alert">'
        '<h1>无权访问</h1><p>该页面不在你的后端授权范围内。</p></section></main>'
    )
    fixtures = (
        (expected_url, denied),
        ("https://example.com/fae/manage/", denied),
        ("https://agent.orbbec.com.cn/fae/manage/sessions", denied),
        (expected_url, denied + '<section class="fae-workbench"></section>'),
        (expected_url, denied + denied),
    )
    program = f"""
const {{ JSDOM }} = require({json.dumps(str(ROOT / 'webui/node_modules/jsdom'))});
const {{ viewerDeniedExpression }} = require({json.dumps(str(probe))});
const expected = {json.dumps(expected_url, ensure_ascii=False)};
const fixtures = {json.dumps(fixtures, ensure_ascii=False)};
const results = fixtures.map(([url, html]) => {{
  const dom = new JSDOM(html, {{ url, runScripts: "outside-only" }});
  return dom.window.eval(viewerDeniedExpression(expected));
}});
process.stdout.write(JSON.stringify(results));
"""
    result = subprocess.run(
        ["/opt/homebrew/bin/node", "-e", program],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [True, False, False, False, False]


def _write_fake_chrome(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import base64
            import hashlib
            import json
            import os
            from pathlib import Path
            import socket
            import struct
            import sys
            import time

            profile = next(
                value.split("=", 1)[1]
                for value in sys.argv[1:]
                if value.startswith("--user-data-dir=")
            )
            Path(os.environ["FAKE_CHROME_PID"]).write_text(str(os.getpid()), encoding="ascii")
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(4)
            port = listener.getsockname()[1]
            Path(profile, "DevToolsActivePort").write_text(f"{port}\\n", encoding="ascii")

            def receive_headers(connection):
                value = b""
                while b"\\r\\n\\r\\n" not in value:
                    block = connection.recv(4096)
                    if not block: break
                    value += block
                return value

            connection, _ = listener.accept()
            receive_headers(connection)
            payload = json.dumps({
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/1"
            }).encode()
            connection.sendall(
                b"HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\n"
                + f"Content-Length: {len(payload)}\\r\\nConnection: close\\r\\n\\r\\n".encode()
                + payload
            )
            connection.close()

            connection, _ = listener.accept()
            headers = receive_headers(connection)
            Path(os.environ["FAKE_CDP_ACCEPTED"]).write_text("accepted", encoding="ascii")
            if os.environ["FAKE_CDP_MODE"] == "open_hang":
                while True: time.sleep(1)
            key = None
            for line in headers.decode("latin1").split("\\r\\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            if key is None: raise SystemExit(2)
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()).decode()
            connection.sendall(
                "HTTP/1.1 101 Switching Protocols\\r\\n"
                "Upgrade: websocket\\r\\nConnection: Upgrade\\r\\n"
                f"Sec-WebSocket-Accept: {accept}\\r\\n\\r\\n".encode()
            )

            def exact(size):
                result = b""
                while len(result) < size:
                    block = connection.recv(size - len(result))
                    if not block: raise EOFError
                    result += block
                return result

            def receive_frame():
                first, second = exact(2)
                length = second & 0x7f
                if length == 126: length = struct.unpack("!H", exact(2))[0]
                elif length == 127: length = struct.unpack("!Q", exact(8))[0]
                mask = exact(4) if second & 0x80 else b""
                payload = exact(length)
                if mask:
                    payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                return first & 0x0f, payload

            def send_json(value):
                payload = json.dumps(value, separators=(",", ":")).encode()
                if len(payload) < 126:
                    header = bytes((0x81, len(payload)))
                else:
                    header = bytes((0x81, 126)) + struct.pack("!H", len(payload))
                connection.sendall(header + payload)

            log = Path(os.environ["FAKE_CDP_LOG"])
            while True:
                opcode, payload = receive_frame()
                if opcode == 8: break
                if opcode != 1: continue
                message = json.loads(payload)
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(message, separators=(",", ":")) + "\\n")
                if os.environ["FAKE_CDP_MODE"] == "command_hang":
                    while True: time.sleep(1)
                result = {}
                if message["method"] == "Network.setCookie": result = {"success": True}
                elif message["method"] == "Runtime.evaluate": result = {"result": {"value": True}}
                send_json({"id": message["id"], "result": result})
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o700)


def _process_alive(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    try:
        os.kill(int(pid_file.read_text(encoding="ascii")), 0)
    except (ProcessLookupError, ValueError):
        return False
    return True


def _run_fae_report_probe(tmp_path: Path, mode: str, render_mode: str = "report"):
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    function = _bash_function(
        script, "terminate_acceptance_process", "verify_fae_workbench_cloud_contract"
    )
    fake_chrome = tmp_path / "fake-chrome"
    _write_fake_chrome(fake_chrome)
    fake_node = tmp_path / "fake-node"
    fake_node.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "printf '%s' \"$$\" > \"$FAKE_NODE_PID\"\n"
        "exec /opt/homebrew/bin/node \"$@\"\n",
        encoding="utf-8",
    )
    fake_node.chmod(0o700)
    function = function.replace(
        "local chrome=/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome",
        f"local chrome={fake_chrome}",
    )
    function = function.replace("local node=/opt/homebrew/bin/node", f"local node={fake_node}")
    function = function.replace("local probe_deadline_ms=12000", "local probe_deadline_ms=1200")
    function = function.replace("local command_timeout_ms=2000", "local command_timeout_ms=400")
    function = function.replace("local watchdog_seconds=15", "local watchdog_seconds=3")
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    report_modes = {"report", "compat-report"}
    identity = "owner" if render_mode in report_modes else "viewer"
    artifact = {
        "report": "fae-reports",
        "compat-report": "fae-compat-reports",
        "viewer-denied": "fae-viewer",
    }[render_mode]
    verifier = {
        "report": "reports_ready",
        "compat-report": "reports_compatibility",
        "viewer-denied": "viewer_denied",
    }[render_mode]
    browser_cookie = workspace / f"{identity}.browser.json"
    browser_cookie.write_text(
        f'{{"__Host-platform_session":"{identity}-session",'
        f'"__Host-platform_csrf":"{identity}-csrf"}}',
        encoding="utf-8",
    )
    browser_cookie.chmod(0o600)
    harness = tmp_path / "report-probe.sh"
    harness.write_text(
        f"""#!/bin/bash
set -euo pipefail
fail() {{ exit 91; }}
python={ROOT / 'backend/.venv/bin/python'}
repository_root={ROOT}
chrome_pid=""
node_pid=""
probe_watchdog_pid=""
{function}
verify_fae_{verifier} {browser_cookie} {workspace} || fail
""",
        encoding="utf-8",
    )
    pid_file = tmp_path / "chrome.pid"
    node_pid_file = tmp_path / "node.pid"
    log = tmp_path / "cdp.log"
    accepted = tmp_path / "accepted"
    process = subprocess.Popen(
        ["/bin/bash", str(harness)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env={
            **os.environ,
            "FAKE_CHROME_PID": str(pid_file),
            "FAKE_NODE_PID": str(node_pid_file),
            "FAKE_CDP_LOG": str(log),
            "FAKE_CDP_ACCEPTED": str(accepted),
            "FAKE_CDP_MODE": mode,
        },
    )
    started = time.monotonic()
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
    elapsed = time.monotonic() - started
    alive = _process_alive(pid_file)
    node_alive = _process_alive(node_pid_file)
    if alive:
        try:
            os.kill(int(pid_file.read_text(encoding="ascii")), signal.SIGKILL)
        except ProcessLookupError:
            pass
    if node_alive:
        try:
            os.kill(int(node_pid_file.read_text(encoding="ascii")), signal.SIGKILL)
        except ProcessLookupError:
            pass
    messages = tuple(
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
    ) if log.exists() else ()
    return {
        "returncode": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "elapsed": elapsed,
        "alive": alive,
        "node_alive": node_alive,
        "messages": messages,
        "accepted": accepted.exists(),
        "browser_cookie_exists": browser_cookie.exists(),
        "profile_exists": (workspace / f"{artifact}-chrome-profile").exists(),
        "target_exists": (workspace / f"{artifact}-chrome-target.json").exists(),
    }


def test_fae_report_probe_injects_exact_cookies_and_navigates_exact_url(tmp_path):
    result = _run_fae_report_probe(tmp_path, "happy")

    assert result["returncode"] == 0, result["stderr"]
    assert result["stdout"] == "FAE_REPORTS_READY_OK\n"
    assert result["timed_out"] is False
    assert result["alive"] is False
    assert result["node_alive"] is False
    assert result["browser_cookie_exists"] is False
    assert result["profile_exists"] is False
    assert result["target_exists"] is False
    methods = [message["method"] for message in result["messages"]]
    assert methods == [
        "Network.enable",
        "Network.setCookie",
        "Network.setCookie",
        "Page.enable",
        "Runtime.enable",
        "Page.navigate",
        "Runtime.evaluate",
    ]
    cookie_messages = [
        message for message in result["messages"] if message["method"] == "Network.setCookie"
    ]
    assert [message["params"] for message in cookie_messages] == [
        {
            "name": "__Host-platform_session",
            "value": "owner-session",
            "url": "https://agent.orbbec.com.cn",
            "secure": True,
            "httpOnly": True,
            "sameSite": "Lax",
        },
        {
            "name": "__Host-platform_csrf",
            "value": "owner-csrf",
            "url": "https://agent.orbbec.com.cn",
            "secure": True,
            "httpOnly": False,
            "sameSite": "Lax",
        },
    ]
    navigate = next(message for message in result["messages"] if message["method"] == "Page.navigate")
    assert navigate["params"] == {"url": "https://agent.orbbec.com.cn/fae/manage/reports"}


def test_fae_viewer_probe_injects_viewer_cookies_and_navigates_exact_url(tmp_path):
    result = _run_fae_report_probe(tmp_path, "happy", "viewer-denied")

    assert result["returncode"] == 0, result["stderr"]
    assert result["stdout"] == "FAE_VIEWER_DENIED_OK\n"
    cookie_messages = [
        message for message in result["messages"] if message["method"] == "Network.setCookie"
    ]
    assert [message["params"]["value"] for message in cookie_messages] == [
        "viewer-session",
        "viewer-csrf",
    ]
    navigate = next(message for message in result["messages"] if message["method"] == "Page.navigate")
    assert navigate["params"] == {"url": "https://agent.orbbec.com.cn/fae/manage/"}
    assert result["alive"] is False
    assert result["node_alive"] is False
    assert result["browser_cookie_exists"] is False
    assert result["profile_exists"] is False
    assert result["target_exists"] is False


def test_fae_compatibility_probe_redirects_to_the_canonical_report(tmp_path):
    result = _run_fae_report_probe(tmp_path, "happy", "compat-report")

    assert result["returncode"] == 0, result["stderr"]
    assert result["stdout"] == "FAE_REPORTS_COMPATIBILITY_OK\n"
    navigate = next(
        message
        for message in result["messages"]
        if message["method"] == "Page.navigate"
    )
    assert navigate["params"] == {
        "url": "https://agent.orbbec.com.cn/admin/fae/reports"
    }
    evaluation = next(
        message
        for message in result["messages"]
        if message["method"] == "Runtime.evaluate"
    )
    assert "https://agent.orbbec.com.cn/fae/manage/reports" in evaluation[
        "params"
    ]["expression"]


@pytest.mark.parametrize("mode", ("open_hang", "command_hang"))
def test_fae_report_probe_times_out_and_removes_processes_and_artifacts(tmp_path, mode):
    result = _run_fae_report_probe(tmp_path, mode)

    assert result["timed_out"] is False
    assert result["returncode"] == 91
    assert result["elapsed"] < 5
    assert result["accepted"] is True
    assert result["alive"] is False
    assert result["node_alive"] is False
    assert result["browser_cookie_exists"] is False
    assert result["profile_exists"] is False
    assert result["target_exists"] is False


def test_fae_probe_failure_runs_acceptance_cleanup_and_restores_feature_and_lock(tmp_path):
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    acceptance = _bash_function(script, "accept_v2_real", "enable_with_rollback")
    event_log = tmp_path / "events.log"
    evidence = tmp_path / "evidence"
    harness = tmp_path / "acceptance-cleanup.sh"
    harness.write_text(
        f"""#!/bin/bash
set -euo pipefail
fail() {{ return 1; }}
require_private_file() {{ :; }}
validate_v2_quality_review() {{ printf 'reviewer'; }}
cookie_config() {{ : > "$2"; printf '{{}}' > "$3"; }}
cleanup_fae_report_processes() {{ printf 'process-cleanup\\n' >> {event_log}; }}
remote_feature() {{ printf 'feature:%s\\n' "$1" >> {event_log}; }}
release_action_lock() {{ printf 'lock-release\\n' >> {event_log}; }}
verify_access_history_authorization_contract() {{ :; }}
verify_access_history_browser_contract() {{ :; }}
verify_fae_workbench_cloud_contract() {{
  printf '%s' "$temporary" > {tmp_path / 'temporary-path'}
  return 1
}}
member_cookie_file={tmp_path / 'member.cookie'}
owner_cookie_file={tmp_path / 'owner.cookie'}
viewer_cookie_file={tmp_path / 'viewer.cookie'}
hr_prompt_file={tmp_path / 'prompt'}
evidence_file={evidence}
python={ROOT / 'backend/.venv/bin/python'}
{acceptance}
accept_v2_real
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["/bin/bash", str(harness)], text=True, capture_output=True, check=False
    )

    assert result.returncode != 0
    assert event_log.read_text(encoding="utf-8").splitlines() == [
        "process-cleanup",
        "feature:0",
        "lock-release",
    ]
    temporary = Path((tmp_path / "temporary-path").read_text(encoding="utf-8"))
    assert temporary.exists() is False
