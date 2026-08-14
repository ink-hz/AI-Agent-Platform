from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"
SNIPPET = CLOUD / "demo-preview.nginx.conf"
INSTALLER = CLOUD / "install-demo-preview.sh"
ROLLBACK = CLOUD / "rollback-demo-preview.sh"
AGENT_TEMPLATE = CLOUD / "agent-domain.nginx.conf"

INCLUDE = "include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;"
PREVIEW_PREFIX = "/_preview/dingtalk-r1/"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _location_body(value: str, marker: str) -> str:
    start = value.index(marker)
    brace = value.index("{", start)
    depth = 0
    for index in range(brace, len(value)):
        if value[index] == "{":
            depth += 1
        elif value[index] == "}":
            depth -= 1
            if depth == 0:
                return value[brace + 1 : index]
    raise AssertionError(f"unterminated block: {marker}")


def test_preview_snippet_has_exact_prefix_and_preserves_the_full_upstream_uri() -> None:
    value = _text(SNIPPET)

    assert "location = /_preview/dingtalk-r1 {" in value
    assert "return 308 /_preview/dingtalk-r1/;" in value
    assert "location ^~ /_preview/dingtalk-r1/ {" in value
    assert "location /_preview" not in value
    assert "location ^~ /_preview/dingtalk-r1 {" not in value
    body = _location_body(value, "location ^~ /_preview/dingtalk-r1/")
    assert "proxy_pass http://127.0.0.1:8081;" in body
    assert "proxy_pass http://127.0.0.1:8081/;" not in body
    assert "rewrite " not in body
    assert "proxy_redirect" not in body


def test_exact_redirect_is_non_cacheable_and_keeps_browser_hardening() -> None:
    body = _location_body(_text(SNIPPET), "location = /_preview/dingtalk-r1")

    assert "auth_basic off;" in body
    assert "return 308 /_preview/dingtalk-r1/;" in body
    assert 'add_header Cache-Control "no-store" always;' in body
    assert 'add_header X-Content-Type-Options "nosniff" always;' in body
    assert 'add_header X-Frame-Options "DENY" always;' in body
    assert 'add_header Referrer-Policy "no-referrer" always;' in body


def test_preview_location_supports_qr_callback_and_all_login_posts() -> None:
    value = _text(SNIPPET)
    body = _location_body(value, "location ^~ /_preview/dingtalk-r1/")

    assert "auth_basic off;" in body
    assert "limit_except" not in body
    assert "deny all" not in body
    for endpoint in (
        "/_preview/dingtalk-r1/api/v1/login/start",
        "/_preview/dingtalk-r1/api/v1/login/callback",
        "/_preview/dingtalk-r1/api/v1/login/in-client",
        "/_preview/dingtalk-r1/api/v1/logout",
    ):
        assert endpoint.startswith(PREVIEW_PREFIX)


def test_preview_location_has_bounded_body_redacted_log_and_timeout_margin() -> None:
    body = _location_body(
        _text(SNIPPET), "location ^~ /_preview/dingtalk-r1/"
    )

    assert "client_max_body_size 1m;" in body
    assert (
        "access_log /var/log/nginx/ai-fae-agent.access.log "
        "agent_platform_redacted;"
    ) in body
    assert "proxy_read_timeout 330s;" in body
    assert "proxy_send_timeout 330s;" in body
    assert "proxy_connect_timeout 10s;" in body
    assert "$request_uri" not in body
    assert "$args" not in body


def test_preview_location_replaces_untrusted_forwarding_and_authorization_headers() -> None:
    body = _location_body(
        _text(SNIPPET), "location ^~ /_preview/dingtalk-r1/"
    )

    for required in (
        "proxy_set_header Host $host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $remote_addr;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
        'proxy_set_header Forwarded "";',
        'proxy_set_header Authorization "";',
        'proxy_set_header Connection "";',
    ):
        assert required in body
    assert "$proxy_add_x_forwarded_for" not in body
    assert "proxy_set_header Cookie" not in body


def test_preview_location_has_no_store_and_browser_security_headers() -> None:
    body = _location_body(
        _text(SNIPPET), "location ^~ /_preview/dingtalk-r1/"
    )

    for required in (
        'add_header Cache-Control "no-store" always;',
        'add_header X-Content-Type-Options "nosniff" always;',
        'add_header X-Frame-Options "DENY" always;',
        'add_header Referrer-Policy "no-referrer" always;',
    ):
        assert required in body


def test_agent_template_adds_only_one_fixed_include_before_https_platform_root() -> None:
    value = _text(AGENT_TEMPLATE)

    assert value.count(INCLUDE) == 1
    https = value[value.index("# HTTPS_ENTRY_BEGIN") :]
    include_index = https.index(INCLUDE)
    root_index = https.index("location / {")
    assert include_index < root_index
    assert "proxy_pass http://127.0.0.1:8080;" in _location_body(
        https, "location / {"
    )
    http = value[: value.index("# HTTPS_ENTRY_BEGIN")]
    assert INCLUDE not in http
    assert value.count("listen 80;") == 1
    assert value.count("listen 443 ssl;") == 1


