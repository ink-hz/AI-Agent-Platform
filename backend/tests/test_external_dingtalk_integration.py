from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_control_plane_migration import control_database  # noqa: F401
from test_dingtalk_auth_api import FakeAuth

from app.control_plane.auth import AuthSecrets
from app.control_plane.crypto import IdentityKeyring
from app.control_plane.dingtalk_external_provider import DingTalkExternalIdentityProvider
from app.control_plane.partner_identity_crypto import PartnerProviderIdentityCodec
from app.control_plane.partner_models import PartnerIdentityError, VerifiedProviderSubject
from app.control_plane.partner_provider import PartnerAuthenticationBroker
from app.control_plane.partner_repository import PartnerRepository
from app.control_plane.partner_service import PartnerService
from app.control_plane.routes_auth import build_auth_router
from app.control_plane.routes_partner import build_partner_auth_router
from app.execution_relay.content_crypto import ContentCodec


@pytest.fixture
def service(control_database):
    environment = control_database['environments']['production']
    codec = PartnerProviderIdentityCodec(
        IdentityKeyring(1, 'partner-provider-encryption', {1: b'e' * 32}),
        IdentityKeyring(1, 'partner-provider-lookup-hmac', {1: b'h' * 32}, transition_versions=(1,)),
    )
    result = PartnerService(
        PartnerRepository(environment['urls']['platform_control_app'], identity_codec=codec),
        content_codec=ContentCodec(IdentityKeyring(1, 'platform-content-encryption', {1: b'c' * 32})),
    )
    return result


def attempt(service):
    state, nonce = uuid4().bytes * 2, uuid4().bytes * 2
    service.create_external_login_attempt(state_digest=state, state_key_version=1, browser_nonce_digest=nonce)
    attempt_id = service.consume_external_login_attempt(state_digest=state, state_key_version=1, browser_nonce_digest=nonce)
    return attempt_id, nonce


def provision(service, subject=None):
    attempt_id, nonce = attempt(service)
    verified = VerifiedProviderSubject(provider_kind='dingtalk_external', provider_subject=subject or 'client:' + str(uuid4()), display_name='外部用户', verified_at=datetime.now(UTC))
    return service.provision_external_identity(verified, login_attempt_id=attempt_id, browser_nonce_digest=nonce)


@pytest.mark.postgres
def test_atomic_auto_access_and_encrypted_identity(service, control_database):
    assert hasattr(service, 'provision_external_identity'), 'automatic external provisioning is missing'
    stable = 'client:' + str(uuid4())
    first = provision(service, stable)
    second = provision(service, stable)
    assert first.subject_id == second.subject_id
    assert service.decide_fae_access(first.subject_id).allowed
    assert service.external_verified_at(stable) is not None
    env = control_database['environments']['production']
    with psycopg.connect(env['admin']) as conn:
        row = conn.execute("select provider_subject_ciphertext from platform_control.partner_provider_identities where partner_operator_id=%s", (first.partner_operator_id,)).fetchone()
        assert stable.encode() not in bytes(row[0])
        assert conn.execute("select agent_id,created_by_internal_user_id,grant_origin from platform_control.partner_agent_grants where subject_id=%s", (first.subject_id,)).fetchall() == [('ai-fae-agent', None, 'external_oauth')]
        assert conn.execute("select actor_internal_user_id from platform_control.audit_events where target_internal_id=%s and event_type='fae_external_authenticated'", (str(first.subject_id),)).fetchall() == [(None,), (None,)]


