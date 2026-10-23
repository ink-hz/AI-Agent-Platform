from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_control_plane_migration import control_database  # noqa: F401
from test_dingtalk_auth_api import FakeAuth
from test_external_dingtalk_integration import service  # noqa: F401

from app.control_plane.auth import AuthSecrets
from app.control_plane.dingtalk_external_provider import DingTalkExternalIdentityProvider
from app.control_plane.partner_provider import PartnerAuthenticationBroker
from app.control_plane.routes_auth import build_auth_router
from app.control_plane.routes_partner import build_partner_auth_router

CALLBACK = '/api/v1/auth/dingtalk/callback'
COOKIE_PREFIX = '__Host-fae-dt-nonce-'


def cookie_name(state):
    return COOKIE_PREFIX + hashlib.sha256(state.encode('ascii')).hexdigest()


@pytest.fixture
def browser_app(service):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={'errcode': 0, 'user_info': {'unionid': 'browser-state-identity', 'nick': 'Private name'}})
    provider = DingTalkExternalIdentityProvider(
        client_id='test-app', client_secret='secret',
        redirect_uri='https://agent.orbbec.com.cn' + CALLBACK,
        verified_at_for_subject=lambda _: None, transport=httpx.MockTransport(transport),
    )
    broker = PartnerAuthenticationBroker(provider, service, state_secrets=AuthSecrets(b's' * 32, key_version=1))
    launches = []
    class Launch:
        async def issue_partner(self, subject_id):
            launches.append(subject_id)
            return SimpleNamespace(launch_url='https://fae.orbbec.com.cn/#partner_launch=opaque-launch')
    app = FastAPI()
    app.state.partner_auth_broker = broker
    app.state.agent_launch_service = Launch()
    app.include_router(build_partner_auth_router(broker, agent_launch_service=app.state.agent_launch_service))
    auth = FakeAuth()
    app.include_router(build_auth_router(auth, static_dir='static', public_assets=frozenset(), detailed_health=lambda _: {}))
    return app, calls, launches, broker, auth


def start(client):
    response = client.get('/partner-auth/start', follow_redirects=False)
    assert response.status_code == 302, response.text
    return parse_qs(urlsplit(response.headers['location']).query)['state'][0], response


def complete(client, state, **kwargs):
    return client.get(CALLBACK, params={'state': state, 'code': 'fake-code'}, follow_redirects=False, **kwargs)


def secure(response):
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['pragma'] == 'no-cache'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert "default-src 'none'" in response.headers['content-security-policy']
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert "base-uri 'none'" in response.headers['content-security-policy']


@pytest.mark.postgres
def test_two_pending_qr_pages_complete_independently(browser_app):
    app, calls, launches, _, auth = browser_app
    with TestClient(app, base_url='https://agent.orbbec.com.cn') as client:
        first, _ = start(client)
        second, _ = start(client)
        for state in [first, second]:
            response = complete(client, state)
            assert response.status_code == 302, response.text
            assert response.headers['location'].startswith('https://fae.orbbec.com.cn/')
        assert len(calls) == 2 and len(launches) == 2 and auth.provider_calls == 0


@pytest.mark.postgres
@pytest.mark.parametrize('accept', ['application/json', 'text/html'])
def test_three_pending_limit_precedes_attempt_creation_and_completion_frees_slot(browser_app, control_database, accept):
    app, calls, _, _, _ = browser_app
    env = control_database['environments']['production']
    with TestClient(app, base_url='https://agent.orbbec.com.cn') as client:
        states = [start(client)[0] for _ in range(3)]
        before_cookies = dict(client.cookies)
        with psycopg.connect(env['admin']) as conn:
            before_count = conn.execute('select count(*) from platform_control.partner_login_attempts').fetchone()[0]
        full = client.get('/partner-auth/start', headers={'accept': accept}, follow_redirects=False)
        assert full.status_code == 429
        assert dict(client.cookies) == before_cookies
        assert 'set-cookie' not in full.headers
        secure(full)
        if accept == 'text/html':
            assert 'https://fae.orbbec.com.cn/partner/login' in full.text
        else:
            assert full.json() == {'detail': {'code': 'partner_auth_pending_limit'}}
        with psycopg.connect(env['admin']) as conn:
            assert conn.execute('select count(*) from platform_control.partner_login_attempts').fetchone()[0] == before_count
        assert calls == []
        assert complete(client, states[1]).status_code == 302
        assert set(client.cookies) == {cookie_name(states[0]), cookie_name(states[2])}
        fourth, _ = start(client)
        for state in [states[0], states[2], fourth]:
            assert complete(client, state).status_code == 302
        assert len(calls) == 4 and not dict(client.cookies)


