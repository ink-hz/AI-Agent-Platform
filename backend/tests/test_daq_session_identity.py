"""DAQ owner → encrypted replica → authorized list/detail; no model calls."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest

from app.cloud_replica.models import VerifiedSubject
from app.cloud_sync.projection import project_rows
from app.observability.identity import FaeSessionIdentity
from app.observability.models import Page, SessionDetail, TurnDetail
from app.sync_remote.importer import normalize_row
from test_cloud_sync_sources import NOW, UID, records, encode
from test_control_plane_migration import control_database  # noqa: F401
from test_fae_session_identity import summary

DAQ = 'ai-daq-fae-agent'


def daq_summary(**changes):
    return summary('enterprise_member').model_copy(update={'agent_id': DAQ, **changes})


def test_daq_trusted_owner_survives_opt_in_without_rekeying_any_business_record():
    raw, = project_rows('daq', records('daq'))
    assert raw.verified_subject == VerifiedSubject(UID, 'enterprise_member')
    legacy = encode(replace(raw, verified_subject=None))
    current = encode(raw, include_verified_subject=True)
    assert current.pop('verified_subject') == {'subject_id': UID, 'subject_type': 'enterprise_member'}
    assert current == legacy


@pytest.mark.parametrize('changes', [
    {'owner_subject_id': None, 'owner_subject_type': None},
    {'owner_subject_type': None}, {'user_id': str(uuid4())},
    {'metadata': {'authentication_mode': 'platform_partner'}},
    {'owner_subject_type': 'partner_operator', 'user_id': None},
    {'owner_subject_id': 'malformed'}, {'internal_user_id': str(uuid4())},
])
def test_unconfirmed_daq_owner_stays_unknown_despite_metadata_claims(changes):
    tables = records('daq')
    row = tables['chat_sessions'][0]
    row.update(changes)
    row.setdefault('metadata', {}).update(display_name='不能相信的昵称',
        verified_subject={'subject_id': UID, 'subject_type': 'enterprise_member'})
    raw, = project_rows('daq', tables)
    assert raw.verified_subject is None
    assert raw.primary_sender_name is None
    imported = normalize_row('daq', 'chat_sessions', row, NOW)
    assert 'verified_subject' not in imported.values['details']


def test_daq_import_derives_reference_from_owner_instead_of_metadata():
    row = records('daq')['chat_sessions'][0]
    row['metadata']['verified_subject'] = {'subject_id': str(uuid4()), 'subject_type': 'partner_operator'}
    assert normalize_row('daq', 'chat_sessions', row, NOW).values['details']['verified_subject'] == {
        'subject_id': UID, 'subject_type': 'enterprise_member'}


@pytest.mark.parametrize('agent,kind,subject_type,valid', [
    (DAQ, 'fae', 'enterprise_member', True),
    (DAQ, 'fae', 'partner_operator', False),
    (DAQ, 'admin', 'enterprise_member', False),
    ('ai-fae-agent', 'fae', 'partner_operator', True),
    ('unknown-agent', 'fae', 'enterprise_member', False),
])
def test_verified_reference_preserves_exact_agent_and_type_allowlist(agent, kind, subject_type, valid):
    ref = VerifiedSubject(UID, subject_type)
    if valid:
        ref.require_fae_scope(agent, kind)
    else:
        with pytest.raises(ValueError, match='scope_invalid'):
            ref.require_fae_scope(agent, kind)


class AgentRows:
    def __init__(self):
        self.calls = []

    def read(self, session_id, subject_ids, *, agent_id='ai-fae-agent'):
        self.calls.append((session_id, agent_id, subject_ids))
        return [dict(subject_id=s, subject_type='enterprise_member',
                     display_name='数采提问人' if agent_id == DAQ else '相机提问人') for s in subject_ids]


def test_mixed_list_and_each_turn_are_resolved_in_separate_agent_scopes():
    rows, context = AgentRows(), SimpleNamespace(session_id=uuid4())
    identity = FaeSessionIdentity(rows, content_codec=None)
    camera, daq = summary('enterprise_member'), daq_summary()
    result = identity.hydrate(Page(items=[camera, daq], total=2, limit=50, offset=0), context)
    assert [item.primary_sender_name for item in result.items] == ['相机提问人', '数采提问人']
    assert {agent for _, agent, _ in rows.calls} == {'ai-fae-agent', DAQ}
    detail = SessionDetail(**daq.model_dump(), turns=[TurnDetail(
        turn_key=f'turn:{i}', session_key=daq.session_key, agent_id=DAQ,
        source_kind='fae', turn_index=i, question='原始问题', answer='原始答案', created_at=NOW,
    ) for i in range(2)])
    resolved = identity.hydrate(detail, context)
    assert resolved.primary_sender_name == '数采提问人'
    assert all(t.sender_name == '数采提问人' and t.verified_subject == daq.verified_subject for t in resolved.turns)
    assert all(t.question == '原始问题' and t.answer == '原始答案' for t in resolved.turns)


def test_daq_without_resolver_does_not_display_replica_nickname():
    item = daq_summary(verified_subject=None, primary_sender_name='冒名昵称')
    projected = FaeSessionIdentity.project(item, {})
    assert projected.primary_sender_name is None
    assert projected.sender_identity_status == 'unavailable'


def test_daq_verified_reference_without_enterprise_profile_keeps_identity_but_no_name():
    class MissingProfile:
        def read(self, session_id, subject_ids, **kwargs):
            return [dict(subject_id=s, subject_type='enterprise_member', display_name=None) for s in subject_ids]

    item = daq_summary()
    projected = FaeSessionIdentity(MissingProfile(), content_codec=None).hydrate(
        item, SimpleNamespace(session_id=uuid4()))
    assert projected.verified_subject == item.verified_subject
    assert projected.primary_sender_name is None
    assert projected.sender_identity_status == 'unavailable'


@pytest.mark.postgres
@pytest.mark.parametrize('grant_agent,selected_agent,allowed', [
    (DAQ, DAQ, True), ('ai-fae-agent', DAQ, False),
    (DAQ, 'ai-fae-agent', False), ('ai-fae-agent', 'ai-fae-agent', True),
])
def test_real_database_identity_authorization_is_agent_scoped(control_database, grant_agent, selected_agent, allowed):
    env = control_database['environments']['production']
    actor, session = uuid4(), uuid4()
    with psycopg.connect(env['admin']) as c:
        c.execute("insert into platform_control.internal_users(internal_user_id,role,display_name,status) values (%s,'management_viewer','有范围的观察者','active')", (actor,))
        c.execute("insert into platform_control.agent_access_subjects(subject_id,subject_type,status) values (%s,'enterprise_member','active')", (actor,))
        c.execute('insert into platform_control.enterprise_subject_links(subject_id,internal_user_id) values (%s,%s)', (actor, actor))
        c.execute("insert into platform_control.web_sessions(session_id,internal_user_id,token_hash,token_hash_key_version,csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) values (%s,%s,%s,1,%s,1,now()+interval '1 hour',now()+interval '2 hours')", (session, actor, uuid4().bytes*2, uuid4().bytes*2))
        c.execute('insert into platform_control.observation_grants(observation_grant_id,viewer_internal_user_id,agent_id,created_by) values (%s,%s,%s,%s)', (uuid4(), actor, grant_agent, actor))
    query = 'select * from platform_control.read_agent_session_subjects_v127(%s,%s,%s::uuid[])'
    with psycopg.connect(env['urls']['platform_control_app'], autocommit=True) as c:
        if allowed:
            assert c.execute(query, (session, selected_agent, [actor])).fetchone()[:3] == (actor, 'enterprise_member', '有范围的观察者')
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(query, (session, 'unknown-agent', [actor]))
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(query, (session, selected_agent, [actor]*101))
        else:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(query, (session, selected_agent, [actor]))
    with psycopg.connect(env['admin']) as c:
        c.execute('update platform_control.web_sessions set revoked_at=now() where session_id=%s', (session,))
    with psycopg.connect(env['urls']['platform_control_app'], autocommit=True) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(query, (session, selected_agent, [actor]))
