from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from test_control_plane_migration import control_database  # noqa: F401

from app.control_plane import external_dingtalk_probe as probe


def private_file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    path.chmod(0o600)
    return str(path)


def settings(monkeypatch, tmp_path, dsn):
    monkeypatch.setenv('FAE_PROBE_ENVIRONMENT', 'development')
    for name, value in [('DATABASE_URL', dsn), ('APP_KEY', 'probe-app'), ('APP_SECRET', 'private-secret')]:
        monkeypatch.setenv(f'FAE_PROBE_{name}_FILE', private_file(tmp_path, name, value))
    for index, (name, purpose) in enumerate([
        ('AUTH', 'fae-probe-auth'),
        ('PROVIDER_ENCRYPTION', 'partner-provider-encryption'),
        ('PROVIDER_LOOKUP', 'partner-provider-lookup-hmac'),
        ('CONTENT', 'platform-content-encryption'),
    ], 1):
        document = dict(purpose=purpose, active_version=1, keys={'1': base64.b64encode(bytes([index]) * 32).decode()})
        if name == 'PROVIDER_LOOKUP':
            document['transition_versions'] = [1]
        monkeypatch.setenv(f'FAE_PROBE_{name}_KEYRING_FILE', private_file(tmp_path, name, json.dumps(document)))


@pytest.mark.parametrize('environment', ['', 'production', 'preview'])
def test_factory_requires_explicit_development(monkeypatch, environment):
    monkeypatch.setenv('FAE_PROBE_ENVIRONMENT', environment)
    with pytest.raises(ValueError, match='fae_probe_configuration_invalid'):
        probe.create_app()


def test_factory_rejects_production_dsn_before_connect(monkeypatch, tmp_path):
    settings(monkeypatch, tmp_path, 'postgresql://platform_control_app:secret@127.0.0.1/agent_platform_control')
    with pytest.raises(ValueError, match='fae_probe_configuration_invalid'):
        probe.create_app()


def test_factory_rejects_public_secret_and_shared_key_material(monkeypatch, tmp_path):
    settings(monkeypatch, tmp_path, 'postgresql://platform_control_app_preview:secret@127.0.0.1/agent_platform_control_preview')
    (tmp_path / 'APP_SECRET').chmod(0o644)
    with pytest.raises(ValueError, match='fae_probe_configuration_invalid'):
        probe.create_app()
    (tmp_path / 'APP_SECRET').chmod(0o600)
    auth = json.loads((tmp_path / 'AUTH').read_text())
    content = json.loads((tmp_path / 'CONTENT').read_text())
    content['keys'] = auth['keys']
    (tmp_path / 'CONTENT').write_text(json.dumps(content))
    with pytest.raises(ValueError, match='fae_probe_configuration_invalid'):
        probe.create_app()


@pytest.fixture
def configured_app(monkeypatch, tmp_path, control_database):
    preview = control_database['environments']['preview']
    settings(monkeypatch, tmp_path, preview['urls']['platform_control_app_preview'])
    calls = []
    def provider(request):
        calls.append(request)
        if request.url.path.endswith('userAccessToken'):
            code = json.loads(request.content)['code']
            return httpx.Response(200, json={'accessToken': 'token-' + code})
        account = request.headers['x-acs-dingtalk-access-token'].removeprefix('token-')
        return httpx.Response(200, json={'unionId': 'raw-provider-' + account, 'nick': 'Private nickname'})
    app = probe.create_app(transport=httpx.MockTransport(provider))
    return app, calls


def start(client):
    response = client.get('/_dev/fae-external/start', follow_redirects=False)
    assert response.status_code == 302, response.text
    cookie = response.headers['set-cookie']
    assert all(part in cookie for part in ['__Host-fae-dt-nonce=', 'HttpOnly', 'Secure', 'SameSite=lax', 'Path=/', 'Max-Age=300'])
    query = parse_qs(urlsplit(response.headers['location']).query)
    assert query['redirect_uri'] == ['https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback']
    return query['state'][0]


def callback(client, state, account='one'):
    return client.get('/api/v1/auth/dingtalk/callback', params={'state': state, 'authCode': account})


def secure(response):
    assert response.headers['cache-control'] == 'no-store'
    assert "default-src 'none'" in response.headers['content-security-policy']
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert response.headers['referrer-policy'] == 'no-referrer'


