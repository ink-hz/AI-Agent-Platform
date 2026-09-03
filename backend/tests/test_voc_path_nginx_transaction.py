from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "deploy" / "cloud" / "voc_path_nginx_transaction.py"


def _module():
    specification = importlib.util.spec_from_file_location(
        "voc_path_nginx_transaction", SCRIPT
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
    location / { return 308 https://agent.orbbec.com.cn$request_uri; }
}

server {
    listen 443 ssl;
    server_name agent.orbbec.com.cn;
    ssl_certificate /example/fullchain.pem;
    ssl_certificate_key /example/privkey.pem;

    location = /office/health { return 404; }
    location ^~ /office/ { proxy_pass http://127.0.0.1:8011; }
    location / {
        proxy_pass http://127.0.0.1:8080;
    }
}
"""


def test_transaction_inserts_exact_voc_locations_before_platform_root():
    transformed = _module().transform(CURRENT)
    selectors = (
        "location = /voc {",
        "location = /voc/health {",
        "location ^~ /voc/assets/ {",
        "location ^~ /voc/ {",
    )

    positions = [transformed.index(selector) for selector in selectors]
    assert positions == sorted(positions)
    assert positions[-1] < transformed.index("location / {", positions[-1])
    assert "return 308 /voc/$is_args$args;" in transformed
    assert "return 404;" in transformed[positions[1] : positions[2]]
    assert transformed.count("proxy_pass http://172.29.0.3:18130;") == 2
    assert "location ^~ /office/" in transformed
    assert "ssl_certificate /example/fullchain.pem;" in transformed


def test_transaction_repeats_security_and_header_boundaries_for_voc_content():
    transformed = _module().transform(CURRENT)
    assets_start = transformed.index("location ^~ /voc/assets/ {")
    app_start = transformed.index("location ^~ /voc/ {")
    root_start = transformed.index("location / {", app_start)
    assets = transformed[assets_start:app_start]
    application = transformed[app_start:root_start]

    assert 'add_header Cache-Control "public, max-age=31536000, immutable" always;' in assets
    assert 'add_header Cache-Control "private, no-store" always;' in application
    for block in (assets, application):
        for directive in (
            "client_max_body_size 1m;",
            "proxy_set_header X-Real-IP $remote_addr;",
            "proxy_set_header X-Forwarded-For $remote_addr;",
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
    assert "proxy_buffering off;" not in assets
    assert "proxy_buffering off;" in application


def test_transaction_fails_closed_on_existing_voc_ambiguous_server_or_root():
    transaction = _module()
    with pytest.raises(ValueError, match="existing VOC location"):
        transaction.transform(CURRENT.replace("/office/health", "/voc/health"))
    with pytest.raises(ValueError, match="existing VOC location"):
        transaction.transform(
            CURRENT.replace(
                "    location = /office/health { return 404; }",
                "    location ~ ^/voc/private { return 404; }",
            )
        )
    https = CURRENT[CURRENT.index("server {", CURRENT.index("server {") + 1) :]
    with pytest.raises(ValueError, match="exact Agent HTTPS server"):
        transaction.transform(CURRENT + "\n" + https)
    with pytest.raises(ValueError, match="root location"):
        transaction.transform(
            CURRENT.replace(
                "    location / {\n        proxy_pass http://127.0.0.1:8080;",
                "    location /platform {\n        proxy_pass http://127.0.0.1:8080;",
            )
        )
    with pytest.raises(ValueError, match="invalid"):
        transaction.transform(CURRENT.rsplit("}", 1)[0])


def test_transaction_cli_is_locked_atomic_backed_up_and_nginx_only():
    value = SCRIPT.read_text(encoding="utf-8")

    for required in (
        "/opt/orbbec-agent-platform/private/deploy-input.transaction.lock",
        "/opt/orbbec-agent-platform/private/deploy-input.lock",
        "fcntl.flock",
        "fcntl.LOCK_EX | fcntl.LOCK_NB",
        "/root/nginx-backups/voc-path-",
        "https-server.conf",
        "os.replace",
        '[_NGINX, "-t"]',
        '[_SYSTEMCTL, "reload", "nginx"]',
        "os.O_RDWR | getattr(os, \"O_NOFOLLOW\", 0)",
    ):
        assert required in value
    assert "os.O_RDWR | os.O_CREAT" not in value
    for forbidden in (
        '"restart", "nginx"',
        "docker compose",
        "platform-api",
        "ai-fae",
        "systemctl restart",
    ):
        assert forbidden not in value


def test_transaction_installs_and_backs_up_exact_config_under_one_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    transaction = _module()
    private = tmp_path / "private"
    backups = tmp_path / "backups"
    private.mkdir(mode=0o700)
    backups.mkdir(mode=0o700)
    lock = private / "deploy-input.transaction.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    source = tmp_path / "agent-domain.conf"
    source.write_text(CURRENT, encoding="utf-8")
    source.chmod(0o644)
    commands: list[list[str]] = []

    monkeypatch.setattr(transaction, "_ROOT_UID", os.getuid(), raising=False)
    monkeypatch.setattr(transaction, "_ROOT_GID", os.getgid(), raising=False)
    monkeypatch.setattr(transaction, "_LOCK_PATH", lock)
    monkeypatch.setattr(transaction, "_INPUT_LOCK_PATH", private / "deploy-input.lock")
    monkeypatch.setattr(transaction, "_BACKUP_PREFIX", str(backups / "voc-path-"))
    monkeypatch.setattr(transaction.os, "geteuid", os.getuid)
    monkeypatch.setattr(
        transaction.subprocess,
        "run",
        lambda command, *, check: commands.append(command),
    )

    backup = transaction.run_transaction(source)

    assert source.read_text(encoding="utf-8") == transaction.transform(CURRENT)
    assert (backup / "agent-domain.conf").read_text(encoding="utf-8") == CURRENT
    assert (backup / "https-server.conf").read_text(encoding="utf-8") == transaction._https_agent_server(CURRENT)[2]
    assert commands == [
        [transaction._NGINX, "-t"],
        [transaction._SYSTEMCTL, "reload", "nginx"],
    ]