@pytest.mark.postgres
def test_simultaneous_start_snapshots_preserve_every_independent_attempt(browser_app, control_database):
    app, calls, launches, _, auth = browser_app
    env = control_database['environments']['production']
    with TestClient(app, base_url='https://agent.orbbec.com.cn') as client:
        states = [start(client)[0] for _ in range(2)]
        snapshot = '; '.join(name + '=' + nonce for name, nonce in client.cookies.items())
        # Two in-flight tabs can each send the same two-cookie snapshot before
        # either response reaches the shared browser jar. Model that ordering
        # deterministically, then let the client merge both Set-Cookie headers.
        for _ in range(2):
            response = client.get('/partner-auth/start', headers={'cookie': snapshot}, follow_redirects=False)
            assert response.request.headers['cookie'] == snapshot
            assert response.status_code == 302, response.text
            states.append(parse_qs(urlsplit(response.headers['location']).query)['state'][0])
        assert len(set(states)) == 4
        assert set(client.cookies) == {cookie_name(state) for state in states}
        pending_cookies = dict(client.cookies)
        with psycopg.connect(env['admin']) as conn:
            attempt_count = conn.execute('select count(*) from platform_control.partner_login_attempts').fetchone()[0]
        full = client.get('/partner-auth/start', follow_redirects=False)
        assert full.status_code == 429
        assert full.json() == {'detail': {'code': 'partner_auth_pending_limit'}}
        assert 'set-cookie' not in full.headers
        assert dict(client.cookies) == pending_cookies
        with psycopg.connect(env['admin']) as conn:
            assert conn.execute('select count(*) from platform_control.partner_login_attempts').fetchone()[0] == attempt_count
        assert not calls and not launches
        with TestClient(app, base_url='https://agent.orbbec.com.cn') as stranger:
            for state in states:
                assert complete(stranger, state).status_code == 401
        assert not calls and not launches
        for index, state in enumerate(states):
            completed = complete(client, state)
            assert completed.status_code == 302, completed.text
            assert completed.headers['location'].startswith('https://fae.orbbec.com.cn/')
            assert set(client.cookies) == {cookie_name(pending) for pending in states[index + 1:]}
            assert len(calls) == index + 1 and len(launches) == index + 1
            assert complete(client, state).status_code == 401
            assert len(calls) == index + 1
        assert not dict(client.cookies) and auth.provider_calls == 0


@pytest.mark.postgres
def test_cookie_namespace_security_and_only_matching_cookie_deleted(browser_app):
    app, _, _, _, _ = browser_app
    with TestClient(app, base_url='https://agent.orbbec.com.cn') as client:
        state, response = start(client)
        name = cookie_name(state)
        assert response.headers['set-cookie'].startswith(name + '=')
        assert state not in name
        assert '__Host-fae-dt-nonce' not in client.cookies
        for flag in ['HttpOnly', 'Secure', 'SameSite=lax', 'Path=/', 'Max-Age=300']:
            assert flag in response.headers['set-cookie']
        secure(response)
        completed = complete(client, state)
        assert completed.headers['set-cookie'].startswith(name + '=')
        assert 'Max-Age=0' in completed.headers['set-cookie']
        secure(completed)


@pytest.mark.postgres
@pytest.mark.parametrize('mode', ['missing', 'stranger', 'mismatched', 'invalid_nonce', 'duplicate_cookie', 'legacy_cookie'])
def test_browser_binding_failures_reject_before_provider_without_consuming_valid_attempt(browser_app, caplog, mode):
    app, calls, _, _, _ = browser_app
    with TestClient(app, base_url='https://agent.orbbec.com.cn') as client:
        state, _ = start(client)
        name = cookie_name(state)
        nonce = client.cookies.get(name)
        bad_cookie = {
            'missing': '', 'stranger': '', 'mismatched': name + '=' + 'x' * 43,
            'invalid_nonce': name + '=short',
            'duplicate_cookie': name + '=' + nonce + '; ' + name + '=' + nonce,
            'legacy_cookie': '__Host-fae-dt-nonce=' + nonce,
        }[mode]
        with TestClient(app, base_url='https://agent.orbbec.com.cn') as stranger:
            rejected = complete(stranger, state, headers={'cookie': bad_cookie, 'accept': 'text/html'})
        assert rejected.status_code == 401 and not calls
        diagnostics = [record.getMessage() for record in caplog.records if record.name == 'app.control_plane.external_browser_state']
        expected_reason = ('storage_rejected' if mode == 'mismatched' else
                           'invalid_cookie_shape' if mode in {'invalid_nonce', 'duplicate_cookie'} else
                           'missing_cookie')
        assert diagnostics == ['external_dingtalk_auth_rejected reason=' + expected_reason]
        assert nonce not in diagnostics[0] and state not in diagnostics[0]
        assert 'set-cookie' not in rejected.headers
        secure(rejected)
        assert complete(client, state).status_code == 302
        assert len(calls) == 1