@pytest.mark.postgres
def test_attempt_browser_nonce_replay_expiry_and_single_provision(service, control_database):
    assert hasattr(service, 'create_external_login_attempt'), 'browser-bound external attempts missing'
    state, nonce = b's' * 32, b'n' * 32
    service.create_external_login_attempt(state_digest=state, state_key_version=1, browser_nonce_digest=nonce)
    with pytest.raises(PartnerIdentityError, match='partner_auth_state_invalid'):
        service.consume_external_login_attempt(state_digest=state, state_key_version=1, browser_nonce_digest=b'x' * 32)
    token = service.consume_external_login_attempt(state_digest=state, state_key_version=1, browser_nonce_digest=nonce)
    with pytest.raises(PartnerIdentityError, match='partner_auth_state_replay'):
        service.consume_external_login_attempt(state_digest=state, state_key_version=1, browser_nonce_digest=nonce)
    verified = VerifiedProviderSubject(provider_kind='dingtalk_external', provider_subject='client:' + str(uuid4()), display_name='用户', verified_at=datetime.now(UTC))
    service.provision_external_identity(verified, login_attempt_id=token, browser_nonce_digest=nonce)
    with pytest.raises(PartnerIdentityError, match='partner_auth_state_invalid'):
        service.provision_external_identity(verified, login_attempt_id=token, browser_nonce_digest=nonce)
    env = control_database['environments']['production']
    with psycopg.connect(env['admin']) as conn:
        assert conn.execute("select expires_at-created_at from platform_control.partner_login_attempts where login_attempt_id=%s", (token,)).fetchone()[0] == timedelta(minutes=5)


@pytest.mark.postgres
def test_concurrent_login_reuses_subject(service):
    assert hasattr(service, 'provision_external_identity'), 'automatic external provisioning is missing'
    stable = 'client:' + str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: provision(service, stable), range(4)))
    assert len({value.subject_id for value in results}) == 1


@pytest.mark.postgres
@pytest.mark.parametrize('revocation', ['identity', 'subject', 'operator', 'realm', 'grant'])
def test_login_never_restores_revoked_access(service, control_database, revocation):
    assert hasattr(service, 'provision_external_identity'), 'automatic external provisioning is missing'
    stable = 'client:' + str(uuid4())
    identity = provision(service, stable)
    env = control_database['environments']['production']
    statements = {
        'identity': ('update platform_control.partner_provider_identities set revoked_at=now() where partner_operator_id=%s', identity.partner_operator_id),
        'subject': ("update platform_control.agent_access_subjects set status='suspended' where subject_id=%s", identity.subject_id),
        'operator': ("update platform_control.partner_operators set status='suspended' where partner_operator_id=%s", identity.partner_operator_id),
        'realm': ("update platform_control.partner_organizations set status='suspended' where partner_organization_id=%s", identity.partner_organization_id),
        'grant': ("update platform_control.partner_agent_grants set revoked_at=now(),revoked_by_internal_user_id=(select internal_user_id from platform_control.internal_users where role='platform_owner' limit 1) where subject_id=%s", identity.subject_id),
    }
    sql, key = statements[revocation]
    with psycopg.connect(env['admin']) as conn:
        if revocation == 'grant':
            from test_partner_operator_migration import _seed_owner
            _seed_owner(conn)
        conn.execute(sql, (key,))
    try:
        with pytest.raises(PartnerIdentityError):
            provision(service, stable)
    finally:
        if revocation == 'realm':
            with psycopg.connect(env['admin']) as conn:
                conn.execute("update platform_control.partner_organizations set status='active' where partner_organization_id=%s", (key,))


