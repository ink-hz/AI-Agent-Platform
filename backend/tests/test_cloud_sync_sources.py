from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import importlib
from uuid import UUID

import pytest

from app.cloud_replica.crypto import stable_id
from app.cloud_replica.exporter import build_session_record
from app.cloud_replica.sanitize import SanitizationPolicy, sanitize_session
from app.sync_remote.voc_export import project_voc_rows

NOW = datetime(2026, 10, 29, 3, tzinfo=UTC)
SID = '11111111-1111-4111-8111-111111111111'
TID = '22222222-2222-4222-8222-222222222222'
UID = '33333333-3333-4333-8333-333333333333'


def module(name):
    try:
        return importlib.import_module('app.cloud_sync.' + name)
    except ModuleNotFoundError:
        pytest.fail('cloud snapshot adapter contract is not implemented')


def records(kind='fae'):
    prefix = 'admin_' if kind == 'admin' else ''
    return {
        prefix + 'chat_sessions': [dict(id=SID, external_session_id=kind + ':native',
            channel='web', user_id=UID, external_user_id=None,
            conversation_title=None, created_at=NOW, last_active_at=NOW,
            owner_subject_id=UID, owner_subject_type='enterprise_member',
            metadata={'authentication_mode': 'platform_enterprise'})],
        prefix + 'chat_turns': [dict(id=TID, session_id=SID, turn_index=1,
            question='A complete question', answer='A complete answer', created_at=NOW,
            question_at=NOW, answer_at=NOW, outcome='answered', fallback_used=False,
            duration_ms=10, sources=[], done={}, metadata={'agent_id': 'ai-daq-fae-agent'} )],
        prefix + 'turn_feedback': [], prefix + 'turn_reviews': [],
    }


def project(kind, tables=None):
    return module('projection').project_rows(kind, tables or records(kind))


def encode(raw, **kwargs):
    return build_session_record(raw, sanitize_session(raw, SanitizationPolicy()), b'k' * 32, **kwargs)


def test_fae_canonical_identity_and_verified_owner_opt_in():
    raw, = project('fae')
    assert raw.session_key == 'fae:' + SID
    assert raw.turns[0].turn_key == 'fae:' + TID
    assert raw.title == 'A complete question'
    assert raw.user_identity == '***3333'
    assert raw.verified_subject.subject_id == UID
    assert encode(raw)['user_id'] == stable_id('user:fae', '***3333', b'k' * 32)
    assert encode(raw, include_verified_subject=True)['user_id'] == stable_id(
        'user:fae', 'verified-subject:enterprise_member:' + UID, b'k' * 32)


def test_fae_partner_identity_keeps_null_user_and_strict_owner():
    tables = records()
    tables['chat_sessions'][0].update(user_id=None, owner_subject_type='partner_operator',
        metadata={'authentication_mode': 'platform_partner'})
    raw, = project('fae', tables)
    assert raw.user_identity is None
    assert raw.verified_subject.subject_type == 'partner_operator'


def test_fae_metadata_cannot_mint_owner():
    tables = records()
    tables['chat_sessions'][0].update(owner_subject_id=None, owner_subject_type=None,
        metadata={'verified_subject': {'subject_id': UID, 'subject_type': 'enterprise_member'}})
    assert project('fae', tables)[0].verified_subject is None


@pytest.mark.parametrize('changes', [
    {'user_id': SID}, {'owner_subject_type': 'unknown'},
    {'metadata': {'authentication_mode': 'anonymous'}}, {'internal_user_id': SID},
])
def test_invalid_fae_owner_fails_visibly(changes):
    tables = records()
    tables['chat_sessions'][0].update(changes)
    with pytest.raises(ValueError, match='source_identity_unverified'):
        project('fae', tables)


def test_daq_keeps_fae_family_and_user_hmac_scope():
    raw, = project('daq')
    identity = 'daq:' + hashlib.md5(UID.encode()).hexdigest()
    assert (raw.agent_id, raw.source_kind, raw.session_key) == ('ai-daq-fae-agent', 'fae', 'daq:' + SID)
    assert raw.user_identity == identity
    assert raw.verified_subject is None
    assert encode(raw)['user_id'] == stable_id('user:fae', identity, b'k' * 32)
    assert encode(raw)['key'] != encode(project('fae')[0])['key']


