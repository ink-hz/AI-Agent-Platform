from pathlib import Path


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"
NGINX = CLOUD / "agent-domain.basic-auth.nginx.conf"
FORMAL_NGINX = CLOUD / "agent-domain.nginx.conf"
INSTALLER = CLOUD / "install-agent-domain.sh"
PUBLISHER = CLOUD / "publish-agent-domain.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "cloud-platform.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_nginx_entry_authenticates_every_https_path_and_keeps_loopback_upstream():
    value = _text(NGINX)

    assert "log_format agent_platform_redacted" in value
    assert value.index("log_format agent_platform_redacted") < value.index(
        "access_log /var/log/nginx/ai-fae-agent.access.log agent_platform_redacted;"
    )
    assert "server_name __AGENT_DOMAIN__;" in value
    assert value.index("location ^~ /.well-known/acme-challenge/") < value.index(
        "return 308 https://__AGENT_DOMAIN__$request_uri;"
    )
    assert "# HTTPS_ENTRY_BEGIN" in value
    assert "listen 443 ssl;" in value
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in value
    assert "ssl_session_tickets off;" in value
    assert 'auth_basic "Orbbec Agent Platform";' in value
    assert "auth_basic_user_file __HTPASSWD_PATH__;" in value
    assert "auth_delay 1s;" in value
    assert "location / {" in value
    assert "proxy_pass http://127.0.0.1:8080;" in value
    assert "proxy_set_header X-Real-IP $remote_addr;" in value
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in value
    assert 'proxy_set_header Forwarded "";' in value
    assert "$proxy_add_x_forwarded_for" not in value
    assert 'proxy_set_header Authorization "";' in value
    assert "proxy_buffering off;" in value
    assert "proxy_cache off;" in value
    assert "proxy_read_timeout 300s;" in value
    assert 'add_header Strict-Transport-Security "max-age=31536000" always;' in value
    assert 'add_header X-Content-Type-Options "nosniff" always;' in value
    assert 'add_header X-Frame-Options "DENY" always;' in value
    assert 'add_header Referrer-Policy "no-referrer" always;' in value
    assert "limit_except GET HEAD OPTIONS" in value


def test_remote_installer_is_fail_closed_private_and_fae_safe():
    value = _text(INSTALLER)

    for required in (
        "set -euo pipefail",
        "umask 077",
        "read -r agent_password",
        "openssl passwd -6 -stdin",
        "chown root:www-data",
        "chmod 640",
        "curl-wrong.conf",
        "/root/nginx-backups/agent-platform-",
        "rollback-agent-domain-",
        "certbot certonly",
        "--webroot",
        "nginx -t",
        "systemctl reload nginx",
        "PLATFORM_CLOUD_AUTH_MODE=basic-auth",
        "docker compose",
        "--no-deps",
        "platform-api",
        "platform-loopback",
        "ai-fae-backend",
        "StartedAt",
        "127.0.0.1:8080",
        "AGENT_DOMAIN_INSTALL_OK domain=",
    ):
        assert required in value
    for forbidden in (
        "set -x",
        "security ",
        "sudo",
        "docker restart ai-fae-backend",
        "docker stop ai-fae-backend",
        "docker rm ai-fae-backend",
        "docker compose down",
        "systemctl restart nginx",
    ):
        assert forbidden not in value
    assert "agent_password" not in " ".join(
        line for line in value.splitlines() if line.lstrip().startswith(("echo ", "logger "))
    )
    assert "/usr/bin/printf '%s\\n' \"$agent_password\"" not in value


def test_local_publisher_uses_private_files_noninteractive_ssh_and_stable_output():
    value = _text(PUBLISHER)

    for required in (
        "mode_600_file",
        "CLOUD_ADMIN_HOST",
        "CLOUD_ADMIN_KEY",
        "root@47.106.112.69",
        "AGENT_PUBLIC_IP",
        "/usr/bin/dig",
        "AGENT_BASIC_AUTH_PASSWORD_FILE",
        "BatchMode=yes",
        "IdentitiesOnly=yes",
        "StrictHostKeyChecking=yes",
        "--noproxy '*'",
        "--config",
        '--resolve "$AGENT_DOMAIN:80:$AGENT_PUBLIC_IP"',
        '--resolve "$AGENT_DOMAIN:443:$AGENT_PUBLIC_IP"',
        "AGENT_DOMAIN_PUBLISH_OK domain=",
    ):
        assert required in value
    for forbidden in ("set -x", "security ", "sudo", "-u \"$", "--user \"$"):
        assert forbidden not in value
    assert "/usr/bin/printf" not in " ".join(
        line for line in value.splitlines() if "agent_password" in line
    )
    assert '"$AGENT_BASIC_AUTH_PASSWORD_FILE"' in value
    assert '< "$AGENT_BASIC_AUTH_PASSWORD_FILE"' in value


def test_runbook_covers_private_credential_rotation_acceptance_and_rollback():
    value = _text(RUNBOOK).lower()

    for required in (
        "temporary administrator public entry",
        "agentadmin",
        "mode 0600",
        "never uses keychain",
        "credential rotation",
        "agent.orbbec.com.cn",
        "basic auth",
        "rollback-agent-domain",
        "one-year backfill",
        "five-minute synchronization",
    ):
        assert required in value


def test_formal_nginx_routes_voc_without_exposing_health_or_credentials():
    value = _text(FORMAL_NGINX)
    selectors = (
        "location = /voc {",
        "location = /voc/health {",
        "location ^~ /voc/assets/ {",
        "location ^~ /voc/ {",
    )

    positions = [value.index(selector) for selector in selectors]
    assert positions == sorted(positions)
    assert value.index("location ^~ /voc/ {") < value.index(
        "location / {", positions[-1]
    )
    assert "return 308 /voc/$is_args$args;" in value
    health = value[positions[1] : positions[2]]
    assert "return 404;" in health
    assets = value[positions[2] : positions[3]]
    assert "proxy_pass http://172.29.0.3:18130;" in assets
    assert 'add_header Cache-Control "public, max-age=31536000, immutable" always;' in assets
    application = value[positions[3] : value.index("location / {", positions[3])]
    assert "proxy_pass http://172.29.0.3:18130;" in application
    assert "proxy_buffering off;" in application
    assert "proxy_cache off;" in application
    assert 'add_header Cache-Control "private, no-store" always;' in application

    for block in (assets, application):
        for directive in (
            "client_max_body_size 1m;",
            "proxy_set_header X-Forwarded-For $remote_addr;",
            'proxy_set_header Forwarded "";',
            'proxy_set_header Authorization "";',
            'add_header Strict-Transport-Security "max-age=31536000" always;',
            'add_header X-Content-Type-Options "nosniff" always;',
            'add_header X-Frame-Options "DENY" always;',
            'add_header Referrer-Policy "no-referrer" always;',
            "add_header Content-Security-Policy",
            'add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;',
        ):
            assert directive in block
        assert "$proxy_add_x_forwarded_for" not in block
