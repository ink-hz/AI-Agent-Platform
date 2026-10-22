"""Contracts for the direct ZenTao SDK path and its isolation from other sites."""

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
DOMAIN = ROOT / "deploy/cloud/agent-domain.nginx.conf"
COMPOSE = ROOT / "deploy/sdk/compose.yaml"


def _location(config: str, opening: str) -> str:
    start = config.index(opening)
    depth = 0
    for end in range(config.index("{", start), len(config)):
        if config[end] == "{":
            depth += 1
        elif config[end] == "}":
            depth -= 1
            if depth == 0:
                return config[start:end + 1]
    raise AssertionError(f"unclosed location: {opening}")


def test_sdk_entry_checks_platform_identity_and_uses_scoped_zen_proxy() -> None:
    config = DOMAIN.read_text(encoding="utf-8")
    no_slash = _location(config, "location = /sdk {")
    entry = _location(config, "location = /sdk/ {")
    access = _location(config, "location = /_sdk_access {")
    proxy = _location(config, "location ^~ /sdk/ {")
    assert "return 308 /sdk/;" in no_slash
    assert "proxy_pass http://127.0.0.1:8080/api/v1/sdk/sso/entry;" in entry
    assert "error_page 401 = @sdk_login;" in entry
    assert "proxy_pass http://127.0.0.1:8080/api/v1/sdk/sso/access;" in access
    assert "internal;" in access
    assert "proxy_set_header Cookie $http_cookie;" in access
    assert "auth_request /_sdk_access;" in proxy
    assert "error_page 401 = @sdk_login;" in proxy
    assert "return 302 /login?return_path=%2Fsdk%2F;" in _location(config, "location @sdk_login {")
    assert "proxy_pass http://127.0.0.1:18131;" in proxy
    assert "proxy_cookie_path ~^/$ /sdk/;" in proxy
    assert "client_max_body_size 100m;" in proxy
    assert "proxy_set_header Authorization \"\";" in proxy
    assert "proxy_set_header Cookie $sdk_zentao_cookie;" in proxy
    assert "proxy_set_header Accept-Language \"zh-cn\";" in proxy
    assert "Content-Security-Policy" not in proxy
    assert "location = /office" in config


def test_sdk_public_api_and_installers_are_denied() -> None:
    config = DOMAIN.read_text(encoding="utf-8")
    for path in ("api.php", "install.php", "upgrade.php"):
        location = _location(config, f"location = /sdk/{path} {{")
        assert "return 403;" in location
    assert "location ^~ /sdk/api.php/ { return 403; }" in config


def test_zentao_web_root_and_cookie_filter_are_sdk_scoped() -> None:
    config = DOMAIN.read_text(encoding="utf-8")
    assert "map $cookie_zentaosid $sdk_zentao_cookie" in config
    assert "$http_cookie" not in _location(config, "location ^~ /sdk/ {")
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert services["zentao"]["environment"]["ZT_WEB_ROOT"] == "/sdk"

