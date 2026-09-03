import threading
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"
NGINX = CLOUD / "agent-domain.basic-auth.nginx.conf"
FORMAL_NGINX = CLOUD / "agent-domain.nginx.conf"
INSTALLER = CLOUD / "install-agent-domain.sh"
PUBLISHER = CLOUD / "publish-agent-domain.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "cloud-platform.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _location_block(value: str, selector: str) -> str:
    start = value.index(selector)
    next_location = value.find("\n    location ", start + len(selector))
    return value[start:] if next_location < 0 else value[start:next_location]


def _workspace_proxy_for_path(value: str, route: str) -> str:
    path = route.split("?", 1)[0]
    if path.startswith("/fae/manage/"):
        selector = "location ^~ /fae/manage/ {"
    elif path.startswith("/office/"):
        selector = "location ^~ /office/ {"
    elif path.startswith("/fae/"):
        selector = "location ^~ /fae/ {"
    elif path.startswith("/voc/"):
        selector = "location ^~ /voc/ {"
    elif path.startswith("/admin/"):
        selector = "location ^~ /admin/ {"
    else:
        selector = "location / {"
        value = value[value.rindex(selector) :]
    selected = _location_block(value, selector)
    owners = [
        line.split()[1].removesuffix(";")
        for line in selected.splitlines()
        if line.strip().startswith("proxy_pass ")
    ]
    assert len(owners) == 1, route
    return owners[0]


