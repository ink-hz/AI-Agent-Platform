import asyncio
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import load_config
from app.local_secrets import SecretFileUnavailable
from app.main import build_external_dingtalk_provider


class VerificationService:
    def external_verified_at(self, _subject):
        return None


@pytest.fixture(autouse=True)
def isolated_external_settings(monkeypatch):
    for name in (
        "PLATFORM_PARTNER_DINGTALK_CLIENT_ID",
        "PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET_FILE",
        "PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def external_config(tmp_path):
    enterprise_secret = tmp_path / "enterprise-secret"
    enterprise_secret.write_text("enterprise-secret-value")
    enterprise_secret.chmod(0o600)
    external_secret = tmp_path / "external-secret"
    external_secret.write_text("external-secret-value")
    external_secret.chmod(0o600)
    config = load_config()
    return replace(
        config,
        partner_dingtalk_client_id="external-client",
        partner_dingtalk_client_secret_file=str(external_secret),
        control_plane=replace(
            config.control_plane,
            dingtalk_app_key="enterprise-client",
            dingtalk_app_secret_file=str(enterprise_secret),
        ),
    )


def test_external_factory_rejects_enterprise_only_credentials(tmp_path):
    secret = tmp_path / "enterprise-secret"
    secret.write_text("enterprise-secret-value")
    secret.chmod(0o600)
    config = load_config()
    config = replace(
        config,
        control_plane=replace(
            config.control_plane,
            dingtalk_app_key="enterprise-client",
            dingtalk_app_secret_file=str(secret),
        ),
    )

    with pytest.raises(RuntimeError, match="external_dingtalk_credentials_required"):
        build_external_dingtalk_provider(config, VerificationService())


@pytest.mark.parametrize("missing", ["client_id", "secret_file", "both"])
def test_external_factory_requires_both_settings(external_config, missing):
    changes = {}
    if missing in {"client_id", "both"}:
        changes["partner_dingtalk_client_id"] = ""
    if missing in {"secret_file", "both"}:
        changes["partner_dingtalk_client_secret_file"] = ""
    with pytest.raises(RuntimeError, match="external_dingtalk_credentials_required"):
        build_external_dingtalk_provider(replace(external_config, **changes), VerificationService())


def test_external_factory_rejects_enterprise_client_id(external_config):
    config = replace(external_config, partner_dingtalk_client_id="enterprise-client")
    with pytest.raises(RuntimeError, match="external_dingtalk_registration_must_differ_from_enterprise"):
        build_external_dingtalk_provider(config, VerificationService())


def test_external_factory_uses_external_registration_for_oauth(external_config):
    provider = build_external_dingtalk_provider(external_config, VerificationService())
    requests = []

    def transport(request):
        requests.append(request)
        if request.url.path.endswith("userAccessToken"):
            return httpx.Response(200, json={"accessToken": "test-access-token"})
        return httpx.Response(200, json={"unionId": "external-union", "nick": "Partner"})

    async def authenticate():
        await provider._client.aclose()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        try:
            return await provider.finish_auth({"state": "test-state", "authCode": "test-code"})
        finally:
            await provider.aclose()

    query = parse_qs(urlsplit(provider.begin_auth("test-state")).query)
    assert query["client_id"] == ["external-client"]
    assert query["scope"] == ["openid"]
    assert query["redirect_uri"] == ["https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback"]
    asyncio.run(authenticate())
    token_body = json.loads(requests[0].content)
    assert token_body["clientId"] == "external-client"
    assert token_body["clientSecret"] == "external-secret-value"
    assert "enterprise-secret-value" not in str(token_body)


@pytest.mark.parametrize("invalid", ["missing", "unreadable", "group_readable", "symlink", "relative"])
def test_external_factory_uses_private_secret_reader(external_config, tmp_path, monkeypatch, invalid):
    secret = Path(external_config.partner_dingtalk_client_secret_file)
    if invalid == "missing":
        secret.unlink()
    elif invalid == "unreadable":
        import app.local_secrets as local_secrets

        original_open = local_secrets.os.open

        def deny_secret_open(path, *args, **kwargs):
            if path == secret:
                raise PermissionError("test secret is unreadable")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(local_secrets.os, "open", deny_secret_open)
    elif invalid == "group_readable":
        secret.chmod(0o640)
    elif invalid == "symlink":
        link = tmp_path / "linked-secret"
        link.symlink_to(secret)
        secret = link
    else:
        monkeypatch.chdir(tmp_path)
        secret = Path(secret.name)
    config = replace(external_config, partner_dingtalk_client_secret_file=str(secret))
    with pytest.raises(SecretFileUnavailable, match="secret file unavailable"):
        build_external_dingtalk_provider(config, VerificationService())


def test_external_credentials_default_to_empty():
    config = load_config()
    assert config.partner_dingtalk_client_id == ""
    assert config.partner_dingtalk_client_secret_file == ""


def test_external_settings_load_from_environment_without_changing_enterprise(monkeypatch, tmp_path):
    from test_config import _enable_production_identity

    _enable_production_identity(monkeypatch, tmp_path)
    before = load_config()
    monkeypatch.setenv("PLATFORM_PARTNER_DINGTALK_CLIENT_ID", " external-client ")
    monkeypatch.setenv("PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET_FILE", " /run/secrets/external-secret ")
    after = load_config()
    assert after.partner_dingtalk_client_id == "external-client"
    assert after.partner_dingtalk_client_secret_file == "/run/secrets/external-secret"
    assert after.control_plane == before.control_plane


@pytest.mark.parametrize("identity_enabled", [False, True])
def test_external_inline_secret_is_rejected(monkeypatch, tmp_path, identity_enabled):
    if identity_enabled:
        from test_config import _enable_production_identity

        _enable_production_identity(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET", "inline-secret-value")
    with pytest.raises(ValueError, match="identity credentials must use secret files"):
        load_config()
