from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "deploy" / "cloud" / "office_path_nginx_transaction.py"


def _module():
    specification = importlib.util.spec_from_file_location(
        "office_path_nginx_transaction", SCRIPT
    )
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


CURRENT = """\
log_format agent_platform_redacted
    '$request_method $uri $status $body_bytes_sent $request_time';

server {
    listen 80;
    server_name agent.orbbec.com.cn;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
    }
    location / { return 308 https://agent.orbbec.com.cn$request_uri; }
}

server {
    listen 443 ssl;
    server_name agent.orbbec.com.cn;
    ssl_certificate /example/fullchain.pem;
    ssl_certificate_key /example/privkey.pem;

    location = /admin {
        return 308 /admin/;
    }
    location = /admin/health {
        proxy_pass http://127.0.0.1:8011/health;
    }
    location = /admin/chat {
        proxy_pass http://127.0.0.1:8011/chat;
    }
    location /admin/services {
        proxy_pass http://127.0.0.1:8011/services;
    }
    location /admin/service-portal/ {
        proxy_pass http://127.0.0.1:8011/service-portal/;
    }
    location = /admin/service-feedback {
        client_max_body_size 12m;
        proxy_pass http://127.0.0.1:8011/service-feedback;
    }
    location /admin/service-feedback-admin/ {
        proxy_pass http://127.0.0.1:8011/service-feedback-admin/;
    }
    location /admin/shuttle/ {
        proxy_pass http://127.0.0.1:8011/shuttle/;
    }
    location /admin/lodging/ {
        proxy_pass http://127.0.0.1:8011/lodging/;
    }
    location ^~ /admin/assets/ {
        proxy_pass http://127.0.0.1:8011/assets/;
    }
    location ^~ /admin/ {
        proxy_pass http://127.0.0.1:8011/;
    }

    location /api/v1/platform-only {
        proxy_pass http://127.0.0.1:8080;
    }
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
"""


def test_transaction_replaces_admin_locations_and_preserves_platform_tls_acme():
    transformed = _module().transform(CURRENT)

    assert "location = /admin" not in transformed
    assert "location ^~ /admin/" not in transformed
    assert "location /admin/" not in transformed
    assert "proxy_pass http://127.0.0.1:8080;" in transformed
    assert "location /api/v1/platform-only" in transformed
    assert "ssl_certificate /example/fullchain.pem;" in transformed
    assert "location ^~ /.well-known/acme-challenge/" in transformed
    assert transformed.count("location ^~ /office/ {") == 1
    assert "proxy_pass http://127.0.0.1:8011;" in transformed
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in transformed
    assert 'proxy_set_header Forwarded "";' in transformed
    assert 'proxy_set_header Authorization "";' in transformed
    assert "proxy_set_header Cookie" not in transformed
    assert "location = /office/health" in transformed
    assert "return 404;" in transformed
    assert transformed.count("zone=ai_admin_office_chat:10m") == 1
    assert transformed.count("zone=ai_admin_office_conn:10m") == 1
    redacted_log = " ".join(
        line.strip()
        for line in transformed.splitlines()[:3]
    )
    assert "$request_method $uri $status $body_bytes_sent $request_time" in redacted_log
    assert "$request " not in redacted_log
    assert "$args" not in redacted_log
    assert "$http_cookie" not in redacted_log


def test_every_office_location_with_cache_headers_repeats_security_headers():
    transformed = _module().transform(CURRENT)
    required = (
        'add_header Strict-Transport-Security "max-age=31536000" always;',
        'add_header X-Content-Type-Options "nosniff" always;',
        'add_header X-Frame-Options "DENY" always;',
        'add_header Referrer-Policy "no-referrer" always;',
        "add_header Content-Security-Policy",
        'add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;',
    )
    selectors = (
        "location = /office/chat {",
        "location = /office/service-feedback {",
        "location ^~ /office/assets/ {",
        "location ^~ /office/knowledge-assets/ {",
        "location ^~ /office/ {",
    )

    for selector in selectors:
        start = transformed.index(selector)
        end = transformed.index("\n    }", start)
        block = transformed[start:end]
        assert "add_header Cache-Control" in block
        for header in required:
            assert header in block, (selector, header)


@pytest.mark.parametrize(
    "unsafe_variable",
    ["$request", "$request_uri", "$args", "$http_cookie", "$http_authorization"],
)
def test_transaction_rejects_sensitive_redacted_log_variables(unsafe_variable: str):
    value = CURRENT.replace("$uri", unsafe_variable, 1)

    with pytest.raises(ValueError, match="redacted log"):
        _module().transform(value)


def test_transaction_fails_closed_on_duplicate_server_missing_root_or_bad_braces():
    transaction = _module()
    https_block = CURRENT[CURRENT.index("server {", CURRENT.index("server {") + 1):]
    with pytest.raises(ValueError, match="exact Agent HTTPS server"):
        transaction.transform(CURRENT + "\n" + https_block)
    with pytest.raises(ValueError, match="root location"):
        transaction.transform(CURRENT.replace(
            "    location / {\n        proxy_pass http://127.0.0.1:8080;",
            "    location /platform {\n        proxy_pass http://127.0.0.1:8080;",
        ))
    with pytest.raises(ValueError, match="invalid"):
        transaction.transform(CURRENT.rsplit("}", 1)[0])


def test_transaction_rejects_unknown_admin_selector_existing_office_and_replay():
    transaction = _module()
    unknown = CURRENT.replace(
        "    location / {\n        proxy_pass http://127.0.0.1:8080;",
        "    location ~ ^/admin/private { return 404; }\n"
        "    location / {\n        proxy_pass http://127.0.0.1:8080;",
    )
    with pytest.raises(ValueError, match="admin location"):
        transaction.transform(unknown)
    with pytest.raises(ValueError, match="office location"):
        transaction.transform(CURRENT.replace("/admin/assets/", "/office/assets/"))

    transformed = transaction.transform(CURRENT)
    with pytest.raises(ValueError, match="office location"):
        transaction.transform(transformed)