@pytest.mark.postgres
def test_real_preview_provision_repeated_identity_isolation_and_report(configured_app):
    app, calls = configured_app
    with TestClient(app, base_url='https://probe.test', client=('127.0.0.1', 1234)) as client:
        secure(client.get('/health'))
        assert client.get('/health').json() == {'ready': True}
        assert client.get('/docs').status_code == 404
        for account in ['one', 'one', 'two']:
            state = start(client)
            response = callback(client, state, account)
            assert response.status_code == 200, response.text
            assert '验证成功' in response.text
            assert 'Max-Age=0' in response.headers['set-cookie']
            secure(response)
            for secret in ['raw-provider', 'Private nickname', 'token-', state, 'platform-session']:
                assert secret not in response.text
        report = probe.safe_report(app.state.partner_service)
        assert report['subject_count'] == 2
        assert sorted(item['login_count'] for item in report['subjects']) == [1, 2]
        assert all(item['state'] == 'active' for item in report['subjects'])
        assert all(set(item) == {'subject_id', 'login_count', 'state'} for item in report['subjects'])
        assert len(calls) == 6
    assert app.state.provider._client.is_closed


@pytest.mark.postgres
def test_malformed_callback_nonce_and_replay_precede_provider_exchange(configured_app):
    app, calls = configured_app
    with TestClient(app, base_url='https://probe.test', client=('127.0.0.2', 1234)) as client:
        state = start(client)
        nonce = client.cookies.get('__Host-fae-dt-nonce')
        for suffix in ['&authCode=x&authCode=y', '&authCode=x&code=y', '&authCode=x&error=denied', '&authCode=', '&authCode=x&unknown=y']:
            response = client.get('/api/v1/auth/dingtalk/callback?state=' + state + suffix)
            assert response.status_code == 401
            secure(response)
            assert 'Max-Age=0' in response.headers['set-cookie']
        assert calls == []
        response = callback(client, state)
        assert response.status_code == 401 and calls == []
        client.cookies.set('__Host-fae-dt-nonce', nonce, domain='probe.test', path='/')
        assert callback(client, state).status_code == 200
        client.cookies.set('__Host-fae-dt-nonce', nonce, domain='probe.test', path='/')
        response = callback(client, state)
        assert response.status_code == 401
        assert response.json() == {'code': 'partner_auth_state_replay'}
        assert len(calls) == 2


@pytest.mark.postgres
def test_rate_limit_uses_actual_asgi_client_and_does_not_trust_forwarded_headers(configured_app):
    app, calls = configured_app
    with TestClient(app, base_url='https://probe.test', client=('127.0.0.3', 1234)) as client:
        for index in range(20):
            response = client.get('/api/v1/auth/dingtalk/callback?bad=1', headers={'x-forwarded-for': f'192.0.2.{index}'})
            assert response.status_code == 401
        response = client.get('/_dev/fae-external/start')
        assert response.status_code == 429
        assert response.json() == {'code': 'fae_probe_rate_limited'}
        assert calls == []


def test_cli_configuration_failure_contains_only_stable_code(monkeypatch, capsys):
    monkeypatch.delenv('FAE_PROBE_ENVIRONMENT', raising=False)
    assert probe.main(['report']) == 1
    assert json.loads(capsys.readouterr().out) == {'code': 'fae_probe_report_unavailable'}


@pytest.mark.postgres
def test_report_marks_revoked_provider_identity_inactive(configured_app, control_database):
    import psycopg
    app, _ = configured_app
    before = {row['subject_id'] for row in probe.safe_report(app.state.partner_service)['subjects']}
    with TestClient(app, base_url='https://probe.test', client=('127.0.0.4', 1234)) as client:
        assert callback(client, start(client), 'revoked-report').status_code == 200
        subjects = probe.safe_report(app.state.partner_service)['subjects']
        target = next(row['subject_id'] for row in subjects if row['subject_id'] not in before)
        with psycopg.connect(control_database['environments']['preview']['admin']) as connection:
            connection.execute('update platform_control.partner_provider_identities set revoked_at=now() where partner_operator_id in (select partner_operator_id from platform_control.partner_operators where subject_id=%s)', (target,))
        report = probe.safe_report(app.state.partner_service)
        assert next(row['state'] for row in report['subjects'] if row['subject_id'] == target) == 'inactive'


@pytest.mark.parametrize('unsafe', ['indirection', 'ambient'])
def test_factory_rejects_libpq_configuration_fallthrough(monkeypatch, tmp_path, unsafe):
    dsn = 'postgresql://platform_control_app_preview:secret@127.0.0.1:5432/agent_platform_control_preview'
    if unsafe == 'indirection':
        dsn += '?service=production'
    settings(monkeypatch, tmp_path, dsn)
    if unsafe == 'ambient':
        monkeypatch.setenv('PGSERVICE', 'production')
    with pytest.raises(ValueError, match='fae_probe_configuration_invalid'):
        probe.create_app()
