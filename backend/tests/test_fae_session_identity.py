from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import psycopg
from test_control_plane_migration import control_database  # noqa: F401
from test_external_dingtalk_integration import service, provision  # noqa: F401

from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from app.observability.models import Page, SessionDetail, SessionSummary, TurnDetail, SessionFilters
from app.observability.repository import ObservabilityReadError

NOW = datetime.now(UTC)
SUBJECT = UUID('00000000-0000-4000-8000-000000000123')


def summary(subject_type='partner_operator', **changes):
    return SessionSummary(
        session_key='fae:verified', agent_id='ai-fae-agent', source_kind='fae',
        channel='web', created_at=NOW, last_active_at=NOW, turn_count=1,
        feedback_count=0, review_count=0, freshness='fresh',
        verified_subject={'subject_id': SUBJECT, 'subject_type': subject_type}
        if subject_type else None,
        **changes,
    )


class Rows:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def read(self, session_id, subject_ids):
        self.calls.append((session_id, subject_ids))
        return self.rows


def codec():
    return ContentCodec(IdentityKeyring(1, 'platform-content-encryption', {1: b'c' * 32}))


def resolver(rows):
    from app.observability.identity import FaeSessionIdentity
    return FaeSessionIdentity(rows, content_codec=codec())


@pytest.mark.parametrize('subject_type', ['enterprise_member', 'partner_operator'])
def test_resolves_only_typed_authoritative_subject_in_list_and_turns(subject_type):
    sealed = codec().seal_json(f'agent-subject-display:{SUBJECT}', {'display_name': '外部测试员'})
    rows = Rows([{
        'subject_id': SUBJECT, 'subject_type': subject_type,
        'display_name': '内部测试员' if subject_type == 'enterprise_member' else None,
        'display_name_ciphertext': sealed.ciphertext if subject_type == 'partner_operator' else None,
        'display_name_key_version': sealed.key_version if subject_type == 'partner_operator' else None,
    }])
    context = SimpleNamespace(session_id=uuid4())
    item = summary(subject_type)
    detail = SessionDetail(**item.model_dump(), turns=[TurnDetail(
        turn_key='turn:1', session_key=item.session_key, agent_id=item.agent_id,
        source_kind='fae', turn_index=1, question='test', answer='test', created_at=NOW,
        verified_subject=item.verified_subject,
    )])
    reader = resolver(rows)
    page = reader.hydrate(Page(items=[item], total=1, limit=50, offset=0), context)
    result = reader.hydrate(detail, context)
    expected = '内部测试员' if subject_type == 'enterprise_member' else '外部测试员'
    assert page.items[0].primary_sender_name == expected
    assert result.primary_sender_name == expected
    assert result.turns[0].sender_name == expected
    assert result.sender_identity_status == 'name_only'
    assert rows.calls == [(context.session_id, (SUBJECT,))] * 2
    assert item.primary_sender_name is None  # no shared/cached object mutation


def test_legacy_never_gains_claimed_name_or_triggers_identity_lookup():
    rows = Rows([])
    result = resolver(rows).hydrate(
        Page(items=[summary(None, primary_sender_name='metadata nickname')], total=1, limit=50, offset=0),
        SimpleNamespace(session_id=uuid4()),
    )
    assert result.items[0].primary_sender_name is None
    assert result.items[0].sender_identity_status == 'unavailable'
    assert rows.calls == []


def test_unknown_and_mismatched_subject_do_not_gain_names():
    rows = Rows([{'subject_id': SUBJECT, 'subject_type': 'enterprise_member', 'display_name': 'wrong type'}])
    result = resolver(rows).hydrate(summary(), SimpleNamespace(session_id=uuid4()))
    assert result.primary_sender_name is None
    assert result.verified_subject.subject_id == SUBJECT


