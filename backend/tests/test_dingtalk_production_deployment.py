from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"


def test_production_compose_runs_identity_and_least_privilege_workers():
    value = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    services = value["services"]

    assert set(services) == {
        "platform-postgres",
        "platform-api",
        "platform-loopback",
        "platform-directory",
        "platform-dingtalk-stream",
    }
    api = services["platform-api"]
    startup = api["command"][2]
    assert "IFS= read" not in startup
    for secret_name in ("dingtalk-app-key", "dingtalk-agent-id", "dingtalk-corp-id"):
        assert f"/bin/cat /run/secrets/{secret_name}" in startup
    for variable in (
        "PLATFORM_DINGTALK_APP_KEY",
        "PLATFORM_DINGTALK_AGENT_ID",
        "PLATFORM_DINGTALK_CORP_ID",
    ):
        assert f'test -n "$${variable}"' in startup
    assert api["environment"]["PLATFORM_IDENTITY_MODE"] == "production"
    assert api["environment"]["PLATFORM_PUBLIC_BASE_URL"] == "https://agent.orbbec.com.cn"
    assert api["environment"]["PLATFORM_ROUTE_PREFIX"] == "/"
    assert api["environment"]["PLATFORM_COOKIE_NAME"] == "__Host-platform_session"
    assert api["environment"]["PLATFORM_TRUSTED_PROXY_CIDRS"] == "172.30.0.3/32"
    assert set(api["networks"]) == {"platform-internal", "platform-edge"}
    assert api["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.4"
    assert services["platform-postgres"]["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.2"

    directory = services["platform-directory"]
    stream = services["platform-dingtalk-stream"]
    assert directory["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.5"
    assert directory["networks"]["platform-edge"]["ipv4_address"] == "172.31.0.5"
    assert stream["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.6"
    assert stream["networks"]["platform-edge"]["ipv4_address"] == "172.31.0.6"
    assert api["networks"]["platform-edge"]["ipv4_address"] == "172.31.0.4"
    assert services["platform-loopback"]["networks"]["platform-edge"]["ipv4_address"] == "172.31.0.3"
    assert directory["command"] == [
        "python", "-m", "app.control_plane.worker_runtime", "directory"
    ]
    assert stream["command"] == [
        "python", "-m", "app.control_plane.worker_runtime", "stream"
    ]
    assert directory["restart"] == stream["restart"] == "unless-stopped"
    assert directory["read_only"] is stream["read_only"] is True
    assert directory["cap_drop"] == stream["cap_drop"] == ["ALL"]
    assert set(directory["networks"]) == set(stream["networks"]) == {
        "platform-internal", "platform-edge"
    }
    assert directory["volumes"] != stream["volumes"]
    assert "PLATFORM_CONTROL_STREAM_DATABASE_URL_FILE" not in directory["environment"]
    assert "PLATFORM_CONTROL_DIRECTORY_DATABASE_URL_FILE" not in stream["environment"]

    serialized = (CLOUD / "compose.yaml").read_text(encoding="utf-8")
    for forbidden in ("clientSecret:", "dingtalk-app-secret:", "corp-id:"):
        assert forbidden not in serialized
    assert services["platform-loopback"]["ports"] == ["127.0.0.1:8080:8080"]
    assert services["platform-loopback"]["environment"] == {
        "PLATFORM_LOOPBACK_TARGET_BASE_URL": "http://172.30.0.4:8080",
        "PLATFORM_LOOPBACK_TRUSTED_PROXY_CIDRS": "127.0.0.1/32,172.31.0.1/32",
        "PLATFORM_LOOPBACK_SOURCE_ADDRESS": "172.30.0.3",
    }
    for name, service in services.items():
        if name != "platform-loopback":
            assert "ports" not in service


def test_runtime_image_contains_control_migrations():
    dockerfile = (CLOUD / "Dockerfile").read_text(encoding="utf-8")
    assert "backend/control_migrations" in dockerfile


def test_formal_nginx_uses_backend_auth_and_preserves_basic_auth_rollback():
    formal = (CLOUD / "dingtalk_nginx_transaction.py").read_text(encoding="utf-8")
    rollback = (CLOUD / "agent-domain.basic-auth.nginx.conf").read_text(
        encoding="utf-8"
    )

    assert "proxy_pass http://127.0.0.1:8080;" in formal
    assert "proxy_read_timeout 360s;" in formal
    assert "proxy_send_timeout 360s;" in formal
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in formal
    assert 'proxy_set_header Forwarded "";' in formal
    assert 'proxy_set_header Authorization "";' in formal
    assert 'Content-Security-Policy "default-src \'none\';' in formal
    assert "orbbec-agent-demo-preview.conf" in formal

    assert 'auth_basic "Orbbec Agent Platform";' in rollback
    assert "limit_except GET HEAD OPTIONS" in rollback
    assert "proxy_pass http://127.0.0.1:8080;" in rollback


def test_cutover_and_rollback_are_atomic_and_fae_safe():
    publish = (CLOUD / "publish-dingtalk-production.sh").read_text(encoding="utf-8")
    rollback = (CLOUD / "rollback-dingtalk-production.sh").read_text(encoding="utf-8")

    for script in (publish, rollback):
        assert "set -euo pipefail" in script
        assert "nginx -t" in script
        assert "systemctl reload nginx" in script
        assert "ai-fae-backend" in script
        assert "StartedAt" in script
        for forbidden in (
            "docker restart ai-fae-backend",
            "docker stop ai-fae-backend",
            "docker compose down",
            "systemctl restart nginx",
        ):
            assert forbidden not in script
    assert "PLATFORM_IDENTITY_MODE=production" in publish
    assert "agent-domain.basic-auth.nginx.conf" in rollback
    assert "PREVIOUS_RELEASE" in publish
    assert "PREVIOUS_PLATFORM_ENV" in publish
    assert "PREVIOUS_RELEASE" in rollback
    assert 'stop "${services_to_stop[@]}"' in rollback
    assert 'up -d --force-recreate "${services_to_start[@]}"' in rollback
    assert '/bin/ln -sfn "$PREVIOUS_RELEASE" "$platform_root/current"' in rollback


def test_cutover_supports_a_fail_closed_first_owner_login_stage():
    publish = (CLOUD / "publish-dingtalk-production.sh").read_text(
        encoding="utf-8"
    )

    assert "--allow-unbound-owner" in publish
    assert 'expected_readiness="0:1:1"' in publish
    assert 'OWNER_BOOTSTRAP=%q' in publish
    assert "DINGTALK_PRODUCTION_OWNER_LOGIN_REQUIRED" in publish
    assert "dingtalk_nginx_transaction.py" in publish
    assert '[[ "$code" == "302" ]]' in publish
    assert "/usr/bin/tr -d '\\r'" in publish
    assert "/usr/bin/grep -Fxiq 'location: /login'" in publish
    assert "https://agent.orbbec.com.cn/login" in publish


def test_identity_secret_bootstrap_is_noninteractive_and_service_scoped():
    script = (CLOUD / "bootstrap-dingtalk-production-secrets.sh").read_text(
        encoding="utf-8"
    )

    for required in (
        "identity-encryption-keyring",
        "identity-hmac-keyring",
        "rate-limit-hmac-keyring",
        "control-database-url",
        "control-audit-database-url",
        "control-directory-worker-database-url",
        "control-stream-ingest-database-url",
        "orbbec-agent-platform-api-secrets",
        "orbbec-agent-platform-directory-secrets",
        "orbbec-agent-platform-stream-secrets",
        "chown 10001:10001",
        "chmod 600",
    ):
        assert required in script
    for forbidden in ("security ", "read -s", "set -x", "dingtalk-app-secret="):
        assert forbidden not in script
    assert script.count("openssl rand 32") >= 3
    assert "cmp -s" not in script


def test_initial_owner_binding_uses_exact_private_provider_id_and_two_phase_receipt():
    script = (CLOUD / "bind-production-owner.sh").read_text(encoding="utf-8")

    for required in (
        "dingtalk-owner-userid",
        "show-directory-generation",
        "bind-owner",
        "--provider-id-file",
        "--receipt-file",
        "--receipt-key-file",
        "--confirm",
        "--approver",
        "platform_control_owner",
        "owner_binding=1",
        "docker run --rm",
        "{{.Config.Image}}",
        "--network orbbec-agent-platform-internal",
    ):
        assert required in script
    assert script.count("--approver") >= 2
    for forbidden in (
        "display_name", "苍渊", "grep.*name", "security ", "set -x",
        "run --rm --no-deps",
    ):
        assert forbidden not in script


def test_production_acceptance_covers_platform_identity_workers_and_fae_invariants():
    script = (CLOUD / "accept-dingtalk-production.sh").read_text(
        encoding="utf-8"
    )

    for required in (
        "https://agent.orbbec.com.cn/",
        "https://agent.orbbec.com.cn/login",
        "/api/v1/account",
        "platform-identity-mode",
        "platform-directory",
        "platform-dingtalk-stream",
        "dingtalk-directory-event",
        "platform_owner",
        "active_generation_id",
        "127.0.0.1:8080",
        "ai-fae-backend",
        "FAE_STARTED_AT",
        "nginx -t",
        "DINGTALK_PRODUCTION_ACCEPTANCE_OK release=",
        "location: /login",
    ):
        assert required in script
    for forbidden in (
        "docker restart ai-fae-backend",
        "docker stop ai-fae-backend",
        "docker compose down",
        "systemctl restart nginx",
        "set -x",
        'auth_basic "AI ADMIN Demo";',
        "/admin/?view=services",
    ):
        assert forbidden not in script