@pytest.mark.postgres
def test_http_external_authcode_nonce_and_no_platform_session(service):
    calls = []
    def transport(request):
        calls.append(request.url.path)
        if request.url.path.endswith('userAccessToken'):
            return httpx.Response(200, json={'accessToken': 'private-token'})
        return httpx.Response(200, json={'unionId': 'union-' + str(stable), 'nick': '外部昵称'})
    stable = uuid4()
    provider = DingTalkExternalIdentityProvider(client_id='test-app', client_secret='secret', redirect_uri='https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback', verified_at_for_subject=lambda _: None, transport=httpx.MockTransport(transport))
    broker = PartnerAuthenticationBroker(provider, service, state_secrets=AuthSecrets(b's' * 32, key_version=1))
    app = FastAPI()
    auth = FakeAuth()
    launches = []
    from app.control_plane.agent_launch import AgentLaunchRepository, AgentLaunchService
    real_launch = AgentLaunchService(
        repository=AgentLaunchRepository(service.repository._control_database_url),
        secrets=broker.state_secrets, authorization=None,
        partner_service=service, partner_provider=provider,
    )
    class Launch:
        async def issue_partner(self, subject_id):
            launches.append(subject_id)
            return await real_launch.issue_partner(subject_id)
    app.state.partner_auth_broker = broker
    app.state.agent_launch_service = Launch()
    app.include_router(build_partner_auth_router(broker, agent_launch_service=app.state.agent_launch_service))
    app.include_router(build_auth_router(auth, static_dir='static', public_assets=frozenset(), detailed_health=lambda _: {}))
    with TestClient(app, base_url='https://agent.orbbec.com.cn') as client:
        started = client.get('/partner-auth/start', follow_redirects=False)
        state = parse_qs(urlsplit(started.headers['location']).query)['state'][0]
        assert state.startswith('fae-dt-v1.'), 'external state must use reserved namespace'
        cookie = started.headers['set-cookie']
        assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
        callback = '/api/v1/auth/dingtalk/callback'
        with TestClient(app, base_url='https://agent.orbbec.com.cn') as stranger:
            assert stranger.get(callback, params={'state': state, 'authCode': 'code'}, follow_redirects=False).status_code == 401
        assert calls == []
        duplicate = client.get(callback + '?state=' + state + '&authCode=x&authCode=y', follow_redirects=False)
        assert duplicate.status_code == 401 and not calls
        client.cookies.set(auth.cookie_name, 'valid-cookie')
        completed = client.get(callback, params={'state': state, 'authCode': 'code'}, follow_redirects=False)
        assert completed.status_code == 302, completed.text
        assert completed.headers['location'].startswith('https://fae.orbbec.com.cn/')
        assert len(launches) == 1
        launch_code = parse_qs(urlsplit(completed.headers['location']).fragment)['partner_launch'][0]
        binding = asyncio.run(real_launch.exchange(launch_code))
        assert binding.subject_id == launches[0]
        assert binding.subject_type == 'partner_operator'
        assert binding.display_name == '外部昵称'
        assert auth.provider_calls == 0
        assert auth.cookie_name + '=' not in completed.headers.get('set-cookie', '')
        assert 'union-' not in completed.text
        assert client.get(callback, params={'state': state, 'authCode': 'code'}, follow_redirects=False).status_code == 401
        assert len(calls) == 2
    asyncio.run(provider.aclose())