def _http_response(port: int, route: str) -> tuple[int, str, bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", route)
        response = connection.getresponse()
        return response.status, response.getheader("Location", ""), response.read()
    finally:
        connection.close()


def _start_http_server(handler: type[BaseHTTPRequestHandler]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    return server, thread


def _stop_http_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    assert not thread.is_alive()


def _upstream_handler(owner: str) -> type[BaseHTTPRequestHandler]:
    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"{owner}:ready:{self.path}".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    return UpstreamHandler


def _proxy_handler(
    nginx_server: str, upstream_ports: dict[str, int]
) -> type[BaseHTTPRequestHandler]:
    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            owner = _workspace_proxy_for_path(nginx_server, self.path)
            upstream = HTTPConnection(
                "127.0.0.1", upstream_ports[owner], timeout=0.5
            )
            try:
                upstream.request("GET", self.path)
                response = upstream.getresponse()
                body = response.read()
            except OSError:
                body = f"{owner}:unavailable".encode()
                self.send_response(502)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(body)
                return
            finally:
                upstream.close()
            self.send_response(response.status)
            location = response.getheader("Location")
            if location is not None:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    return ProxyHandler


def _assert_no_workspace_failure_fallback(nginx_server: str) -> None:
    assert "error_page" not in nginx_server
    assert "recursive_error_pages" not in nginx_server
    assert "proxy_intercept_errors on" not in nginx_server


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


def test_formal_nginx_assigns_hardened_non_overlapping_fae_route_owners():
    value = _text(FORMAL_NGINX)
    selectors = (
        "location = /fae {",
        "location = /fae/manage {",
        "location ^~ /fae/manage/ {",
        "location = /fae/health {",
        "location = /fae/api/chat {",
        "location = /fae/api/attachments {",
        "location ^~ /fae/api/ {",
        "location ^~ /fae/assets/ {",
        "location ^~ /fae/ {",
    )
    positions = [value.index(selector) for selector in selectors]

    assert positions == sorted(positions)
    assert value.index("location ^~ /fae/manage/ {") < value.index(
        "location ^~ /fae/ {"
    )
    assert value.index("location ^~ /fae/ {") < value.index(
        "location / {", positions[-1]
    )
    root_redirect = _location_block(value, "location = /fae {")
    management_redirect = _location_block(value, "location = /fae/manage {")
    health = _location_block(value, "location = /fae/health {")
    assert "return 308 /fae/$is_args$args;" in root_redirect
    assert "return 308 /fae/manage/$is_args$args;" in management_redirect
    assert "return 404;" in health

    management = _location_block(value, "location ^~ /fae/manage/ {")
    chat = _location_block(value, "location = /fae/api/chat {")
    attachments = _location_block(value, "location = /fae/api/attachments {")
    api = _location_block(value, "location ^~ /fae/api/ {")
    assets = _location_block(value, "location ^~ /fae/assets/ {")
    application = _location_block(value, "location ^~ /fae/ {")

    assert "proxy_pass http://127.0.0.1:8080;" in management
    assert "rewrite " not in management
    for block in (chat, attachments, api, assets, application):
        assert "proxy_pass http://127.0.0.1:8000;" in block
    assert "rewrite ^/fae/api/chat$ /chat break;" in chat
    assert "rewrite ^/fae/api/attachments$ /attachments break;" in attachments
    assert "rewrite ^/fae/api(/.*)$ $1 break;" in api
    assert "rewrite " not in assets
    assert "rewrite " not in application
    assert "proxy_buffering off;" in chat
    assert "proxy_request_buffering off;" in chat
    assert "proxy_read_timeout 330s;" in chat
    assert "proxy_send_timeout 330s;" in chat
    assert "client_max_body_size 50m;" in attachments
    assert (
        'add_header Cache-Control "public, max-age=31536000, immutable" always;'
        in assets
    )

    proxy_blocks = (management, chat, attachments, api, assets, application)
    for block in proxy_blocks:
        for directive in (
            "proxy_set_header Host $host;",
            "proxy_set_header X-Real-IP $remote_addr;",
            "proxy_set_header X-Forwarded-For $remote_addr;",
            "proxy_set_header X-Forwarded-Proto $scheme;",
            'proxy_set_header Forwarded "";',
            'proxy_set_header Authorization "";',
            'proxy_set_header Connection "";',
            'add_header Strict-Transport-Security "max-age=31536000" always;',
            'add_header X-Content-Type-Options "nosniff" always;',
            'add_header X-Frame-Options "DENY" always;',
            'add_header Referrer-Policy "no-referrer" always;',
            "add_header Content-Security-Policy",
            'add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;',
        ):
            assert directive in block
        assert "$proxy_add_x_forwarded_for" not in block

    for block in (management, chat, attachments, api, application):
        assert 'add_header Cache-Control "private, no-store" always;' in block

    for block in (root_redirect, management_redirect, health):
        for directive in (
            'add_header Cache-Control "private, no-store" always;',
            'add_header Strict-Transport-Security "max-age=31536000" always;',
            'add_header X-Content-Type-Options "nosniff" always;',
            'add_header X-Frame-Options "DENY" always;',
            'add_header Referrer-Policy "no-referrer" always;',
            "add_header Content-Security-Policy",
            'add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;',
        ):
            assert directive in block


def test_formal_nginx_has_exact_workspace_ownership_and_failure_isolation():
    value = _text(FORMAL_NGINX).split("# HTTPS_ENTRY_BEGIN", 1)[1]
    platform_upstream = "proxy_pass http://127.0.0.1:8080;"
    office_upstream = "proxy_pass http://127.0.0.1:8011;"
    fae_upstream = "proxy_pass http://127.0.0.1:8000;"
    voc_upstream = "proxy_pass http://172.29.0.3:18130;"

    owned_blocks = {
        "platform-admin-root": _location_block(value, "location = /admin {"),
        "platform-admin": _location_block(value, "location ^~ /admin/ {"),
        "platform-fae-management": _location_block(
            value, "location ^~ /fae/manage/ {"
        ),
        "office": _location_block(value, "location ^~ /office/ {"),
        "fae": _location_block(value, "location ^~ /fae/ {"),
        "voc": _location_block(value, "location ^~ /voc/ {"),
    }
    expected_upstreams = {
        "platform-admin-root": platform_upstream,
        "platform-admin": platform_upstream,
        "platform-fae-management": platform_upstream,
        "office": office_upstream,
        "fae": fae_upstream,
        "voc": voc_upstream,
    }
    all_upstreams = {
        platform_upstream,
        office_upstream,
        fae_upstream,
        voc_upstream,
    }

    for owner, block in owned_blocks.items():
        expected = expected_upstreams[owner]
        assert expected in block
        assert all(other not in block for other in all_upstreams - {expected})

    # HR, Marketing, and every unmatched product path deliberately stay on the
    # final Platform catch-all; no competing prefix may steal those workspaces.
    catch_all = value[value.rindex("    location / {") :]
    assert platform_upstream in catch_all
    location_lines = tuple(
        line.strip()
        for line in value.splitlines()
        if line.strip().startswith("location ")
    )
    assert all(
        "/hr" not in line and "/marketing" not in line
        for line in location_lines
    )

    # An unavailable workspace upstream must surface only from its own block.
    # In particular, none of the non-Platform owners may fall back or redirect
    # into the generic management product.
    isolated_upstreams = {
        office_upstream: (
            "location = /office/chat {",
            "location = /office/service-feedback {",
            "location ^~ /office/assets/ {",
            "location ^~ /office/knowledge-assets/ {",
            "location ^~ /office/ {",
        ),
        fae_upstream: (
            "location = /fae/api/chat {",
            "location = /fae/api/attachments {",
            "location ^~ /fae/api/ {",
            "location ^~ /fae/assets/ {",
            "location ^~ /fae/ {",
        ),
        voc_upstream: (
            "location ^~ /voc/assets/ {",
            "location ^~ /voc/ {",
        ),
    }
    for expected, selectors in isolated_upstreams.items():
        for selector in selectors:
            block = _location_block(value, selector)
            assert expected in block
            assert all(other not in block for other in all_upstreams - {expected})
            assert "error_page" not in block
            assert "proxy_intercept_errors on" not in block
            assert "return 30" not in block
            assert "/admin" not in block


def test_each_stubbed_workspace_upstream_failure_is_isolated_to_its_routes():
    value = _text(FORMAL_NGINX).split("# HTTPS_ENTRY_BEGIN", 1)[1]
    _assert_no_workspace_failure_fallback(value)
    routes = (
        "/",
        "/hr/",
        "/marketing/gtm/conversations/mkt%3Aone",
        "/admin/sessions",
        "/fae/manage/issues",
        "/office/",
        "/office/?view=services",
        "/fae/",
        "/fae/conversations/fae%3Aone",
        "/voc/",
        "/voc/records/VOC-20260903-001",
    )
    route_owners = {
        route: _workspace_proxy_for_path(value, route) for route in routes
    }
    platform = "http://127.0.0.1:8080"
    office = "http://127.0.0.1:8011"
    fae = "http://127.0.0.1:8000"
    voc = "http://172.29.0.3:18130"
    assert route_owners == {
        "/": platform,
        "/hr/": platform,
        "/marketing/gtm/conversations/mkt%3Aone": platform,
        "/admin/sessions": platform,
        "/fae/manage/issues": platform,
        "/office/": office,
        "/office/?view=services": office,
        "/fae/": fae,
        "/fae/conversations/fae%3Aone": fae,
        "/voc/": voc,
        "/voc/records/VOC-20260903-001": voc,
    }
    owners = {
        platform,
        office,
        fae,
        voc,
    }
    expected_failure_routes = {
        office: {"/office/", "/office/?view=services"},
        fae: {"/fae/", "/fae/conversations/fae%3Aone"},
        voc: {"/voc/", "/voc/records/VOC-20260903-001"},
    }
    for failed_upstream, expected_changed in expected_failure_routes.items():
        upstreams = {
            owner: _start_http_server(_upstream_handler(owner)) for owner in owners
        }
        upstream_ports = {
            owner: server.server_port
            for owner, (server, _thread) in upstreams.items()
        }
        proxy, proxy_thread = _start_http_server(
            _proxy_handler(value, upstream_ports)
        )
        stopped: set[str] = set()
        try:
            baseline = {
                route: _http_response(proxy.server_port, route) for route in routes
            }
            assert all(response[0] == 200 for response in baseline.values())
            server, thread = upstreams[failed_upstream]
            _stop_http_server(server, thread)
            stopped.add(failed_upstream)
            failed = {
                route: _http_response(proxy.server_port, route) for route in routes
            }
            changed = {
                route for route in routes if failed[route] != baseline[route]
            }
            assert changed == expected_changed
            assert all(failed[route][0] == 502 for route in changed)
            assert all(failed[route][1] == "" for route in changed)
            assert all(b"unavailable" in failed[route][2] for route in changed)
            assert all(
                failed[route] == baseline[route]
                for route in routes
                if route not in changed
            )
        finally:
            _stop_http_server(proxy, proxy_thread)
            for owner, (server, thread) in upstreams.items():
                if owner not in stopped:
                    _stop_http_server(server, thread)


def test_failure_isolation_gate_rejects_server_level_admin_fallback():
    value = _text(FORMAL_NGINX).split("# HTTPS_ENTRY_BEGIN", 1)[1]
    regressed = value.replace(
        "    listen 443 ssl;",
        "    listen 443 ssl;\n    error_page 502 =302 /admin;",
        1,
    )

    with pytest.raises(AssertionError):
        _assert_no_workspace_failure_fallback(regressed)