def test_decryption_failure_is_explicit_and_does_not_leak_protected_values():
    rows = Rows([{'subject_id': SUBJECT, 'subject_type': 'partner_operator', 'display_name': None,
                 'display_name_ciphertext': b'protected-value', 'display_name_key_version': 1}])
    with pytest.raises(ObservabilityReadError, match='session identity unavailable') as error:
        resolver(rows).hydrate(summary(), SimpleNamespace(session_id=uuid4()))
    assert 'protected-value' not in str(error.value)


def test_other_agent_source_is_not_identity_directory_input():
    item = summary().model_copy(update={'agent_id': 'unsupported-agent'})
    rows = Rows([])
    assert resolver(rows).hydrate(item, SimpleNamespace(session_id=uuid4())) == item
    assert rows.calls == []


def test_internal_only_identity_configuration_still_starts_without_partner_content_key(tmp_path):
    from app.main import build_fae_session_identity
    path = tmp_path / 'control-url'
    path.write_text('postgresql://platform_control_app:test@localhost/agent_platform_control')
    path.chmod(0o600)
    reader = build_fae_session_identity(SimpleNamespace(
        control_plane=SimpleNamespace(control_database_url_file=str(path)),
        content_encryption_keyring_file='',
    ))
    assert reader.content_codec is None


def test_missing_external_name_key_reports_unavailable_instead_of_attribute_error():
    from app.observability.identity import FaeSessionIdentity
    sealed = codec().seal_json(f'agent-subject-display:{SUBJECT}', {'display_name': '外部测试员'})
    rows = Rows([{'subject_id': SUBJECT, 'subject_type': 'partner_operator', 'display_name': None,
                 'display_name_ciphertext': sealed.ciphertext, 'display_name_key_version': sealed.key_version}])
    with pytest.raises(ObservabilityReadError, match='session identity unavailable'):
        FaeSessionIdentity(rows, content_codec=None).hydrate(summary(), SimpleNamespace(session_id=uuid4()))


def test_owned_auth_app_initializes_identity_reader_without_optional_content_key(tmp_path, monkeypatch):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from app.config import load_config
    from app.control_plane.models import IdentityMode
    from app.control_plane.authorization import AuthorizationService
    from app.main import create_app
    from test_dingtalk_auth_api import FakeAuth
    from test_observability_api import StaticObservabilityService
    import json

    monkeypatch.setenv('PLATFORM_IDENTITY_MODE', 'disabled')
    monkeypatch.setenv('PLATFORM_EXECUTION_RELAY_ENABLED', '0')
    monkeypatch.setenv('PLATFORM_PARTNER_PROVIDER_KIND', '')
    monkeypatch.setenv('PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE', '')
    config = load_config()
    secret = tmp_path / 'control-url'
    secret.write_text('postgresql://platform_control_app:test@localhost/agent_platform_control'); secret.chmod(0o600)
    config = replace(config, control_plane=replace(config.control_plane,
        mode=IdentityMode.PRODUCTION, control_database_url_file=str(secret)))
    monkeypatch.setattr('app.main.load_config', lambda: config)
    auth = FakeAuth()
    monkeypatch.setattr('app.main.build_identity_auth', lambda _: auth)
    registry = tmp_path / 'registry.yaml'; registry.write_text('version: 1\nagents: []\n')
    contract = tmp_path / 'contract.json'; contract.write_text(json.dumps({'bots': []}))
    app = create_app(registry_path=str(registry), cluster_contract_path=str(contract), start_poller=False,
                     agent_launch_service=object(), organization_directory=object(),
                     observability_service=StaticObservabilityService(),
                     identity_authorization=AuthorizationService(SimpleNamespace(permits=lambda *_: False)))
    assert app.state.fae_session_identity.content_codec is None
    class Sessions:
        subject_type = 'enterprise_member'

        async def list_sessions(self, *args):
            return Page(items=[summary(self.subject_type)], total=1, limit=50, offset=0)

    sessions = Sessions()
    app.state.observability_service = sessions
    rows = Rows([{'subject_id': SUBJECT, 'subject_type': 'enterprise_member', 'display_name': '内部测试员'}])
    app.state.fae_session_identity.repository = rows
    client = TestClient(app)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    assert client.get('/api/sessions?agent_id=ai-fae-agent').json()['items'][0]['primary_sender_name'] == '内部测试员'
    sessions.subject_type = 'partner_operator'
    rows.rows = [{'subject_id': SUBJECT, 'subject_type': 'partner_operator', 'display_name': None}]
    assert client.get('/api/sessions?agent_id=ai-fae-agent').status_code == 503


