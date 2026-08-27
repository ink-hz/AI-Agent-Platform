from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "deploy" / "cloud" / "dingtalk_nginx_transaction.py"


def _module():
    specification = importlib.util.spec_from_file_location(
        "dingtalk_nginx_transaction", SCRIPT
    )
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


CURRENT = """\
server {
    listen 80;
    server_name agent.orbbec.com.cn;
    location / { return 308 https://agent.orbbec.com.cn$request_uri; }
}
server {
    listen 443 ssl;
    server_name agent.orbbec.com.cn;
    ssl_certificate /example/fullchain.pem;

    auth_basic "Orbbec Agent Platform";
    auth_basic_user_file /example/htpasswd;
    auth_delay 1s;

    location = /admin/ {
        auth_basic "AI ADMIN Demo";
        auth_basic_user_file /example/admin-htpasswd;
        proxy_pass http://127.0.0.1:8012/admin/;
    }

    include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;

    location / {
        limit_except GET HEAD OPTIONS {
            deny all;
        }
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
"""


def test_transaction_changes_only_platform_root_and_server_shared_auth():
    transformed = _module().transform(CURRENT)

    admin = """\
    location = /admin/ {
        auth_basic "AI ADMIN Demo";
        auth_basic_user_file /example/admin-htpasswd;
        proxy_pass http://127.0.0.1:8012/admin/;
    }
"""
    assert admin in transformed
    assert "include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;" not in transformed
    assert "ssl_certificate /example/fullchain.pem;" in transformed
    assert 'auth_basic "Orbbec Agent Platform";' not in transformed
    assert 'auth_basic "AI ADMIN Demo";' in transformed
    assert "limit_except GET HEAD OPTIONS" not in transformed
    assert "proxy_read_timeout 360s;" in transformed
    assert "proxy_send_timeout 360s;" in transformed
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in transformed
    assert 'proxy_set_header Authorization "";' in transformed
    assert "Content-Security-Policy" in transformed
    assert "location / { return 308" in transformed
    assert "location ^~ /assets/" in transformed
    asset_start = transformed.index("location ^~ /assets/")
    root_start = transformed.index("location / {", asset_start)
    asset_boundary = transformed[asset_start:root_start]
    for directive in (
        "proxy_pass http://127.0.0.1:8080;",
        "proxy_hide_header Set-Cookie;",
        "gzip on;",
        "gzip_vary on;",
        "gzip_min_length 1024;",
        "gzip_types text/css application/javascript text/javascript application/json "
        "image/svg+xml font/woff font/woff2;",
    ):
        assert directive in asset_boundary
    assert "proxy_hide_header Cache-Control;" not in asset_boundary
    assert "add_header Cache-Control" not in asset_boundary
    assert "proxy_buffering off;" not in asset_boundary


def test_transaction_fails_closed_on_missing_shared_auth_or_ambiguous_root():
    transaction = _module()
    with pytest.raises(ValueError, match="shared authentication"):
        transaction.transform(CURRENT.replace('    auth_delay 1s;\n', ""))
    with pytest.raises(ValueError, match="root location"):
        transaction.transform(CURRENT.replace("    location / {\n", "    location /api {\n"))