def test_admin_full_platform_evidence_is_required_before_metadata_names_are_displayed():
    tables = records('admin')
    row = tables['admin_chat_sessions'][0]
    row.update(user_id=UID, external_user_id='platform:' + UID, internal_user_id=UID,
        verification_method='platform_session', verified_at=NOW,
        metadata={'display_name': '  Verified name  ', 'primary_department': '  Department  '})
    raw, = project('admin', tables)
    assert raw.user_identity == 'admin:' + hashlib.md5(UID.encode()).hexdigest()
    assert raw.primary_sender_name == 'Verified name' and raw.primary_sender_department == 'Department'
    assert raw.details['verified_internal_user_id'] == UID
    row['verified_at'] = None
    raw, = project('admin', tables)
    assert raw.user_identity == '***3333'
    assert 'verified_internal_user_id' not in raw.details
    assert raw.primary_sender_name is None and raw.primary_sender_department is None


def test_admin_verified_blank_names_match_existing_view_null_semantics():
    tables = records('admin')
    row = tables['admin_chat_sessions'][0]
    row.update(user_id=UID, external_user_id='platform:' + UID, internal_user_id=UID,
        verification_method='platform_session', verified_at=NOW,
        metadata={'display_name': '   ', 'primary_department': ''})
    raw, = project('admin', tables)
    assert raw.primary_sender_name is None and raw.primary_sender_department is None


def test_child_feedback_review_changes_are_preserved_without_session_timestamp_change():
    tables = records()
    first = project('fae', tables)[0]
    tables['turn_feedback'] = [dict(id=SID, turn_id=TID, rating='bad', created_at=NOW)]
    tables['turn_reviews'] = [dict(id=TID, turn_id=TID, review_status='fixed', created_at=NOW, updated_at=NOW)]
    second = project('fae', tables)[0]
    assert second.last_active_at == first.last_active_at
    assert second.turns[0].feedback_sentiments == ('negative',)
    assert second.turns[0].review_statuses == ('fixed',)
    assert encode(first) != encode(second)


def test_failed_native_turn_stays_failed_with_original_timestamps():
    tables = records()
    tables['chat_turns'][0].update(answer='', answer_at=None, question_at=None, outcome='cancelled')
    turn = project('fae', tables)[0].turns[0]
    assert turn.answer == '' and turn.outcome == 'cancelled'
    assert turn.created_at == NOW and turn.question_at is None and turn.answer_at is None
    assert turn.answer_time_status == 'unavailable'


@pytest.mark.parametrize('mutation', ['duplicate_session', 'duplicate_turn', 'orphan_turn', 'orphan_feedback', 'missing_table', 'naive_time', 'wrong_text'])
def test_incomplete_or_malformed_snapshot_is_never_silently_dropped(mutation):
    tables = records()
    if mutation == 'duplicate_session': tables['chat_sessions'] *= 2
    if mutation == 'duplicate_turn': tables['chat_turns'] *= 2
    if mutation == 'orphan_turn': tables['chat_turns'][0]['session_id'] = TID
    if mutation == 'orphan_feedback': tables['turn_feedback'] = [dict(id=SID, turn_id=SID, created_at=NOW, rating='good')]
    if mutation == 'missing_table': del tables['turn_reviews']
    if mutation == 'naive_time': tables['chat_sessions'][0]['created_at'] = NOW.replace(tzinfo=None)
    if mutation == 'wrong_text': tables['chat_turns'][0]['answer'] = {'wrong': 'shape'}
    with pytest.raises(ValueError, match='source_'):
        project('fae', tables)


def test_voc_uuid5_grouping_and_only_sent_answers_match_existing_projection():
    rows = [dict(id=1, internal_user_id=UID, conversation_id='conversation', conversation_type='group',
        received_at=NOW, question_at=NOW, question='VOC question', answer='sent text', answer_at=NOW,
        delivery_status='sent', job_status='completed', sender_name='Employee', sender_department='Support'),
        dict(id=2, internal_user_id=UID, conversation_id='conversation', conversation_type='group',
        received_at=NOW, question_at=NOW, question='Pending question', answer='must not publish', answer_at=NOW,
        delivery_status='pending', job_status='claimed')]
    legacy = project_voc_rows(rows)
    raw, = module('projection').project_voc_snapshot(rows)
    assert raw.session_key == 'voc:' + legacy['chat_sessions'][0]['id']
    assert raw.turns[0].turn_key == 'voc:' + legacy['chat_turns'][0]['id']
    assert raw.user_identity == 'voc:' + hashlib.md5(UID.encode()).hexdigest()
    assert raw.turns[1].answer == '' and raw.turns[1].outcome == 'delivery_pending'
    assert raw.turns[1].answer_at is None
    assert raw.primary_sender_name == 'Employee'


