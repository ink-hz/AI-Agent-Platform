from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"


def _bash_array(script: str, name: str) -> tuple[str, ...]:
    body = script.split(f"{name}=(\n", 1)[1].split("\n)", 1)[0]
    return tuple(line.strip() for line in body.splitlines() if line.strip())


def test_compose_is_isolated_loopback_only_and_hardened():
    value = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    services = value["services"]

    assert set(services) == {
        "platform-api",
        "platform-loopback",
        "platform-postgres",
        "platform-directory",
        "platform-dingtalk-stream",
    }
    assert "ports" not in services["platform-postgres"]
    assert "ports" not in services["platform-api"]
    assert services["platform-loopback"]["ports"] == ["127.0.0.1:8080:8080"]
    assert services["platform-loopback"]["command"] == [
        "uvicorn", "app.cloud_replica.loopback_proxy:create_app", "--factory",
        "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers",
    ]
    assert services["platform-loopback"]["volumes"] == []
    assert services["platform-loopback"]["read_only"] is True
    assert services["platform-loopback"]["cap_drop"] == ["ALL"]
    assert set(services["platform-loopback"]["networks"]) == {
        "platform-edge", "platform-internal"
    }
    assert services["platform-postgres"]["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.2"
    assert services["platform-loopback"]["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.3"
    assert services["platform-api"]["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.4"
    assert services["platform-api"]["read_only"] is True
    assert services["platform-api"]["cap_drop"] == ["ALL"]
    assert services["platform-api"]["security_opt"] == ["no-new-privileges:true"]
    assert services["platform-api"]["user"] not in {"0", "root", "0:0"}
    assert services["platform-api"]["environment"]["PLATFORM_DEPLOYMENT_MODE"] == "cloud-replica"
    assert services["platform-api"]["environment"]["PLATFORM_CLOUD_AUTH_MODE"] == "dingtalk"
    assert services["platform-api"]["environment"]["PLATFORM_HOST"] == "127.0.0.1"
    assert services["platform-api"]["environment"]["PLATFORM_REVIEW_ENABLED"] == "0"
    assert services["platform-api"]["environment"]["PLATFORM_ATTACHMENT_ENABLED"] == "0"
    assert services["platform-api"]["environment"]["PLATFORM_TRUSTED_PROXY_CIDRS"] == "172.30.0.3/32"
    assert services["platform-loopback"]["environment"]["PLATFORM_LOOPBACK_TRUSTED_PROXY_CIDRS"] == "127.0.0.1/32,172.31.0.1/32"
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
    assert '"--no-proxy-headers"' in dockerfile
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
        "mode=dingtalk",
        'up -d --force-recreate platform-postgres',
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
    assert 'PLATFORM_CLOUD_AUTH_MODE=dingtalk' in script
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
        ("agent_platform_control",) * 6
        + ("agent_platform_control_preview",) * 6
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
        "grant platform_control_owner_preview "
        "to platform_control_migrator_preview"
    ) in script
    assert "revoke platform_control_owner from platform_control_migrator" in script
    assert (
        "revoke platform_control_owner_preview "
        "from platform_control_migrator_preview"
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
        "login password \"$",
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
    runner = (
        ROOT / "backend" / "app" / "control_plane" / "migrate.py"
    ).read_text(encoding="utf-8")

    assert '"platform_control_owner"' in runner
    assert '"platform_control_owner_preview"' in runner
    assert "owner_role" in runner
    assert "set local role" in runner.lower()
    assert "PLATFORM_CONTROL_OWNER_ROLE" in runner


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