@pytest.mark.postgres
@pytest.mark.parametrize('accept', ['text/html', 'text/html; q=0', 'application/json', '*/*', 'application/xhtml+xml'])
def test_expired_and_replayed_state_preserve_codes_or_offer_fixed_html_recovery(browser_app, control_database, accept):
    app, calls, _, broker, _ = browser_app
    with TestClient(app, base_url='https://agent.orbbec.com.cn') as client:
        state, _ = start(client)
        name = cookie_name(state)
        nonce = client.cookies.get(name)
        assert complete(client, state).status_code == 302
        client.cookies.set(name, nonce, domain='agent.orbbec.com.cn', path='/')
        replayed = complete(client, state, headers={'accept': accept})
        assert replayed.status_code == 401
        assert len(calls) == 1
        expired_state, _ = start(client)
        env = control_database['environments']['production']
        with psycopg.connect(env['admin']) as conn:
            conn.execute('update platform_control.partner_login_attempts set created_at=now()-interval \'6 minutes\', expires_at=now()-interval \'1 second\' where state_digest=%s', (broker.state_secrets.digest('oauth-state', expired_state),))
        expired = complete(client, expired_state, headers={'accept': accept})
        assert expired.status_code == 401 and len(calls) == 1
        for response, code in [(replayed, 'partner_auth_state_replay'), (expired, 'partner_auth_state_expired')]:
            secure(response)
            if accept == 'text/html':
                assert response.headers['content-type'].startswith('text/html')
                assert 'https://fae.orbbec.com.cn/partner/login' in response.text
                assert 'href="https://fae.orbbec.com.cn/"' in response.text
                assert '同一浏览器' in response.text
                assert '<meta http-equiv=' not in response.text
            else:
                assert response.json() == {'detail': {'code': code}}
            assert state not in response.text and nonce not in response.text and expired_state not in response.text


@pytest.mark.postgres
@pytest.mark.parametrize('malformed', ['invalid_state', 'legacy_state', 'duplicate_state', 'duplicate_code', 'unknown', 'oversized'])
def test_malformed_html_callback_redacts_response_and_diagnostic_logs(browser_app, caplog, malformed):
    app, calls, _, _, auth = browser_app
    marker = 'PRIVATE-email@example.com-<script>alert(1)</script>'
    with TestClient(app, base_url='https://agent.orbbec.com.cn') as client:
        state, _ = start(client)
        nonce = client.cookies.get(cookie_name(state))
        params = [('state', state), ('code', marker)]
        if malformed == 'invalid_state':
            params[0] = ('state', 'fae-dt-sns-v1.' + marker)
        elif malformed == 'legacy_state':
            params[0] = ('state', 'fae-dt-v1.' + 'a' * 43)
        elif malformed == 'duplicate_state':
            params.append(('state', marker))
        elif malformed == 'duplicate_code':
            params.append(('code', marker))
        elif malformed == 'unknown':
            params.append(('unknown', marker))
        else:
            params.append(('unknown', marker * 300))
        with caplog.at_level(logging.WARNING, logger='app.control_plane.external_browser_state'):
            response = client.get(CALLBACK, params=params, headers={'accept': 'text/html'}, follow_redirects=False)
        assert response.status_code == 401
        secure(response)
        diagnostics = [record.getMessage() for record in caplog.records if record.name == 'app.control_plane.external_browser_state']
        assert len(diagnostics) == 1
        assert diagnostics[0] in {
            'external_dingtalk_auth_rejected reason=invalid_state_shape',
            'external_dingtalk_auth_rejected reason=invalid_callback_shape',
        }
        for private in [marker, state, nonce, 'Private name']:
            assert private not in response.text and private not in '\n'.join(diagnostics)
        assert not calls and auth.provider_calls == 0
        assert complete(client, state).status_code == 302


@pytest.mark.parametrize('state', [None, '', 'fae-dt-v1.' + 'a' * 43, 'fae-dt-sns-v1.' + 'a' * 31,
                                   'fae-dt-sns-v1.' + 'a' * 129, 'fae-dt-sns-v1.' + '/' * 43,
                                   'fae-dt-sns-v1.' + 'a' * 43 + '\n'])
def test_invalid_state_cannot_select_a_cookie_namespace(state):
    from app.control_plane.external_browser_state import nonce_cookie_name
    assert nonce_cookie_name(state) is None


@pytest.mark.parametrize('size', [32, 43, 128])
def test_state_cookie_namespace_is_fixed_length_opaque_and_deterministic(size):
    from app.control_plane.external_browser_state import nonce_cookie_name
    state = 'fae-dt-sns-v1.' + 'a' * size
    assert nonce_cookie_name(state) == cookie_name(state)
    assert len(nonce_cookie_name(state)) == len(COOKIE_PREFIX) + 64


@pytest.mark.postgres
def test_broker_start_exposes_typed_state_without_revealing_secrets_in_repr(browser_app):
    _, _, _, broker, _ = browser_app
    started = broker.begin_auth()
    assert started.state == parse_qs(urlsplit(started.authorization_url).query)['state'][0]
    assert started.state not in repr(started)
    assert started.browser_nonce not in repr(started)