def test_factory_builds_partner_service_without_brain_or_relay(monkeypatch, tmp_path):
    import base64
    import json
    import app.main as main
    from app.config import load_config
    assert callable(getattr(main, 'build_partner_service', None)), 'production partner service factory missing'
    monkeypatch.setenv('PLATFORM_EXECUTION_RELAY_ENABLED', '0')
    monkeypatch.setenv('PLATFORM_AGENT_BRAIN_ENABLED', '0')
    monkeypatch.setenv('PLATFORM_PARTNER_PROVIDER_KIND', 'dingtalk_external')
    paths = {}
    for name, purpose, key in [('encryption', 'partner-provider-encryption', b'e'), ('hmac', 'partner-provider-lookup-hmac', b'h'), ('content', 'platform-content-encryption', b'c')]:
        path = tmp_path / name
        payload = {'purpose': purpose, 'active_version': 1, 'keys': {'1': base64.b64encode(key * 32).decode()}}
        if name == 'hmac':
            payload['transition_versions'] = [1]
        path.write_text(json.dumps(payload)); path.chmod(0o600); paths[name] = str(path)
    monkeypatch.setenv('PLATFORM_PARTNER_ENCRYPTION_KEYRING_FILE', paths['encryption'])
    monkeypatch.setenv('PLATFORM_PARTNER_HMAC_KEYRING_FILE', paths['hmac'])
    monkeypatch.setenv('PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE', paths['content'])
    config = load_config()
    assert config.content_encryption_keyring_file == paths['content']
    dsn = tmp_path / 'dsn'
    dsn.write_text('postgresql://platform_control_app:password@127.0.0.1:5432/agent_platform_control'); dsn.chmod(0o600)
    from dataclasses import replace
    config = replace(config, control_plane=replace(config.control_plane, control_database_url_file=str(dsn)))
    service = main.build_partner_service(config)
    assert isinstance(service, PartnerService)
    assert not config.agent_brain_enabled and not config.execution_relay_enabled
    secret = tmp_path / "oauth-secret"
    secret.write_text("test-secret"); secret.chmod(0o600)
    external_secret = tmp_path / "external-oauth-secret"
    external_secret.write_text("external-test-secret"); external_secret.chmod(0o600)
    monkeypatch.setenv("PLATFORM_PARTNER_DINGTALK_CLIENT_ID", "external-test-app")
    monkeypatch.setenv("PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET_FILE", str(external_secret))
    control = replace(config.control_plane, dingtalk_app_key="test-app", dingtalk_app_secret_file=str(secret))
    original_load = main.load_config
    monkeypatch.setattr(main, "load_config", lambda **kwargs: replace(original_load(**kwargs), control_plane=control))
    from test_dingtalk_auth_api import _app
    auth = FakeAuth(); auth.secrets = AuthSecrets(b"s" * 32, key_version=1)
    app = _app(tmp_path, monkeypatch, auth)
    assert isinstance(app.state.partner_service, PartnerService)
    assert isinstance(app.state.partner_provider, DingTalkExternalIdentityProvider)
    assert app.state.partner_auth_broker.service is app.state.partner_service
    query = parse_qs(urlsplit(app.state.partner_provider.begin_auth("factory-state")).query)
    assert query["client_id"] == ["external-test-app"]
    with TestClient(app, base_url="https://agent.example.test") as client:
        assert client.get("/partner-auth/start?return_path=/", follow_redirects=False).status_code == 400


@pytest.mark.postgres
def test_system_provision_rolls_back_if_audit_fails(service, control_database):
    env = control_database['environments']['production']
    stable = 'client:' + str(uuid4())
    with psycopg.connect(env['admin']) as admin:
        before = admin.execute('select count(*) from platform_control.partner_provider_identities').fetchone()[0]
        admin.execute("""create function platform_control.reject_external_audit_test() returns trigger language plpgsql as $$ begin if new.event_type='fae_external_authenticated' then raise exception 'audit unavailable'; end if; return new; end $$""")
        admin.execute('create trigger reject_external_audit_test before insert on platform_control.audit_events for each row execute function platform_control.reject_external_audit_test()')
    try:
        with pytest.raises(PartnerIdentityError):
            provision(service, stable)
        with psycopg.connect(env['admin']) as admin:
            assert admin.execute('select count(*) from platform_control.partner_provider_identities').fetchone()[0] == before
        assert service.external_verified_at(stable) is None
    finally:
        with psycopg.connect(env['admin']) as admin:
            admin.execute('drop trigger reject_external_audit_test on platform_control.audit_events')
            admin.execute('drop function platform_control.reject_external_audit_test()')


@pytest.mark.postgres
def test_external_functions_only_app_and_no_table_mutation(service, control_database):
    for env in control_database['environments'].values():
        with psycopg.connect(env['admin']) as admin:
            functions = admin.execute("select oid::regprocedure::text,prosecdef,proconfig from pg_proc where pronamespace='platform_control'::regnamespace and proname in ('create_external_dingtalk_attempt_v123','consume_external_dingtalk_attempt_v123','provision_external_dingtalk_v123','read_external_dingtalk_identity_v123')").fetchall()
            assert len(functions) == 4
            for signature, security_definer, settings in functions:
                assert security_definer and 'search_path=pg_catalog, platform_control' in settings
                for role in env['roles']:
                    actual = admin.execute('select has_function_privilege(%s,%s,\'execute\')', (role, signature)).fetchone()[0]
                    assert actual == (role == env['roles'][1]), (role, signature)
        with psycopg.connect(env['urls'][env['roles'][1]]) as app:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute("update platform_control.partner_agent_grants set grant_origin='external_oauth'")