def test_session_api_hydrates_list_detail_and_handles_failure():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from app.observability.routes import router

    class Sessions:
        async def list_sessions(self, *args):
            return Page(items=[summary('enterprise_member')], total=1, limit=50, offset=0)

        async def get_session(self, key):
            return SessionDetail(**summary('enterprise_member').model_dump(), turns=[])

    app = FastAPI()
    app.include_router(router)
    app.state.observability_service = Sessions()
    rows = Rows([{'subject_id': SUBJECT, 'subject_type': 'enterprise_member', 'display_name': '接口测试员'}])
    app.state.fae_session_identity = resolver(rows)

    @app.middleware('http')
    async def test_context(request: Request, call_next):
        request.state.auth_context = SimpleNamespace(session_id=uuid4())
        return await call_next(request)

    with TestClient(app) as client:
        assert client.get('/api/sessions?agent_id=ai-fae-agent').json()['items'][0]['primary_sender_name'] == '接口测试员'
        assert client.get('/api/sessions/fae:verified').json()['primary_sender_name'] == '接口测试员'
        assert len(rows.calls) == 2
        rows.rows = [{'subject_id': SUBJECT, 'subject_type': 'partner_operator', 'display_name': None,
                      'display_name_ciphertext': b'bad', 'display_name_key_version': 1}]
        # A stored reference with the wrong type never becomes another user.
        assert client.get('/api/sessions/fae:verified').json()['primary_sender_name'] is None


def test_session_api_without_identity_reader_never_trusts_replica_nickname():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.observability.routes import router

    class Sessions:
        async def get_session(self, _key):
            item = summary(primary_sender_name='untrusted nickname')
            return SessionDetail(**item.model_dump(), turns=[TurnDetail(
                turn_key='turn:1', session_key=item.session_key, agent_id=item.agent_id,
                source_kind='fae', turn_index=1, question='test', answer='test', created_at=NOW,
                sender_name='untrusted turn nickname', verified_subject=item.verified_subject,
            )])

    app = FastAPI(); app.include_router(router)
    app.state.observability_service = Sessions()
    result = TestClient(app).get('/api/sessions/fae:verified').json()
    assert result['primary_sender_name'] is None
    assert result['turns'][0]['sender_name'] is None
    assert result['verified_subject']['subject_id'] == str(SUBJECT)