def test_installer_is_hash_locked_and_selects_the_https_platform_root_structurally() -> None:
    value = _text(INSTALLER)

    for required in (
        "set -euo pipefail",
        "umask 077",
        "EXPECTED_LIVE_SHA256",
        "sha256sum",
        "listen 443 ssl",
        "server_name agent.orbbec.com.cn",
        "proxy_pass http://127.0.0.1:8080",
        INCLUDE,
        "candidate",
        "nginx -t",
        "systemctl reload nginx",
    ):
        assert required in value
    assert "382d733e1a581569f4ceedd03ce24ab9113f61a595015bc0449e1319026c1e97" not in value
    assert "systemctl restart nginx" not in value
    assert "sites-enabled/agent-domain.conf" in value
    assert "sites-available/agent-domain.conf" not in value


def test_installer_captures_and_rechecks_root_fae_admin_listener_and_container_invariants() -> None:
    value = _text(INSTALLER)

    for required in (
        "sites-enabled",
        "listeners",
        "docker inspect",
        "RestartCount",
        "StartedAt",
        "Config.Image",
        "agent.orbbec.com.cn/",
        "agent.orbbec.com.cn/admin",
        "fae.orbbec.com.cn/",
        "http://47.106.112.69/",
        "restore_on_failure",
        "trap restore_on_failure EXIT",
        "cmp",
    ):
        assert required in value
    for forbidden in (
        "docker restart",
        "docker stop",
        "docker rm",
        "docker compose down",
        "systemctl restart nginx",
        "set -x",
        "security ",
        "keychain",
    ):
        assert forbidden not in value.lower()


def test_installer_patcher_preserves_live_admin_and_http_blocks_byte_for_byte(
    tmp_path: Path,
) -> None:
    script = _text(INSTALLER)
    patcher = script.split("<<'PY'\n", 2)[2].split("\nPY\n", 1)[0]
    live = tmp_path / "live.conf"
    candidate = tmp_path / "candidate.conf"
    value = """\
server {
    listen 80;
    server_name agent.orbbec.com.cn;
    location / { return 308 https://agent.orbbec.com.cn$request_uri; }
}
server {
    listen 443 ssl;
    server_name agent.orbbec.com.cn;
    auth_basic "Orbbec Agent Platform";
    limit_req_zone $binary_remote_addr zone=admin:10m rate=1r/s;
    location ^~ /admin/ {
        proxy_pass http://127.0.0.1:8090;
    }
    location / {
        limit_except GET HEAD OPTIONS { deny all; }
        proxy_pass http://127.0.0.1:8080;
    }
}
"""
    live.write_text(value, encoding="utf-8")

    subprocess.run(
        [sys.executable, "-c", patcher, str(live), str(candidate)],
        check=True,
        text=True,
        capture_output=True,
    )
    updated = candidate.read_text(encoding="utf-8")
    inserted = f"    {INCLUDE}\n\n"
    assert updated.replace(inserted, "", 1) == value
    assert updated.index(INCLUDE) > updated.index("location ^~ /admin/")
    assert updated.index(INCLUDE) < updated.index("proxy_pass http://127.0.0.1:8080;")


def test_installer_patcher_rejects_ambiguous_https_platform_roots(tmp_path: Path) -> None:
    script = _text(INSTALLER)
    patcher = script.split("<<'PY'\n", 2)[2].split("\nPY\n", 1)[0]
    live = tmp_path / "live.conf"
    candidate = tmp_path / "candidate.conf"
    live.write_text(
        """\
server {
    listen 443 ssl;
    server_name agent.orbbec.com.cn;
    location / { proxy_pass http://127.0.0.1:8080; }
    location / { proxy_pass http://127.0.0.1:8080; }
}
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-c", patcher, str(live), str(candidate)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert not candidate.exists()


def test_rollback_is_idempotent_restores_only_preview_nginx_and_stops_only_demo_services() -> None:
    value = _text(ROLLBACK)

    for required in (
        "set -euo pipefail",
        "orbbec-agent-demo-preview.conf",
        "agent-domain.conf",
        "platform-api-demo-preview",
        "platform-loopback-demo-preview",
        "compose.demo-preview.yaml",
        "nginx -t",
        "systemctl reload nginx",
        "RestartCount",
        "StartedAt",
        "AGENT_DEMO_PREVIEW_ROLLBACK_OK",
    ):
        assert required in value
    assert re.search(r"docker compose[^\n]*", value)
    for forbidden in (
        "docker compose down",
        "platform-api ",
        "platform-loopback ",
        "ai-fae-backend ",
        "docker restart",
        "systemctl restart nginx",
        "rm -rf",
    ):
        assert forbidden not in value