@pytest.mark.postgres
def test_expired_attempt_and_stale_consumption_are_rejected(service, control_database):
    env = control_database['environments']['production']
    state, nonce = uuid4().bytes * 2, uuid4().bytes * 2
    created = service.create_external_login_attempt(state_digest=state, state_key_version=1, browser_nonce_digest=nonce)
    with psycopg.connect(env['admin']) as admin:
        admin.execute("update platform_control.partner_login_attempts set created_at=now()-interval '6 minutes',expires_at=now()-interval '1 minute' where login_attempt_id=%s", (created,))
    with pytest.raises(PartnerIdentityError, match='partner_auth_state_expired'):
        service.consume_external_login_attempt(state_digest=state, state_key_version=1, browser_nonce_digest=nonce)
    consumed, nonce = attempt(service)
    with psycopg.connect(env['admin']) as admin:
        admin.execute("update platform_control.partner_login_attempts set consumed_at=now()-interval '61 seconds' where login_attempt_id=%s", (consumed,))
    with pytest.raises(PartnerIdentityError, match='partner_auth_state_invalid'):
        service.provision_external_identity(VerifiedProviderSubject(provider_kind='dingtalk_external', provider_subject='client:' + str(uuid4()), display_name='用户', verified_at=datetime.now(UTC)), login_attempt_id=consumed, browser_nonce_digest=nonce)


@pytest.mark.postgres
def test_owner_identity_trace_is_audited_and_forbidden_to_nonowner(service, control_database):
    from test_partner_operator_migration import _seed_owner
    from app.control_plane.authorization import AuthorizationService
    from app.control_plane.models import AuthContext, Role
    from app.control_plane.routes_manage import authenticated_context, csrf_protection
    from app.control_plane.routes_partner import router
    stable = 'client:union-' + str(uuid4())
    identity = provision(service, stable)
    env = control_database['environments']['production']
    with psycopg.connect(env['admin']) as admin:
        owner = _seed_owner(admin)
    request_id = uuid4()
    app = FastAPI(); app.state.partner_service = service; app.include_router(router)
    context = AuthContext(owner, Role.PLATFORM_OWNER, uuid4(), False)
    app.dependency_overrides[authenticated_context] = lambda: context
    app.dependency_overrides[csrf_protection] = lambda: True
    path = f'/api/v1/manage/partners/subjects/{identity.subject_id}/external-identity-detail'
    route = '/api/v1/manage/partners/subjects/{subject_id}/external-identity-detail'
    assert AuthorizationService(None).decide(context, method='POST', route_template=route, agent_ids=()).allowed
    with TestClient(app) as client:
        response = client.post(path, json={'reason': 'trace identity', 'request_id': str(request_id)})
        assert response.status_code == 200, response.text
        assert response.json()['union_id'] == stable.split(':', 1)[1]
        assert response.json()['display_name'] == '外部用户'
        assert response.headers['cache-control'] == 'no-store'
        context = AuthContext(uuid4(), Role.PLATFORM_ADMIN, uuid4(), False)
        assert client.post(path, json={'reason': 'trace identity', 'request_id': str(uuid4())}).status_code == 403
    with psycopg.connect(env['admin']) as admin:
        assert admin.execute("select actor_internal_user_id from platform_control.audit_events where request_id=%s and event_type='fae_external_identity_read'", (request_id,)).fetchone()[0] == owner
    with pytest.raises(PartnerIdentityError, match='owner_required'):
        service.external_identity_detail(actor_id=uuid4(), subject_id=identity.subject_id, request_id=uuid4())