def test_profiles_reject_agent_schema_mismatch_before_connecting(tmp_path):
    models = module('models')
    for agent, kind, schema in [('ai-fae-agent', 'daq', 'daq_fae'), ('ai-daq-fae-agent', 'daq', 'public'), ('voc', 'voc', 'public')]:
        with pytest.raises(ValueError, match='source_profile_invalid'):
            models.SourceProfile(agent, kind, str(tmp_path / 'dsn'), 'database', schema)


@pytest.mark.parametrize('location', ['session', 'turn'])
def test_metadata_must_have_object_shape(location):
    tables = records()
    tables['chat_sessions' if location == 'session' else 'chat_turns'][0]['metadata'] = ['invalid']
    with pytest.raises(ValueError, match='source_row_invalid'):
        project('fae', tables)


@pytest.mark.parametrize('outcome', ['failed', 'cancelled', 'error', 'processing', 'pending'])
def test_error_or_inflight_turn_text_never_becomes_successful_trace(outcome):
    tables = records()
    tables['chat_turns'][0].update(answer='Visible native status text', outcome=outcome)
    turn = project('fae', tables)[0].turns[0]
    assert turn.answer == 'Visible native status text'
    assert turn.trace.status == outcome


@pytest.mark.parametrize('kind',['fae','admin','daq'])
def test_zero_based_native_turn_index_is_preserved(kind):
    tables = records(kind)
    tables[('admin_' if kind == 'admin' else '') + 'chat_turns'][0]['turn_index'] = 0
    raw, = project(kind,tables)
    assert raw.turns[0].turn_index == 0


@pytest.mark.parametrize('kind',['fae','admin','daq','voc'])
def test_existing_exporter_and_legacy_assembler_have_identical_stable_identity_bytes(kind):
    from app.cloud_replica.source import ReplicaSource
    if kind == 'voc':
        native = project_voc_rows([dict(id=1, internal_user_id=UID, conversation_id='conversation',
            conversation_type='group', received_at=NOW, question='Question', answer='Answer',
            answer_at=NOW, delivery_status='sent', question_at=NOW)])
        raw, = project('voc', native)
        expected_sid, expected_tid = native['chat_sessions'][0]['id'], native['chat_turns'][0]['id']
        expected_user = 'voc:' + hashlib.md5(UID.encode()).hexdigest()
    else:
        raw, = project(kind)
        expected_sid, expected_tid = SID,TID
        expected_user = 'daq:' + hashlib.md5(UID.encode()).hexdigest() if kind == 'daq' else '***3333'
    # These are the existing SQL-view identities, passed through the actual
    # legacy source assembler and unchanged encoder, not a second hash implementation.
    legacy, = ReplicaSource._assemble([dict(session_key=kind+':'+expected_sid,
        agent_id={'fae':'ai-fae-agent','admin':'ai-admin-agent','daq':'ai-daq-fae-agent','voc':'voc'}[kind],
        source_kind='fae' if kind in {'fae','daq'} else kind, channel=raw.channel,
        title=raw.title, user_identity=expected_user, primary_sender_name=None,
        primary_sender_department=None, created_at=NOW,last_active_at=NOW,replica_updated_at=NOW)],
        [dict(turn_key=kind+':'+expected_tid,session_key=kind+':'+expected_sid,turn_index=raw.turns[0].turn_index,
            question=raw.turns[0].question,answer=raw.turns[0].answer,created_at=NOW,
            question_at=NOW,answer_at=NOW,question_time_status='exact',answer_time_status='exact',
            outcome=raw.turns[0].outcome,fallback_used=raw.turns[0].fallback_used,duration_ms=raw.turns[0].duration_ms)],[],[],[])
    new_record, old_record = encode(raw),encode(legacy)
    assert (new_record['key'],new_record['user_id'],new_record['turns'][0]['key']) == (
        old_record['key'],old_record['user_id'],old_record['turns'][0]['key'])