@pytest.mark.postgres
def test_subject_projection_checks_real_session_role_scope_and_bounds(control_database):
    env = control_database['environments']['production']
    actor, session = uuid4(), uuid4()
    with psycopg.connect(env['admin']) as connection:
        connection.execute("insert into platform_control.internal_users(internal_user_id,role,display_name,status) values (%s,'management_viewer','测试观察者','active')", (actor,))
        connection.execute("insert into platform_control.agent_access_subjects(subject_id,subject_type,status) values (%s,'enterprise_member','active')", (actor,))
        connection.execute("insert into platform_control.enterprise_subject_links(subject_id,internal_user_id) values (%s,%s)", (actor, actor))
        connection.execute("insert into platform_control.web_sessions(session_id,internal_user_id,token_hash,token_hash_key_version,csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) values (%s,%s,%s,1,%s,1,now()+interval '1 hour',now()+interval '2 hours')", (session, actor, uuid4().bytes * 2, uuid4().bytes * 2))
    sql = 'select * from platform_control.read_fae_session_subjects_v126(%s,%s::uuid[])'
    app_url = env['urls']['platform_control_app']
    with psycopg.connect(app_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(sql, (session, [actor]))

    with psycopg.connect(env['admin']) as connection:
        connection.execute("insert into platform_control.observation_grants(observation_grant_id,viewer_internal_user_id,agent_id,created_by) values (%s,%s,'ai-fae-agent',%s)", (uuid4(), actor, actor))
    with psycopg.connect(app_url, autocommit=True) as connection:
        row = connection.execute(sql, (session, [actor])).fetchone()
        assert row[:3] == (actor, 'enterprise_member', '测试观察者')
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(sql, (session, [actor] * 101))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(sql, (uuid4(), [actor]))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute('select * from platform_control.agent_access_subjects')
    with psycopg.connect(env['admin']) as connection:
        connection.execute('update platform_control.web_sessions set revoked_at=now() where session_id=%s', (session,))
    with psycopg.connect(app_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(sql, (session, [actor]))


@pytest.mark.postgres
@pytest.mark.parametrize('role,grant_agent,revoked_grant,expired,inactive,allowed', [
    ('platform_owner', None, False, False, False, True),
    ('member', 'ai-fae-agent', False, False, False, False),
    ('management_viewer', 'ai-daq-fae-agent', False, False, False, False),
    ('management_viewer', 'ai-fae-agent', True, False, False, False),
    ('management_viewer', 'ai-fae-agent', False, True, False, False),
    ('management_viewer', 'ai-fae-agent', False, False, True, False),
])
def test_subject_projection_permission_matrix(control_database, role, grant_agent, revoked_grant, expired, inactive, allowed):
    env = control_database['environments']['production']
    actor, session = uuid4(), uuid4()
    with psycopg.connect(env['admin']) as connection:
        connection.execute("insert into platform_control.internal_users(internal_user_id,role,display_name,status) values (%s,%s,'权限测试员',%s)", (actor, role, 'inactive' if inactive else 'active'))
        connection.execute("insert into platform_control.web_sessions(session_id,internal_user_id,token_hash,token_hash_key_version,csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) values (%s,%s,%s,1,%s,1,%s,%s)", (session, actor, uuid4().bytes * 2, uuid4().bytes * 2,
            NOW + timedelta(hours=-2 if expired else 1),
            NOW + timedelta(hours=-1 if expired else 2)))
        if grant_agent:
            connection.execute("insert into platform_control.observation_grants(observation_grant_id,viewer_internal_user_id,agent_id,created_by,revoked_at) values (%s,%s,%s,%s,%s)", (uuid4(), actor, grant_agent, actor, NOW if revoked_grant else None))
    with psycopg.connect(env['urls']['platform_control_app'], autocommit=True) as connection:
        if allowed:
            assert connection.execute('select * from platform_control.read_fae_session_subjects_v126(%s,%s::uuid[])', (session, [SUBJECT])).fetchall() == []
        else:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute('select * from platform_control.read_fae_session_subjects_v126(%s,%s::uuid[])', (session, [SUBJECT]))


@pytest.mark.postgres
def test_external_provision_to_signed_persisted_replica_and_authorized_api(service, control_database, tmp_path):
    """Provider verification boundary is synthetic; identity, crypto, DB and API are real."""
    import io
    import asyncio
    from datetime import timedelta
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from app.cloud_replica.crypto import BatchSigner, BatchVerifier, FieldCipher
    from app.cloud_replica.exporter import ReplicaExporter
    from app.cloud_replica.protocol import BatchLimits
    from app.cloud_replica.store import ReplicaStore, import_verified_stream
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.cloud_replica.sanitize import SanitizationPolicy
    from app.observability.identity import FaeSubjectRepository
    from app.observability.routes import router
    from app.observability.service import ObservabilityService
    from test_fae_verified_subject_replica import _raw, NOW as EXPORTED_AT
    from test_web_session_security import _seed_current_bound_member, _db_repository, Provider
    from app.control_plane.auth import DingTalkWebAuth
    from app.control_plane.middleware import IdentitySecurityMiddleware
    from app.control_plane.authorization import AuthorizationService

    subject = provision(service)  # real external mapping, grant, encrypted name and audit
    env = control_database['environments']['production']
    actor, _, _ = _seed_current_bound_member(env)
    with psycopg.connect(env['admin']) as connection:
        connection.execute("update platform_control.internal_users set role='platform_admin' where internal_user_id=%s", (actor,))
    auth_repository = _db_repository(env)
    provider = Provider(user_id=actor)
    auth = DingTalkWebAuth(
        repository=auth_repository, secrets=auth_repository.secrets,
        qr_login=provider.complete, in_client_login=provider.complete,
        environment='production', route_prefix='/', public_base_url='https://agent.example.test', app_key='test-app',
    )
    started = auth.start_qr('/admin/')
    login = asyncio.run(auth.complete_qr(started.state, 'verified-provider-code'))
    session = auth.authenticate(login.cookie_token)[0].session_id
    private = Ed25519PrivateKey.generate()
    raw = _raw(subject_id=str(subject.subject_id))
    exporter = ReplicaExporter(
        source=SimpleNamespace(fetch_sessions=lambda **_: (raw,)), policy=SanitizationPolicy(),
        identity_key=b'i' * 32, signer=BatchSigner(private), source_instance_id='identity-e2e',
        state_path=tmp_path / 'state', queue_dir=tmp_path / 'queue', clock=lambda: EXPORTED_AT,
        include_verified_subject=True,
    )
    batch = exporter.export_batch(after=EXPORTED_AT - timedelta(days=1), through=EXPORTED_AT, limit=100)
    payload = batch.batch_path.read_bytes()
    assert '外部用户'.encode() not in payload
    assert b'dingtalk_external' not in payload
    cipher = FieldCipher(b'e' * 32)
    store = ReplicaStore(env['admin'], cipher=cipher)
    store.migrate()
    imported = import_verified_stream(io.BytesIO(payload), verifier=BatchVerifier(private.public_key()), limits=BatchLimits(), store=store)
    assert imported.status == 'imported'
    assert import_verified_stream(io.BytesIO(payload), verifier=BatchVerifier(private.public_key()), limits=BatchLimits(), store=store).status == 'replayed'
    repository = ReplicaObservabilityRepository(env['admin'], cipher=cipher)
    page = repository.list_sessions(SessionFilters(agent_id='ai-fae-agent'), 50, 0)
    assert len(page.items) == 1
    with psycopg.connect(env['admin']) as connection:
        encrypted = bytes(connection.execute('select display_payload from platform_replica.sessions').fetchone()[0])
    assert str(subject.subject_id).encode() not in encrypted
    assert '外部用户'.encode() not in encrypted
    app = FastAPI(); app.include_router(router)
    app.state.observability_service = ObservabilityService(repository)
    app.state.fae_session_identity = resolver(FaeSubjectRepository(env['urls']['platform_control_app']))

    app.add_middleware(IdentitySecurityMiddleware, auth=auth, public_assets=frozenset(),
                       authorization=AuthorizationService(SimpleNamespace(permits=lambda *_: False)),
                       routes=tuple(app.router.routes))

    with TestClient(app, base_url='https://agent.example.test') as client:
        assert client.get('/api/sessions?agent_id=ai-fae-agent').status_code == 401
        client.cookies.set(auth.cookie_name, login.cookie_token)
        listed = client.get('/api/sessions?agent_id=ai-fae-agent')
        assert listed.status_code == 200, listed.text
        item = listed.json()['items'][0]
        assert item['primary_sender_name'] == '外部用户'
        detail = client.get('/api/sessions/' + item['session_key']).json()
        assert detail['primary_sender_name'] == detail['turns'][0]['sender_name'] == '外部用户'
        assert detail['verified_subject']['subject_id'] == str(subject.subject_id)
        with psycopg.connect(env['admin']) as connection:
            connection.execute('update platform_control.web_sessions set revoked_at=now() where session_id=%s', (session,))
        assert client.get('/api/sessions?agent_id=ai-fae-agent').status_code == 401