@pytest.mark.postgres
def test_provider_restart_uses_durable_eight_hour_lease(service, control_database):
    import hashlib
    stable = hashlib.sha256(b'test-app').hexdigest() + ':union-' + str(uuid4())
    identity = provision(service, stable)
    provider = DingTalkExternalIdentityProvider(
        client_id='test-app', client_secret='secret',
        redirect_uri='https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback',
        verified_at_for_subject=service.external_verified_at,
        transport=httpx.MockTransport(lambda _: pytest.fail('lease check must not contact provider')),
    )
    async def check():
        assert await provider.check_subject(stable) == 'active'
        env = control_database['environments']['production']
        with psycopg.connect(env['admin']) as admin:
            admin.execute("update platform_control.partner_provider_identities set verified_at=now()-interval '8 hours' where partner_operator_id=%s", (identity.partner_operator_id,))
        assert await provider.check_subject(stable) == 'inactive'
        await provider.aclose()
    asyncio.run(check())


def test_production_external_enable_still_requires_real_account_evidence(monkeypatch):
    from app.config import load_config
    monkeypatch.setenv('PLATFORM_ENVIRONMENT', 'production')
    monkeypatch.setenv('PLATFORM_PARTNER_IDENTITY_ENABLED', '1')
    monkeypatch.setenv('PLATFORM_PARTNER_PROVIDER_KIND', 'dingtalk_external')
    monkeypatch.delenv('PLATFORM_PARTNER_PROVIDER_RELEASE_FILE', raising=False)
    monkeypatch.delenv('PLATFORM_PARTNER_PROVIDER_RELEASE_SHA256', raising=False)
    with pytest.raises(ValueError, match='partner_provider_release_required'):
        load_config()


@pytest.mark.postgres
def test_existing_identity_in_other_realm_is_not_moved_or_granted(service, control_database):
    from test_partner_operator_migration import _seed_owner
    stable = 'client:' + str(uuid4())
    identity = provision(service, stable)
    env = control_database['environments']['production']
    with psycopg.connect(env['admin']) as admin:
        owner = _seed_owner(admin)
    realm = service.create_organization(actor_id=owner, display_name='Managed partner', reason='test other realm', request_id=uuid4())
    with psycopg.connect(env['admin']) as admin:
        admin.execute('update platform_control.partner_operators set partner_organization_id=%s where partner_operator_id=%s', (realm.partner_organization_id, identity.partner_operator_id))
    with pytest.raises(PartnerIdentityError, match='partner_identity_conflict'):
        provision(service, stable)
    with psycopg.connect(env['admin']) as admin:
        assert admin.execute('select partner_organization_id from platform_control.partner_operators where partner_operator_id=%s', (identity.partner_operator_id,)).fetchone()[0] == realm.partner_organization_id


@pytest.mark.postgres
def test_callback_rate_limit_precedes_provider_exchange(service):
    from contextlib import asynccontextmanager
    from ipaddress import ip_address
    from types import SimpleNamespace
    from app.control_plane.rate_limit import RateLimitExceeded
    from app.control_plane.routes_partner import finish_external_dingtalk_authentication
    from starlette.requests import Request
    calls = []
    provider = DingTalkExternalIdentityProvider(
        client_id='test-app', client_secret='secret',
        redirect_uri='https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback',
        verified_at_for_subject=service.external_verified_at,
        transport=httpx.MockTransport(lambda _: pytest.fail('rate limited exchange reached provider')),
    )
    class Limiter:
        def check_callback(self, ip):
            calls.append(ip)
            raise RateLimitExceeded(30)
        @asynccontextmanager
        async def provider_exchange(self):
            pytest.fail('exchange semaphore should not be acquired')
            yield
    app = FastAPI()
    app.state.partner_auth_broker = PartnerAuthenticationBroker(provider, service, state_secrets=AuthSecrets(b's' * 32, key_version=1))
    app.state.agent_launch_service = object()
    app.state.identity_auth = SimpleNamespace(rate_limiter=Limiter())
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/v1/auth/dingtalk/callback', 'query_string': b'state=fae-dt-v1.test&authCode=code', 'headers': [], 'app': app, 'state': {'edge_source': SimpleNamespace(ip=ip_address('192.0.2.1'))}})
    response = asyncio.run(finish_external_dingtalk_authentication(request))
    assert response.status_code == 429 and response.headers['retry-after'] == '30'
    assert calls == [ip_address('192.0.2.1')]
    asyncio.run(provider.aclose())
